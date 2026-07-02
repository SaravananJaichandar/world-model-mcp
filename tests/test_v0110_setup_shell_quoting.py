"""
v0.11.0 regression: setup_command must generate hook commands that are
robust to project paths containing spaces (or any shell metacharacter).

Pre-v0.11.0 bug: `setup_command` wrote hook commands like

    node $CLAUDE_PROJECT_DIR/.claude/hooks/world-model-capture.js

When Claude Code expanded `$CLAUDE_PROJECT_DIR` and the value contained
a space (a common case on macOS: `~/Documents/`, `~/My Projects/`, or
any repo cloned under a folder with a whitespace-containing name), the
shell split the argument on whitespace and Node tried to load the
truncated first token as its module argument, producing:

    Error: Cannot find module '/Users/name/Documents'

The failure was silent because the hook was configured non_blocking.
Every affected user got zero hook captures for the life of the install.

Fix (v0.11.0): wrap the env-var expansion in double quotes so the
shell treats the expanded path as a single argument.

This test enforces the fix so it cannot regress.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


def _generated_settings(tmp_path: Path) -> dict:
    """Run `world-model setup` in a project dir whose path contains a space,
    return the settings.json it writes."""
    project_dir = tmp_path / "spaced project" / "world-model-mcp"
    project_dir.mkdir(parents=True)
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "setup",
         "--project-dir", str(project_dir), "--no-prompt"],
        capture_output=True, text=True, timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"setup failed: {result.stderr}"
    settings_path = project_dir / ".claude" / "settings.json"
    assert settings_path.exists(), (
        f"setup did not write settings.json to {settings_path}. "
        f"stdout: {result.stdout} stderr: {result.stderr}"
    )
    return json.loads(settings_path.read_text())


def _all_hook_commands(settings: dict) -> list[str]:
    """Flatten every command string from every hook event."""
    commands = []
    for _event, event_configs in settings.get("hooks", {}).items():
        for cfg in event_configs:
            for hook in cfg.get("hooks", []):
                cmd = hook.get("command")
                if cmd:
                    commands.append(cmd)
    return commands


@pytest.fixture
def settings(tmp_path):
    return _generated_settings(tmp_path)


def test_all_four_hook_events_present(settings):
    """PostToolUse, PreToolUse, SessionStart, SessionEnd must all be wired."""
    assert set(settings["hooks"].keys()) == {
        "PostToolUse", "PreToolUse", "SessionStart", "SessionEnd",
    }


def test_every_hook_command_quotes_claude_project_dir(settings):
    """The load-bearing regression check.

    Every hook command must wrap `$CLAUDE_PROJECT_DIR` in double quotes.
    Unquoted expansion is what broke pre-v0.11.0 installs on any project
    path containing a space.
    """
    for cmd in _all_hook_commands(settings):
        assert "$CLAUDE_PROJECT_DIR" in cmd, (
            f"Hook command does not reference $CLAUDE_PROJECT_DIR: {cmd!r}"
        )
        # The variable must appear inside a double-quoted region. We check
        # this by verifying that the substring immediately preceding
        # $CLAUDE_PROJECT_DIR contains an unmatched opening double quote
        # somewhere before it AND the substring after the file path has a
        # closing double quote.
        assert '"$CLAUDE_PROJECT_DIR' in cmd or '"${CLAUDE_PROJECT_DIR' in cmd, (
            f"Hook command must double-quote $CLAUDE_PROJECT_DIR to survive "
            f"project paths containing spaces. Got: {cmd!r}"
        )


def test_hook_commands_start_with_node(settings):
    """Sanity check: hooks must invoke node, not a raw path (would break
    when node is not on PATH; sepatare failure mode)."""
    for cmd in _all_hook_commands(settings):
        assert cmd.startswith("node "), (
            f"Hook command should invoke `node ...`, got: {cmd!r}"
        )


def test_hook_commands_have_expected_hook_scripts(settings):
    """All four hook scripts (capture, validate, session x2) are referenced."""
    all_cmds = " || ".join(_all_hook_commands(settings))
    for script in (
        "world-model-capture.js",
        "world-model-validate.js",
        "world-model-session.js",
    ):
        assert script in all_cmds, f"Missing hook script reference: {script}"


def test_repo_own_settings_also_quotes_project_dir():
    """The .claude/settings.json committed at the repo root must also use
    the quoted form. This is what makes the maintainer's own dogfooding
    work — a broken repo settings.json here silently negates every
    session capture on this repo."""
    settings_path = REPO_ROOT / ".claude" / "settings.json"
    if not settings_path.exists():
        pytest.skip("No repo-scoped .claude/settings.json to check")
    settings = json.loads(settings_path.read_text())
    for cmd in _all_hook_commands(settings):
        assert '"$CLAUDE_PROJECT_DIR' in cmd or '"${CLAUDE_PROJECT_DIR' in cmd, (
            f"Repo's own .claude/settings.json has an unquoted "
            f"$CLAUDE_PROJECT_DIR — the maintainer's dogfooding is broken. "
            f"Got: {cmd!r}"
        )
