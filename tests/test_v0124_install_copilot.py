"""
v0.12.4: install-copilot adapter.

Registers world-model-mcp with GitHub Copilot Chat in VS Code by merging
into .vscode/mcp.json. VS Code uses a top-level ``servers`` key (not
``mcpServers`` like Claude Code / Cursor). Users commonly already have
this file populated with other MCP servers (github, playwright, etc.),
so the installer MUST merge — an overwrite would silently blow away
their existing config.

Regression discipline:
  - fresh install writes the bundled template intact
  - existing file with other servers: our entry is added, others preserved
  - existing file with a stale world-model entry: skipped unless --force
  - --force overwrites ONLY the world-model key; other servers preserved
  - malformed JSON: refuse with a clear error, exit nonzero
  - top-level not-an-object: refuse with a clear error
  - 'servers' not-an-object: refuse with a clear error
  - --dry-run does not touch disk
  - the bundled adapter file is well-formed JSON with the right shape
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from world_model_server.cli import install_copilot_command


REPO_ROOT = Path(__file__).parent.parent
BUNDLED = REPO_ROOT / "world_model_server" / "adapters" / "copilot" / "mcp.json"


# ---------------------------------------------------------------------------
# B: bundled adapter file sanity
# ---------------------------------------------------------------------------


def test_b1_bundled_adapter_exists():
    assert BUNDLED.exists(), f"Bundled adapter missing: {BUNDLED}"


def test_b1_bundled_adapter_uses_vscode_servers_key():
    """VS Code Copilot Chat MCP config uses 'servers', NOT 'mcpServers'."""
    data = json.loads(BUNDLED.read_text())
    assert "servers" in data, "Bundled adapter must use VS Code's 'servers' key"
    assert "mcpServers" not in data, (
        "The 'mcpServers' key is Claude Code / Cursor convention; VS Code "
        "Copilot Chat does not read it."
    )


def test_b1_bundled_adapter_registers_world_model():
    data = json.loads(BUNDLED.read_text())
    assert "world-model" in data["servers"]
    entry = data["servers"]["world-model"]
    assert entry["command"] == "python3"
    assert entry["args"] == ["-m", "world_model_server.server"]


# ---------------------------------------------------------------------------
# I: installer behavior
# ---------------------------------------------------------------------------


def _run(tmp_path, *, force=False, dry_run=False):
    args = SimpleNamespace(
        project_dir=str(tmp_path),
        force=force,
        dry_run=dry_run,
    )
    install_copilot_command(args)


def test_i1_fresh_install_creates_vscode_mcp_json(tmp_path):
    _run(tmp_path)
    target = tmp_path / ".vscode" / "mcp.json"
    assert target.exists()
    data = json.loads(target.read_text())
    assert "world-model" in data["servers"]


def test_i1_fresh_install_matches_bundled_template(tmp_path):
    _run(tmp_path)
    target = tmp_path / ".vscode" / "mcp.json"
    written = json.loads(target.read_text())
    bundled = json.loads(BUNDLED.read_text())
    assert written == bundled


def test_i2_merge_preserves_other_servers(tmp_path):
    """The load-bearing regression: an existing github entry must survive."""
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    existing = {
        "servers": {
            "github": {
                "type": "http",
                "url": "https://api.githubcopilot.com/mcp",
            },
            "playwright": {
                "command": "npx",
                "args": ["-y", "@microsoft/mcp-server-playwright"],
            },
        }
    }
    (vscode / "mcp.json").write_text(json.dumps(existing, indent=2))
    _run(tmp_path)
    data = json.loads((vscode / "mcp.json").read_text())
    assert "github" in data["servers"], "github MCP entry got clobbered"
    assert "playwright" in data["servers"], "playwright entry got clobbered"
    assert data["servers"]["github"]["url"] == "https://api.githubcopilot.com/mcp"
    assert "world-model" in data["servers"]


def test_i2_merge_preserves_top_level_non_servers_keys(tmp_path):
    """VS Code mcp.json may include an inputs array and other keys — those
    must not be touched."""
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    existing = {
        "inputs": [{"type": "promptString", "id": "gh_token"}],
        "servers": {"github": {"type": "http", "url": "https://x.example"}},
    }
    (vscode / "mcp.json").write_text(json.dumps(existing, indent=2))
    _run(tmp_path)
    data = json.loads((vscode / "mcp.json").read_text())
    assert data.get("inputs") == existing["inputs"]


def test_i3_existing_world_model_entry_skipped_without_force(tmp_path, capsys):
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    existing = {"servers": {"world-model": {"command": "user-custom-cmd"}}}
    (vscode / "mcp.json").write_text(json.dumps(existing, indent=2))
    _run(tmp_path)
    data = json.loads((vscode / "mcp.json").read_text())
    # User's custom command must survive
    assert data["servers"]["world-model"]["command"] == "user-custom-cmd"


def test_i3_force_overwrites_world_model_only(tmp_path):
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    existing = {
        "servers": {
            "world-model": {"command": "user-custom-cmd"},
            "github": {"type": "http", "url": "https://x.example"},
        }
    }
    (vscode / "mcp.json").write_text(json.dumps(existing, indent=2))
    _run(tmp_path, force=True)
    data = json.loads((vscode / "mcp.json").read_text())
    # world-model is now the bundled entry
    bundled = json.loads(BUNDLED.read_text())
    assert data["servers"]["world-model"] == bundled["servers"]["world-model"]
    # github is preserved
    assert data["servers"]["github"]["url"] == "https://x.example"


def test_i4_file_without_servers_key_gets_servers_added(tmp_path):
    """A user could have `.vscode/mcp.json` with `inputs` but no `servers`.
    We add the `servers` key without disturbing anything else."""
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    existing = {"inputs": [{"type": "promptString", "id": "x"}]}
    (vscode / "mcp.json").write_text(json.dumps(existing, indent=2))
    _run(tmp_path)
    data = json.loads((vscode / "mcp.json").read_text())
    assert "servers" in data
    assert "world-model" in data["servers"]
    assert data["inputs"] == existing["inputs"]


# ---------------------------------------------------------------------------
# E: error paths (refuse rather than corrupt)
# ---------------------------------------------------------------------------


def test_e1_malformed_json_refused(tmp_path):
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    (vscode / "mcp.json").write_text("{not-json}")
    with pytest.raises(SystemExit):
        _run(tmp_path)
    # File left untouched
    assert (vscode / "mcp.json").read_text() == "{not-json}"


def test_e2_top_level_array_refused(tmp_path):
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    (vscode / "mcp.json").write_text("[]")
    with pytest.raises(SystemExit):
        _run(tmp_path)
    assert (vscode / "mcp.json").read_text() == "[]"


def test_e3_servers_not_object_refused(tmp_path):
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    (vscode / "mcp.json").write_text('{"servers": ["not-an-object"]}')
    with pytest.raises(SystemExit):
        _run(tmp_path)


# ---------------------------------------------------------------------------
# D: --dry-run
# ---------------------------------------------------------------------------


def test_d1_dry_run_does_not_create_file(tmp_path):
    _run(tmp_path, dry_run=True)
    assert not (tmp_path / ".vscode" / "mcp.json").exists()


def test_d1_dry_run_does_not_modify_existing_file(tmp_path):
    vscode = tmp_path / ".vscode"
    vscode.mkdir()
    original = json.dumps({"servers": {"github": {"type": "http", "url": "https://x"}}}, indent=2)
    (vscode / "mcp.json").write_text(original)
    _run(tmp_path, dry_run=True)
    assert (vscode / "mcp.json").read_text() == original


# ---------------------------------------------------------------------------
# C: CLI wiring
# ---------------------------------------------------------------------------


def test_c1_subcommand_registered_in_cli_help():
    """world-model install-copilot must appear in the top-level help."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "install-copilot" in result.stdout


def test_c1_subcommand_dry_run_via_cli(tmp_path):
    result = subprocess.run(
        [
            sys.executable, "-m", "world_model_server.cli",
            "install-copilot",
            "--project-dir", str(tmp_path),
            "--dry-run",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    # Should print the merged JSON — check for a signature substring
    assert "world-model" in result.stdout
    assert "servers" in result.stdout
    assert not (tmp_path / ".vscode" / "mcp.json").exists()
