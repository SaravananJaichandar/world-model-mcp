"""
v0.10 Hermes Agent adapter tests.

F1: Hermes adapter bundled files (config-snippet.yaml)
F2: install-hermes CLI (dry-run, writes, idempotent, YAML merge preservation,
    absolute-path enforcement, --python override, --db-path override,
    handling of missing pyyaml, malformed YAML, non-mapping mcp_servers)

Same shape as test_v010_openclaw_features.py but the config file is YAML
instead of JSON. The YAML merge must preserve every other key in the file
and must default the interpreter path to an absolute value (sys.executable).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# Skip the whole module if ruamel.yaml is unavailable — install-hermes needs
# it for round-trip comment preservation, and there's no point running merge
# tests without it. The absent-ruamel case is handled by a fail-fast error
# message in the CLI itself.
pytest.importorskip("ruamel.yaml")
from ruamel.yaml import YAML  # noqa: E402


def _yaml_rt():
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _dump(data):
    import io
    buf = io.StringIO()
    _yaml_rt().dump(data, buf)
    return buf.getvalue()


def _load(text):
    return _yaml_rt().load(text)


# ============================================================================
# F1: Bundled adapter files
# ============================================================================

def test_f1_hermes_adapter_dir_bundled():
    """Hermes adapter files must be inside the package so installs from PyPI
    ship them."""
    bundle = REPO_ROOT / "world_model_server" / "adapters" / "hermes"
    assert bundle.exists()
    assert (bundle / "config-snippet.yaml").exists()


def test_f1_hermes_bundled_yaml_is_valid():
    """The bundled config-snippet.yaml must parse to a mapping with
    mcp_servers.world-model present."""
    bundle = REPO_ROOT / "world_model_server" / "adapters" / "hermes" / "config-snippet.yaml"
    data = _load(bundle.read_text())
    assert isinstance(data, dict)
    assert "mcp_servers" in data
    assert "world-model" in data["mcp_servers"]
    entry = data["mcp_servers"]["world-model"]
    assert entry["args"] == ["-m", "world_model_server.server"]
    assert "WORLD_MODEL_DB_PATH" in entry["env"]


def test_f1_hermes_top_level_readme_exists():
    """The top-level adapters/hermes/README.md is what users see first."""
    readme = REPO_ROOT / "adapters" / "hermes" / "README.md"
    assert readme.exists()
    text = readme.read_text()
    assert "Hermes" in text
    assert "install-hermes" in text
    # ClawMem overlap must be documented so users understand the
    # MemoryProvider-slot trade-off before they choose it
    assert "ClawMem" in text or "MemoryProvider" in text


# ============================================================================
# F2: install-hermes CLI
# ============================================================================

def test_f2_install_hermes_dry_run(tmp_path):
    """--dry-run prints proposed entry without writing."""
    cfg = tmp_path / "config.yaml"
    original = {"model": {"provider": "anthropic"}}
    cfg.write_text(_dump(original))
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg), "--dry-run"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "Would write" in result.stdout
    # Config unchanged
    assert _load(cfg.read_text()) == original


def test_f2_install_hermes_writes_and_defaults_absolute_python(tmp_path):
    """First install merges the world-model entry with an absolute-path
    default for --python (sys.executable)."""
    cfg = tmp_path / "config.yaml"
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr

    data = _load(cfg.read_text())
    entry = data["mcp_servers"]["world-model"]
    assert entry["args"] == ["-m", "world_model_server.server"]
    assert Path(entry["command"]).is_absolute()
    assert entry["env"]["WORLD_MODEL_DB_PATH"] == ".claude/world-model"
    assert entry["enabled"] is True
    assert entry["timeout"] == 30


def test_f2_install_hermes_preserves_other_config_keys(tmp_path):
    """Merge must preserve every unrelated key in the existing config.yaml."""
    cfg = tmp_path / "config.yaml"
    original = {
        "model": {"provider": "anthropic", "name": "claude-opus-4-7"},
        "memory": {"user_profile_max_chars": 1375},
        "mcp_servers": {"github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]}},
        "agent": {"max_iterations": 20},
    }
    cfg.write_text(_dump(original))

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr

    data = _load(cfg.read_text())
    # Original top-level keys preserved
    assert data["model"] == original["model"]
    assert data["memory"] == original["memory"]
    assert data["agent"] == original["agent"]
    # Pre-existing MCP server preserved
    assert "github" in data["mcp_servers"]
    assert data["mcp_servers"]["github"] == original["mcp_servers"]["github"]
    # New world-model entry added
    assert "world-model" in data["mcp_servers"]


def test_f2_install_hermes_preserves_comments_and_blank_lines(tmp_path):
    """Round-trip merge MUST preserve comments and blank lines.

    Regression test for a real bug found during E2E verification against
    Hermes v0.17.0: the reference `~/.hermes/config.yaml` is heavily commented
    (1327 lines, ~1000 of which are documentation comments). A naive
    yaml.safe_dump rewrite stripped every comment and reduced the file to
    158 lines, destroying the user's config documentation. install-hermes
    uses ruamel.yaml round-trip mode to prevent this.
    """
    cfg = tmp_path / "config.yaml"
    original_text = (
        "# Top-level config comment - MUST survive install-hermes\n"
        "\n"
        "# Model section header comment\n"
        "model:\n"
        "  # Inline comment on the provider field\n"
        "  provider: anthropic  # trailing comment on provider\n"
        "  name: claude-opus-4-7\n"
        "\n"
        "# Memory section separator comment\n"
        "memory:\n"
        "  user_profile_max_chars: 1375\n"
        "\n"
        "# End-of-file marker comment\n"
    )
    cfg.write_text(original_text)

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr

    after_text = cfg.read_text()
    # Every original comment must still be present
    assert "# Top-level config comment - MUST survive install-hermes" in after_text
    assert "# Model section header comment" in after_text
    assert "# Inline comment on the provider field" in after_text
    assert "# trailing comment on provider" in after_text
    assert "# Memory section separator comment" in after_text
    assert "# End-of-file marker comment" in after_text
    # And the new world-model entry must be present
    assert "world-model" in after_text


def test_f2_install_hermes_idempotent(tmp_path):
    """Second install without --force refuses to overwrite."""
    cfg = tmp_path / "config.yaml"

    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    first_text = cfg.read_text()

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "already present" in result.stdout.lower()
    # File unchanged
    assert cfg.read_text() == first_text


def test_f2_install_hermes_force_overwrites(tmp_path):
    """--force replaces the existing world-model entry."""
    cfg = tmp_path / "config.yaml"

    # First install with default db-path
    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    first = _load(cfg.read_text())
    assert first["mcp_servers"]["world-model"]["env"]["WORLD_MODEL_DB_PATH"] == ".claude/world-model"

    # Force overwrite with a different db-path
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg), "--force", "--db-path", "/custom/db/path"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr

    second = _load(cfg.read_text())
    assert second["mcp_servers"]["world-model"]["env"]["WORLD_MODEL_DB_PATH"] == "/custom/db/path"


def test_f2_install_hermes_rejects_relative_python(tmp_path):
    """--python MUST be an absolute path. A relative value is a hard error."""
    cfg = tmp_path / "config.yaml"
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg), "--python", "python3"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    assert "absolute path" in (result.stdout + result.stderr).lower()
    # No config written
    assert not cfg.exists()


def test_f2_install_hermes_creates_parent_dir(tmp_path):
    """If ~/.hermes/ doesn't exist, install creates it."""
    cfg = tmp_path / "subdir" / "config.yaml"
    assert not cfg.parent.exists()

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert cfg.exists()


def test_f2_install_hermes_rejects_non_mapping_yaml(tmp_path):
    """If the config file exists but is not a YAML mapping, refuse to write."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("- a\n- list\n- not\n- a\n- mapping\n")
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    # Config unchanged
    assert cfg.read_text() == "- a\n- list\n- not\n- a\n- mapping\n"


def test_f2_install_hermes_rejects_malformed_yaml(tmp_path):
    """Malformed YAML in the config file is a hard error, not a silent
    overwrite."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mcp_servers:\n  world-model:\n    command: [unclosed")
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    # Config unchanged
    assert cfg.read_text() == "mcp_servers:\n  world-model:\n    command: [unclosed"


def test_f2_install_hermes_rejects_non_mapping_mcp_servers(tmp_path):
    """If mcp_servers exists but is a list instead of a mapping, refuse."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mcp_servers:\n  - a\n  - list\n")
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    assert "mcp_servers" in (result.stdout + result.stderr).lower()


# ============================================================================
# F3: CLI subcommand registration regression
# ============================================================================

def test_f3_install_hermes_registered_in_cli_help():
    """`install-hermes` must appear in the top-level --help output."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    assert "install-hermes" in result.stdout


def test_f3_all_install_subcommands_still_present():
    """All prior install-* subcommands must still be registered — no regression
    from adding install-hermes."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    for cmd in ("install-cursor", "install-pi", "install-codex", "install-hermes"):
        assert cmd in result.stdout, f"Missing CLI subcommand: {cmd}"
