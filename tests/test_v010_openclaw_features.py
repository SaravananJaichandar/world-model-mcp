"""
v0.10 OpenClaw adapter tests.

F1: OpenClaw adapter bundled files (openclaw.json snippet)
F2: install-openclaw CLI (dry-run, writes, idempotent, JSON merge preservation,
    absolute-path enforcement, --python override, --db-path override)

The install command merges into a JSON config at ~/.openclaw/openclaw.json
rather than appending to TOML like install-codex does. That difference is
what these tests exercise: the merge must preserve all other keys in the
config file, must default the interpreter path to an absolute value
(sys.executable), and must refuse to write a relative --python override.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


# ============================================================================
# F1: Bundled adapter files
# ============================================================================

def test_f1_openclaw_adapter_dir_bundled():
    """OpenClaw adapter files must be inside the package so installs from PyPI
    ship them."""
    bundle = REPO_ROOT / "world_model_server" / "adapters" / "openclaw"
    assert bundle.exists()
    assert (bundle / "openclaw.json").exists()


def test_f1_openclaw_bundled_json_is_valid():
    """The bundled openclaw.json snippet must be parseable JSON with
    mcp.servers.world-model present."""
    bundle = REPO_ROOT / "world_model_server" / "adapters" / "openclaw" / "openclaw.json"
    data = json.loads(bundle.read_text())
    assert "mcp" in data
    assert "servers" in data["mcp"]
    assert "world-model" in data["mcp"]["servers"]
    entry = data["mcp"]["servers"]["world-model"]
    assert entry["args"] == ["-m", "world_model_server.server"]
    assert "WORLD_MODEL_DB_PATH" in entry["env"]


def test_f1_openclaw_top_level_readme_exists():
    """The top-level adapters/openclaw/README.md is what users browsing the
    repo see first — it must exist."""
    readme = REPO_ROOT / "adapters" / "openclaw" / "README.md"
    assert readme.exists()
    text = readme.read_text()
    assert "OpenClaw" in text
    assert "openclaw mcp add" in text


# ============================================================================
# F2: install-openclaw CLI
# ============================================================================

def test_f2_install_openclaw_dry_run(tmp_path):
    """--dry-run prints proposed entry without writing."""
    cfg = tmp_path / "openclaw.json"
    original = {"messages": {"ackReactionScope": "group-mentions"}}
    cfg.write_text(json.dumps(original))
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg), "--dry-run"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "Would write" in result.stdout
    # Config unchanged
    assert json.loads(cfg.read_text()) == original


def test_f2_install_openclaw_writes_and_defaults_absolute_python(tmp_path):
    """First install merges the world-model entry with an absolute-path
    default for --python (sys.executable)."""
    cfg = tmp_path / "openclaw.json"
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr

    data = json.loads(cfg.read_text())
    assert data["mcp"]["servers"]["world-model"]["args"] == ["-m", "world_model_server.server"]
    # Default command MUST be an absolute path (sys.executable). OpenClaw
    # spawn does not inherit shell PATH; a relative binary name breaks probe.
    assert Path(data["mcp"]["servers"]["world-model"]["command"]).is_absolute()
    assert data["mcp"]["servers"]["world-model"]["env"]["WORLD_MODEL_DB_PATH"] == ".claude/world-model"


def test_f2_install_openclaw_preserves_other_config_keys(tmp_path):
    """Merge must preserve every unrelated key in the existing openclaw.json."""
    cfg = tmp_path / "openclaw.json"
    original = {
        "commands": {"native": "auto", "restart": True},
        "messages": {"ackReactionScope": "group-mentions"},
        "mcp": {"servers": {"pre-existing-server": {"command": "/bin/echo", "args": []}}},
        "meta": {"lastTouchedVersion": "2026.6.11"},
    }
    cfg.write_text(json.dumps(original))

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr

    data = json.loads(cfg.read_text())
    # Original top-level keys preserved
    assert data["commands"] == original["commands"]
    assert data["messages"] == original["messages"]
    assert data["meta"] == original["meta"]
    # Pre-existing MCP server preserved
    assert "pre-existing-server" in data["mcp"]["servers"]
    assert data["mcp"]["servers"]["pre-existing-server"] == original["mcp"]["servers"]["pre-existing-server"]
    # New world-model entry added
    assert "world-model" in data["mcp"]["servers"]


def test_f2_install_openclaw_idempotent(tmp_path):
    """Second install without --force refuses to overwrite."""
    cfg = tmp_path / "openclaw.json"

    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    first_text = cfg.read_text()

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "already present" in result.stdout.lower()
    # File unchanged
    assert cfg.read_text() == first_text


def test_f2_install_openclaw_force_overwrites(tmp_path):
    """--force replaces the existing world-model entry."""
    cfg = tmp_path / "openclaw.json"

    # First install with default db-path
    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    first = json.loads(cfg.read_text())
    assert first["mcp"]["servers"]["world-model"]["env"]["WORLD_MODEL_DB_PATH"] == ".claude/world-model"

    # Force overwrite with a different db-path
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg), "--force", "--db-path", "/custom/db/path"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr

    second = json.loads(cfg.read_text())
    assert second["mcp"]["servers"]["world-model"]["env"]["WORLD_MODEL_DB_PATH"] == "/custom/db/path"


def test_f2_install_openclaw_rejects_relative_python(tmp_path):
    """--python MUST be an absolute path. A relative value is a hard error
    because OpenClaw's process spawn does not inherit shell PATH."""
    cfg = tmp_path / "openclaw.json"
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg), "--python", "python3"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    assert "absolute path" in (result.stdout + result.stderr).lower()
    # No config written
    assert not cfg.exists()


def test_f2_install_openclaw_creates_parent_dir(tmp_path):
    """If ~/.openclaw/ doesn't exist, install creates it."""
    cfg = tmp_path / "subdir" / "openclaw.json"
    assert not cfg.parent.exists()

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert cfg.exists()


def test_f2_install_openclaw_rejects_non_object_json(tmp_path):
    """If the config file exists but is not a JSON object, refuse to write."""
    cfg = tmp_path / "openclaw.json"
    cfg.write_text('["a", "list", "not", "an", "object"]')
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    # Config unchanged
    assert cfg.read_text() == '["a", "list", "not", "an", "object"]'


def test_f2_install_openclaw_rejects_malformed_json(tmp_path):
    """Malformed JSON in the config file is a hard error, not a silent
    overwrite."""
    cfg = tmp_path / "openclaw.json"
    cfg.write_text('{"mcp": {broken}')
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-openclaw",
         "--config-path", str(cfg)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    # Config unchanged
    assert cfg.read_text() == '{"mcp": {broken}'


# ============================================================================
# F3: CLI subcommand registration regression
# ============================================================================

def test_f3_install_openclaw_registered_in_cli_help():
    """`install-openclaw` must appear in the top-level --help output."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    assert "install-openclaw" in result.stdout


def test_f3_all_install_subcommands_still_present():
    """All prior install-* subcommands must still be registered — no regression
    from adding install-openclaw."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    for cmd in ("install-cursor", "install-pi", "install-codex", "install-openclaw"):
        assert cmd in result.stdout, f"Missing CLI subcommand: {cmd}"
