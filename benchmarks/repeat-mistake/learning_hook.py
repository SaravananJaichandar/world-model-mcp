"""
Phase 5: Constraint extraction from classified baseline failures.

Reads the classified failures file produced by failure_classifier.py and
extracts learnable constraints. The output is a constraints.json file
that the treatment arm orchestrator loads via --constraints.

Per-category extraction strategy (locked in DESIGN.md):

- Tool-Use: extract the failing tool pattern as a soft constraint
  ("avoid running X with arguments Y when working on Z-type problems")
- Incorrect File: extract the wrong-file path and the repo as a hard
  constraint ("for django ORDER BY issues, the fix is in get_order_by
  not in column-selection code")
- Endless File Reading: extract the file pattern read repeatedly as a
  "do not re-read this file family without first making an edit"
  guidance constraint
- Wrong Solution: extract the failing approach as a fact ("this
  approach didn't work: <one-line summary>")
- Syntax Error: extract the syntactic pattern as a hard constraint
  ("when modifying X, validate the syntax before committing")
- Misunderstood Problem Statement: extract a "verify-before-act"
  guidance constraint ("for this kind of problem, re-read the
  problem statement and verify which behavior is expected")
- Other: skip; no constraint extracted

Constraint extraction is done by Claude headless (same model as the
agent and judge). The extraction prompt is the SAME for all tasks
within a category (locked verbatim below). Each extracted constraint
is one short string that will be appended to the treatment arm's
agent prompt.

The output is shaped so that the orchestrator's _load_constraints
function reads it: {"constraints": ["...", "...", ...]}.

The treatment arm loads ALL constraints (across all baseline failures
for the same subset) into every treatment-arm prompt. This is the
"learning loop" the essay describes: the agent sees what failed last
time when it attempts a related task.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from claude_client import ClaudeClient  # noqa: E402

# Categories that produce constraints. "Other" is skipped because the
# failure mode is not actionable enough.
EXTRACTABLE_CATEGORIES = {
    "Tool-Use",
    "Incorrect File",
    "Endless File Reading",
    "Wrong Solution",
    "Syntax Error",
    "Misunderstood Problem Statement",
}

EXTRACTION_PROMPT = """You are extracting a single short learnable constraint from a coding agent's failed attempt. This constraint will be shown to the agent on its next attempt at a similar task, so it must be:

1. Concrete and actionable (not vague advice).
2. One line, under 200 characters.
3. Specific to the failure mode, not generic engineering wisdom.
4. Phrased as a directive the agent can follow (e.g., "When editing X, do Y", "For this kind of problem, look in Z first").

The failure mode category for this attempt was: {category}

The agent's reasoning (from the classifier): {reasoning}

The original problem statement:
{problem_statement}

The agent's patch attempt (may be empty):
{patch}

Output EXACTLY one line in this format. No commentary, no preamble.

CONSTRAINT: <your one-line constraint>"""


@dataclass
class ExtractedConstraint:
    instance_id: str
    repo: str
    category: str
    constraint: str
    extraction_raw: str
    extraction_duration_sec: float


def _parse_constraint(raw: str) -> str:
    """Parse 'CONSTRAINT: X' from the extraction output."""
    if not raw:
        return ""
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("CONSTRAINT:"):
            return line.split(":", 1)[1].strip()
    # Fallback: use the first non-empty line
    for line in raw.splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def extract_one(
    *,
    instance_id: str,
    repo: str,
    category: str,
    reasoning: str,
    problem_statement: str,
    patch: str,
    client: ClaudeClient,
) -> ExtractedConstraint:
    prompt = EXTRACTION_PROMPT.format(
        category=category,
        reasoning=reasoning,
        problem_statement=problem_statement[:2000],
        patch=patch[:1500] if patch else "(empty patch)",
    )

    t0 = time.monotonic()
    result = client.complete(prompt, timeout_sec=90)
    dt = time.monotonic() - t0

    if not result.ok:
        return ExtractedConstraint(
            instance_id=instance_id,
            repo=repo,
            category=category,
            constraint="",
            extraction_raw=f"FAILED: {result.error}",
            extraction_duration_sec=dt,
        )

    constraint = _parse_constraint(result.text)
    return ExtractedConstraint(
        instance_id=instance_id,
        repo=repo,
        category=category,
        constraint=constraint,
        extraction_raw=result.text,
        extraction_duration_sec=dt,
    )


def _load_classified(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _load_progress(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            iid = rec.get("instance_id")
            if iid:
                out[iid] = rec
        except json.JSONDecodeError:
            continue
    return out


def _load_tasks_lookup() -> dict:
    from task_setup import load_subset
    return {t.instance_id: t for t in load_subset()}


def extract_from_classified(
    *,
    classified_path: Path,
    progress_path: Optional[Path],
    out_path: Path,
    raw_log_path: Optional[Path] = None,
    client: Optional[ClaudeClient] = None,
    on_progress=None,
) -> dict:
    """Extract constraints from a classified failures file.

    Writes:
      out_path:      {"constraints": [...]}   (used by treatment arm)
      raw_log_path:  one JSON per extraction (for audit)
    """
    if client is None:
        client = ClaudeClient(timeout_sec=90, max_retries=3)

    classified = _load_classified(classified_path)
    progress = _load_progress(progress_path) if progress_path else {}
    tasks = _load_tasks_lookup()

    constraint_strings: list[str] = []
    per_category: dict[str, int] = {}
    raw_records: list[dict] = []

    skipped = 0
    extracted = 0

    for i, cls in enumerate(classified, start=1):
        iid = cls.get("instance_id")
        category = cls.get("category", "Other")
        reasoning = cls.get("reasoning", "")
        if category not in EXTRACTABLE_CATEGORIES:
            skipped += 1
            continue

        task = tasks.get(iid)
        repo = task.repo if task else "unknown"
        problem_statement = task.problem_statement if task else "(task not found)"
        patch = progress.get(iid, {}).get("extracted_patch", "") if progress else ""

        if on_progress:
            on_progress(f"[{i}/{len(classified)}] extracting from {iid} ({category})")

        ec = extract_one(
            instance_id=iid,
            repo=repo,
            category=category,
            reasoning=reasoning,
            problem_statement=problem_statement,
            patch=patch,
            client=client,
        )

        raw_records.append({
            "instance_id": ec.instance_id,
            "repo": ec.repo,
            "category": ec.category,
            "constraint": ec.constraint,
            "extraction_raw": ec.extraction_raw,
            "extraction_duration_sec": ec.extraction_duration_sec,
        })

        if ec.constraint:
            tag = f"[{ec.repo}/{ec.category}] {ec.constraint}"
            constraint_strings.append(tag)
            per_category[ec.category] = per_category.get(ec.category, 0) + 1
            extracted += 1
            if on_progress:
                on_progress(f"    -> {tag[:160]}")
        else:
            if on_progress:
                on_progress(f"    -> (empty extraction)")

    # Deduplicate (preserve order)
    seen: set[str] = set()
    deduped: list[str] = []
    for c in constraint_strings:
        if c in seen:
            continue
        seen.add(c)
        deduped.append(c)

    out_path.write_text(json.dumps({"constraints": deduped}, indent=2))
    if raw_log_path is not None:
        with raw_log_path.open("w") as f:
            for rec in raw_records:
                f.write(json.dumps(rec) + "\n")

    summary = {
        "classified_path": str(classified_path),
        "out_path": str(out_path),
        "total_classified": len(classified),
        "skipped_other": skipped,
        "extracted": extracted,
        "constraints_emitted": len(deduped),
        "per_category": per_category,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--classified", required=True, type=str,
        help="Path to <arm>_classified.jsonl from failure_classifier",
    )
    parser.add_argument(
        "--progress", default=None, type=str,
        help="Optional path to <arm>_progress.jsonl from orchestrator (used to read agent patches)",
    )
    parser.add_argument(
        "--out", required=True, type=str,
        help="Path to write constraints.json",
    )
    parser.add_argument(
        "--raw-log", default=None, type=str,
        help="Optional path to write per-extraction raw log",
    )
    args = parser.parse_args()

    classified_path = Path(args.classified).resolve()
    progress_path = Path(args.progress).resolve() if args.progress else None
    out_path = Path(args.out).resolve()
    raw_log_path = Path(args.raw_log).resolve() if args.raw_log else None

    def log(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    log(f"Extracting constraints from {classified_path}")
    summary = extract_from_classified(
        classified_path=classified_path,
        progress_path=progress_path,
        out_path=out_path,
        raw_log_path=raw_log_path,
        on_progress=log,
    )

    print()
    print("=" * 60)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
