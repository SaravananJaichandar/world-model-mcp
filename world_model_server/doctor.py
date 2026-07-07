"""
`world-model doctor` — silent-failure detection for the world-model install
in the current project.

Runs a battery of static checks against `.claude/settings.json`, `.mcp.json`,
`.claude/hooks/`, the SQLite databases under `.claude/world-model/`, and
optionally the Claude Code session transcripts under
`~/.claude/projects/*/`. Reports each check's status as PASS / WARN / FAIL
with a specific fix hint attached.

Motivating cases (from v0.11.2 dogfooding trace):

- Unquoted ``$CLAUDE_PROJECT_DIR`` in hook commands. If the project path
  contains a space, the shell splits the expanded value on whitespace and
  Node dies with ``MODULE_NOT_FOUND``. Silent failure — no user-visible
  symptom, hooks just don't fire. Fixed in v0.11.0 for new installs but
  users who ran ``world-model setup`` on <=v0.10 still have the bad
  ``settings.json``.
- Missing project ``.mcp.json``. Hooks may fire (in Claude Code interactive
  modes) but MCP tool calls have nowhere to go.
- Missing or empty world-model DB. Setup never ran, or the DB path is
  pointing at the wrong place because ``WORLD_MODEL_DB_PATH`` overrode
  the default.
- Stale queue file (``events-queue.jsonl`` exists but nothing has ingested
  it — server never restarted since the last hook fired).
- Missing hook scripts under ``.claude/hooks/``.
- Node not on PATH (the hook scripts use ``#!/usr/bin/env node``).

Design:

- Each check is a pure function returning a ``CheckResult``.
- ``run_checks`` composes them and returns a list.
- ``main`` (called from the CLI) prints a human-friendly table with fix
  hints and exits with the highest severity seen (0 for all-pass or
  warn-only, 1 for any fail).
- ``--json`` prints machine-readable output.
- ``--fix`` attempts safe auto-fixes (only for a small allow-list: quoting
  in settings.json, adding a stub .mcp.json). Never touches the DB.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional


# ============================================================================
# Data model
# ============================================================================


PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass
class CheckResult:
    """The outcome of one diagnostic check."""

    name: str
    status: str  # PASS / WARN / FAIL
    detail: str
    fix_hint: Optional[str] = None
    auto_fix: Optional[Callable[[Path], None]] = field(default=None, repr=False)


# ============================================================================
# Individual checks
# ============================================================================


def check_node_available(project_dir: Path) -> CheckResult:
    """Node.js must be on PATH — every hook script uses `#!/usr/bin/env node`."""
    node = shutil.which("node")
    if not node:
        return CheckResult(
            name="Node.js on PATH",
            status=FAIL,
            detail="`node` not found on PATH — hook scripts will fail to run",
            fix_hint="Install Node.js 20+ (macOS: `brew install node`; Linux: use nvm or system package manager)",
        )
    try:
        result = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=5
        )
        version = result.stdout.strip()
    except Exception:
        version = "unknown"
    return CheckResult(
        name="Node.js on PATH",
        status=PASS,
        detail=f"{node} ({version})",
    )


def check_settings_json_present(project_dir: Path) -> CheckResult:
    settings_path = project_dir / ".claude" / "settings.json"
    if not settings_path.exists():
        return CheckResult(
            name=".claude/settings.json present",
            status=FAIL,
            detail="Not found — hooks are not configured for this project",
            fix_hint="Run `python -m world_model_server.cli setup` in this directory",
        )
    return CheckResult(
        name=".claude/settings.json present",
        status=PASS,
        detail=f"{settings_path}",
    )


def check_settings_json_shell_quoting(project_dir: Path) -> CheckResult:
    """v0.11.0 fix: hook commands must double-quote $CLAUDE_PROJECT_DIR.

    Users who ran `world-model setup` on <=v0.10 have unquoted expansions
    and get silent hook failures on any project path containing a space.
    """
    settings_path = project_dir / ".claude" / "settings.json"
    if not settings_path.exists():
        return CheckResult(
            name="settings.json hook shell-quoting",
            status=WARN,
            detail="Skipped (settings.json missing)",
        )
    try:
        settings = json.loads(settings_path.read_text())
    except Exception as e:
        return CheckResult(
            name="settings.json hook shell-quoting",
            status=FAIL,
            detail=f"settings.json is not valid JSON: {e}",
            fix_hint="Delete .claude/settings.json and re-run `world-model setup`",
        )
    unquoted = []
    for event_name, event_configs in settings.get("hooks", {}).items():
        for cfg in event_configs:
            for hook in cfg.get("hooks", []):
                cmd = hook.get("command", "")
                if "$CLAUDE_PROJECT_DIR" in cmd:
                    if not ('"$CLAUDE_PROJECT_DIR' in cmd or '"${CLAUDE_PROJECT_DIR' in cmd):
                        unquoted.append(f"  {event_name}: {cmd}")
    if unquoted:
        return CheckResult(
            name="settings.json hook shell-quoting",
            status=FAIL,
            detail=(
                "Unquoted $CLAUDE_PROJECT_DIR in hook commands — silent hook "
                "failure if this project's path contains a space\n" + "\n".join(unquoted)
            ),
            fix_hint=(
                "Re-run `python -m world_model_server.cli setup` (v0.11.0+ writes quoted "
                "commands), or manually wrap $CLAUDE_PROJECT_DIR in double quotes"
            ),
            auto_fix=_auto_fix_settings_quoting,
        )
    return CheckResult(
        name="settings.json hook shell-quoting",
        status=PASS,
        detail="All hook commands quote $CLAUDE_PROJECT_DIR",
    )


def check_hooks_scripts_present(project_dir: Path) -> CheckResult:
    hooks_dir = project_dir / ".claude" / "hooks"
    expected = (
        "world-model-capture.js",
        "world-model-validate.js",
        "world-model-session.js",
        "world-model-inject.js",
    )
    if not hooks_dir.exists():
        return CheckResult(
            name=".claude/hooks/ scripts present",
            status=FAIL,
            detail="Directory does not exist — hook commands cannot resolve",
            fix_hint="Run `python -m world_model_server.cli setup` to install bundled hooks",
        )
    missing = [f for f in expected if not (hooks_dir / f).exists()]
    if missing:
        return CheckResult(
            name=".claude/hooks/ scripts present",
            status=FAIL,
            detail=f"Missing hook scripts: {', '.join(missing)}",
            fix_hint="Re-run `python -m world_model_server.cli setup` to reinstall bundled hooks",
        )
    return CheckResult(
        name=".claude/hooks/ scripts present",
        status=PASS,
        detail=f"All {len(expected)} hook scripts present",
    )


def check_mcp_json_present(project_dir: Path) -> CheckResult:
    """Project needs .mcp.json so Claude Code knows about world-model as an MCP server."""
    mcp_path = project_dir / ".mcp.json"
    if not mcp_path.exists():
        return CheckResult(
            name=".mcp.json registers world-model",
            status=WARN,
            detail=(
                "Not found — hooks will fire but MCP tool calls from within a "
                "Claude Code session cannot reach world-model-mcp"
            ),
            fix_hint=(
                "Create .mcp.json at repo root registering `world-model` "
                "with `python3 -m world_model_server.server`"
            ),
            auto_fix=_auto_fix_create_mcp_json,
        )
    try:
        data = json.loads(mcp_path.read_text())
    except Exception as e:
        return CheckResult(
            name=".mcp.json registers world-model",
            status=FAIL,
            detail=f".mcp.json is not valid JSON: {e}",
            fix_hint="Delete .mcp.json and re-create; see docs/adapters/claude-code/",
        )
    servers = data.get("mcpServers", {})
    if "world-model" not in servers:
        return CheckResult(
            name=".mcp.json registers world-model",
            status=WARN,
            detail=(
                ".mcp.json exists but does not register `world-model` — MCP tool "
                "calls from within a Claude Code session cannot reach it"
            ),
            fix_hint="Add a `mcpServers.world-model` entry (see docs/adapters/claude-code/)",
        )
    return CheckResult(
        name=".mcp.json registers world-model",
        status=PASS,
        detail=f"{mcp_path}",
    )


def check_world_model_db_dir(project_dir: Path) -> CheckResult:
    db_dir = Path(os.environ.get("WORLD_MODEL_DB_PATH", project_dir / ".claude" / "world-model"))
    if not db_dir.is_absolute():
        db_dir = (project_dir / db_dir).resolve()
    if not db_dir.exists():
        return CheckResult(
            name="world-model DB directory",
            status=FAIL,
            detail=f"DB directory {db_dir} does not exist",
            fix_hint="Run `python -m world_model_server.cli setup` to initialize",
        )
    expected_dbs = ("facts.db", "entities.db", "constraints.db")
    missing = [d for d in expected_dbs if not (db_dir / d).exists()]
    if missing:
        return CheckResult(
            name="world-model DB directory",
            status=FAIL,
            detail=f"Missing DB files: {', '.join(missing)} under {db_dir}",
            fix_hint="Run `python -m world_model_server.cli setup` to re-initialize",
        )
    return CheckResult(
        name="world-model DB directory",
        status=PASS,
        detail=f"{db_dir}",
    )


def check_stale_events_queue(project_dir: Path) -> CheckResult:
    """A non-empty events-queue.jsonl means hooks are firing but the server has
    not run since to ingest them. Not a bug — just informational."""
    db_dir = Path(os.environ.get("WORLD_MODEL_DB_PATH", project_dir / ".claude" / "world-model"))
    if not db_dir.is_absolute():
        db_dir = (project_dir / db_dir).resolve()
    queue = db_dir / "events-queue.jsonl"
    if not queue.exists():
        return CheckResult(
            name="events-queue.jsonl not stale",
            status=PASS,
            detail="No queued events (hooks either fired and ingested, or never fired)",
        )
    try:
        size = queue.stat().st_size
        n_lines = sum(1 for _ in queue.open()) if size > 0 else 0
    except Exception:
        n_lines = -1
    if n_lines <= 0:
        return CheckResult(
            name="events-queue.jsonl not stale",
            status=PASS,
            detail="Queue file exists but is empty",
        )
    return CheckResult(
        name="events-queue.jsonl not stale",
        status=WARN,
        detail=(
            f"{n_lines} queued event(s) waiting for ingest — start the "
            f"world-model server to drain the queue"
        ),
        fix_hint="Run any world-model CLI command (e.g. `world-model status`) to trigger ingest",
    )


def check_recent_hook_failures(project_dir: Path) -> CheckResult:
    """Scan Claude Code project transcripts for hook_non_blocking_error entries
    mentioning world-model hooks. Distinguish historical from current failures
    by comparing transcript mtimes to the last modification of settings.json.

    Severity:
      FAIL - hooks failed in a session that ran AFTER the current settings.json
             was last touched → the current install is still broken
      WARN - hooks only failed in sessions from BEFORE the current settings.json
             was written → historical, likely already fixed
      PASS - no world-model hook failures ever
    """
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return CheckResult(
            name="Claude Code hook error history",
            status=WARN,
            detail="Skipped (no ~/.claude/projects directory)",
        )
    encoded = str(project_dir.resolve()).replace("/", "-").replace(" ", "-")
    project_transcripts = claude_projects / encoded
    if not project_transcripts.exists():
        return CheckResult(
            name="Claude Code hook error history",
            status=WARN,
            detail=f"No Claude Code transcripts found for this project ({project_transcripts})",
        )

    settings_path = project_dir / ".claude" / "settings.json"
    settings_mtime = settings_path.stat().st_mtime if settings_path.exists() else 0.0

    def _has_hook_error(path: Path) -> bool:
        try:
            with path.open() as f:
                for line in f:
                    if "hook_non_blocking_error" in line and "world-model" in line:
                        return True
        except Exception:
            pass
        return False

    post_fix_failures = []
    pre_fix_failures = 0
    for jsonl in project_transcripts.glob("*.jsonl"):
        if not _has_hook_error(jsonl):
            continue
        if jsonl.stat().st_mtime > settings_mtime:
            post_fix_failures.append(jsonl.name)
        else:
            pre_fix_failures += 1

    if post_fix_failures:
        return CheckResult(
            name="Claude Code hook error history",
            status=FAIL,
            detail=(
                f"Hooks failed in {len(post_fix_failures)} Claude Code session(s) that "
                f"ran AFTER the current settings.json was last modified — the current "
                f"install is broken. Check shell-quoting or hook-scripts checks above."
            ),
            fix_hint=(
                "Grep the most recent failing transcript for the exact error: "
                f"grep hook_non_blocking_error {project_transcripts}/{post_fix_failures[0]} | head"
            ),
        )

    if pre_fix_failures:
        return CheckResult(
            name="Claude Code hook error history",
            status=WARN,
            detail=(
                f"Historical: {pre_fix_failures} session transcript(s) had world-model "
                f"hook failures, but all predate the current settings.json (mtime "
                f"comparison). The install has been fixed since; new sessions should "
                f"capture correctly."
            ),
        )

    return CheckResult(
        name="Claude Code hook error history",
        status=PASS,
        detail="No world-model hook failures in any session transcript",
    )


# ---------------------------------------------------------------------------
# v0.12.13: Copilot CLI hook-error signature scan
# ---------------------------------------------------------------------------
#
# Copilot CLI on Windows implements the Claude Code hook contract in a way
# that produces two silent-failure modes documented in copilot-cli #4001:
#
#   (A) PowerShell parses bash-shaped commands, producing ParserError.
#   (B) Copilot doesn't export $CLAUDE_PROJECT_DIR; paths resolve to
#       "/.claude/..." which doesn't exist. Error signature: `No such
#       file or directory: /.claude/...`.
#
# Both surface in ~/.copilot/logs/process-*.log (macOS/Linux) or the
# equivalent under %USERPROFILE%\.copilot\logs\ on Windows.
#
# This check scans those logs if present, categorizes findings by
# signature, and reports which of the two bugs is being hit. It does NOT
# scan Windows-specific PATH shims (Git Bash vs WSL launcher) — that
# needs Windows-side testing and lands separately.


COPILOT_LOG_GLOBS = ("process-*.log", "hooks-*.log", "*.log")
COPILOT_ERROR_POWERSHELL_PARSE = "ParserError"
COPILOT_ERROR_MISSING_PROJECT_DIR = "/.claude/"  # cwd=/ + relative expansion


def _copilot_log_dir() -> Optional[Path]:
    """Return the Copilot log directory if it exists.
    ~/.copilot/logs/ on macOS/Linux; %USERPROFILE%\\.copilot\\logs\\ on Windows.
    Path.home() handles both."""
    candidate = Path.home() / ".copilot" / "logs"
    return candidate if candidate.exists() else None


def check_copilot_hook_signatures(project_dir: Path) -> CheckResult:
    """Scan ~/.copilot/logs/*.log for the two signatures documented in
    github/copilot-cli#4001. SKIP when no Copilot install detected — this
    check is opt-in via Copilot's mere presence, not a required part of
    every doctor run.

    Reports separately how many log files show each signature so operators
    can tell which of the two Copilot-CLI bugs is affecting them.
    """
    log_dir = _copilot_log_dir()
    if log_dir is None:
        return CheckResult(
            name="Copilot CLI hook error signatures",
            status=PASS,
            detail="Skipped: no ~/.copilot/logs/ directory (Copilot CLI not installed here)",
        )

    # Dedupe across overlapping globs (*.log matches everything else too).
    log_files = sorted({
        p for pattern in COPILOT_LOG_GLOBS for p in log_dir.glob(pattern)
    })
    if not log_files:
        return CheckResult(
            name="Copilot CLI hook error signatures",
            status=PASS,
            detail=f"Copilot log dir present ({log_dir}) but no log files match",
        )

    parser_errors = []
    missing_dir_errors = []
    for log in log_files:
        try:
            text = log.read_text(errors="replace")
        except Exception:
            continue
        if COPILOT_ERROR_POWERSHELL_PARSE in text:
            parser_errors.append(log.name)
        if COPILOT_ERROR_MISSING_PROJECT_DIR in text:
            missing_dir_errors.append(log.name)

    if not parser_errors and not missing_dir_errors:
        return CheckResult(
            name="Copilot CLI hook error signatures",
            status=PASS,
            detail=f"Scanned {len(log_files)} Copilot log(s); no hook error signatures found",
        )

    parts = []
    if parser_errors:
        parts.append(
            f"{len(parser_errors)} log(s) show PowerShell ParserError "
            f"(Copilot running bash-shaped commands through PowerShell)"
        )
    if missing_dir_errors:
        parts.append(
            f"{len(missing_dir_errors)} log(s) show `/.claude/...` path resolution "
            f"(Copilot not exporting $CLAUDE_PROJECT_DIR; cwd resolves to /)"
        )
    return CheckResult(
        name="Copilot CLI hook error signatures",
        status=WARN,
        detail=(
            "Hook errors detected in Copilot CLI logs. "
            + " AND ".join(parts)
            + f". See github/copilot-cli#4001. Log dir: {log_dir}"
        ),
        fix_hint=(
            "Copilot-side bug (not world-model-mcp). Workaround: wrap hook "
            "commands in `bash -c '...'` and fall back to the stdin JSON's "
            "cwd field when $CLAUDE_PROJECT_DIR is unset. Full trace: "
            "https://github.com/github/copilot-cli/issues/4001"
        ),
    )


ALL_CHECKS: List[Callable[[Path], CheckResult]] = [
    check_node_available,
    check_settings_json_present,
    check_settings_json_shell_quoting,
    check_hooks_scripts_present,
    check_mcp_json_present,
    check_world_model_db_dir,
    check_stale_events_queue,
    check_recent_hook_failures,
    check_copilot_hook_signatures,
]


# ============================================================================
# Auto-fix helpers (only invoked when --fix is set)
# ============================================================================


def _auto_fix_settings_quoting(project_dir: Path) -> None:
    """Rewrite .claude/settings.json to double-quote $CLAUDE_PROJECT_DIR."""
    settings_path = project_dir / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text())
    for event_name, event_configs in settings.get("hooks", {}).items():
        for cfg in event_configs:
            for hook in cfg.get("hooks", []):
                cmd = hook.get("command", "")
                if "$CLAUDE_PROJECT_DIR" in cmd:
                    if not ('"$CLAUDE_PROJECT_DIR' in cmd or '"${CLAUDE_PROJECT_DIR' in cmd):
                        # Wrap the whole $CLAUDE_PROJECT_DIR/... path segment in quotes.
                        # Pattern: node $CLAUDE_PROJECT_DIR/.claude/hooks/foo.js [args]
                        hook["command"] = re.sub(
                            r"\$CLAUDE_PROJECT_DIR(\S+?\.js)",
                            r'"$CLAUDE_PROJECT_DIR\1"',
                            cmd,
                        )
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")


def _auto_fix_create_mcp_json(project_dir: Path) -> None:
    """Write a minimal .mcp.json registering world-model."""
    mcp_path = project_dir / ".mcp.json"
    content = {
        "mcpServers": {
            "world-model": {
                "command": "python3",
                "args": ["-m", "world_model_server.server"],
                "env": {
                    "WORLD_MODEL_DB_PATH": ".claude/world-model",
                },
            }
        }
    }
    mcp_path.write_text(json.dumps(content, indent=2) + "\n")


# ============================================================================
# Runner + CLI entry point
# ============================================================================


def run_checks(project_dir: Path) -> List[CheckResult]:
    return [check(project_dir) for check in ALL_CHECKS]


def _severity_rank(status: str) -> int:
    return {PASS: 0, WARN: 1, FAIL: 2}.get(status, 0)


def _format_table(results: List[CheckResult]) -> str:
    """Human-friendly table with fix hints indented under each finding."""
    lines = []
    for r in results:
        symbol = {PASS: "✓", WARN: "!", FAIL: "✗"}[r.status]
        lines.append(f"[{symbol}] {r.status}   {r.name}")
        for detail_line in r.detail.splitlines():
            lines.append(f"           {detail_line}")
        if r.fix_hint:
            lines.append(f"           Fix: {r.fix_hint}")
        lines.append("")
    return "\n".join(lines)


def doctor_command(args) -> None:
    """CLI entry point wired in world_model_server/cli.py."""
    project_dir = Path(args.project_dir).resolve()
    results = run_checks(project_dir)

    if getattr(args, "json", False):
        payload = [
            {
                "name": r.name,
                "status": r.status,
                "detail": r.detail,
                "fix_hint": r.fix_hint,
            }
            for r in results
        ]
        print(json.dumps(payload, indent=2))
    else:
        print(_format_table(results))
        # Summary line
        counts = {PASS: 0, WARN: 0, FAIL: 0}
        for r in results:
            counts[r.status] += 1
        print(f"Summary: {counts[PASS]} pass, {counts[WARN]} warn, {counts[FAIL]} fail")

    # --fix: attempt safe auto-fixes for FAIL/WARN entries that support them
    if getattr(args, "fix", False):
        fixed = 0
        for r in results:
            if r.status in (FAIL, WARN) and r.auto_fix is not None:
                try:
                    r.auto_fix(project_dir)
                    fixed += 1
                    print(f"  auto-fixed: {r.name}")
                except Exception as e:
                    print(f"  auto-fix failed for {r.name}: {e}")
        if fixed:
            print(f"\n{fixed} check(s) auto-fixed. Re-run `world-model doctor` to confirm.")

    # Exit code: 1 if any FAIL, else 0
    if any(r.status == FAIL for r in results):
        sys.exit(1)
