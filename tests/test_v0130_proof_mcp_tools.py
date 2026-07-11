"""
v0.13 — MCP tool exposure for the tamper-evident audit log proof APIs.

Covers:
- WorldModelTools.prove_entry_inclusion returns bundle JSON when opt-in
  is on
- WorldModelTools.prove_entry_inclusion returns not_enabled error JSON
  when opt-in is off (does not raise, does not create audit tables)
- WorldModelTools.prove_entry_inclusion returns not_found error JSON
  for a row_id that was never audited
- WorldModelTools.prove_entry_inclusion returns unclosed error JSON
  when the entry has not yet been sealed in an epoch
- WorldModelTools.get_audit_log_head returns the current head state
- WorldModelTools.get_audit_log_head returns not_enabled error JSON
  when opt-in is off
- Both tools' JSON return values round-trip through json.loads without
  raising (well-formed output)
- Bundle produced by MCP path verifies through the reference verifier
  (integration: MCP → bundle → verify_inclusion_bundle → True)
"""

import json
import os
import tempfile
from unittest import mock

import pytest

from world_model_server import audit_keys, tamper_evident
from world_model_server.config import Config
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event
from world_model_server.tools import WorldModelTools


def _make_event(i: int) -> Event:
    return Event(
        session_id="mcp-proof-test",
        event_type="file_edit",
        tool_name="Edit",
        entity_id=f"file-{i}",
        success=True,
    )


async def _build_tools(tmp_path: str) -> tuple[KnowledgeGraph, WorldModelTools]:
    kg = KnowledgeGraph(tmp_path)
    await kg.initialize()
    config = Config(db_path=tmp_path)
    tools = WorldModelTools(kg, config)
    return kg, tools


@pytest.mark.asyncio
class TestProveEntryInclusionMCP:
    async def test_opt_in_off_returns_not_enabled_json(self):
        env = {k: v for k, v in os.environ.items() if k != "WORLD_MODEL_AUDIT_LOG"}
        with mock.patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmp:
                _, tools = await _build_tools(tmp)
                out = await tools.prove_entry_inclusion(row_id="anything")
                parsed = json.loads(out)
                assert parsed.get("kind") == "not_enabled"
                assert "opted in" in parsed.get("error", "").lower()

    async def test_row_id_not_found_returns_not_found_json(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                _, tools = await _build_tools(tmp)
                out = await tools.prove_entry_inclusion(row_id="does-not-exist")
                parsed = json.loads(out)
                assert parsed.get("kind") == "not_found"

    async def test_unclosed_entry_returns_unclosed_json(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "10",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build_tools(tmp)
                events = [_make_event(i) for i in range(3)]
                for e in events:
                    await kg.create_event(e)
                out = await tools.prove_entry_inclusion(row_id=events[0].id)
                parsed = json.loads(out)
                assert parsed.get("kind") == "unclosed"
                assert "retry" in parsed.get("error", "").lower()

    async def test_valid_bundle_returned_and_verifies_end_to_end(self):
        """
        End-to-end: call the MCP-facing method, parse the returned JSON,
        run it through the reference verifier with the operator's public
        keys. This is the exact path a real compliance auditor would run.
        """
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build_tools(tmp)
                events = [_make_event(i) for i in range(6)]
                for e in events:
                    await kg.create_event(e)

                out = await tools.prove_entry_inclusion(row_id=events[2].id)
                bundle = json.loads(out)
                # Must be a real bundle, not an error object.
                assert "error" not in bundle
                assert bundle["row_id"] == events[2].id
                assert bundle["epoch"]["seq"] == 1

                # Load operator public keys and verify end-to-end.
                pk = audit_keys.read_public_keys(kg.db_path)
                ed_pub = bytes.fromhex(pk["ed25519"]["public_key_hex"])
                slh_pub = bytes.fromhex(pk["slh_dsa"]["public_key_hex"])
                ok, reason = tamper_evident.verify_inclusion_bundle(
                    bundle, ed_pub, slh_pub
                )
                assert ok, reason


@pytest.mark.asyncio
class TestGetAuditLogHeadMCP:
    async def test_opt_in_off_returns_not_enabled(self):
        env = {k: v for k, v in os.environ.items() if k != "WORLD_MODEL_AUDIT_LOG"}
        with mock.patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmp:
                _, tools = await _build_tools(tmp)
                out = await tools.get_audit_log_head()
                parsed = json.loads(out)
                assert parsed.get("kind") == "not_enabled"

    async def test_head_reflects_state_after_writes(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build_tools(tmp)
                for i in range(7):
                    await kg.create_event(_make_event(i))
                out = await tools.get_audit_log_head()
                parsed = json.loads(out)
                assert parsed["head_entry_seq"] == 7
                assert parsed["head_epoch_seq"] == 2
                assert parsed["unclosed_entry_count"] == 1
                assert len(parsed["epoch_chain"]) == 2
                # Genesis constants surfaced for auditor reference.
                assert parsed["genesis_entry_hash"] == tamper_evident.GENESIS_HASH
                assert parsed["genesis_epoch_root"] == tamper_evident.EPOCH_GENESIS_ROOT


@pytest.mark.asyncio
class TestToolListDiscovery:
    """
    Confirm the two new tools are declared in the MCP server's list_tools
    response. Compliance auditors discover which tools are available via
    this exact path; if the tools are not in the list, they cannot be
    called.
    """

    async def test_new_tools_appear_in_list(self):
        # The server's list_tools handler is closure-scoped inside main().
        # Reach the declaration by importing the tool list constant is not
        # possible (there is no such constant). Instead, we check that the
        # WorldModelTools class exposes the two methods the dispatch
        # depends on. This is the failure the MCP client would hit at
        # tool-call time.
        assert hasattr(WorldModelTools, "prove_entry_inclusion")
        assert hasattr(WorldModelTools, "get_audit_log_head")

        # Also confirm the tool names are declared in server.py by
        # searching the source; this catches "you added the method but
        # forgot to register the Tool()".
        import inspect
        from world_model_server import server as server_module
        src = inspect.getsource(server_module)
        assert 'name="prove_entry_inclusion"' in src
        assert 'name="get_audit_log_head"' in src
        assert 'elif name == "prove_entry_inclusion":' in src
        assert 'elif name == "get_audit_log_head":' in src
