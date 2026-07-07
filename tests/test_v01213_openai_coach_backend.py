"""
v0.12.13: OpenAI-compatible Coach backend tests.

Covers:
  - Config exposes verification_backend / verification_base_url /
    verification_api_key with the right defaults + env-var wiring
  - verify_answer routes by backend flag to the correct _run_coach_* helper
  - _run_coach_openai_compatible calls chat.completions.create with the
    system prompt in the messages list (OpenAI convention, not Anthropic)
  - Response parsing works against OpenAI-shape .choices[0].message.content
  - _build_openai_compatible_client handles missing base_url, missing
    openai package, and API key priority
  - verify_answer's never-raises contract holds identically for both backends
  - Backward compat: default backend is 'anthropic'; existing calls untouched
"""

from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from world_model_server.config import Config
from world_model_server.models import Fact
from world_model_server.verification import (
    verify_answer,
    _run_coach_anthropic,
    _run_coach_openai_compatible,
    _run_coach,  # backward-compat shim
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_facts() -> list:
    return [
        Fact(
            id="f1",
            fact_text="POST /users requires JWT",
            evidence_path="src/api/users.ts:42",
            valid_at=datetime.now(),
            status="canonical",
        ),
    ]


def _fake_anthropic_client(payload_json: str) -> MagicMock:
    """Client whose .messages.create returns a response with .content[0].text = payload."""
    resp = MagicMock()
    resp.content = [MagicMock(text=payload_json)]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=resp)
    return client


def _fake_openai_client(payload_json: str) -> MagicMock:
    """Client whose .chat.completions.create returns a response with
    .choices[0].message.content = payload."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = MagicMock()
    resp.choices[0].message.content = payload_json
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


HIGH_PAYLOAD = (
    '{"verified_claims":["POST /users requires JWT"],'
    '"unverified_claims":[],'
    '"source_pointers":[{"claim":"POST /users requires JWT","fact_id":"f1"}],'
    '"reasoning":"matches f1"}'
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_default_backend_is_anthropic(monkeypatch):
    """Backward compat: unset env means anthropic."""
    monkeypatch.delenv("WORLD_MODEL_VERIFICATION_BACKEND", raising=False)
    c = Config()
    assert c.verification_backend == "anthropic"


def test_config_backend_can_be_openai_compatible(monkeypatch):
    monkeypatch.setenv("WORLD_MODEL_VERIFICATION_BACKEND", "openai-compatible")
    c = Config()
    assert c.verification_backend == "openai-compatible"


def test_config_verification_base_url_from_env(monkeypatch):
    monkeypatch.setenv("WORLD_MODEL_VERIFICATION_BASE_URL", "https://openrouter.ai/api/v1")
    c = Config()
    assert c.verification_base_url == "https://openrouter.ai/api/v1"


def test_config_verification_api_key_from_env(monkeypatch):
    monkeypatch.setenv("WORLD_MODEL_VERIFICATION_API_KEY", "sk-test-123")
    c = Config()
    assert c.verification_api_key == "sk-test-123"


def test_config_verification_base_url_defaults_none(monkeypatch):
    monkeypatch.delenv("WORLD_MODEL_VERIFICATION_BASE_URL", raising=False)
    c = Config()
    assert c.verification_base_url is None


# ---------------------------------------------------------------------------
# _run_coach_openai_compatible — call shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_coach_uses_chat_completions_create():
    """Regression: OpenAI-compat path must use .chat.completions.create, not .messages.create."""
    client = _fake_openai_client(HIGH_PAYLOAD)
    await _run_coach_openai_compatible(
        client, "some-model", "q", "a", _sample_facts(),
    )
    assert client.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_openai_coach_moves_system_into_messages_list():
    """OpenAI convention: system prompt is a message with role=system, not
    a top-level `system=` argument. Regression: don't pass Anthropic-shape."""
    client = _fake_openai_client(HIGH_PAYLOAD)
    await _run_coach_openai_compatible(
        client, "some-model", "q", "a", _sample_facts(),
    )
    call = client.chat.completions.create.await_args
    # The call must NOT include a `system=` kwarg (that's Anthropic-specific)
    assert "system" not in call.kwargs
    # It MUST include messages with a system role first
    messages = call.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


@pytest.mark.asyncio
async def test_openai_coach_parses_choices_shape():
    """Response text lives at .choices[0].message.content on OpenAI-shape."""
    client = _fake_openai_client(HIGH_PAYLOAD)
    verified, unverified, pointers, reasoning = await _run_coach_openai_compatible(
        client, "m", "q", "a", _sample_facts(),
    )
    assert verified == ["POST /users requires JWT"]
    assert pointers[0]["fact_id"] == "f1"


@pytest.mark.asyncio
async def test_openai_coach_uses_temperature_zero_deterministic():
    client = _fake_openai_client(HIGH_PAYLOAD)
    await _run_coach_openai_compatible(client, "m", "q", "a", _sample_facts())
    call = client.chat.completions.create.await_args
    assert call.kwargs["temperature"] == 0.0


# ---------------------------------------------------------------------------
# verify_answer routes by backend flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_answer_routes_to_anthropic_by_default():
    """Backward compat: no backend arg → Anthropic path."""
    client = _fake_anthropic_client(HIGH_PAYLOAD)
    result = await verify_answer(client, "m", "q", "a", _sample_facts())
    assert result.confidence == "HIGH"
    assert client.messages.create.await_count == 1


@pytest.mark.asyncio
async def test_verify_answer_routes_to_openai_when_backend_set():
    client = _fake_openai_client(HIGH_PAYLOAD)
    result = await verify_answer(
        client, "m", "q", "a", _sample_facts(),
        backend="openai-compatible",
    )
    assert result.confidence == "HIGH"
    assert client.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_verify_answer_openai_backend_swallows_exceptions():
    """OpenAI-shape client raising must return LOW+error, not propagate."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("simulated 500"))
    result = await verify_answer(
        client, "m", "q", "a", _sample_facts(),
        backend="openai-compatible",
    )
    assert result.confidence == "LOW"
    assert "coach_call_failed" in result.error


@pytest.mark.asyncio
async def test_verify_answer_openai_no_client_error_message_differs():
    """When client is None, error message differs by backend so operators
    can tell whether they hit the Anthropic or openai-compat path."""
    r_anth = await verify_answer(None, "m", "q", "a", _sample_facts(), backend="anthropic")
    r_oai = await verify_answer(None, "m", "q", "a", _sample_facts(), backend="openai-compatible")
    assert r_anth.error == "no_anthropic_api_key"
    assert r_oai.error == "no_verification_client"


# ---------------------------------------------------------------------------
# _build_openai_compatible_client
# ---------------------------------------------------------------------------


def test_build_openai_client_returns_none_without_base_url():
    from world_model_server.tools import _build_openai_compatible_client
    config = Config(verification_backend="openai-compatible", verification_base_url=None)
    client = _build_openai_compatible_client(config)
    assert client is None


def test_build_openai_client_returns_none_when_openai_missing():
    """Simulate the openai package not being importable."""
    from world_model_server.tools import _build_openai_compatible_client
    config = Config(
        verification_backend="openai-compatible",
        verification_base_url="https://example.com/v1",
    )
    with patch.dict("sys.modules", {"openai": None}):
        # patch.dict with None triggers ImportError on `from openai import ...`
        client = _build_openai_compatible_client(config)
    assert client is None


def test_build_openai_client_uses_explicit_config_key_first(monkeypatch):
    from world_model_server.tools import _build_openai_compatible_client
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fallback")
    config = Config(
        verification_backend="openai-compatible",
        verification_base_url="https://openrouter.ai/api/v1",
        verification_api_key="sk-explicit-config",
    )
    client = _build_openai_compatible_client(config)
    if client is None:
        pytest.skip("openai package not installed in this test environment")
    assert client.api_key == "sk-explicit-config"


def test_build_openai_client_falls_back_to_openrouter_env(monkeypatch):
    from world_model_server.tools import _build_openai_compatible_client
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    config = Config(
        verification_backend="openai-compatible",
        verification_base_url="https://openrouter.ai/api/v1",
        verification_api_key=None,
    )
    client = _build_openai_compatible_client(config)
    if client is None:
        pytest.skip("openai package not installed in this test environment")
    assert client.api_key == "sk-or-env"


def test_build_openai_client_falls_back_to_placeholder_for_local(monkeypatch):
    """Local Ollama/vLLM endpoints don't require auth; the client still needs
    an api_key argument, so we fall back to a labeled placeholder."""
    from world_model_server.tools import _build_openai_compatible_client
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = Config(
        verification_backend="openai-compatible",
        verification_base_url="http://localhost:11434/v1",
        verification_api_key=None,
    )
    client = _build_openai_compatible_client(config)
    if client is None:
        pytest.skip("openai package not installed in this test environment")
    assert client.api_key == "sk-local-no-auth-needed"


# ---------------------------------------------------------------------------
# Backward compat: _run_coach shim
# ---------------------------------------------------------------------------


def test_run_coach_shim_points_to_anthropic_path():
    """v0.12.12 tests that patched _run_coach directly must still work."""
    assert _run_coach is _run_coach_anthropic


# ---------------------------------------------------------------------------
# WorldModelTools.verify_retrieval respects the backend config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_verify_retrieval_uses_openai_backend_when_configured(tmp_path):
    from world_model_server.knowledge_graph import KnowledgeGraph
    from world_model_server.tools import WorldModelTools

    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()
    await kg.create_fact(Fact(
        id="db-1",
        fact_text="POST /users requires JWT",
        evidence_path="src/api/users.ts:42",
        status="canonical",
    ))

    config = Config(
        db_path=str(tmp_path / "wm"),
        verification_backend="openai-compatible",
        verification_base_url="https://openrouter.ai/api/v1",
        verification_api_key="sk-test",
    )
    tools = WorldModelTools(kg, config)

    # Force _build_openai_compatible_client to return our mock instead of a
    # real AsyncOpenAI client, so the test doesn't depend on `openai` being
    # installed AND doesn't hit the network.
    fake_client = _fake_openai_client(HIGH_PAYLOAD)
    with patch(
        "world_model_server.tools._build_openai_compatible_client",
        return_value=fake_client,
    ):
        result = await tools.verify_retrieval(
            query="Does /users need auth?",
            answer="Yes, POST /users requires JWT.",
            fact_ids=["db-1"],
        )

    assert result.confidence == "HIGH"
    # Load-bearing regression: openai-compat path uses chat.completions.create,
    # NOT messages.create.
    assert fake_client.chat.completions.create.await_count == 1
    assert not fake_client.messages.create.called if hasattr(fake_client, "messages") else True
