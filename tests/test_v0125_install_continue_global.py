"""
v0.12.5: install-continue --global config-merge path.

The v0.10 install-continue writes a standalone per-project YAML at
.continue/mcpServers/world-model.yaml. v0.12.5 adds --global which
merges into the user-global ~/.continue/config.yaml instead. Continue's
schema puts mcpServers as a LIST of entries (each with a `name` key),
NOT a mapping — this is distinct from Hermes' mcp_servers-mapping shape,
and merge logic is written for the list case specifically.

Regression discipline:
  - fresh install (no file) creates a config.yaml with a single-entry
    mcpServers list
  - existing file with other mcpServers entries: our entry is appended,
    others preserved and their positions kept
  - existing file with a world-model entry: skipped unless --force
  - --force replaces the world-model entry in place (other entries at
    their original indices)
  - top-level non-mcpServers keys preserved
  - ruamel.yaml round-trip preserves comments
  - malformed YAML: refused with clear error
  - mcpServers-not-a-list: refused
  - --dry-run does not touch disk
  - per-project mode (no --global) still works unchanged (v0.10
    behavior contract)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from world_model_server.cli import install_continue_command


# ruamel.yaml is now required for --global — skip the whole suite if
# not installed rather than failing every test.
pytest.importorskip("ruamel.yaml")

from ruamel.yaml import YAML  # noqa: E402


def _load(path: Path):
    return YAML().load(path.read_text())


def _run(tmp_path, *, use_global=True, force=False, dry_run=False, config_path=None):
    args = SimpleNamespace(
        project_dir=str(tmp_path),
        global_config=use_global,
        config_path=config_path,
        python="/usr/bin/python3",
        db_path=None,
        force=force,
        dry_run=dry_run,
    )
    install_continue_command(args)


# ---------------------------------------------------------------------------
# Fresh install
# ---------------------------------------------------------------------------


def test_fresh_global_install_creates_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    _run(tmp_path, config_path=str(cfg))
    assert cfg.exists()
    data = _load(cfg)
    assert isinstance(data["mcpServers"], list)
    assert len(data["mcpServers"]) == 1
    entry = data["mcpServers"][0]
    assert entry["name"] == "world-model"
    assert entry["type"] == "stdio"
    assert entry["command"] == "/usr/bin/python3"
    assert entry["args"] == ["-m", "world_model_server.server"]
    assert entry["env"]["WORLD_MODEL_DB_PATH"] == ".claude/world-model"


# ---------------------------------------------------------------------------
# Merge: other entries preserved
# ---------------------------------------------------------------------------


def test_merge_appends_and_preserves_other_entries(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcpServers:\n"
        "  - name: sqlite\n"
        "    type: stdio\n"
        "    command: npx\n"
        "    args: [-y, mcp-sqlite]\n"
        "  - name: playwright\n"
        "    type: stdio\n"
        "    command: npx\n"
        "    args: [-y, '@microsoft/mcp-server-playwright']\n"
    )
    _run(tmp_path, config_path=str(cfg))
    data = _load(cfg)
    names = [e["name"] for e in data["mcpServers"]]
    assert names == ["sqlite", "playwright", "world-model"], (
        "Existing entries must be preserved in order; ours appended last."
    )


def test_merge_preserves_top_level_non_mcp_servers_keys(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "name: my-continue-config\n"
        "version: 0.0.1\n"
        "schema: v1\n"
        "models:\n"
        "  - name: claude\n"
        "    provider: anthropic\n"
        "mcpServers:\n"
        "  - name: sqlite\n"
        "    command: npx\n"
    )
    _run(tmp_path, config_path=str(cfg))
    data = _load(cfg)
    assert data.get("name") == "my-continue-config"
    assert data.get("version") == "0.0.1"
    assert data.get("schema") == "v1"
    assert data["models"][0]["name"] == "claude"


def test_merge_comments_survive_round_trip(tmp_path):
    """The whole reason install-hermes uses ruamel.yaml is comment
    preservation. Same guarantee must hold for --global here."""
    cfg = tmp_path / "config.yaml"
    original = (
        "# My personal continue setup — DO NOT DELETE\n"
        "name: my-config\n"
        "# List of MCP servers I use across projects\n"
        "mcpServers:\n"
        "  - name: sqlite\n"
        "    command: npx\n"
    )
    cfg.write_text(original)
    _run(tmp_path, config_path=str(cfg))
    text = cfg.read_text()
    assert "# My personal continue setup — DO NOT DELETE" in text
    assert "# List of MCP servers I use across projects" in text


# ---------------------------------------------------------------------------
# Existing world-model entry: skip vs --force
# ---------------------------------------------------------------------------


def test_existing_world_model_entry_skipped_without_force(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcpServers:\n"
        "  - name: world-model\n"
        "    command: user-custom-path\n"
        "    args: [--custom]\n"
    )
    _run(tmp_path, config_path=str(cfg))
    data = _load(cfg)
    # User's custom command untouched
    assert data["mcpServers"][0]["command"] == "user-custom-path"


def test_force_replaces_world_model_entry_in_place(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcpServers:\n"
        "  - name: sqlite\n"
        "    command: npx\n"
        "  - name: world-model\n"
        "    command: old-path\n"
        "  - name: playwright\n"
        "    command: npx\n"
    )
    _run(tmp_path, config_path=str(cfg), force=True)
    data = _load(cfg)
    names = [e["name"] for e in data["mcpServers"]]
    # Order preserved — world-model still at index 1
    assert names == ["sqlite", "world-model", "playwright"]
    # And its command is now the fresh value
    assert data["mcpServers"][1]["command"] == "/usr/bin/python3"


# ---------------------------------------------------------------------------
# Structural errors: refuse rather than corrupt
# ---------------------------------------------------------------------------


def test_malformed_yaml_refused(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("this: is: not: valid: yaml: [\n")
    original = cfg.read_text()
    with pytest.raises(SystemExit):
        _run(tmp_path, config_path=str(cfg))
    assert cfg.read_text() == original


def test_mcp_servers_not_a_list_refused(tmp_path):
    """Continue's schema wants a list. If a user has a mapping (Hermes
    style), refuse rather than silently break their config."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcpServers:\n"
        "  world-model:\n"
        "    command: foo\n"
    )
    original = cfg.read_text()
    with pytest.raises(SystemExit):
        _run(tmp_path, config_path=str(cfg))
    assert cfg.read_text() == original


def test_top_level_not_mapping_refused(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("- just\n- a\n- list\n")
    original = cfg.read_text()
    with pytest.raises(SystemExit):
        _run(tmp_path, config_path=str(cfg))
    assert cfg.read_text() == original


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


def test_dry_run_does_not_create_file(tmp_path):
    cfg = tmp_path / "config.yaml"
    _run(tmp_path, config_path=str(cfg), dry_run=True)
    assert not cfg.exists()


def test_dry_run_does_not_modify_existing(tmp_path):
    cfg = tmp_path / "config.yaml"
    original = "mcpServers:\n  - name: sqlite\n    command: npx\n"
    cfg.write_text(original)
    _run(tmp_path, config_path=str(cfg), dry_run=True)
    assert cfg.read_text() == original


# ---------------------------------------------------------------------------
# --python validation still fires under --global
# ---------------------------------------------------------------------------


def test_relative_python_rejected_in_global_mode(tmp_path):
    args = SimpleNamespace(
        project_dir=str(tmp_path),
        global_config=True,
        config_path=str(tmp_path / "config.yaml"),
        python="python3",
        db_path=None,
        force=False,
        dry_run=False,
    )
    with pytest.raises(SystemExit):
        install_continue_command(args)


# ---------------------------------------------------------------------------
# Per-project mode still works (v0.10 behavior contract)
# ---------------------------------------------------------------------------


def test_per_project_mode_still_writes_standalone_yaml(tmp_path):
    """--global unset -> per-project behavior identical to v0.10."""
    _run(tmp_path, use_global=False)
    target = tmp_path / ".continue" / "mcpServers" / "world-model.yaml"
    assert target.exists()
    text = target.read_text()
    assert "name: world-model-mcp" in text
    assert "  - name: world-model" in text
    assert "WORLD_MODEL_DB_PATH: .claude/world-model" in text


def test_per_project_mode_does_not_touch_home_config(tmp_path):
    """Per-project mode must NEVER read or write ~/.continue/config.yaml."""
    _run(tmp_path, use_global=False)
    assert not (tmp_path / "config.yaml").exists()
