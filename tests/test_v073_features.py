"""
v0.7.3 feature tests.

F1: Opt-in telemetry (off by default, prompted once, inspectable, fail-open)
F2: world-model demo CLI command (guided tour)
F3: Pi adapter (TypeScript extension package shipped + bundled)
Backward-compat regression: v0.7.0 .. v0.7.2 surface unchanged

Conventions follow v0.4 .. v0.7.2 suites.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ============================================================================
# F1: Telemetry
# ============================================================================

def test_f1_telemetry_off_by_default(tmp_path, monkeypatch):
    """A fresh install has consent_status == 'unset' and is_enabled is False."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Reload module so its constants pick up the patched HOME
    import importlib
    from world_model_server import telemetry as t
    importlib.reload(t)
    assert t.consent_status() == "unset"
    assert t.is_enabled() is False


def test_f1_telemetry_kill_switch_overrides_consent(tmp_path, monkeypatch):
    """WORLD_MODEL_TELEMETRY_DISABLE=1 disables even when user opted in."""
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    from world_model_server import telemetry as t
    importlib.reload(t)
    t.set_consent(True)
    assert t.is_enabled() is True
    monkeypatch.setenv("WORLD_MODEL_TELEMETRY_DISABLE", "1")
    assert t.is_enabled() is False


def test_f1_telemetry_install_id_is_stable_across_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    from world_model_server import telemetry as t
    importlib.reload(t)
    id1 = t.get_install_id()
    id2 = t.get_install_id()
    assert id1 == id2
    # Looks like a UUID
    assert len(id1) == 36 and id1.count("-") == 4


def test_f1_telemetry_record_is_fail_open_on_network_error(tmp_path, monkeypatch):
    """record() must never raise even when the ingest endpoint fails.
    Post-v0.14: no PAT/token gate — sink is an unauthenticated URL that
    rate-limits by install_id server-side. This test pins the fail-open
    contract by pointing at a non-routable endpoint."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("WORLD_MODEL_TELEMETRY_ENDPOINT",
                       "http://127.0.0.1:1/api/telemetry/ingest")
    import importlib
    from world_model_server import telemetry as t
    importlib.reload(t)
    t.set_consent(True)
    # Should not raise; daemon thread does the send and eats any error.
    t.record("setup_completed", {"version_at_setup": True})


def test_f1_telemetry_record_sync_returns_false_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    from world_model_server import telemetry as t
    importlib.reload(t)
    # Default state is unset == disabled
    assert t.record_sync("test_event") is False


def test_f1_telemetry_preview_payload_omits_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    from world_model_server import telemetry as t
    importlib.reload(t)
    payload = t.preview_payload("setup_completed", {"version_at_setup": True})
    # Must contain only safe fields
    assert "event" in payload
    assert "version" in payload
    assert "install_id" in payload
    # Make sure we did not accidentally include any path or content key
    for k in payload:
        assert k.lower() not in ("path", "file", "content", "hostname", "user", "ip")


def test_f1_telemetry_cli_status_subcommand(tmp_path):
    """`world-model telemetry --status` runs without error and prints status."""
    env = {**os.environ, "HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "telemetry", "--status"],
        capture_output=True, text=True, timeout=30, env=env, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "Telemetry status" in result.stdout
    assert "Install ID" in result.stdout


def test_f1_telemetry_cli_enable_then_disable(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    r1 = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "telemetry", "--enable"],
        capture_output=True, text=True, timeout=30, env=env, cwd=str(REPO_ROOT),
    )
    assert r1.returncode == 0
    assert "enabled" in r1.stdout.lower()

    r2 = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "telemetry", "--status"],
        capture_output=True, text=True, timeout=30, env=env, cwd=str(REPO_ROOT),
    )
    assert "enabled" in r2.stdout

    r3 = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "telemetry", "--disable"],
        capture_output=True, text=True, timeout=30, env=env, cwd=str(REPO_ROOT),
    )
    assert r3.returncode == 0
    assert "disabled" in r3.stdout.lower()


def test_f1_setup_no_prompt_skips_telemetry_question(tmp_path):
    """`world-model setup --no-prompt` must not block waiting for input."""
    project = tmp_path / "proj"
    project.mkdir()
    env = {**os.environ, "HOME": str(tmp_path), "WORLD_MODEL_NO_PROMPT": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "setup",
         "--project-dir", str(project), "--no-prompt"],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "Setup complete" in result.stdout


# ============================================================================
# F2: world-model demo CLI
# ============================================================================

def test_f2_demo_runs_on_fresh_project(tmp_path):
    """`world-model demo` on a fresh project initializes the KG and exits 0."""
    project = tmp_path / "fresh"
    project.mkdir()
    env = {**os.environ, "HOME": str(tmp_path), "WORLD_MODEL_NO_PROMPT": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "demo",
         "--project-dir", str(project)],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    # Must exercise each named primitive in the output
    out = result.stdout
    assert "PreToolUse" in out
    assert "Contradiction detection" in out
    assert "PostCompact injection" in out
    assert "Compaction audit log" in out


def test_f2_demo_creates_world_model_dir(tmp_path):
    project = tmp_path / "proj2"
    project.mkdir()
    env = {**os.environ, "HOME": str(tmp_path), "WORLD_MODEL_NO_PROMPT": "1"}
    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "demo",
         "--project-dir", str(project)],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(REPO_ROOT),
    )
    assert (project / ".claude" / "world-model").exists()


# ============================================================================
# F3: Pi adapter
# ============================================================================

def test_f3_pi_adapter_files_exist():
    adapter_dir = REPO_ROOT / "adapters" / "pi"
    assert adapter_dir.exists()
    assert (adapter_dir / "index.ts").exists()
    assert (adapter_dir / "package.json").exists()
    assert (adapter_dir / "README.md").exists()


def test_f3_pi_adapter_package_json_valid():
    pkg = REPO_ROOT / "adapters" / "pi" / "package.json"
    data = json.loads(pkg.read_text())
    assert data["name"] == "world-model-pi"
    # Pi extension manifest must point at index.ts
    assert "pi" in data
    assert "./index.ts" in data["pi"]["extensions"]
    # Peer dep declared so pi's loader can resolve runtime
    assert "@earendil-works/pi-coding-agent" in data.get("peerDependencies", {})


def test_f3_pi_adapter_index_ts_wires_right_events():
    """index.ts must subscribe to tool_call, context, and session_compact."""
    src = (REPO_ROOT / "adapters" / "pi" / "index.ts").read_text()
    assert 'pi.on("tool_call"' in src
    assert 'pi.on("context"' in src
    assert 'pi.on("session_compact"' in src
    # Must invoke the Python helpers, not reimplement them
    assert "world_model_server.hook_helper" in src
    assert "world_model_server.inject_helper" in src


def test_f3_pi_adapter_bundled_in_package():
    """install-pi reads from the bundled copy inside the installed package."""
    pkg_root = REPO_ROOT / "world_model_server" / "adapters" / "pi"
    assert pkg_root.exists()
    assert (pkg_root / "index.ts").exists()
    assert (pkg_root / "package.json").exists()


def test_f3_install_pi_cli_copies_files(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    env = {**os.environ, "HOME": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-pi",
         "--project-dir", str(project)],
        capture_output=True, text=True, timeout=30, env=env, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    target = project / "adapters" / "world-model-pi"
    assert (target / "index.ts").exists()
    assert (target / "package.json").exists()


def test_f3_install_pi_cli_force_flag(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    target = project / "adapters" / "world-model-pi"
    target.mkdir(parents=True)
    (target / "index.ts").write_text("// stub")
    env = {**os.environ, "HOME": str(tmp_path)}
    # Without --force: skip
    r1 = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-pi",
         "--project-dir", str(project)],
        capture_output=True, text=True, timeout=30, env=env, cwd=str(REPO_ROOT),
    )
    assert r1.returncode == 0
    assert "skip" in r1.stdout.lower() or "already exists" in r1.stdout.lower()
    # The pre-existing stub stays
    assert (target / "index.ts").read_text() == "// stub"

    # With --force: overwrite
    r2 = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-pi",
         "--project-dir", str(project), "--force"],
        capture_output=True, text=True, timeout=30, env=env, cwd=str(REPO_ROOT),
    )
    assert r2.returncode == 0
    assert "// stub" not in (target / "index.ts").read_text()


# ============================================================================
# Backward-compat regression
# ============================================================================

def test_bc_existing_cli_subcommands_present():
    """All v0.6/v0.7 subcommands must still be registered."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    out = result.stdout
    for cmd in (
        "setup", "seed", "query", "decisions", "register", "projects",
        "search-global", "health", "decay", "recall", "export-claude-md",
        "migrate", "status", "audit-compactions", "install-cursor",
        # New in v0.7.3:
        "demo", "telemetry", "install-pi",
    ):
        assert cmd in out, f"CLI subcommand missing: {cmd}"


def test_bc_v072_http_transport_still_works(tmp_path):
    """v0.7.2's HTTP transport must still boot after v0.7.3 changes."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    env = {
        **os.environ,
        "WORLD_MODEL_TRANSPORT": "http",
        "WORLD_MODEL_HTTP_HOST": "127.0.0.1",
        "WORLD_MODEL_HTTP_PORT": str(port),
        "WORLD_MODEL_HTTP_PATH": "/mcp",
        "WORLD_MODEL_DB_PATH": str(tmp_path / "wm"),
        "HOME": str(tmp_path),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "world_model_server.server"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
    )
    try:
        import time
        import urllib.request
        ok = False
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as resp:
                    if 200 <= resp.status < 300:
                        ok = True
                        break
            except Exception:
                time.sleep(0.25)
        assert ok, "HTTP server did not respond on /healthz"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# (v0.14: removed six tests for the now-deleted _embedded_token PAT
# machinery. The old GitHub Issues sink is gone; new sink is an
# unauthenticated etch.systems endpoint that rate-limits by install_id
# server-side. See test_telemetry_v014_sink_migration.py for the
# replacement coverage.)


def test_bc_setup_without_no_prompt_in_ci_does_not_hang(tmp_path):
    """In a non-TTY (CI) environment, setup must complete without hanging on input."""
    project = tmp_path / "proj"
    project.mkdir()
    env = {**os.environ, "HOME": str(tmp_path)}
    # stdin is not a TTY in subprocess, so the prompt branch should be skipped.
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "setup",
         "--project-dir", str(project)],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(REPO_ROOT),
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    assert "Setup complete" in result.stdout
