"""
v0.14.0+: Adapter documentation config validation tests.

Locks in the config claims made in docs/adapters/mesh-llm.md and
docs/adapters/buzz.md so silently renaming env vars, changing MCP entry
points, or updating adapter JSON schemas fails loudly in CI rather than
shipping broken docs to users.

Covers:
  - Both adapter docs exist and are non-empty
  - Every WORLD_MODEL_* env var referenced in either doc is one that
    world_model_server.config.Config actually reads (or an explicitly
    whitelisted env var read elsewhere in the repo)
  - The BUZZ doc's ACP session/new JSON parses cleanly and has the shape
    ACP clients expect
  - The BUZZ doc's world-model MCP server invocation matches the reference
    adapter config shipped in the repo (world_model_server/adapters/cursor/
    mcp.json). If the module path changes, adapter docs must stay in sync.

Why these tests exist:
  Docs that ship stale env var names or broken JSON silently break every
  new user's first setup. That is the exact class of bug we hit during the
  Karthik design-partner integration. These tests fail the build instead.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from world_model_server import config as config_module

DOCS_DIR = Path(__file__).parent.parent / "docs" / "adapters"
MESH_LLM_DOC = DOCS_DIR / "mesh-llm.md"
BUZZ_DOC = DOCS_DIR / "buzz.md"
REPO_ROOT = Path(__file__).parent.parent
REFERENCE_ADAPTER_CONFIG = (
    REPO_ROOT / "world_model_server" / "adapters" / "cursor" / "mcp.json"
)

# Env vars read outside world_model_server.config.Config that adapter docs
# may legitimately reference. Kept small on purpose; expand as new
# non-Config env vars land.
NON_CONFIG_ENV_VARS: set[str] = {
    "WORLD_MODEL_AUDIT_LOG",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_doc(path: Path) -> str:
    assert path.exists(), f"docs file missing: {path}"
    return path.read_text()


def _extract_json_blocks(markdown_text: str) -> list[dict]:
    """Extract fenced JSON code blocks from a markdown document. Any block
    that fails to parse fails the test — we do not ship broken JSON."""
    blocks = re.findall(
        r"```json\s*\n(.*?)```",
        markdown_text,
        flags=re.DOTALL,
    )
    parsed: list[dict] = []
    for i, raw in enumerate(blocks):
        try:
            parsed.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            pytest.fail(
                f"json block {i} in doc failed to parse: {exc}\n---\n{raw}"
            )
    return parsed


def _extract_referenced_env_vars(markdown_text: str) -> set[str]:
    """Every WORLD_MODEL_* identifier referenced in bash/sh code blocks in
    a markdown document. Used to detect stale env var names."""
    bash_blocks = re.findall(
        r"```(?:bash|sh)\s*\n(.*?)```",
        markdown_text,
        flags=re.DOTALL,
    )
    env_vars: set[str] = set()
    for block in bash_blocks:
        env_vars.update(re.findall(r"\bWORLD_MODEL_[A-Z_]+", block))
    return env_vars


def _config_env_var_names() -> set[str]:
    """Every env var actually read by world_model_server.config.Config,
    discovered by inspecting the module source for os.getenv calls. Uses
    source inspection (not runtime introspection) so we catch env vars
    behind default_factory lambdas that only fire when instantiated.

    Handles multi-line os.getenv() calls (name on the following line) via
    `\\s*` between the paren and the opening quote — several config
    fields are formatted that way.
    """
    source = Path(config_module.__file__).read_text()
    return set(re.findall(r'os\.getenv\(\s*"([A-Z_]+)"', source))


# ---------------------------------------------------------------------------
# Docs existence
# ---------------------------------------------------------------------------


class TestDocsExist:
    def test_mesh_llm_doc_exists(self) -> None:
        assert MESH_LLM_DOC.exists()
        assert MESH_LLM_DOC.stat().st_size > 0

    def test_buzz_doc_exists(self) -> None:
        assert BUZZ_DOC.exists()
        assert BUZZ_DOC.stat().st_size > 0


# ---------------------------------------------------------------------------
# Env var integrity per doc
# ---------------------------------------------------------------------------


class TestMeshLLMDocEnvVars:
    def test_all_referenced_env_vars_are_real(self) -> None:
        """Every WORLD_MODEL_* env var referenced in mesh-llm.md must be
        one Config actually reads (or explicitly whitelisted)."""
        referenced = _extract_referenced_env_vars(_read_doc(MESH_LLM_DOC))
        known = _config_env_var_names() | NON_CONFIG_ENV_VARS
        unknown = referenced - known
        assert not unknown, (
            f"mesh-llm.md references env vars not read by Config or "
            f"whitelisted: {sorted(unknown)}. Add to config, whitelist, or "
            f"fix the docs."
        )

    def test_expected_env_vars_present(self) -> None:
        """The Mesh-LLM integration story depends on these three env vars.
        If any are missing from the doc, the setup instructions break."""
        referenced = _extract_referenced_env_vars(_read_doc(MESH_LLM_DOC))
        required = {
            "WORLD_MODEL_VERIFICATION_BACKEND",
            "WORLD_MODEL_VERIFICATION_BASE_URL",
            "WORLD_MODEL_VERIFICATION_MODEL",
        }
        missing = required - referenced
        assert not missing, (
            f"mesh-llm.md is missing critical env vars: {sorted(missing)}"
        )


class TestBUZZDocEnvVars:
    def test_all_referenced_env_vars_are_real(self) -> None:
        referenced = _extract_referenced_env_vars(_read_doc(BUZZ_DOC))
        known = _config_env_var_names() | NON_CONFIG_ENV_VARS
        unknown = referenced - known
        assert not unknown, (
            f"buzz.md references env vars not read by Config or "
            f"whitelisted: {sorted(unknown)}"
        )


# ---------------------------------------------------------------------------
# BUZZ doc: ACP session/new JSON structure
# ---------------------------------------------------------------------------


class TestBUZZDocConfig:
    def test_json_blocks_parse(self) -> None:
        """Every fenced ```json block in buzz.md must be valid JSON."""
        blocks = _extract_json_blocks(_read_doc(BUZZ_DOC))
        assert blocks, "buzz.md should contain at least one json code block"

    def test_session_new_shape(self) -> None:
        """The ACP session/new example must have the shape ACP clients
        expect: jsonrpc, method, params.mcpServers[] with each entry having
        name / command / args."""
        blocks = _extract_json_blocks(_read_doc(BUZZ_DOC))
        session_new = next(
            (b for b in blocks if b.get("method") == "session/new"),
            None,
        )
        assert session_new is not None, (
            "buzz.md must contain a session/new JSON example"
        )
        assert session_new.get("jsonrpc") == "2.0"
        params = session_new.get("params", {})
        servers = params.get("mcpServers")
        assert isinstance(servers, list) and servers, (
            "params.mcpServers must be a non-empty list"
        )
        for i, server in enumerate(servers):
            assert "name" in server, f"server {i} missing name"
            assert "command" in server, f"server {i} missing command"
            assert "args" in server, f"server {i} missing args"
            assert isinstance(server["args"], list), (
                f"server {i} args must be a list"
            )

    def test_world_model_server_command_matches_reference(self) -> None:
        """The world-model MCP server invocation in buzz.md must match the
        one shipped in every other adapter (e.g. Cursor). If the module
        path or command changes, all adapter docs need updating together —
        this test forces that."""
        reference = json.loads(REFERENCE_ADAPTER_CONFIG.read_text())
        ref_world_model = reference["mcpServers"]["world-model"]
        ref_command = ref_world_model["command"]
        ref_args = ref_world_model["args"]

        blocks = _extract_json_blocks(_read_doc(BUZZ_DOC))
        session_new = next(
            b for b in blocks if b.get("method") == "session/new"
        )
        world_model_server = next(
            s for s in session_new["params"]["mcpServers"]
            if s["name"] == "world-model"
        )

        assert world_model_server["command"] == ref_command, (
            f"BUZZ doc world-model command "
            f"'{world_model_server['command']}' does not match reference "
            f"adapter command '{ref_command}'"
        )
        assert world_model_server["args"] == ref_args, (
            f"BUZZ doc world-model args {world_model_server['args']} do "
            f"not match reference adapter args {ref_args}"
        )


# ---------------------------------------------------------------------------
# Cross-doc invariants
# ---------------------------------------------------------------------------


class TestAdapterDocsInvariants:
    def test_both_docs_reference_same_mcp_module_path(self) -> None:
        """Both adapter docs must reference the same MCP server module path.
        If someone renames world_model_server.server, both docs must be
        updated together."""
        mesh = _read_doc(MESH_LLM_DOC)
        buzz = _read_doc(BUZZ_DOC)
        assert "world_model_server.server" in mesh, (
            "mesh-llm.md must reference world_model_server.server as the "
            "MCP server module path"
        )
        assert "world_model_server.server" in buzz, (
            "buzz.md must reference world_model_server.server as the MCP "
            "server module path"
        )
