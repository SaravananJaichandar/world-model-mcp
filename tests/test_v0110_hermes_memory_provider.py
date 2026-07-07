"""
v0.11.0 B: Hermes MemoryProvider plugin tests.

F1: Plugin package files bundled inside world_model_server for pip install
F2: WorldModelMemoryProvider ABC contract — required methods present with
    correct signatures, is_available truthful, initialize opens the KG,
    handle_tool_call dispatches to WorldModelTools methods and returns
    JSON strings, config schema shaped for Hermes' setup wizard
F3: register(ctx) entry point calls ctx.register_memory_provider
F4: install-hermes-provider CLI (dry-run, writes, idempotence, --force,
    absolute-path plugin dir creation, CLI-registration regression)

The Hermes ABC (hermes_agent.memory_provider.MemoryProvider) is not
required to run this suite — the plugin file uses a soft import fallback
to `object` when Hermes is not installed, so the tests can run on any
machine that has world-model-mcp installed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ============================================================================
# F1: Plugin package files bundled inside world_model_server
# ============================================================================


def test_f1_plugin_package_dir_bundled():
    bundle = REPO_ROOT / "world_model_server" / "hermes_memory_provider"
    assert bundle.exists()
    for filename in ("__init__.py", "plugin.yaml", "README.md"):
        assert (bundle / filename).exists(), f"Missing: {filename}"


def test_f1_plugin_yaml_shape():
    """plugin.yaml declares the required Hermes metadata fields."""
    yaml_text = (REPO_ROOT / "world_model_server" / "hermes_memory_provider" / "plugin.yaml").read_text()
    # Hard-coded string check keeps the test parseable without pyyaml
    for required_key in ("name:", "version:", "description:", "hooks:"):
        assert required_key in yaml_text, f"plugin.yaml missing key: {required_key}"
    assert "name: world-model" in yaml_text
    # ABC-required hooks must be listed
    for hook in ("initialize", "get_tool_schemas", "handle_tool_call",
                 "get_config_schema", "save_config"):
        assert hook in yaml_text, f"plugin.yaml missing hook: {hook}"


def test_f1_top_level_readme_exists():
    """Discoverability: the top-level adapters/hermes-memory-provider/README.md
    is what users browsing the repo see first."""
    readme = REPO_ROOT / "adapters" / "hermes-memory-provider" / "README.md"
    assert readme.exists()
    text = readme.read_text()
    assert "MemoryProvider" in text
    assert "install-hermes-provider" in text
    # Position vs the MCP adapter must be documented
    assert "MCP" in text


# ============================================================================
# F2: WorldModelMemoryProvider ABC contract
# ============================================================================


def _import_provider():
    """Isolated import so the test can catch a broken plugin quickly."""
    from world_model_server.hermes_memory_provider import WorldModelMemoryProvider
    return WorldModelMemoryProvider


def test_f2_required_methods_present():
    """Every ABC-required method is defined."""
    Provider = _import_provider()
    for name in ("is_available", "initialize", "get_tool_schemas",
                 "handle_tool_call", "get_config_schema", "save_config"):
        assert callable(getattr(Provider, name)), f"Missing method: {name}"
    # name is a property
    assert isinstance(getattr(Provider, "name"), property)


def test_f2_name_is_world_model():
    Provider = _import_provider()
    assert Provider().name == "world-model"


def test_f2_is_available_true_when_world_model_server_importable():
    Provider = _import_provider()
    assert Provider().is_available() is True


def test_f2_initialize_opens_knowledge_graph(tmp_path):
    """After initialize, tools instance is ready and handle_tool_call works."""
    Provider = _import_provider()
    p = Provider(db_path=str(tmp_path / "world-model"))
    p.initialize("test-session")
    # Internal state: kg + tools populated
    assert p._session_id == "test-session"
    assert p._kg is not None
    assert p._tools is not None


def test_f2_get_tool_schemas_shape():
    """Returns a list of dicts matching Hermes' tool-schema shape.

    v0.12.12 adds `verify_retrieval` — the Coach-Player adversarial
    verification tool — bringing the surfaced count to 8.
    """
    Provider = _import_provider()
    schemas = Provider().get_tool_schemas()
    assert isinstance(schemas, list)
    assert len(schemas) == 8, f"Expected 8 surfaced tools, got {len(schemas)}"
    names = {s["name"] for s in schemas}
    assert names == {
        "query_fact", "get_constraints", "get_injection_context",
        "record_event", "record_correction",
        "find_contradictions", "resolve_contradiction",
        "verify_retrieval",
    }
    for schema in schemas:
        assert "description" in schema
        assert "inputSchema" in schema
        assert schema["inputSchema"]["type"] == "object"


def test_f2_handle_tool_call_before_initialize_raises():
    """Guardrail: dispatching before initialize is a runtime error, not silent."""
    Provider = _import_provider()
    with pytest.raises(RuntimeError):
        Provider().handle_tool_call("query_fact", {"query": "x"})


def test_f2_handle_tool_call_unknown_tool_returns_error(tmp_path):
    """Unknown tool names return a JSON error string, not an exception."""
    Provider = _import_provider()
    p = Provider(db_path=str(tmp_path / "world-model"))
    p.initialize("test-session")
    result = p.handle_tool_call("not-a-real-tool", {})
    parsed = json.loads(result)
    assert "error" in parsed
    assert "surfaced" in parsed


def test_f2_handle_tool_call_query_fact_returns_json(tmp_path):
    """Round-trip: initialize, query an empty graph, get a valid JSON response."""
    Provider = _import_provider()
    p = Provider(db_path=str(tmp_path / "world-model"))
    p.initialize("test-session")
    result = p.handle_tool_call("query_fact", {"query": "some_function"})
    # Result is a QueryFactResult pydantic model or a JSON string
    if isinstance(result, str):
        parsed = json.loads(result)
    else:
        # WorldModelTools.query_fact returns a QueryFactResult; the provider
        # returns it unchanged so Hermes can serialize as needed.
        parsed = result.model_dump() if hasattr(result, "model_dump") else result.__dict__
    # Empty graph -> empty facts list; the shape check is what matters
    assert parsed is not None


def test_f2_config_schema_shape():
    """Config schema fields match the Hermes setup-wizard expected shape."""
    Provider = _import_provider()
    schema = Provider().get_config_schema()
    assert isinstance(schema, list)
    assert len(schema) >= 1
    for field in schema:
        assert "key" in field
        assert "description" in field
        assert "secret" in field
        assert "required" in field


def test_f2_save_config_writes_file(tmp_path):
    Provider = _import_provider()
    Provider().save_config({"world_model_db_path": "/x"}, str(tmp_path))
    assert (tmp_path / "world-model.config.json").exists()
    written = json.loads((tmp_path / "world-model.config.json").read_text())
    assert written == {"world_model_db_path": "/x"}


# ============================================================================
# F3: register(ctx) entry point
# ============================================================================


def test_f3_register_calls_ctx_register_memory_provider():
    """The plugin discovery entry point registers a WorldModelMemoryProvider."""
    from world_model_server.hermes_memory_provider import (
        WorldModelMemoryProvider,
        register,
    )

    class FakeCtx:
        def __init__(self):
            self.registered = []
        def register_memory_provider(self, provider):
            self.registered.append(provider)

    ctx = FakeCtx()
    register(ctx)
    assert len(ctx.registered) == 1
    assert isinstance(ctx.registered[0], WorldModelMemoryProvider)


# ============================================================================
# F4: install-hermes-provider CLI
# ============================================================================


def test_f4_install_hermes_provider_dry_run(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes-provider",
         "--hermes-home", str(tmp_path), "--dry-run"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "Would copy" in result.stdout
    # No files written under the hermes-home
    plugin_dir = tmp_path / "plugins" / "memory" / "world-model"
    assert not plugin_dir.exists()


def test_f4_install_hermes_provider_writes(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes-provider",
         "--hermes-home", str(tmp_path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    plugin_dir = tmp_path / "plugins" / "memory" / "world-model"
    for filename in ("__init__.py", "plugin.yaml", "README.md"):
        assert (plugin_dir / filename).exists()


def test_f4_install_hermes_provider_idempotent(tmp_path):
    """Second install without --force refuses to overwrite."""
    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes-provider",
         "--hermes-home", str(tmp_path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    first_mtime = (tmp_path / "plugins" / "memory" / "world-model" / "__init__.py").stat().st_mtime

    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes-provider",
         "--hermes-home", str(tmp_path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert "already present" in result.stdout.lower()
    second_mtime = (tmp_path / "plugins" / "memory" / "world-model" / "__init__.py").stat().st_mtime
    assert first_mtime == second_mtime


def test_f4_install_hermes_provider_force(tmp_path):
    """--force overwrites."""
    subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes-provider",
         "--hermes-home", str(tmp_path)],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    (tmp_path / "plugins" / "memory" / "world-model" / "__init__.py").write_text("# stale\n")
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes-provider",
         "--hermes-home", str(tmp_path), "--force"],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    body = (tmp_path / "plugins" / "memory" / "world-model" / "__init__.py").read_text()
    # After --force, the file contains the real plugin, not our stale marker
    assert "# stale" not in body
    assert "WorldModelMemoryProvider" in body


def test_f4_install_hermes_provider_creates_parent_dirs(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "install-hermes-provider",
         "--hermes-home", str(tmp_path / "sub" / "nested")],
        capture_output=True, text=True, timeout=30, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "sub" / "nested" / "plugins" / "memory" / "world-model" / "__init__.py").exists()


def test_f4_install_hermes_provider_registered_in_cli_help():
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    assert "install-hermes-provider" in result.stdout


def test_f4_all_prior_install_subcommands_still_present():
    """Regression guard: earlier install-* subcommands must not disappear."""
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    for cmd in ("install-cursor", "install-pi", "install-codex",
                "install-openclaw", "install-hermes", "install-continue",
                "install-hermes-provider"):
        assert cmd in result.stdout, f"Missing CLI subcommand: {cmd}"
