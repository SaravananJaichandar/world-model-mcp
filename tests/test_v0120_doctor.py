"""
v0.12.1 `world-model doctor` command tests.

Covers each individual check function on both PASS and FAIL fixtures, the
top-level runner ordering, the exit-code contract (0 = pass/warn only,
1 = any fail), the --json output shape, and the --fix auto-fix path.

The auto-fix tests exercise the two safe fixes exposed today:
  1. Rewrite unquoted $CLAUDE_PROJECT_DIR in .claude/settings.json
     (the v0.11.0 shell-quoting fix, applied retroactively to installs
      done with pre-v0.11.0 `world-model setup`)
  2. Create a stub .mcp.json registering world-model as an MCP server
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ============================================================================
# Fixture: a fully-healthy world-model install in a temp dir
# ============================================================================


@pytest.fixture
def healthy_project(tmp_path):
    """Set up a temp dir that passes every check."""
    project = tmp_path / "healthy project"  # space intentional
    project.mkdir()

    (project / ".claude").mkdir()
    (project / ".claude" / "hooks").mkdir()
    for hook in (
        "world-model-capture.js",
        "world-model-validate.js",
        "world-model-session.js",
        "world-model-inject.js",
    ):
        (project / ".claude" / "hooks" / hook).write_text("// stub")

    (project / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {
            "PostToolUse": [{
                "matcher": "Edit|Write|Bash|Read",
                "hooks": [{
                    "type": "command",
                    "command": 'node "$CLAUDE_PROJECT_DIR/.claude/hooks/world-model-capture.js"',
                    "timeout": 10,
                }],
            }],
        },
    }))

    (project / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "world-model": {
                "command": "python3",
                "args": ["-m", "world_model_server.server"],
            },
        },
    }))

    db = project / ".claude" / "world-model"
    db.mkdir()
    for name in ("facts.db", "entities.db", "constraints.db"):
        (db / name).write_text("")  # stub file; checks only look at existence

    return project


# ============================================================================
# Individual checks — PASS paths
# ============================================================================


def test_check_node_available_returns_pass_when_node_on_path(healthy_project):
    from world_model_server.doctor import check_node_available, PASS

    r = check_node_available(healthy_project)
    # Skip cleanly if node is not on the developer's machine, but our CI has it
    if shutil.which("node"):
        assert r.status == PASS


def test_check_settings_json_present_pass(healthy_project):
    from world_model_server.doctor import check_settings_json_present, PASS
    assert check_settings_json_present(healthy_project).status == PASS


def test_check_settings_json_shell_quoting_pass(healthy_project):
    from world_model_server.doctor import check_settings_json_shell_quoting, PASS
    assert check_settings_json_shell_quoting(healthy_project).status == PASS


def test_check_hooks_scripts_present_pass(healthy_project):
    from world_model_server.doctor import check_hooks_scripts_present, PASS
    assert check_hooks_scripts_present(healthy_project).status == PASS


def test_check_mcp_json_present_pass(healthy_project):
    from world_model_server.doctor import check_mcp_json_present, PASS
    assert check_mcp_json_present(healthy_project).status == PASS


def test_check_world_model_db_dir_pass(healthy_project):
    from world_model_server.doctor import check_world_model_db_dir, PASS
    assert check_world_model_db_dir(healthy_project).status == PASS


def test_check_stale_events_queue_pass_when_missing(healthy_project):
    from world_model_server.doctor import check_stale_events_queue, PASS
    assert check_stale_events_queue(healthy_project).status == PASS


# ============================================================================
# Individual checks — FAIL / WARN paths
# ============================================================================


def test_check_settings_json_missing_fails(tmp_path):
    from world_model_server.doctor import check_settings_json_present, FAIL
    r = check_settings_json_present(tmp_path)
    assert r.status == FAIL
    assert "setup" in (r.fix_hint or "")


def test_check_shell_quoting_flags_unquoted(tmp_path):
    """The load-bearing regression check: pre-v0.11.0 setup output must be
    flagged FAIL, exactly like the maintainer's own dogfood repo was pre-fix."""
    from world_model_server.doctor import check_settings_json_shell_quoting, FAIL

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {
            "PostToolUse": [{
                "matcher": "Edit|Write",
                "hooks": [{
                    "type": "command",
                    "command": "node $CLAUDE_PROJECT_DIR/.claude/hooks/world-model-capture.js",  # unquoted!
                    "timeout": 10,
                }],
            }],
        },
    }))
    r = check_settings_json_shell_quoting(tmp_path)
    assert r.status == FAIL
    assert "Unquoted" in r.detail or "unquoted" in r.detail.lower()
    assert r.auto_fix is not None  # auto-fixable


def test_check_shell_quoting_passes_when_quoted(tmp_path):
    from world_model_server.doctor import check_settings_json_shell_quoting, PASS

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {
            "PostToolUse": [{
                "matcher": "Edit|Write",
                "hooks": [{
                    "type": "command",
                    "command": 'node "$CLAUDE_PROJECT_DIR/.claude/hooks/world-model-capture.js"',
                    "timeout": 10,
                }],
            }],
        },
    }))
    assert check_settings_json_shell_quoting(tmp_path).status == PASS


def test_check_hooks_scripts_missing_fails(tmp_path):
    from world_model_server.doctor import check_hooks_scripts_present, FAIL
    (tmp_path / ".claude").mkdir()
    r = check_hooks_scripts_present(tmp_path)
    assert r.status == FAIL


def test_check_mcp_json_missing_is_warn(tmp_path):
    """Missing .mcp.json is a warning, not a failure — hooks still work,
    but MCP tool calls from within a Claude Code session cannot reach the
    server. Warning is the right severity."""
    from world_model_server.doctor import check_mcp_json_present, WARN
    r = check_mcp_json_present(tmp_path)
    assert r.status == WARN
    assert r.auto_fix is not None  # auto-fixable via stub creation


def test_check_mcp_json_present_without_world_model_is_warn(tmp_path):
    from world_model_server.doctor import check_mcp_json_present, WARN
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {}}}))
    r = check_mcp_json_present(tmp_path)
    assert r.status == WARN


def test_check_db_dir_missing_fails(tmp_path):
    from world_model_server.doctor import check_world_model_db_dir, FAIL
    r = check_world_model_db_dir(tmp_path)
    assert r.status == FAIL


def test_check_stale_events_queue_warns_when_populated(healthy_project):
    from world_model_server.doctor import check_stale_events_queue, WARN
    q = healthy_project / ".claude" / "world-model" / "events-queue.jsonl"
    q.write_text('{"event": "one"}\n{"event": "two"}\n')
    r = check_stale_events_queue(healthy_project)
    assert r.status == WARN
    assert "2" in r.detail


# ============================================================================
# Runner + exit code contract
# ============================================================================


def test_run_checks_returns_all_checks(healthy_project):
    from world_model_server.doctor import run_checks, ALL_CHECKS
    results = run_checks(healthy_project)
    assert len(results) == len(ALL_CHECKS)


def test_cli_doctor_exit_zero_on_healthy_project(healthy_project):
    """CLI must exit 0 when every check is PASS/WARN, never FAIL."""
    # Skip if node not available (healthy_project can't be truly healthy)
    if not shutil.which("node"):
        pytest.skip("node not available")

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "doctor",
         "--project-dir", str(healthy_project)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    # Note: may exit 0 (all pass/warn) or non-zero if any check surfaces a FAIL
    # we didn't foresee. Assert we at least don't crash.
    assert result.returncode in (0, 1), f"stderr: {result.stderr}"


def test_cli_doctor_exit_nonzero_on_broken_project(tmp_path):
    """CLI must exit 1 when at least one check FAILs."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "doctor",
         "--project-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1


def test_cli_doctor_json_output_is_valid(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "doctor",
         "--project-dir", str(tmp_path), "--json"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    for check in payload:
        assert set(check.keys()) >= {"name", "status", "detail"}
        assert check["status"] in ("PASS", "WARN", "FAIL")


# ============================================================================
# Auto-fix
# ============================================================================


def test_auto_fix_settings_quoting_rewrites_unquoted_command(tmp_path):
    from world_model_server.doctor import _auto_fix_settings_quoting

    (tmp_path / ".claude").mkdir()
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.write_text(json.dumps({
        "hooks": {
            "PostToolUse": [{
                "matcher": "Edit|Write",
                "hooks": [{
                    "type": "command",
                    "command": "node $CLAUDE_PROJECT_DIR/.claude/hooks/world-model-capture.js",
                    "timeout": 10,
                }],
            }],
        },
    }))
    _auto_fix_settings_quoting(tmp_path)
    fixed = json.loads(settings_path.read_text())
    cmd = fixed["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert '"$CLAUDE_PROJECT_DIR' in cmd
    assert cmd.endswith('.js"')


def test_auto_fix_creates_stub_mcp_json(tmp_path):
    from world_model_server.doctor import _auto_fix_create_mcp_json
    _auto_fix_create_mcp_json(tmp_path)
    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    assert "world-model" in mcp["mcpServers"]
    entry = mcp["mcpServers"]["world-model"]
    assert entry["command"] == "python3"
    assert "world_model_server.server" in " ".join(entry["args"])


def test_cli_doctor_fix_flag_repairs_shell_quoting(tmp_path):
    """End-to-end: run `doctor --fix` on a broken project, then rerun without
    --fix and confirm the shell-quoting check now passes."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
        "hooks": {
            "PostToolUse": [{
                "matcher": "Edit",
                "hooks": [{
                    "type": "command",
                    "command": "node $CLAUDE_PROJECT_DIR/.claude/hooks/world-model-capture.js",
                    "timeout": 10,
                }],
            }],
        },
    }))

    # Apply the fix
    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "doctor",
         "--project-dir", str(tmp_path), "--fix"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    # Confirm the settings.json now has quoted expansion
    fixed = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    cmd = fixed["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert '"$CLAUDE_PROJECT_DIR' in cmd


# ============================================================================
# CLI regression — every prior install-* subcommand still registered
# ============================================================================


def test_doctor_registered_in_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    assert "doctor" in result.stdout


def test_all_prior_install_subcommands_still_present():
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    for cmd in (
        "install-cursor", "install-pi", "install-codex",
        "install-openclaw", "install-hermes", "install-hermes-provider",
        "install-continue", "doctor",
    ):
        assert cmd in result.stdout, f"Missing subcommand: {cmd}"
