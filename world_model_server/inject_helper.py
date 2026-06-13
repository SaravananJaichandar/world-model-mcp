"""
PostCompact / UserPromptSubmit injection helper (v0.7.0 F1).

Reads a hook payload from stdin, pulls a compact constraint+fact bundle from
the local knowledge graph, and writes a context-injection JSON to stdout that
the agent will splice back into its working context.

Designed to fail open: any error returns {} so it never breaks an active
session if the world model is unavailable.

Input shape (stdin):
{
    "event": "PostCompact" | "UserPromptSubmit" | "SessionStart",
    "project_dir": str,
    "session_id": str (optional),
    "user_prompt": str (optional, for UserPromptSubmit),
    "pre_compact_tokens": int (optional, for PostCompact),
    "post_compact_tokens": int (optional, for PostCompact),
    "max_constraints": int (optional, default 10),
    "max_facts": int (optional, default 10)
}

Output shape (stdout):
{
    "hookSpecificOutput": {
        "hookEventName": "<event>",
        "additionalContext": "<markdown bundle>"
    },
    "facts_count": int,
    "constraints_count": int,
    "audit_id": str (set if a compaction audit row was written)
}
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_constraints(db_dir: Path, limit: int) -> list:
    """Top N constraints by violation_count, read-only."""
    constraints_db = db_dir / "constraints.db"
    if not constraints_db.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{constraints_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT rule_name, description, violation_count "
            "FROM constraints ORDER BY violation_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        logger.warning(f"Constraints read failed: {e}")
        return []


def _load_recent_facts(db_dir: Path, limit: int, search: str | None) -> list:
    """Recent canonical facts, optional LIKE filter, read-only."""
    facts_db = db_dir / "facts.db"
    if not facts_db.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{facts_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if search:
            rows = conn.execute(
                "SELECT fact_text FROM facts WHERE status = 'canonical' AND fact_text LIKE ? "
                "ORDER BY valid_at DESC LIMIT ?",
                (f"%{search}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT fact_text FROM facts WHERE status = 'canonical' "
                "ORDER BY valid_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        logger.warning(f"Facts read failed: {e}")
        return []


def _write_audit_row(
    db_dir: Path,
    event: str,
    session_id: str | None,
    pre_tokens: int | None,
    post_tokens: int | None,
    facts_count: int,
    constraints_count: int,
    summary: str,
) -> str | None:
    """Write one row to compaction_audit (R/W). Returns row id or None on failure."""
    audit_db = db_dir / "audit.db"
    if not audit_db.exists():
        return None
    try:
        row_id = uuid.uuid4().hex
        conn = sqlite3.connect(str(audit_db))
        conn.execute(
            """
            INSERT INTO compaction_audit
              (id, session_id, compacted_at, pre_compact_tokens, post_compact_tokens,
               facts_injected, constraints_injected, injection_event, raw_summary, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                session_id,
                datetime.now().isoformat(),
                pre_tokens,
                post_tokens,
                facts_count,
                constraints_count,
                event,
                summary[:2000] if summary else None,
                "{}",
            ),
        )
        conn.commit()
        conn.close()
        return row_id
    except sqlite3.Error as e:
        logger.warning(f"Audit write failed: {e}")
        return None


def _normalize_payload(payload: dict) -> dict:
    """Accept payload from either Claude Code hooks or Codex CLI hooks.

    Claude Code keys: ``event``, ``project_dir``, ``session_id``,
    ``user_prompt``, ``pre_compact_tokens``, ``post_compact_tokens``.

    Codex CLI keys: ``hook_event_name``, ``cwd``, ``session_id``,
    ``transcript_path``, ``model``, ``permission_mode`` plus event-specific
    fields. Codex does not pass ``project_dir`` (uses ``cwd``) nor
    ``pre/post_compact_tokens`` (we leave those None).

    Returns a normalized dict with Claude-Code-shaped keys so the rest of
    ``build_injection`` does not have to branch.
    """
    if not isinstance(payload, dict):
        return {}
    normalized = dict(payload)
    if "event" not in normalized and "hook_event_name" in normalized:
        normalized["event"] = normalized["hook_event_name"]
    if "project_dir" not in normalized and "cwd" in normalized:
        normalized["project_dir"] = normalized["cwd"]
    return normalized


def build_injection(payload: dict) -> dict:
    payload = _normalize_payload(payload)
    event = payload.get("event", "")
    project_dir = payload.get("project_dir", ".")
    session_id = payload.get("session_id")
    user_prompt = payload.get("user_prompt", "") or ""
    pre_tokens = payload.get("pre_compact_tokens")
    post_tokens = payload.get("post_compact_tokens")
    max_constraints = int(payload.get("max_constraints", 10))
    max_facts = int(payload.get("max_facts", 10))

    if event not in ("PostCompact", "UserPromptSubmit", "SessionStart"):
        return {}

    # v0.7.6 F1: intercept /world-model slash commands BEFORE the
    # search-hint flow. The slash command runs purely from local state
    # and bypasses the constraint/fact load.
    if event == "UserPromptSubmit" and user_prompt:
        try:
            from .slash_command import handle_slash_command
            slash_out = handle_slash_command(user_prompt, project_dir)
            if slash_out is not None:
                return slash_out
        except Exception as exc:
            logger.debug("slash command intercept skipped: %s", exc)

    db_dir = Path(project_dir) / ".claude" / "world-model"
    if not db_dir.exists():
        return {}

    # Use user prompt as a search hint when present (UserPromptSubmit case).
    # Prefer the longest token over short noise; "JWT", "API" etc. should match.
    search = None
    if event == "UserPromptSubmit" and user_prompt:
        tokens = [t.strip(".,:;!?\"'`()[]{}") for t in user_prompt.split()]
        tokens = [t for t in tokens if len(t) >= 3 and t.lower() not in {
            "the", "and", "for", "use", "tell", "what", "how", "why",
            "me", "about", "with", "are", "you", "your",
        }]
        if tokens:
            search = max(tokens, key=len)

    constraints = _load_constraints(db_dir, max_constraints)
    facts = _load_recent_facts(db_dir, max_facts, search)

    if not constraints and not facts:
        return {}

    lines: list = []
    if constraints:
        lines.append("## Active constraints (top by violation count)")
        for c in constraints:
            lines.append(
                f"- {c['rule_name']}: {c['description']} (violated {c['violation_count']}x)"
            )
    if facts:
        if lines:
            lines.append("")
        lines.append("## Recent canonical facts")
        for f in facts:
            lines.append(f"- {f['fact_text']}")

    additional_context = "\n".join(lines).strip()
    if not additional_context:
        return {}

    # Audit (only meaningful on PostCompact)
    audit_id = None
    if event == "PostCompact":
        audit_id = _write_audit_row(
            db_dir, event, session_id, pre_tokens, post_tokens,
            len(facts), len(constraints), additional_context,
        )

    output = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": additional_context,
        },
        "facts_count": len(facts),
        "constraints_count": len(constraints),
    }
    if audit_id:
        output["audit_id"] = audit_id
    return output


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.stdout.write("{}")
            return
        payload = json.loads(raw)
        result = build_injection(payload)
        sys.stdout.write(json.dumps(result))
    except Exception as e:
        logger.warning(f"inject_helper failed: {e}")
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
