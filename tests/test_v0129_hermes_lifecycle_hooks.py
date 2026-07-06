"""
v0.12.9: Hermes lifecycle hooks on the WorldModelMemoryProvider.

The v0.11.0 provider shipped the five required ABC methods (initialize,
get_tool_schemas, handle_tool_call, get_config_schema, save_config).
v0.12.9 layers the five optional lifecycle hooks on top:

  sync_turn         — called after every completed agent turn
  on_pre_compress   — called just before Hermes compresses context
  prefetch          — speculative pre-fetch based on a topic hint
  on_session_end    — called when a Hermes session ends
  on_memory_write   — called when Hermes writes memory through us

Contract for every hook:
- Best-effort: an exception inside a hook must NEVER propagate to the
  Hermes turn. Each hook catches, logs, and returns a safe default.
- Sync front-door: Hermes' hooks are synchronous. Each hook uses
  _run_async to dispatch to the async WorldModelTools methods, mirroring
  the pattern used by the v0.11.0 handle_tool_call.
- No initialize -> safe no-op. Hooks called before initialize() must
  degrade gracefully (return empty payload / None).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from world_model_server.hermes_memory_provider import WorldModelMemoryProvider


# ---------------------------------------------------------------------------
# Fixture: an initialized provider with a real KG behind it
# ---------------------------------------------------------------------------


@pytest.fixture
def provider(tmp_path):
    p = WorldModelMemoryProvider(db_path=str(tmp_path / "wm"))
    p.initialize(session_id="test-session")
    return p


# ---------------------------------------------------------------------------
# All five hooks exist and are callable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hook_name", [
    "sync_turn",
    "on_pre_compress",
    "prefetch",
    "on_session_end",
    "on_memory_write",
])
def test_hook_exists_on_provider_class(hook_name):
    assert hasattr(WorldModelMemoryProvider, hook_name)
    assert callable(getattr(WorldModelMemoryProvider, hook_name))


# ---------------------------------------------------------------------------
# sync_turn
# ---------------------------------------------------------------------------


def test_sync_turn_records_event(provider):
    """A turn passed to sync_turn shows up as an event in the KG."""
    provider.sync_turn({
        "session_id": "test-session",
        "entities": ["src/api/users.ts"],
        "description": "Edited users.ts to add JWT validation",
        "reasoning": "user asked for auth",
        "input": {"file_path": "src/api/users.ts"},
        "output": {"lines_changed": 12},
        "success": True,
    })
    # Consult the KG directly — the event should be there
    import asyncio, aiosqlite
    async def check():
        async with aiosqlite.connect(provider._kg.events_db) as db:
            cur = await db.execute("SELECT COUNT(*) FROM events")
            return (await cur.fetchone())[0]
    assert asyncio.new_event_loop().run_until_complete(check()) >= 1


def test_sync_turn_swallows_exceptions(provider):
    """A malformed payload must NOT raise — hooks are best-effort."""
    # No session_id, no entities, wrong types — the hook must not crash
    provider.sync_turn(None)  # type: ignore[arg-type]
    provider.sync_turn({"session_id": object()})  # unhashable / wrong type


def test_sync_turn_before_initialize_is_noop(tmp_path):
    p = WorldModelMemoryProvider(db_path=str(tmp_path / "wm"))
    # Deliberately NOT initialized. Must not raise.
    p.sync_turn({"session_id": "s"})


# ---------------------------------------------------------------------------
# on_pre_compress
# ---------------------------------------------------------------------------


def test_on_pre_compress_returns_injection_string(provider):
    result = provider.on_pre_compress({"max_facts": 5, "max_constraints": 5})
    assert isinstance(result, str)


def test_on_pre_compress_bundle_reflects_content_type_routing(provider):
    """Seed a rule + a procedure. The injection must include the rule and
    exclude the procedure — that is the v0.12.3 routing contract, and the
    pre-compress hook must honor it."""
    import asyncio
    from world_model_server.models import Fact

    async def seed():
        await provider._kg.create_fact(Fact(
            id="rule-r1",
            fact_text="Always await async database calls",
            evidence_path="rules/async.md",
            status="canonical",
            content_type="rule",
        ))
        await provider._kg.create_fact(Fact(
            id="proc-p1",
            fact_text="Deploy runbook: bump, tag, push, release",
            evidence_path="docs/deploy.md",
            status="canonical",
            content_type="procedure",
        ))

    asyncio.new_event_loop().run_until_complete(seed())
    injection = provider.on_pre_compress({"max_facts": 5})
    assert "Always await async database calls" in injection
    assert "Deploy runbook" not in injection


def test_on_pre_compress_swallows_exceptions(provider):
    """If the underlying tools call blows up, on_pre_compress must return
    empty string, not propagate."""
    with patch.object(
        provider._tools, "get_injection_context",
        side_effect=RuntimeError("simulated failure"),
    ):
        assert provider.on_pre_compress({}) == ""


def test_on_pre_compress_before_initialize_returns_empty(tmp_path):
    p = WorldModelMemoryProvider(db_path=str(tmp_path / "wm"))
    assert p.on_pre_compress({}) == ""


# ---------------------------------------------------------------------------
# prefetch
# ---------------------------------------------------------------------------


def test_prefetch_no_hint_returns_empty(provider):
    assert provider.prefetch(None) == []
    assert provider.prefetch("") == []


def test_prefetch_with_hint_returns_matching_facts(provider):
    import asyncio
    from world_model_server.models import Fact

    async def seed():
        await provider._kg.create_fact(Fact(
            id="f-jwt",
            fact_text="Endpoint POST /users requires JWT authentication",
            evidence_path="src/api/users.ts:42",
            status="canonical",
        ))
    asyncio.new_event_loop().run_until_complete(seed())

    facts = provider.prefetch("JWT")
    assert isinstance(facts, list)
    assert any("JWT" in (f.get("fact_text") or "") for f in facts)


def test_prefetch_swallows_exceptions(provider):
    with patch.object(
        provider._tools, "query_fact",
        side_effect=RuntimeError("simulated failure"),
    ):
        assert provider.prefetch("anything") == []


def test_prefetch_before_initialize_returns_empty(tmp_path):
    p = WorldModelMemoryProvider(db_path=str(tmp_path / "wm"))
    assert p.prefetch("some hint") == []


# ---------------------------------------------------------------------------
# on_session_end
# ---------------------------------------------------------------------------


def test_on_session_end_records_marker_event(provider):
    import asyncio, aiosqlite

    async def event_count():
        async with aiosqlite.connect(provider._kg.events_db) as db:
            cur = await db.execute("SELECT COUNT(*) FROM events")
            return (await cur.fetchone())[0]

    before = asyncio.new_event_loop().run_until_complete(event_count())
    provider.on_session_end({"session_id": "test-session", "turn_count": 12, "duration_ms": 4567})
    after = asyncio.new_event_loop().run_until_complete(event_count())
    assert after == before + 1


def test_on_session_end_swallows_exceptions(provider):
    provider.on_session_end(None)  # type: ignore[arg-type]


def test_on_session_end_before_initialize_is_noop(tmp_path):
    p = WorldModelMemoryProvider(db_path=str(tmp_path / "wm"))
    p.on_session_end({"session_id": "s"})


# ---------------------------------------------------------------------------
# on_memory_write
# ---------------------------------------------------------------------------


def test_on_memory_write_echoes_entry(provider):
    entry = {"content_type": "rule", "fact_text": "always await async db calls"}
    out = provider.on_memory_write(entry)
    assert out == entry


def test_on_memory_write_non_dict_returns_empty_dict(provider):
    """Contract: return a dict even if the caller passes garbage."""
    assert provider.on_memory_write("not a dict") == {}  # type: ignore[arg-type]
    assert provider.on_memory_write(None) == {}  # type: ignore[arg-type]


def test_on_memory_write_before_initialize_still_works(tmp_path):
    """Logging works without _tools; the hook is pure echo."""
    p = WorldModelMemoryProvider(db_path=str(tmp_path / "wm"))
    entry = {"content_type": "fact"}
    assert p.on_memory_write(entry) == entry
