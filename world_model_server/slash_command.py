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

SUBCOMMANDS = ("status", "contradictions", "recent", "help")


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


def _format_help() -> str:
    return (
        "# world-model slash commands\n"
        "\n"
        "- `/world-model status`         summary of constraints, contradictions, and facts\n"
        "- `/world-model contradictions` list current unresolved contradictions\n"
        "- `/world-model recent`         last 10 facts in the knowledge graph\n"
        "- `/world-model help`           show this message\n"
        "\n"
        "All subcommands are read-only in v0.7.6. Write operations "
        "(resolve, forget) land in v0.8."
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
    else:
        body = _format_help()

    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": body,
        },
        "slash_command": subcommand,
    }
