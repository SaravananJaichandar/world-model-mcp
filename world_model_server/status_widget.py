"""
v0.7.6 F2: TUI status widget.

A terminal-pane widget that runs alongside the agent and shows the
current world-model state. Refreshes every 5 seconds by default. Uses
``rich.live`` for the redraw loop and ``rich.table`` for the layout.

Intended use: run ``world-model status-watch`` in a second terminal
beside the agent. The widget reads from the project's
``.claude/world-model/`` directory and surfaces the same counts the
``/world-model status`` slash command shows.

This is a deliberately small widget. Larger Claude-Code-UI-native
integration would require Anthropic-side cooperation; this surface
keeps the dependency on a generic terminal pane that runs in any
shell.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    _RICH_OK = True
except ImportError:
    _RICH_OK = False


def _open_db(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _safe_count(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    if cur.fetchone() is None:
        return 0
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        return conn.execute(sql).fetchone()[0]
    except sqlite3.Error:
        return 0


def snapshot(db_dir: Path) -> dict:
    """Read the current world-model state into a flat dict.

    Returns the same shape regardless of whether the database exists, so
    the widget can render an "uninitialized" panel without crashing.
    """
    state = {
        "initialized": db_dir.exists(),
        "constraints_total": 0,
        "constraints_error": 0,
        "constraints_warning": 0,
        "contradictions_unresolved": 0,
        "facts_total": 0,
        "facts_canonical": 0,
        "facts_synthesized": 0,
        "facts_superseded": 0,
        "last_compaction": None,
        "now": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    }
    if not state["initialized"]:
        return state

    constraints_conn = _open_db(db_dir / "constraints.db")
    if constraints_conn is not None:
        try:
            state["constraints_total"] = _safe_count(constraints_conn, "constraints")
            state["constraints_error"] = _safe_count(
                constraints_conn, "constraints", "severity = 'error'"
            )
            state["constraints_warning"] = _safe_count(
                constraints_conn, "constraints", "severity = 'warning'"
            )
        finally:
            constraints_conn.close()

    facts_conn = _open_db(db_dir / "facts.db")
    if facts_conn is not None:
        try:
            state["facts_total"] = _safe_count(facts_conn, "facts")
            state["facts_canonical"] = _safe_count(
                facts_conn, "facts", "status = 'canonical'"
            )
            state["facts_synthesized"] = _safe_count(
                facts_conn, "facts", "status = 'synthesized'"
            )
            state["facts_superseded"] = _safe_count(
                facts_conn, "facts", "status = 'superseded'"
            )
            state["contradictions_unresolved"] = _safe_count(
                facts_conn, "contradictions", "status = 'unresolved'"
            )
            # Last compaction (if audit table exists)
            cur = facts_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='compaction_audit'"
            )
            if cur.fetchone() is not None:
                row = facts_conn.execute(
                    "SELECT created_at FROM compaction_audit "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                if row:
                    state["last_compaction"] = row[0]
        except sqlite3.Error:
            pass
        finally:
            facts_conn.close()

    return state


def render(state: dict) -> "Panel":
    """Render a single frame from a snapshot dict.

    Returns a ``rich.panel.Panel``. Callers that do not have ``rich``
    installed should not reach this function.
    """
    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style="bold", justify="left")
    table.add_column(justify="right")

    if not state.get("initialized", False):
        table.add_row("State", Text("not initialized", style="yellow"))
        table.add_row("Hint", Text("Run: world-model setup", style="dim"))
        return Panel(
            table,
            title="[bold]world-model status[/bold]",
            subtitle=f"refresh {state.get('now', '?')}",
            border_style="yellow",
        )

    c_err = state.get("constraints_error", 0)
    c_warn = state.get("constraints_warning", 0)
    c_total = state.get("constraints_total", 0)
    table.add_row(
        "Constraints",
        f"[bold]{c_total}[/bold]  "
        f"(error={c_err}, warning={c_warn})",
    )

    contradictions = state.get("contradictions_unresolved", 0)
    style = "red" if contradictions > 0 else "green"
    table.add_row(
        "Unresolved contradictions",
        Text(str(contradictions), style=style),
    )

    f_total = state.get("facts_total", 0)
    f_canon = state.get("facts_canonical", 0)
    f_synth = state.get("facts_synthesized", 0)
    f_sup = state.get("facts_superseded", 0)
    table.add_row(
        "Facts",
        f"[bold]{f_total}[/bold]  "
        f"(canonical={f_canon}, synthesized={f_synth}, superseded={f_sup})",
    )

    last_compact = state.get("last_compaction") or "none"
    table.add_row("Last compaction", str(last_compact))

    return Panel(
        table,
        title="[bold]world-model status[/bold]",
        subtitle=f"refresh {state.get('now', '?')}",
        border_style="cyan",
    )


def run_watch(project_dir: str, interval: float = 5.0) -> None:
    """Run the status widget until interrupted.

    Renders the current snapshot every ``interval`` seconds. Exits
    cleanly on Ctrl-C. If ``rich`` is not installed, falls back to a
    one-shot text dump and prints a hint about installing the extra.
    """
    db_dir = Path(project_dir).resolve() / ".claude" / "world-model"

    if not _RICH_OK:
        # Fail-open: print a one-shot snapshot in plain text.
        state = snapshot(db_dir)
        print("world-model status (plain text, rich not installed):")
        for k, v in state.items():
            print(f"  {k}: {v}")
        print("\nInstall 'rich' for the live TUI: pip install rich")
        return

    console = Console()
    try:
        with Live(
            render(snapshot(db_dir)),
            console=console,
            refresh_per_second=2,
            screen=False,
        ) as live:
            while True:
                time.sleep(interval)
                live.update(render(snapshot(db_dir)))
    except KeyboardInterrupt:
        console.print("[dim]status-watch stopped[/dim]")
