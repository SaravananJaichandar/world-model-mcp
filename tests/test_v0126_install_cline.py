"""
v0.12.6: install-cline adapter.

Registers world-model-mcp with Cline by merging into ~/.cline/mcp.json.
Cline uses a top-level ``mcpServers`` mapping (same shape as Cursor /
Claude Code), distinct from Copilot's ``servers`` and Continue's
mcpServers-list. Users routinely have other MCP servers already
registered via Cline's UI, so the installer merges rather than
overwrites — same discipline as install-copilot.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from world_model_server.cli import install_cline_command


REPO_ROOT = Path(__file__).parent.parent
BUNDLED = REPO_ROOT / "world_model_server" / "adapters" / "cline" / "mcp.json"


# ---------------------------------------------------------------------------
# B: bundled adapter file sanity
# ---------------------------------------------------------------------------


def test_b1_bundled_adapter_exists():
    assert BUNDLED.exists(), f"Bundled adapter missing: {BUNDLED}"


def test_b1_bundled_adapter_uses_mcp_servers_key():
    """Cline uses 'mcpServers' (mapping), NOT 'servers' like Copilot."""
    data = json.loads(BUNDLED.read_text())
    assert "mcpServers" in data
    assert "servers" not in data, (
        "'servers' is Copilot's key; Cline expects 'mcpServers'."
    )


def test_b1_bundled_adapter_registers_world_model():
    data = json.loads(BUNDLED.read_text())
    entry = data["mcpServers"]["world-model"]
    assert entry["command"] == "python3"
    assert entry["args"] == ["-m", "world_model_server.server"]
    # Cline-specific Boolean fields defaulted safely
    assert entry.get("disabled") is False
    assert entry.get("autoApprove") == []


# ---------------------------------------------------------------------------
# I: installer behavior
# ---------------------------------------------------------------------------


def _run(tmp_path, *, force=False, dry_run=False, config_path=None):
    if config_path is None:
        config_path = str(tmp_path / "mcp.json")
    args = SimpleNamespace(
        config_path=config_path,
        force=force,
        dry_run=dry_run,
    )
    install_cline_command(args)


def test_i1_fresh_install_creates_config(tmp_path):
    cfg = tmp_path / "mcp.json"
    _run(tmp_path, config_path=str(cfg))
    assert cfg.exists()
    data = json.loads(cfg.read_text())
    assert "world-model" in data["mcpServers"]


def test_i1_fresh_install_matches_bundled_template(tmp_path):
    cfg = tmp_path / "mcp.json"
    _run(tmp_path, config_path=str(cfg))
    assert json.loads(cfg.read_text()) == json.loads(BUNDLED.read_text())


def test_i2_merge_preserves_other_servers(tmp_path):
    cfg = tmp_path / "mcp.json"
    existing = {
        "mcpServers": {
            "sqlite": {
                "command": "node",
                "args": ["/path/to/sqlite/server.js"],
                "disabled": False,
                "autoApprove": ["query"],
            },
            "brave": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer xxx"},
            },
        }
    }
    cfg.write_text(json.dumps(existing, indent=2))
    _run(tmp_path, config_path=str(cfg))
    data = json.loads(cfg.read_text())
    assert set(data["mcpServers"].keys()) == {"sqlite", "brave", "world-model"}
    assert data["mcpServers"]["sqlite"]["autoApprove"] == ["query"]
    assert data["mcpServers"]["brave"]["headers"]["Authorization"] == "Bearer xxx"


def test_i2_merge_preserves_non_mcp_servers_top_level_keys(tmp_path):
    cfg = tmp_path / "mcp.json"
    existing = {
        "userSettings": {"theme": "dark"},
        "mcpServers": {"sqlite": {"command": "node"}},
    }
    cfg.write_text(json.dumps(existing, indent=2))
    _run(tmp_path, config_path=str(cfg))
    data = json.loads(cfg.read_text())
    assert data.get("userSettings") == {"theme": "dark"}


def test_i3_existing_world_model_entry_skipped_without_force(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps(
        {"mcpServers": {"world-model": {"command": "user-custom"}}}, indent=2
    ))
    _run(tmp_path, config_path=str(cfg))
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["world-model"]["command"] == "user-custom"


def test_i3_force_overwrites_world_model_only(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "world-model": {"command": "user-custom"},
            "sqlite": {"command": "node"},
        }
    }, indent=2))
    _run(tmp_path, config_path=str(cfg), force=True)
    data = json.loads(cfg.read_text())
    bundled = json.loads(BUNDLED.read_text())
    assert data["mcpServers"]["world-model"] == bundled["mcpServers"]["world-model"]
    assert data["mcpServers"]["sqlite"] == {"command": "node"}


def test_i4_file_without_mcp_servers_key_gets_it_added(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"userSettings": {"theme": "dark"}}, indent=2))
    _run(tmp_path, config_path=str(cfg))
    data = json.loads(cfg.read_text())
    assert "mcpServers" in data
    assert "world-model" in data["mcpServers"]
    assert data["userSettings"] == {"theme": "dark"}


# ---------------------------------------------------------------------------
# E: error paths
# ---------------------------------------------------------------------------


def test_e1_malformed_json_refused(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text("{not-json}")
    with pytest.raises(SystemExit):
        _run(tmp_path, config_path=str(cfg))
    assert cfg.read_text() == "{not-json}"


def test_e2_top_level_array_refused(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text("[]")
    with pytest.raises(SystemExit):
        _run(tmp_path, config_path=str(cfg))


def test_e3_mcp_servers_not_object_refused(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text('{"mcpServers": ["not-a-map"]}')
    with pytest.raises(SystemExit):
        _run(tmp_path, config_path=str(cfg))


# ---------------------------------------------------------------------------
# D: --dry-run
# ---------------------------------------------------------------------------


def test_d1_dry_run_does_not_create_file(tmp_path):
    cfg = tmp_path / "mcp.json"
    _run(tmp_path, config_path=str(cfg), dry_run=True)
    assert not cfg.exists()


def test_d1_dry_run_does_not_modify_existing(tmp_path):
    cfg = tmp_path / "mcp.json"
    original = json.dumps({"mcpServers": {"sqlite": {"command": "node"}}}, indent=2)
    cfg.write_text(original)
    _run(tmp_path, config_path=str(cfg), dry_run=True)
    assert cfg.read_text() == original


# ---------------------------------------------------------------------------
# C: CLI wiring
# ---------------------------------------------------------------------------


def test_c1_subcommand_registered_in_help():
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "install-cline" in result.stdout


def test_c1_subcommand_dry_run_via_cli(tmp_path):
    cfg = tmp_path / "mcp.json"
    result = subprocess.run(
        [
            sys.executable, "-m", "world_model_server.cli",
            "install-cline",
            "--config-path", str(cfg),
            "--dry-run",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "world-model" in result.stdout
    assert "mcpServers" in result.stdout
    assert not cfg.exists()
