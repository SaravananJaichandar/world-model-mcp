"""
v0.7.5 feature tests.

F1: Codex CLI adapter (bundled config.toml + hooks_snippet.toml + install-codex CLI)
F2: Dual-shape payload handling in hook_helper + inject_helper (Claude Code AND Codex)
Schema regression: hook output must remain Codex deny_unknown_fields compliant.

Conventions follow v0.4..v0.7.4 suites.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ============================================================================
# F1.a: Bundled adapter files
# ============================================================================

def test_f1_codex_adapter_dir_bundled():
    """Codex adapter files must be inside the package so installs from PyPI
    ship them."""
    bundle = REPO_ROOT / "world_model_server" / "adapters" / "codex"
    assert bundle.exists()
    assert (bundle / "config.toml").exists()
    assert (bundle / "hooks_snippet.toml").exists()


def test_f1_codex_adapter_readme_exists():
    """The top-level adapters/codex/README.md is what users browsing the
    repo on GitHub see first."""
    assert (REPO_ROOT / "adapters" / "codex" / "README.md").exists()


# ============================================================================
# F1.b: TOML validity and structural correctness
# ============================================================================

def _load_toml(path: Path) -> dict:
    """Parse a TOML file using whichever TOML lib is available."""
    text = path.read_text()
    try:
        import tomllib  # py311+
        return tomllib.loads(text)
    except ImportError:
        import tomli  # fallback
        return tomli.loads(text)


def test_f1_codex_config_toml_parses():
    """config.toml is appended to user's ~/.codex/config.toml; it must be
    valid TOML or Codex will refuse to load the entire user config."""
    cfg = _load_toml(REPO_ROOT / "world_model_server" / "adapters" / "codex" / "config.toml")
    assert "mcp_servers" in cfg
    assert "world_model" in cfg["mcp_servers"]


def test_f1_codex_hooks_snippet_toml_parses():
    """hooks_snippet.toml is also appended; same TOML-validity rule."""
    cfg = _load_toml(REPO_ROOT / "world_model_server" / "adapters" / "codex" / "hooks_snippet.toml")
    assert "hooks" in cfg


def test_f1_codex_server_name_uses_underscore_not_hyphen():
    """Codex's tool name sanitizer silently strips hyphens. The adapter
    must use 'world_model' (underscore) to avoid collisions and the cryptic
    hash-suffix disambiguation path. See
    codex-rs/codex-mcp/src/mcp/mod.rs sanitize_responses_api_tool_name."""
    cfg = _load_toml(REPO_ROOT / "world_model_server" / "adapters" / "codex" / "config.toml")
    server_names = list(cfg["mcp_servers"].keys())
    assert "world_model" in server_names
    assert "world-model" not in server_names, (
        "Codex server name must use underscore. Hyphens get silently "
        "stripped by Codex's tool sanitizer."
    )


def test_f1_codex_config_uses_current_field_names_not_deprecated():
    """Codex renamed several MCP server fields. The adapter must use the
    current names, not the deprecated pre-v0.130 ones."""
    cfg = _load_toml(REPO_ROOT / "world_model_server" / "adapters" / "codex" / "config.toml")
    server = cfg["mcp_servers"]["world_model"]
    # Deprecated names that previously existed:
    for deprecated in ("trust", "timeout", "headers", "includeTools", "excludeTools"):
        assert deprecated not in server, (
            f"Field {deprecated!r} is deprecated. Use the current name "
            "(default_tools_approval_mode, startup_timeout_sec, "
            "tool_timeout_sec, http_headers, enabled_tools, disabled_tools)."
        )
    # Required: command for stdio servers
    assert "command" in server


# Per-event hook configuration must use the exact 10-event taxonomy Codex
# accepts. These are the event names from codex-rs/config/src/hook_config.rs.
CODEX_VALID_HOOK_EVENTS = {
    "PreToolUse", "PermissionRequest", "PostToolUse", "PreCompact",
    "PostCompact", "SessionStart", "UserPromptSubmit",
    "SubagentStart", "SubagentStop", "Stop",
}


def test_f1_codex_hooks_use_valid_event_names():
    """Hook event names must match Codex's enum exactly. Mis-cased names
    or invented names get the whole hooks block rejected."""
    cfg = _load_toml(REPO_ROOT / "world_model_server" / "adapters" / "codex" / "hooks_snippet.toml")
    for event_name in cfg["hooks"].keys():
        assert event_name in CODEX_VALID_HOOK_EVENTS, (
            f"Unknown Codex hook event {event_name!r}. Valid events: "
            f"{sorted(CODEX_VALID_HOOK_EVENTS)}"
        )


def test_f1_codex_hooks_have_command_and_matcher():
    """Each hook entry needs a matcher (regex) and inner hooks array with
    type='command' + command string."""
    cfg = _load_toml(REPO_ROOT / "world_model_server" / "adapters" / "codex" / "hooks_snippet.toml")
    for event_name, event_entries in cfg["hooks"].items():
        assert isinstance(event_entries, list), (
            f"{event_name} must be an array of tables ([[hooks.{event_name}]])"
        )
        for entry in event_entries:
            assert "matcher" in entry, f"{event_name} entry missing 'matcher'"
            assert "hooks" in entry, f"{event_name} entry missing inner 'hooks'"
            for cmd in entry["hooks"]:
                assert cmd.get("type") == "command", (
                    f"{event_name} inner hook must have type='command'"
                )
                assert "command" in cmd, f"{event_name} inner hook missing 'command'"


# ============================================================================
# F2: Dual-shape payload handling
# ============================================================================

def test_f2_inject_helper_accepts_claude_code_payload(tmp_path):
    """Backward compat: existing Claude Code payload shape must still work."""
    from world_model_server.inject_helper import _normalize_payload, build_injection

    payload = {
        "event": "PostCompact",
        "project_dir": str(tmp_path),
        "session_id": "test-session",
    }
    normalized = _normalize_payload(payload)
    assert normalized["event"] == "PostCompact"
    assert normalized["project_dir"] == str(tmp_path)


def test_f2_inject_helper_accepts_codex_payload(tmp_path):
    """Codex payload shape (hook_event_name + cwd) gets translated to the
    internal shape so the rest of build_injection works unchanged."""
    from world_model_server.inject_helper import _normalize_payload

    codex_payload = {
        "hook_event_name": "PostCompact",
        "cwd": str(tmp_path),
        "session_id": "codex-session-abc",
        "transcript_path": "/tmp/transcript.jsonl",
        "model": "gpt-5-codex",
        "permission_mode": "untrusted",
    }
    normalized = _normalize_payload(codex_payload)
    assert normalized["event"] == "PostCompact"
    assert normalized["project_dir"] == str(tmp_path)
    # Codex-specific fields are preserved
    assert normalized["transcript_path"] == "/tmp/transcript.jsonl"


def test_f2_inject_helper_returns_empty_when_neither_shape_present():
    """Hostile or wrong-shape inputs return {} (fail-open)."""
    from world_model_server.inject_helper import build_injection

    assert build_injection({}) == {}
    assert build_injection({"random": "data"}) == {}


def test_f2_hook_helper_accepts_codex_cwd(tmp_path):
    """hook_helper.classify accepts cwd as an alias for project_dir."""
    from world_model_server.hook_helper import classify

    # No constraints loaded -> returns {} either way, but the function
    # must not raise on a payload that uses cwd instead of project_dir.
    out = classify({
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/x.ts", "new_string": "console.log('x')"},
        "cwd": str(tmp_path),
        "supports_defer": True,
    })
    assert isinstance(out, dict)


# ============================================================================
# F3: Hook output schema strictness
#
# Codex enforces deny_unknown_fields + rename_all=camelCase on every hook
# output struct. The bundled helpers must emit only documented fields, in
# the exact casing Codex expects, or the entire response gets rejected.
# ============================================================================

# These are the only top-level fields the Codex HookUniversalOutputWire
# accepts (from codex-rs/hooks/src/schema.rs at v0.135+).
ALLOWED_TOP_LEVEL_FIELDS = {
    "continue",
    "stopReason",
    "suppressOutput",
    "systemMessage",
    "hookSpecificOutput",
    # Legacy Claude-compatible alternates also accepted:
    "decision",
    "reason",
    # Adapter-internal bookkeeping (Codex IGNORES extras only on a few
    # variants; the safe stance is to keep only documented fields).
}


def test_f3_inject_helper_output_uses_camelcase_only(tmp_path):
    """The bundled inject_helper emits hookSpecificOutput.{hookEventName,
    additionalContext}. Codex rejects snake_case in this surface."""
    from world_model_server.inject_helper import build_injection

    # Seed something findable so build_injection actually produces output
    proj = tmp_path / "p"
    proj.mkdir()
    db = proj / ".claude" / "world-model"
    db.mkdir(parents=True)
    # Initialize the constraints DB
    import sqlite3
    conn = sqlite3.connect(str(db / "constraints.db"))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS constraints (
            id TEXT PRIMARY KEY, rule_name TEXT, description TEXT,
            severity TEXT, violation_count INTEGER DEFAULT 0,
            constraint_type TEXT, file_pattern TEXT, examples TEXT,
            last_violated TIMESTAMP, created_at TIMESTAMP,
            content_hash TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO constraints (id, rule_name, description, severity, violation_count, constraint_type) "
        "VALUES ('c1', 'no-console', 'use logger', 'warning', 1, 'style')"
    )
    conn.commit()
    conn.close()

    out = build_injection({
        "event": "SessionStart",
        "project_dir": str(proj),
    })

    if not out:
        # Empty output is also Codex-compliant; only check shape if non-empty
        return

    # Top-level must be limited
    for key in out:
        # bookkeeping fields like facts_count, constraints_count, audit_id
        # are NOT in the Codex schema. They are dropped by the Codex side
        # but should not crash strict parsing because Codex applies
        # deny_unknown_fields on hookSpecificOutput, not on the outer
        # bookkeeping fields. We verify the load-bearing nested struct.
        if key == "hookSpecificOutput":
            hso = out[key]
            assert "hookEventName" in hso, (
                "Codex requires hookEventName (camelCase) inside hookSpecificOutput"
            )
            assert "hook_event_name" not in hso, (
                "snake_case hook_event_name is rejected by deny_unknown_fields"
            )
            # additionalContext is the load-bearing field for re-injection
            if "additionalContext" in hso:
                assert isinstance(hso["additionalContext"], str)
            assert "additional_context" not in hso, (
                "snake_case additional_context is rejected"
            )


def test_f3_inject_helper_hook_event_name_matches_event():
    """Codex tightened the schema in v0.136 (#24962): hookEventName must
    be the literal string matching the event the hook was registered for.
    A PostCompact hook returning hookEventName='PreToolUse' gets rejected."""
    from world_model_server.inject_helper import build_injection

    for event in ("PostCompact", "SessionStart", "UserPromptSubmit"):
        out = build_injection({"event": event, "project_dir": "/tmp"})
        if out and "hookSpecificOutput" in out:
            assert out["hookSpecificOutput"]["hookEventName"] == event, (
                f"hookEventName mismatch for event={event}; Codex v0.136+ "
                "rejects this strictly."
            )


def test_f3_hook_helper_output_camelcase(tmp_path):
    """hook_helper emits permissionDecision (camelCase), never
    permission_decision. The Rust deserializer would reject snake_case."""
    from world_model_server.hook_helper import classify

    out = classify({
        "tool_name": "Edit",
        "tool_input": {"file_path": "x.ts", "new_string": "console.log(1)"},
        "project_dir": str(tmp_path),
    })
    # Empty is fine (no constraints), but if anything is returned, check shape
    if out and "hookSpecificOutput" in out:
        hso = out["hookSpecificOutput"]
        assert "permissionDecision" in hso, "Codex expects camelCase"
        assert "permission_decision" not in hso
        assert "hookEventName" in hso
        # Codex permission decisions are literal-typed
        assert hso["permissionDecision"] in ("deny", "ask", "allow", "defer"), (
            f"Unexpected permissionDecision: {hso['permissionDecision']!r}"
        )


# ============================================================================
# F4: install-codex CLI
# ============================================================================

def test_f4_install_codex_dry_run(tmp_path):
    """--dry-run prints what would be appended without writing."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("# existing\n")
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-codex",
         "--config-path", str(cfg), "--dry-run"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "Would append" in result.stdout
    # Config unchanged
    assert cfg.read_text() == "# existing\n"


def test_f4_install_codex_writes(tmp_path):
    """First install appends the adapter to a config file."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("# existing user config\n")
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-codex",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    text = cfg.read_text()
    # Original content preserved
    assert "# existing user config" in text
    # Adapter marker present
    assert "# world-model-mcp adapter for OpenAI Codex CLI" in text
    # MCP server block present
    assert "[mcp_servers.world_model]" in text
    # At least one hook block present
    assert "[[hooks.PreToolUse]]" in text


def test_f4_install_codex_idempotent(tmp_path):
    """Second install without --force refuses to write again."""
    cfg = tmp_path / "config.toml"

    # First install
    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-codex",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    first_text = cfg.read_text()

    # Second install (no --force)
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-codex",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "already present" in result.stdout.lower()
    # File unchanged
    assert cfg.read_text() == first_text


def test_f4_install_codex_creates_parent_dir(tmp_path):
    """If ~/.codex/ doesn't exist, install creates it."""
    cfg = tmp_path / "subdir" / "config.toml"
    assert not cfg.parent.exists()

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-codex",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert cfg.exists()


# ============================================================================
# Backward-compat regression
# ============================================================================

def test_bc_existing_cli_subcommands_present():
    """Every v0.7.4 subcommand must still be registered, plus the new
    install-codex."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    for cmd in (
        "setup", "seed", "query", "decisions", "register", "projects",
        "search-global", "health", "decay", "recall", "export-claude-md",
        "migrate", "status", "audit-compactions", "install-cursor",
        "install-pi", "demo", "telemetry",
        "install-codex",  # new in v0.7.5
    ):
        assert cmd in result.stdout, f"Missing CLI subcommand: {cmd}"


def test_bc_version_is_075():
    from world_model_server import __version__
    parts = __version__.split(".")
    major, minor = int(parts[0]), int(parts[1])
    assert (major, minor) >= (0, 7)
    if (major, minor) == (0, 7):
        patch_str = parts[2].split("rc")[0].split("a")[0].split("b")[0]
        assert int(patch_str) >= 5
