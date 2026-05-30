"""
AGENTS.md / .agents/skills/ constraint reader (v0.7.4 F1).

Reads project-level conventions from AGENTS.md and .agents/skills/*.md files
and surfaces them as virtual constraints alongside the SQLite-backed
constraints used by hook_helper.

Why
---
AGENTS.md is the de-facto standard the community has converged on
(anthropics/claude-code#6235 has >4000 thumbs-up; Zed and Cline adopted it
natively). Treating these files as a first-class constraint source means
world-model-mcp's PreToolUse enforcement covers both:

  * SQLite constraints: learned from corrections, ranked by violation count,
    can graduate to "hard deny" tier after repeat violations
  * AGENTS.md constraints: declarative project conventions a developer wrote
    by hand. Severity defaults to "warning" -- they advise, they do not deny
    unless the author explicitly marks them as `severity: error`

Format support
--------------
Two extraction modes, both safe and deterministic (no LLM calls):

1. Structured fence blocks (preferred for new projects):

       ```constraint
       rule: no-console-log
       severity: error
       file_pattern: "*.ts"
       description: Use logger.debug() not console.log()
       ```

   Or YAML frontmatter with a ``constraints:`` list.

2. Heuristic imperative-sentence extraction (works on existing prose
   AGENTS.md files). Looks for sentence-initial imperatives like
   "Use X", "Never Y", "Always Z", "Prefer A over B". One imperative
   produces one virtual constraint with severity="info".

Public surface
--------------
- read_agents_constraints(project_dir) -> List[dict]
- virtual_constraints_for(project_dir, file_path) -> List[dict]

Both return dicts shaped like the SQLite constraint rows so they can be
mixed into hook_helper.classify() without schema changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

#: File names treated as project-root constraint sources.
ROOT_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "AGENTS.MD")

#: Sub-directories under the project root that hold skill / rule files.
SKILL_DIRS = (".agents/skills", ".agents/rules", ".claude/skills")


def iter_agent_files(project_dir: Path) -> Iterable[Path]:
    """Yield every file the reader should consider, in priority order."""
    pd = Path(project_dir)
    for name in ROOT_FILES:
        p = pd / name
        if p.exists() and p.is_file():
            yield p
    for sub in SKILL_DIRS:
        d = pd / sub
        if d.exists() and d.is_dir():
            for p in sorted(d.glob("*.md")):
                yield p


# ---------------------------------------------------------------------------
# Structured extraction
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(
    r"^```(?:constraint|rule)\s*\n(.*?)\n```",
    flags=re.MULTILINE | re.DOTALL,
)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", flags=re.DOTALL)


def _parse_fence_body(body: str) -> Optional[dict]:
    """Parse the body of a ```constraint fence as a key:value YAML-ish block."""
    fields: dict = {}
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            fields[key] = value
    if not fields.get("rule") and not fields.get("rule_name"):
        return None
    return _normalize(fields)


def _parse_frontmatter(text: str) -> List[dict]:
    """Try to extract a `constraints:` list from YAML frontmatter.

    We avoid a real YAML dep to keep stdio installs zero-dep; we look for
    the minimal `constraints:` list shape and bail on anything fancy.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return []
    body = m.group(1)
    if "constraints:" not in body:
        return []

    constraints: list[dict] = []
    current: dict | None = None
    in_list = False
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.strip() == "constraints:":
            in_list = True
            continue
        if not in_list:
            continue
        stripped = line.lstrip()
        if line.startswith("- ") or stripped.startswith("- "):
            if current is not None:
                norm = _normalize(current)
                if norm:
                    constraints.append(norm)
            current = {}
            after = stripped[2:]
            if ":" in after:
                k, _, v = after.partition(":")
                current[k.strip()] = v.strip().strip('"').strip("'")
            continue
        if current is not None and ":" in stripped:
            k, _, v = stripped.partition(":")
            current[k.strip()] = v.strip().strip('"').strip("'")
    if current is not None:
        norm = _normalize(current)
        if norm:
            constraints.append(norm)
    return constraints


_VALID_SEVERITIES = ("error", "warning", "info")


def _normalize(raw: dict) -> Optional[dict]:
    """Normalize an extracted fence/frontmatter row into the shared dict shape."""
    rule = raw.get("rule") or raw.get("rule_name") or raw.get("name")
    if not rule:
        return None
    description = raw.get("description") or raw.get("desc") or rule
    severity = (raw.get("severity") or "warning").lower()
    if severity not in _VALID_SEVERITIES:
        severity = "warning"
    file_pattern = raw.get("file_pattern") or raw.get("files") or raw.get("glob")
    constraint_type = (raw.get("type") or raw.get("constraint_type") or "style").lower()
    return {
        "rule_name": rule,
        "description": description.strip(),
        "severity": severity,
        "file_pattern": file_pattern,
        "constraint_type": constraint_type,
        "source": "agents_md",
        "violation_count": 0,
        "examples": [],
    }


# ---------------------------------------------------------------------------
# Heuristic extraction
# ---------------------------------------------------------------------------

#: Sentence-initial imperative markers we treat as constraints. The match is
#: anchored so prose discussions ("I use X when ...") don't false-positive.
_IMPERATIVE_RE = re.compile(
    r"^\s*[*\-]?\s*"                     # bullet or list marker
    r"(?P<verb>Use|Never|Always|Avoid|Do not|Don't|Prefer|Require|Forbid)"
    r"\s+(?P<rest>.+?)\.?$",
    flags=re.IGNORECASE,
)

_STRONG_VERBS = {"never", "always", "do not", "don't", "forbid"}
_SOFT_VERBS = {"use", "avoid", "prefer", "require"}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not s:
        s = hashlib.sha1(text.encode()).hexdigest()[:10]
    return s[:60]


def _extract_imperatives(text: str, max_per_file: int = 32) -> List[dict]:
    """Heuristic extraction: imperative sentences -> virtual constraints."""
    out: list[dict] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _IMPERATIVE_RE.match(line)
        if not m:
            continue
        verb = m.group("verb").lower()
        rest = m.group("rest").strip()
        if not rest or len(rest) < 4:
            continue
        rule_seed = f"{verb}-{rest}"[:80]
        rule = "agents-md-" + _slug(rule_seed)
        if rule in seen:
            continue
        seen.add(rule)
        severity = "warning" if verb in _STRONG_VERBS else "info"
        out.append({
            "rule_name": rule,
            "description": f"{verb.capitalize()} {rest}.",
            "severity": severity,
            "file_pattern": None,
            "constraint_type": "style",
            "source": "agents_md",
            "violation_count": 0,
            "examples": [],
        })
        if len(out) >= max_per_file:
            break
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_agents_constraints(project_dir: str | Path) -> List[dict]:
    """Read all AGENTS.md / skills files under ``project_dir`` and return a
    list of constraint dicts. Order: structured fences first, then frontmatter
    entries, then heuristic imperatives.

    The result is *additive* -- callers should mix it with SQLite-backed
    constraints, not replace them.
    """
    project_dir = Path(project_dir)
    if not project_dir.exists():
        return []

    all_constraints: list[dict] = []
    seen_rules: set[str] = set()

    for file in iter_agent_files(project_dir):
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("agents_md_reader: cannot read %s: %s", file, exc)
            continue

        # 1. Fenced blocks
        for body in _FENCE_RE.findall(text):
            norm = _parse_fence_body(body)
            if norm and norm["rule_name"] not in seen_rules:
                norm["_source_file"] = str(file.relative_to(project_dir))
                all_constraints.append(norm)
                seen_rules.add(norm["rule_name"])

        # 2. YAML frontmatter
        for norm in _parse_frontmatter(text):
            if norm["rule_name"] not in seen_rules:
                norm["_source_file"] = str(file.relative_to(project_dir))
                all_constraints.append(norm)
                seen_rules.add(norm["rule_name"])

        # 3. Heuristic imperatives (lowest priority; many of these will be soft)
        for norm in _extract_imperatives(text):
            if norm["rule_name"] not in seen_rules:
                norm["_source_file"] = str(file.relative_to(project_dir))
                all_constraints.append(norm)
                seen_rules.add(norm["rule_name"])

    return all_constraints


def virtual_constraints_for(
    project_dir: str | Path,
    file_path: Optional[str] = None,
) -> List[dict]:
    """Return AGENTS.md constraints filtered by file glob, ready to merge
    into hook_helper.classify()'s constraint list.

    If ``file_path`` is None or no constraints declare a file_pattern, all
    matched. Otherwise only constraints whose file_pattern matches.
    """
    all_c = read_agents_constraints(project_dir)
    if not file_path:
        return all_c
    out: list[dict] = []
    for c in all_c:
        pattern = c.get("file_pattern")
        if not pattern:
            out.append(c)
            continue
        try:
            if fnmatch(file_path, pattern):
                out.append(c)
                continue
            # Support ** in patterns via a permissive collapse
            if "**" in pattern:
                relaxed = pattern.replace("**/", "*").replace("**", "*")
                if fnmatch(file_path, relaxed):
                    out.append(c)
        except Exception:
            continue
    return out


def to_json(constraints: List[dict]) -> str:
    """Serialize a constraint list for the MCP tool response."""
    return json.dumps({"count": len(constraints), "constraints": constraints}, indent=2)
