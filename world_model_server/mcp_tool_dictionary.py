"""
Business Logic Strings dictionary for AI-agent tool calls.

Translates raw MCP / hook tool names (as they land in Etch's events.db)
into English strings a compliance officer, auditor, or lawyer can read
without needing to understand the underlying protocol.

Design contract, in order of importance:

1. **Cross-examination test (Gemini, 2026-07-21):**
   If an auditor asks "why does this log say the agent 'read customer
   data' when the raw payload says `list_directories`?", the answer
   must be that the string was hand-authored to describe the mechanism,
   not inferred by an LLM. Compliance evidence bundles ALWAYS print
   both the raw JSON payload AND the Business String side-by-side —
   the lawyer reads the string, the security engineer reads the JSON,
   and both are cryptographically bound to the same signed row.

2. **Hand-authored only.** Every entry is a considered translation.
   LLM assistance is banned for entries in this file. Compliance
   language must survive a court exhibit; probabilistic generation
   cannot.

3. **Accurate, not sensationalised.** A `Bash` call is "Agent ran a
   shell command," not "Agent seized system control." A `Read` is
   "Agent read a file," not "Agent exfiltrated data." Alarm is the
   auditor's job, not this file's.

4. **Prefix-tolerant.** Tool names arrive with runtime prefixes
   (`claude-code:Bash`, `cursor:Read`, `github:commit`,
   `mcp:my-server:add_fact`). `lookup()` strips the prefix and
   resolves against the bare name so we don't need per-runtime
   duplicates.

5. **Fallback with dignity.** Unknown tools resolve to a neutral
   "Agent performed an action" plus severity `info`. Never leak the
   raw tool name into the Business String; the fallback is
   deliberate, not a bug.

Severity tag maps to the Compliance-view color palette:
  - info    = teal   = read-only or planning operations
  - warning = amber  = state-change operations
  - critical = red   = destructive, secrets-adjacent, or external-egress

Severity can be dynamically upgraded by inspecting `tool_input` at
render-time (e.g. a `Bash` command containing `rm -rf` or a `Read`
of a file matching `.env` / `credentials`). That's a v2 concern;
this file ships the base severity per tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolTranslation:
    """Hand-authored translation of a raw tool name."""
    raw_tool: str            # canonical bare tool name (no runtime prefix)
    business_string: str     # readable English for the audit trail
    severity: str            # "info" | "warning" | "critical"
    category: str            # short label used to group in the UI


# ---------------------------------------------------------------------------
# The dictionary
# ---------------------------------------------------------------------------
#
# Entries kept in alphabetical order within category for reviewability.
# When adding a new entry, also add an assertion in the test file so the
# regression suite catches accidental deletion.


_ENTRIES: tuple[ToolTranslation, ...] = (

    # ----- File system: read-only -----
    ToolTranslation("Glob", "Agent listed files matching a pattern",
                    "info", "Filesystem read"),
    ToolTranslation("Grep", "Agent searched files for a text pattern",
                    "info", "Filesystem read"),
    ToolTranslation("Read", "Agent read a file",
                    "info", "Filesystem read"),

    # ----- File system: mutation -----
    ToolTranslation("Edit", "Agent modified a file",
                    "warning", "Filesystem write"),
    ToolTranslation("NotebookEdit", "Agent modified a Jupyter notebook cell",
                    "warning", "Filesystem write"),
    ToolTranslation("Write", "Agent created or overwrote a file",
                    "warning", "Filesystem write"),

    # ----- Shell / execution -----
    # Bash defaults to `warning`. Callers that inspect the command
    # string should upgrade to `critical` on sudo, rm -rf, curl to
    # untrusted hosts, or credential-file reads.
    ToolTranslation("Bash", "Agent ran a shell command",
                    "warning", "Shell execution"),

    # ----- Web / external egress -----
    ToolTranslation("WebFetch", "Agent retrieved content from an external URL",
                    "warning", "External data egress"),
    ToolTranslation("WebSearch", "Agent queried an external search provider",
                    "info", "External data egress"),

    # ----- Agent / task delegation -----
    ToolTranslation("Agent", "Agent delegated work to a sub-agent",
                    "info", "Task delegation"),
    ToolTranslation("Task", "Agent delegated work to a sub-agent",
                    "info", "Task delegation"),
    ToolTranslation("SubagentStop", "Sub-agent stopped",
                    "info", "Task delegation"),
    ToolTranslation("ToolSearch", "Agent searched for available tool schemas",
                    "info", "Task delegation"),
    ToolTranslation("TodoWrite", "Agent updated its internal task list",
                    "info", "Task delegation"),

    # ----- Session lifecycle (Claude Code hooks) -----
    ToolTranslation("Notification", "Notification recorded",
                    "info", "Session lifecycle"),
    ToolTranslation("PostToolUse", "Agent completed a tool call",
                    "info", "Session lifecycle"),
    ToolTranslation("PreCompact", "Agent context compaction started",
                    "info", "Session lifecycle"),
    ToolTranslation("SessionEnd", "Agent session ended",
                    "info", "Session lifecycle"),
    ToolTranslation("SessionStart", "Agent session started",
                    "info", "Session lifecycle"),
    ToolTranslation("Stop", "Agent stopped",
                    "info", "Session lifecycle"),
    ToolTranslation("UserPromptSubmit", "User submitted a prompt",
                    "info", "Session lifecycle"),

    # ----- Version control (github push webhook) -----
    ToolTranslation("commit", "Developer committed code to a repository",
                    "info", "Version control"),
    ToolTranslation("push", "Developer pushed commits to a remote branch",
                    "info", "Version control"),
    ToolTranslation("pull_request", "Developer opened or updated a pull request",
                    "info", "Version control"),

    # ----- world-model-mcp / knowledge-graph tools -----
    ToolTranslation("add_constraint",
                    "Constraint added to the knowledge graph",
                    "warning", "Knowledge graph"),
    ToolTranslation("add_fact", "Fact added to the knowledge graph",
                    "warning", "Knowledge graph"),
    ToolTranslation("query_fact", "Knowledge graph queried",
                    "info", "Knowledge graph"),
    ToolTranslation("record_correction",
                    "Correction to a prior fact recorded on the signed chain",
                    "warning", "Knowledge graph"),
    ToolTranslation("record_event", "Event recorded on the signed chain",
                    "info", "Knowledge graph"),
    ToolTranslation("resolve_contradiction",
                    "Contradiction between facts resolved",
                    "warning", "Knowledge graph"),

    # ----- Audit chain / verification -----
    ToolTranslation("get_audit_log_head", "Audit chain head fetched",
                    "info", "Audit chain"),
    ToolTranslation("prove_entry_inclusion", "Merkle inclusion proof requested",
                    "info", "Audit chain"),
    ToolTranslation("verify_retrieval",
                    "Retrieval verified by an adversarial Coach LLM",
                    "info", "Audit chain"),
)


_LOOKUP: dict[str, ToolTranslation] = {t.raw_tool: t for t in _ENTRIES}

_FALLBACK = ToolTranslation(
    raw_tool="__fallback__",
    business_string="Agent performed an action",
    severity="info",
    category="Uncategorised",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lookup(tool_name: Optional[str]) -> ToolTranslation:
    """
    Resolve a raw tool_name (possibly runtime-prefixed) to a hand-authored
    translation. Never returns None; unknowns get the fallback so the UI
    can always show something safe.

    Prefix handling:
      "claude-code:Bash"   -> lookup("Bash")
      "cursor:Read"        -> lookup("Read")
      "github:commit"      -> lookup("commit")
      "mcp:my-server:add_fact" -> lookup("add_fact")
      "Bash"               -> lookup("Bash")  (already bare)
    """
    if not tool_name:
        return _FALLBACK
    bare = tool_name.rsplit(":", 1)[-1].strip()
    return _LOOKUP.get(bare, _FALLBACK)


def all_translations() -> tuple[ToolTranslation, ...]:
    """Return every entry in insertion order — useful for building a
    reviewable table in the operator dashboard."""
    return _ENTRIES


def categories() -> list[str]:
    """Distinct categories in the order they first appear in _ENTRIES."""
    seen: list[str] = []
    for t in _ENTRIES:
        if t.category not in seen:
            seen.append(t.category)
    return seen


def coverage() -> dict:
    """Return {'entry_count': N, 'category_count': N, 'severity_counts':
    {info: N, warning: N, critical: N}}. Used by the readiness-signal
    check that guards against accidental deletion of entries."""
    from collections import Counter
    sev = Counter(t.severity for t in _ENTRIES)
    return {
        "entry_count": len(_ENTRIES),
        "category_count": len(categories()),
        "severity_counts": dict(sev),
    }
