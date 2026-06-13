"""
v0.7.6 feature tests.

F1: ``/world-model`` slash command (UserPromptSubmit hook intercept)
F2: ``world-model status-watch`` TUI status widget

Plus regression coverage that v0.7.0 through v0.7.5 surface still works.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ============================================================================
# F1: Slash command detection and dispatch (pure logic, no DB needed)
# ============================================================================

def test_f1_is_slash_command_recognizes_prefix():
    from world_model_server.slash_command import is_slash_command
    assert is_slash_command("/world-model")
    assert is_slash_command("/world-model status")
    assert is_slash_command("/world-model help")
    assert is_slash_command("  /world-model status  ")  # whitespace tolerant


def test_f1_is_slash_command_rejects_non_matches():
    from world_model_server.slash_command import is_slash_command
    assert not is_slash_command("")
    assert not is_slash_command("hello")
    assert not is_slash_command("/world-modelstatus")  # missing space
    assert not is_slash_command("world-model status")  # missing leading slash
    assert not is_slash_command("/wm status")
    assert not is_slash_command(None)  # type-tolerant


def test_f1_is_slash_command_case_insensitive_prefix():
    from world_model_server.slash_command import is_slash_command
    assert is_slash_command("/World-Model status")
    assert is_slash_command("/WORLD-MODEL")


def test_f1_parse_subcommand_extracts_known():
    from world_model_server.slash_command import parse_subcommand
    assert parse_subcommand("/world-model status") == "status"
    assert parse_subcommand("/world-model contradictions") == "contradictions"
    assert parse_subcommand("/world-model recent") == "recent"
    assert parse_subcommand("/world-model help") == "help"


def test_f1_parse_subcommand_defaults_to_help():
    from world_model_server.slash_command import parse_subcommand
    assert parse_subcommand("/world-model") == "help"
    assert parse_subcommand("/world-model   ") == "help"
    # Unrecognized subcommand silently falls through to help (kindness
    # to users who typo inside the agent session).
    assert parse_subcommand("/world-model status_widget") == "help"
    assert parse_subcommand("/world-model bogus") == "help"


# ============================================================================
# F1: Slash command output shape (uses temp project dir)
# ============================================================================

@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def _make_wm_dir(project: Path) -> Path:
    db_dir = project / ".claude" / "world-model"
    db_dir.mkdir(parents=True)
    return db_dir


def _make_constraints_db(db_dir: Path, rows: list[tuple]) -> None:
    """rows: list of (id, rule_name, description, severity)"""
    conn = sqlite3.connect(str(db_dir / "constraints.db"))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS constraints (
            id TEXT PRIMARY KEY, rule_name TEXT, description TEXT,
            severity TEXT, violation_count INTEGER DEFAULT 0,
            constraint_type TEXT, file_pattern TEXT, examples TEXT,
            last_violated TIMESTAMP, created_at TIMESTAMP,
            content_hash TEXT
        )"""
    )
    for row in rows:
        conn.execute(
            "INSERT INTO constraints (id, rule_name, description, severity, "
            "violation_count, constraint_type) VALUES (?, ?, ?, ?, 0, 'style')",
            row,
        )
    conn.commit()
    conn.close()


def _make_facts_db(db_dir: Path, fact_rows: list[tuple], contradiction_rows: list[tuple] = None) -> None:
    """fact_rows: (fact_text, confidence, status)
    contradiction_rows (optional): (id, fact_a, fact_b, status)
    """
    conn = sqlite3.connect(str(db_dir / "facts.db"))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_text TEXT, confidence REAL, status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    for row in fact_rows:
        conn.execute(
            "INSERT INTO facts (fact_text, confidence, status) VALUES (?, ?, ?)",
            row,
        )
    if contradiction_rows is not None:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS contradictions (
                id TEXT PRIMARY KEY,
                fact_a TEXT, fact_b TEXT, status TEXT,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        for row in contradiction_rows:
            conn.execute(
                "INSERT INTO contradictions (id, fact_a, fact_b, status) "
                "VALUES (?, ?, ?, ?)",
                row,
            )
    conn.commit()
    conn.close()


def test_f1_handle_status_with_empty_project(project):
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model status", str(project))
    assert out is not None
    assert "hookSpecificOutput" in out
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "world-model status" in body
    assert "0 constraints" in body
    assert "0 unresolved contradictions" in body


def test_f1_handle_status_with_data(project):
    db_dir = _make_wm_dir(project)
    _make_constraints_db(db_dir, [
        ("c1", "no-console-log", "Use logger", "error"),
        ("c2", "prefer-pathlib", "Use pathlib", "warning"),
        ("c3", "no-secrets", "Never commit secrets", "error"),
    ])
    _make_facts_db(
        db_dir,
        fact_rows=[
            ("fact A", 0.9, "canonical"),
            ("fact B", 0.5, "synthesized"),
        ],
        contradiction_rows=[
            ("ct1", "X is true", "X is false", "unresolved"),
        ],
    )

    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model status", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "3 constraints" in body
    assert "severity=error" in body or "error=" in body or "2 severity" in body
    assert "1 unresolved" in body
    assert "2 facts" in body


def test_f1_handle_contradictions_empty(project):
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model contradictions", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "No unresolved contradictions" in body


def test_f1_handle_contradictions_with_data(project):
    db_dir = _make_wm_dir(project)
    _make_facts_db(
        db_dir,
        fact_rows=[],
        contradiction_rows=[
            ("ct1", "Tests use pytest", "Tests use unittest", "unresolved"),
            ("ct2", "Use uv", "Use pip", "unresolved"),
        ],
    )
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model contradictions", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "pytest" in body
    assert "uv" in body


def test_f1_handle_recent_empty(project):
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model recent", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "No facts" in body


def test_f1_handle_recent_with_facts(project):
    db_dir = _make_wm_dir(project)
    _make_facts_db(
        db_dir,
        fact_rows=[
            ("Repo uses pytest", 0.95, "canonical"),
            ("CI runs on push", 0.8, "corroborated"),
        ],
    )
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model recent", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "pytest" in body
    assert "CI runs" in body


def test_f1_handle_help_works_without_db(project):
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model help", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "/world-model status" in body
    assert "/world-model contradictions" in body
    assert "/world-model recent" in body


def test_f1_handle_returns_none_for_non_slash(project):
    from world_model_server.slash_command import handle_slash_command
    assert handle_slash_command("hello world", str(project)) is None
    assert handle_slash_command("", str(project)) is None
    assert handle_slash_command("/other-tool status", str(project)) is None


def test_f1_output_is_codex_schema_compliant(project):
    """Codex enforces deny_unknown_fields + camelCase on hook output.

    Our slash command returns into the same hookSpecificOutput shape
    inject_helper already uses for PostCompact, so the contract is
    inherited. Verify the camelCase keys and the literal hookEventName
    value (which must equal the registered event, per Codex PR #24962).
    """
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model help", str(project))
    hso = out["hookSpecificOutput"]
    assert "hookEventName" in hso
    assert hso["hookEventName"] == "UserPromptSubmit"
    assert "additionalContext" in hso
    # Snake-case must not appear:
    assert "hook_event_name" not in hso
    assert "additional_context" not in hso


# ============================================================================
# F1: inject_helper integration (the wire-up)
# ============================================================================

def test_f1_inject_helper_routes_slash_command(project):
    """The inject_helper.build_injection function must intercept the
    slash command BEFORE the search-hint flow runs."""
    from world_model_server.inject_helper import build_injection

    # Slash command path: should return the slash command output even
    # when the world-model dir does not exist.
    out = build_injection({
        "event": "UserPromptSubmit",
        "project_dir": str(project),
        "user_prompt": "/world-model help",
    })
    assert "hookSpecificOutput" in out
    assert "world-model" in out["hookSpecificOutput"]["additionalContext"]


def test_f1_inject_helper_does_not_route_non_slash(project):
    """Regression: non-slash prompts go through the existing
    search-hint flow, which returns {} for empty projects."""
    from world_model_server.inject_helper import build_injection
    out = build_injection({
        "event": "UserPromptSubmit",
        "project_dir": str(project),
        "user_prompt": "JWT validation question",
    })
    # Empty project -> empty injection
    assert out == {}


def test_f1_inject_helper_routes_slash_with_data(project):
    """Slash command with real DB data should return the formatted
    status block."""
    db_dir = _make_wm_dir(project)
    _make_constraints_db(db_dir, [("c1", "test", "test rule", "error")])

    from world_model_server.inject_helper import build_injection
    out = build_injection({
        "event": "UserPromptSubmit",
        "project_dir": str(project),
        "user_prompt": "/world-model status",
    })
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "1 constraints" in body


def test_f1_inject_helper_codex_payload_shape_works(project):
    """The Codex payload uses cwd + hook_event_name; the v0.7.5
    normalization layer must still route the slash command correctly."""
    db_dir = _make_wm_dir(project)
    _make_facts_db(db_dir, fact_rows=[])

    from world_model_server.inject_helper import build_injection
    out = build_injection({
        "hook_event_name": "UserPromptSubmit",
        "cwd": str(project),
        "user_prompt": "/world-model recent",
    })
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "No facts" in body


# ============================================================================
# F2: TUI status widget
# ============================================================================

def test_f2_snapshot_handles_missing_dir(tmp_path):
    from world_model_server.status_widget import snapshot
    state = snapshot(tmp_path / "does-not-exist" / "world-model")
    assert state["initialized"] is False
    assert state["constraints_total"] == 0
    assert state["facts_total"] == 0


def test_f2_snapshot_counts_constraints(project):
    db_dir = _make_wm_dir(project)
    _make_constraints_db(db_dir, [
        ("c1", "r1", "d1", "error"),
        ("c2", "r2", "d2", "warning"),
        ("c3", "r3", "d3", "error"),
    ])
    from world_model_server.status_widget import snapshot
    state = snapshot(db_dir)
    assert state["initialized"] is True
    assert state["constraints_total"] == 3
    assert state["constraints_error"] == 2
    assert state["constraints_warning"] == 1


def test_f2_snapshot_counts_facts_and_contradictions(project):
    db_dir = _make_wm_dir(project)
    _make_facts_db(
        db_dir,
        fact_rows=[
            ("f1", 0.9, "canonical"),
            ("f2", 0.5, "synthesized"),
            ("f3", 0.7, "superseded"),
        ],
        contradiction_rows=[
            ("ct1", "a", "b", "unresolved"),
            ("ct2", "c", "d", "resolved"),
        ],
    )
    from world_model_server.status_widget import snapshot
    state = snapshot(db_dir)
    assert state["facts_total"] == 3
    assert state["facts_canonical"] == 1
    assert state["facts_synthesized"] == 1
    assert state["facts_superseded"] == 1
    assert state["contradictions_unresolved"] == 1


def test_f2_render_uninitialized():
    """render() must produce a Panel even for uninitialized state."""
    rich = pytest.importorskip("rich")
    from world_model_server.status_widget import render
    panel = render({"initialized": False, "now": "12:00 UTC"})
    assert panel is not None


def test_f2_render_with_data():
    rich = pytest.importorskip("rich")
    from world_model_server.status_widget import render
    panel = render({
        "initialized": True,
        "constraints_total": 5,
        "constraints_error": 2,
        "constraints_warning": 3,
        "contradictions_unresolved": 1,
        "facts_total": 10,
        "facts_canonical": 7,
        "facts_synthesized": 2,
        "facts_superseded": 1,
        "last_compaction": "2026-06-13T10:00:00",
        "now": "12:00 UTC",
    })
    assert panel is not None


# ============================================================================
# F2: CLI subcommand registration
# ============================================================================

def test_f2_status_watch_cli_subcommand_registered():
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "status-watch" in result.stdout


def test_f2_status_watch_help_shows_flags():
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "status-watch", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "--project-dir" in result.stdout
    assert "--interval" in result.stdout


# ============================================================================
# Backward-compat regression
# ============================================================================

def test_bc_all_v075_subcommands_still_registered():
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    for cmd in (
        "setup", "seed", "query", "decisions", "register", "projects",
        "search-global", "health", "decay", "recall", "export-claude-md",
        "migrate", "status", "audit-compactions", "install-cursor",
        "install-pi", "install-codex", "demo", "telemetry",
        # v0.7.6 additions
        "status-watch",
    ):
        assert cmd in result.stdout, f"Missing CLI subcommand: {cmd}"


def test_bc_version_is_076():
    from world_model_server import __version__
    parts = __version__.split(".")
    major, minor = int(parts[0]), int(parts[1])
    assert (major, minor) >= (0, 7)
    if (major, minor) == (0, 7):
        patch = parts[2].split("rc")[0].split("a")[0].split("b")[0]
        assert int(patch) >= 6


def test_bc_inject_helper_postcompact_still_works(project):
    """Regression: PostCompact path on inject_helper must still produce
    additionalContext when constraints exist (v0.7.0 behavior unchanged)."""
    db_dir = _make_wm_dir(project)
    _make_constraints_db(db_dir, [("c1", "r", "d", "error")])

    from world_model_server.inject_helper import build_injection
    out = build_injection({
        "event": "PostCompact",
        "project_dir": str(project),
    })
    assert "hookSpecificOutput" in out
    assert out["hookSpecificOutput"]["hookEventName"] == "PostCompact"


def test_bc_hook_helper_still_works_with_dual_payloads(project):
    """v0.7.5 dual-shape payload normalization must still work."""
    from world_model_server.hook_helper import classify
    # Claude Code shape:
    out1 = classify({
        "tool_name": "Edit",
        "tool_input": {"file_path": "x.ts", "new_string": "y"},
        "project_dir": str(project),
    })
    assert isinstance(out1, dict)
    # Codex shape:
    out2 = classify({
        "tool_name": "Edit",
        "tool_input": {"file_path": "x.ts", "new_string": "y"},
        "cwd": str(project),
    })
    assert isinstance(out2, dict)
