"""
v0.10 Continue adapter tests.

F1: Continue adapter bundled files (world-model.yaml snippet)
F2: install-continue CLI (dry-run, writes, idempotent, absolute-path
    enforcement, --python override, --db-path override, --force overwrite,
    YAML validity of the written file)

Continue's MCP model is one standalone YAML file per server at
<project>/.continue/mcpServers/<name>.yaml. That means install-continue
does NOT merge into an existing config file; it owns the world-model.yaml
file end-to-end. No ruamel.yaml dependency is needed for this reason —
comment preservation is only a concern when merging into a user-authored
config.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# Continue's YAML files are simple and self-authored — plain pyyaml is enough
# for the tests to parse the output. If pyyaml is missing (it's a transitive
# dep of some things but not a hard dep of world-model-mcp), skip.
yaml = pytest.importorskip("yaml")


# ============================================================================
# F1: Bundled adapter files
# ============================================================================

def test_f1_continue_adapter_dir_bundled():
    """Continue adapter files must be inside the package so installs from PyPI
    ship them. The Python package uses `continue_` because `continue` is a
    Python keyword."""
    bundle = REPO_ROOT / "world_model_server" / "adapters" / "continue_"
    assert bundle.exists()
    assert (bundle / "world-model.yaml").exists()


def test_f1_continue_top_level_readme_exists():
    """The top-level adapters/continue/README.md is what users browsing the
    repo see first."""
    readme = REPO_ROOT / "adapters" / "continue" / "README.md"
    assert readme.exists()
    text = readme.read_text()
    assert "Continue" in text
    assert "install-continue" in text
    assert ".continue/mcpServers/" in text


def test_f1_continue_bundled_yaml_is_valid():
    """The bundled world-model.yaml must parse as valid YAML with the
    required metadata (name, version, schema) and an mcpServers list."""
    bundle = REPO_ROOT / "world_model_server" / "adapters" / "continue_" / "world-model.yaml"
    data = yaml.safe_load(bundle.read_text())
    assert isinstance(data, dict)
    assert data["name"] == "world-model-mcp"
    assert data["schema"] == "v1"
    assert isinstance(data["version"], str)
    servers = data["mcpServers"]
    assert isinstance(servers, list)
    assert len(servers) == 1
    entry = servers[0]
    assert entry["name"] == "world-model"
    assert entry["type"] == "stdio"
    assert entry["args"] == ["-m", "world_model_server.server"]
    assert "WORLD_MODEL_DB_PATH" in entry["env"]


# ============================================================================
# F2: install-continue CLI
# ============================================================================

def test_f2_install_continue_dry_run(tmp_path):
    """--dry-run prints the proposed YAML without writing."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-continue",
         "--project-dir", str(tmp_path), "--dry-run"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "Would write" in result.stdout
    # No files written
    assert not (tmp_path / ".continue").exists()


def test_f2_install_continue_writes_and_defaults_absolute_python(tmp_path):
    """First install writes .continue/mcpServers/world-model.yaml with an
    absolute-path default for --python (sys.executable)."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-continue",
         "--project-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr

    target = tmp_path / ".continue" / "mcpServers" / "world-model.yaml"
    assert target.exists()

    data = yaml.safe_load(target.read_text())
    assert data["name"] == "world-model-mcp"
    assert data["schema"] == "v1"
    entry = data["mcpServers"][0]
    assert entry["type"] == "stdio"
    assert entry["args"] == ["-m", "world_model_server.server"]
    assert Path(entry["command"]).is_absolute()
    assert entry["env"]["WORLD_MODEL_DB_PATH"] == ".claude/world-model"


def test_f2_install_continue_idempotent(tmp_path):
    """Second install without --force refuses to overwrite the existing
    world-model.yaml file."""
    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-continue",
         "--project-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    target = tmp_path / ".continue" / "mcpServers" / "world-model.yaml"
    first_text = target.read_text()

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-continue",
         "--project-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "already present" in result.stdout.lower()
    # File unchanged
    assert target.read_text() == first_text


def test_f2_install_continue_force_overwrites(tmp_path):
    """--force replaces the existing world-model.yaml."""
    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-continue",
         "--project-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    target = tmp_path / ".continue" / "mcpServers" / "world-model.yaml"
    first = yaml.safe_load(target.read_text())
    assert first["mcpServers"][0]["env"]["WORLD_MODEL_DB_PATH"] == ".claude/world-model"

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-continue",
         "--project-dir", str(tmp_path),
         "--force", "--db-path", "/custom/db/path"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr

    second = yaml.safe_load(target.read_text())
    assert second["mcpServers"][0]["env"]["WORLD_MODEL_DB_PATH"] == "/custom/db/path"


def test_f2_install_continue_rejects_relative_python(tmp_path):
    """--python MUST be an absolute path. A relative value is a hard error."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-continue",
         "--project-dir", str(tmp_path), "--python", "python3"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    assert "absolute path" in (result.stdout + result.stderr).lower()
    # No file written
    assert not (tmp_path / ".continue" / "mcpServers" / "world-model.yaml").exists()


def test_f2_install_continue_creates_parent_dirs(tmp_path):
    """If .continue/mcpServers/ doesn't exist, install creates it."""
    project = tmp_path / "some" / "nested" / "project"
    project.mkdir(parents=True)

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-continue",
         "--project-dir", str(project)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert (project / ".continue" / "mcpServers" / "world-model.yaml").exists()


def test_f2_install_continue_yaml_output_parses_cleanly(tmp_path):
    """The exact YAML text written by install-continue must round-trip through
    yaml.safe_load without warnings or errors. Regression guard for the
    hand-formatted YAML in install_continue_command."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-continue",
         "--project-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr

    target = tmp_path / ".continue" / "mcpServers" / "world-model.yaml"
    text = target.read_text()

    # Must parse without exception
    data = yaml.safe_load(text)

    # Continue's required metadata block
    assert data["name"] == "world-model-mcp"
    assert data["schema"] == "v1"
    assert "version" in data

    # Exactly one MCP server, correctly shaped
    assert len(data["mcpServers"]) == 1
    entry = data["mcpServers"][0]
    for required_key in ("name", "type", "command", "args", "env"):
        assert required_key in entry, f"Missing required key: {required_key}"


# ============================================================================
# F3: CLI subcommand registration regression
# ============================================================================

def test_f3_install_continue_registered_in_cli_help():
    """`install-continue` must appear in the top-level --help output."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    assert "install-continue" in result.stdout


def test_f3_all_install_subcommands_still_present():
    """All prior install-* subcommands must still be registered."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    for cmd in ("install-cursor", "install-pi", "install-codex", "install-continue"):
        assert cmd in result.stdout, f"Missing CLI subcommand: {cmd}"
