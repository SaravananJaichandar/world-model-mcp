"""
v0.7.6 F1: in-agent `/world-model` slash command.

The slash command is implemented as a UserPromptSubmit hook intercept.
When the user types `/world-model <subcommand>` in any harness
(Claude Code, Cursor, Codex, pi), the hook detects the prefix and
returns a formatted status block as ``additionalContext`` so the
agent sees the result inside the conversation.

Subcommands shipped in v0.7.6 are READ-ONLY:

- ``/world-model status``        compact one-line summary of the world model
- ``/world-model contradictions`` list current unresolved contradictions
- ``/world-model recent``        last 10 facts captured in this project
- ``/world-model help``          list available subcommands

Write operations (``/world-model resolve <id>``, ``/world-model forget <id>``)
intentionally defer to v0.8 so the v0.7.6 surface stays bounded.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

SLASH_PREFIX = "/world-model"

# Read-only subcommands shipped in v0.7.6.
READ_SUBCOMMANDS = ("status", "contradictions", "recent", "help")

# Write subcommands shipped in v0.8.0. They take a single argument: the
# id of the contradiction or fact to act on. resolve marks a
# contradiction as resolved; forget marks a fact as invalid (does not
# physically delete; the row stays in the audit log).
WRITE_SUBCOMMANDS = ("resolve", "forget")

SUBCOMMANDS = READ_SUBCOMMANDS + WRITE_SUBCOMMANDS


def is_slash_command(user_prompt: str) -> bool:
    """Return True if ``user_prompt`` begins with the slash command prefix.

    Accepts both ``/world-model`` and ``/world-model<space><subcommand>``.
    Case-insensitive on the prefix.
    """
    if not isinstance(user_prompt, str):
        return False
    stripped = user_prompt.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    return lowered == SLASH_PREFIX or lowered.startswith(SLASH_PREFIX + " ")


def parse_subcommand(user_prompt: str) -> str:
    """Return the subcommand string from ``user_prompt``.

    Defaults to ``"help"`` when no subcommand is provided or when the
    subcommand is not recognized. The default is deliberately ``"help"``
    rather than an error because the slash command runs inside the user's
    agent session and a silent failure would be more confusing than a
    fallback to help text.
    """
    stripped = user_prompt.strip()
    rest = stripped[len(SLASH_PREFIX):].strip()
    if not rest:
        return "help"
    first = rest.split()[0].lower()
    if first not in SUBCOMMANDS:
        return "help"
    return first


def parse_argument(user_prompt: str) -> Optional[str]:
    """Return the argument after the subcommand, or None if absent.

    Used by write subcommands (resolve / forget) that take a single id
    argument: ``/world-model resolve ct123`` returns ``"ct123"``.
    """
    stripped = user_prompt.strip()
    rest = stripped[len(SLASH_PREFIX):].strip()
    parts = rest.split(maxsplit=1)
    if len(parts) < 2:
        return None
    arg = parts[1].strip()
    return arg if arg else None


def _open_constraints_db(db_dir: Path) -> Optional[sqlite3.Connection]:
    db_path = db_dir / "constraints.db"
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _open_facts_db(db_dir: Path) -> Optional[sqlite3.Connection]:
    db_path = db_dir / "facts.db"
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _count_constraints(db_dir: Path) -> tuple[int, int]:
    """Return ``(total, by_severity_error)``."""
    conn = _open_constraints_db(db_dir)
    if conn is None:
        return (0, 0)
    try:
        total = conn.execute("SELECT COUNT(*) FROM constraints").fetchone()[0]
        errors = conn.execute(
            "SELECT COUNT(*) FROM constraints WHERE severity = 'error'"
        ).fetchone()[0]
        return (total, errors)
    except sqlite3.Error:
        return (0, 0)
    finally:
        conn.close()


def _count_contradictions(db_dir: Path) -> int:
    """Return the number of unresolved contradictions."""
    conn = _open_facts_db(db_dir)
    if conn is None:
        return 0
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contradictions'"
        )
        if cur.fetchone() is None:
            return 0
        row = conn.execute(
            "SELECT COUNT(*) FROM contradictions WHERE status = 'unresolved'"
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _count_facts(db_dir: Path) -> int:
    conn = _open_facts_db(db_dir)
    if conn is None:
        return 0
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='facts'"
        )
        if cur.fetchone() is None:
            return 0
        row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
        return row[0] if row else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _list_contradictions(db_dir: Path, limit: int = 10) -> list[dict]:
    conn = _open_facts_db(db_dir)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contradictions'"
        )
        if cur.fetchone() is None:
            return []
        rows = conn.execute(
            "SELECT id, fact_a, fact_b, detected_at FROM contradictions "
            "WHERE status = 'unresolved' "
            "ORDER BY detected_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _list_recent_facts(db_dir: Path, limit: int = 10) -> list[dict]:
    conn = _open_facts_db(db_dir)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='facts'"
        )
        if cur.fetchone() is None:
            return []
        rows = conn.execute(
            "SELECT fact_text, confidence, status, created_at FROM facts "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _format_status(db_dir: Path) -> str:
    total, errors = _count_constraints(db_dir)
    contradictions = _count_contradictions(db_dir)
    facts = _count_facts(db_dir)
    lines = ["# world-model status"]
    lines.append(
        f"- {total} constraints loaded ({errors} severity=error)"
    )
    lines.append(f"- {contradictions} unresolved contradictions")
    lines.append(f"- {facts} facts in the knowledge graph")
    lines.append("")
    lines.append(
        "Run `/world-model contradictions` to list, "
        "`/world-model recent` for the latest facts, or "
        "`/world-model help` for all subcommands."
    )
    return "\n".join(lines)


def _format_contradictions(db_dir: Path) -> str:
    rows = _list_contradictions(db_dir)
    if not rows:
        return (
            "# world-model contradictions\n\n"
            "No unresolved contradictions in this project."
        )
    lines = ["# world-model contradictions (unresolved)"]
    for r in rows:
        lines.append(
            f"- [{r.get('id', '?')}] {r.get('fact_a', '?')} vs "
            f"{r.get('fact_b', '?')}"
        )
    return "\n".join(lines)


def _format_recent(db_dir: Path) -> str:
    rows = _list_recent_facts(db_dir)
    if not rows:
        return (
            "# world-model recent\n\n"
            "No facts recorded yet in this project."
        )
    lines = ["# world-model recent facts"]
    for r in rows:
        conf = r.get("confidence")
        status = r.get("status", "?")
        conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else "?"
        lines.append(
            f"- ({status}, conf={conf_str}) {r.get('fact_text', '?')}"
        )
    return "\n".join(lines)


def _resolve_contradiction(db_dir: Path, contradiction_id: str) -> str:
    """Mark a contradiction as resolved by id. Returns a human-readable
    status block formatted for re-injection into the agent context.

    Resolution does not pick a winner automatically; that requires the
    ``resolve_contradiction`` MCP tool with an explicit strategy. The
    slash command writes a manual-resolution row and surfaces the
    contradiction id for follow-up.
    """
    if not contradiction_id:
        return (
            "# world-model resolve\n\n"
            "Missing contradiction id. Usage: "
            "`/world-model resolve <id>`. Run `/world-model contradictions` "
            "to see ids."
        )
    conn = _open_facts_db(db_dir)
    if conn is None:
        return (
            "# world-model resolve\n\n"
            "Facts database not found. Run `world-model setup` first."
        )
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contradictions'"
        )
        if cur.fetchone() is None:
            return (
                "# world-model resolve\n\n"
                "No contradictions table exists yet."
            )
        cur = conn.execute(
            "SELECT id, fact_a, fact_b, status FROM contradictions WHERE id = ?",
            (contradiction_id,),
        )
        row = cur.fetchone()
        if row is None:
            return (
                f"# world-model resolve\n\n"
                f"No contradiction found with id `{contradiction_id}`."
            )
        if row["status"] == "resolved":
            return (
                f"# world-model resolve\n\n"
                f"Contradiction `{contradiction_id}` is already resolved."
            )
        conn.execute(
            "UPDATE contradictions SET status = 'resolved' WHERE id = ?",
            (contradiction_id,),
        )
        conn.commit()
        return (
            f"# world-model resolve\n\n"
            f"Contradiction `{contradiction_id}` marked resolved.\n"
            f"- A: {row['fact_a']}\n"
            f"- B: {row['fact_b']}\n\n"
            f"For confidence-weighted automatic resolution that picks a "
            f"winner, use the `resolve_contradiction` MCP tool with an "
            f"explicit strategy."
        )
    except sqlite3.Error as exc:
        return (
            f"# world-model resolve\n\n"
            f"Database error: {exc}."
        )
    finally:
        conn.close()


def _forget_fact(db_dir: Path, fact_id: str) -> str:
    """Mark a fact as invalid by id. Returns a status block.

    Does not physically delete the row; sets ``invalid_at`` to now so the
    fact stops surfacing in current-only reads but stays in the audit
    log. The MCP ``query_fact`` tool ``current_only=True`` default
    already filters these out.
    """
    if not fact_id:
        return (
            "# world-model forget\n\n"
            "Missing fact id. Usage: "
            "`/world-model forget <id>`. Run `/world-model recent` to see ids."
        )
    conn = _open_facts_db(db_dir)
    if conn is None:
        return (
            "# world-model forget\n\n"
            "Facts database not found. Run `world-model setup` first."
        )
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='facts'"
        )
        if cur.fetchone() is None:
            return (
                "# world-model forget\n\n"
                "No facts table exists yet."
            )
        cur = conn.execute(
            "SELECT id, fact_text, invalid_at FROM facts WHERE id = ?",
            (fact_id,),
        )
        row = cur.fetchone()
        if row is None:
            return (
                f"# world-model forget\n\n"
                f"No fact found with id `{fact_id}`."
            )
        if row["invalid_at"] is not None:
            return (
                f"# world-model forget\n\n"
                f"Fact `{fact_id}` is already invalidated."
            )
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "UPDATE facts SET invalid_at = ? WHERE id = ?",
            (now, fact_id),
        )
        conn.commit()
        return (
            f"# world-model forget\n\n"
            f"Fact `{fact_id}` marked invalid.\n"
            f"- {row['fact_text']}\n\n"
            f"The row is preserved in the audit log; only current-only "
            f"reads will skip it from now on."
        )
    except sqlite3.Error as exc:
        return (
            f"# world-model forget\n\n"
            f"Database error: {exc}."
        )
    finally:
        conn.close()


def _format_help() -> str:
    return (
        "# world-model slash commands\n"
        "\n"
        "Read operations:\n"
        "- `/world-model status`         summary of constraints, contradictions, and facts\n"
        "- `/world-model contradictions` list current unresolved contradictions\n"
        "- `/world-model recent`         last 10 facts in the knowledge graph\n"
        "- `/world-model help`           show this message\n"
        "\n"
        "Write operations (v0.8.0):\n"
        "- `/world-model resolve <id>`   mark a contradiction as resolved\n"
        "- `/world-model forget <id>`    mark a fact as invalid (preserved in audit log)"
    )


def handle_slash_command(user_prompt: str, project_dir: str) -> Optional[dict]:
    """Handle a ``/world-model`` slash command and return a hook output.

    Returns ``None`` if ``user_prompt`` is not a slash command. Otherwise
    returns a dict in the same hookSpecificOutput shape as
    ``inject_helper.build_injection`` so the slash command surfaces inside
    the agent's next turn as ``additionalContext``.
    """
    if not is_slash_command(user_prompt):
        return None
    subcommand = parse_subcommand(user_prompt)
    db_dir = Path(project_dir) / ".claude" / "world-model"

    if subcommand == "status":
        body = _format_status(db_dir)
    elif subcommand == "contradictions":
        body = _format_contradictions(db_dir)
    elif subcommand == "recent":
        body = _format_recent(db_dir)
    elif subcommand == "resolve":
        body = _resolve_contradiction(db_dir, parse_argument(user_prompt) or "")
    elif subcommand == "forget":
        body = _forget_fact(db_dir, parse_argument(user_prompt) or "")
    else:
        body = _format_help()

    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": body,
        },
        "slash_command": subcommand,
    }
