"""
v0.12.7: install-windsurf adapter.

Registers world-model-mcp with Windsurf's Cascade agent by merging into
~/.codeium/windsurf/mcp_config.json. Windsurf uses the top-level
``mcpServers`` mapping — same shape as Cline / Cursor / Claude Code, so
the merge logic is behaviorally identical to install-cline. The only
real difference is the default config path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from world_model_server.cli import install_windsurf_command


REPO_ROOT = Path(__file__).parent.parent
BUNDLED = REPO_ROOT / "world_model_server" / "adapters" / "windsurf" / "mcp_config.json"


# ---------------------------------------------------------------------------
# B: bundled adapter file sanity
# ---------------------------------------------------------------------------


def test_b1_bundled_adapter_exists():
    assert BUNDLED.exists(), f"Bundled adapter missing: {BUNDLED}"


def test_b1_bundled_adapter_uses_mcp_servers_key():
    """Windsurf uses 'mcpServers' (mapping), same shape as Cline."""
    data = json.loads(BUNDLED.read_text())
    assert "mcpServers" in data
    assert "servers" not in data


def test_b1_bundled_adapter_registers_world_model():
    data = json.loads(BUNDLED.read_text())
    entry = data["mcpServers"]["world-model"]
    assert entry["command"] == "python3"
    assert entry["args"] == ["-m", "world_model_server.server"]
    assert entry["env"]["WORLD_MODEL_DB_PATH"] == ".claude/world-model"


# ---------------------------------------------------------------------------
# I: installer behavior
# ---------------------------------------------------------------------------


def _run(tmp_path, *, force=False, dry_run=False, config_path=None):
    if config_path is None:
        config_path = str(tmp_path / "mcp_config.json")
    args = SimpleNamespace(
        config_path=config_path,
        force=force,
        dry_run=dry_run,
    )
    install_windsurf_command(args)


def test_i1_fresh_install_creates_config(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    _run(tmp_path, config_path=str(cfg))
    assert cfg.exists()
    data = json.loads(cfg.read_text())
    assert "world-model" in data["mcpServers"]


def test_i1_fresh_install_matches_bundled(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    _run(tmp_path, config_path=str(cfg))
    assert json.loads(cfg.read_text()) == json.loads(BUNDLED.read_text())


def test_i2_merge_preserves_other_servers(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    existing = {
        "mcpServers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "token123"},
            }
        }
    }
    cfg.write_text(json.dumps(existing, indent=2))
    _run(tmp_path, config_path=str(cfg))
    data = json.loads(cfg.read_text())
    assert "github" in data["mcpServers"], "github MCP entry got clobbered"
    assert data["mcpServers"]["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "token123"
    assert "world-model" in data["mcpServers"]


def test_i2_merge_preserves_top_level_non_mcp_servers_keys(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text(json.dumps({
        "customSetting": {"foo": "bar"},
        "mcpServers": {"github": {"command": "npx"}},
    }, indent=2))
    _run(tmp_path, config_path=str(cfg))
    data = json.loads(cfg.read_text())
    assert data.get("customSetting") == {"foo": "bar"}


def test_i3_existing_world_model_entry_skipped_without_force(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text(json.dumps(
        {"mcpServers": {"world-model": {"command": "user-custom"}}}, indent=2
    ))
    _run(tmp_path, config_path=str(cfg))
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["world-model"]["command"] == "user-custom"


def test_i3_force_overwrites_world_model_only(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "world-model": {"command": "user-custom"},
            "github": {"command": "npx"},
        }
    }, indent=2))
    _run(tmp_path, config_path=str(cfg), force=True)
    data = json.loads(cfg.read_text())
    bundled = json.loads(BUNDLED.read_text())
    assert data["mcpServers"]["world-model"] == bundled["mcpServers"]["world-model"]
    assert data["mcpServers"]["github"] == {"command": "npx"}


def test_i4_file_without_mcp_servers_key_gets_it_added(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text(json.dumps({"customSetting": {"foo": "bar"}}, indent=2))
    _run(tmp_path, config_path=str(cfg))
    data = json.loads(cfg.read_text())
    assert "mcpServers" in data
    assert "world-model" in data["mcpServers"]
    assert data["customSetting"] == {"foo": "bar"}


# ---------------------------------------------------------------------------
# E: error paths
# ---------------------------------------------------------------------------


def test_e1_malformed_json_refused(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text("{not-json}")
    with pytest.raises(SystemExit):
        _run(tmp_path, config_path=str(cfg))
    assert cfg.read_text() == "{not-json}"


def test_e2_top_level_array_refused(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text("[]")
    with pytest.raises(SystemExit):
        _run(tmp_path, config_path=str(cfg))


def test_e3_mcp_servers_not_object_refused(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    cfg.write_text('{"mcpServers": ["not-a-map"]}')
    with pytest.raises(SystemExit):
        _run(tmp_path, config_path=str(cfg))


# ---------------------------------------------------------------------------
# D: --dry-run
# ---------------------------------------------------------------------------


def test_d1_dry_run_does_not_create_file(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    _run(tmp_path, config_path=str(cfg), dry_run=True)
    assert not cfg.exists()


def test_d1_dry_run_does_not_modify_existing(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    original = json.dumps({"mcpServers": {"github": {"command": "npx"}}}, indent=2)
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
    assert "install-windsurf" in result.stdout


def test_c1_subcommand_dry_run_via_cli(tmp_path):
    cfg = tmp_path / "mcp_config.json"
    result = subprocess.run(
        [
            sys.executable, "-m", "world_model_server.cli",
            "install-windsurf",
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
