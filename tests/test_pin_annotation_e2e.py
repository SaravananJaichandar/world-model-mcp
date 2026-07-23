"""
v0.15.0 pin_annotation (ADR-0001 §5) — end-to-end product-flow test.

Simulates the mid-run intervention workflow ADR-0001 describes:

  1. Start an agent session, log a few tool calls
  2. Human "intervenes" mid-run and pins a `human_intervention`
     annotation to the span [tool_call_2, tool_call_5]
  3. Continue the session with more tool calls
  4. Close the epoch
  5. Run the reference verifier against the annotation
  6. Verify:
     - the annotation is in a signed epoch
     - its span reference resolves to real logged tool_calls
     - the epoch signature verifies under the operator's hybrid keys
     - the annotations.db row content matches what was signed

Steps 5-6 currently run through the in-process reference verifier
(`tamper_evident.verify_inclusion_bundle`) rather than the offline
`etch-verify` CLI, because the CLI does not exist yet. The verifier
is the same code path a future CLI would wrap, so the guarantees
being tested here transfer unchanged when the CLI ships. The
subprocess-driven CLI E2E is tracked as a follow-up.
"""

from __future__ import annotations

import json
import tempfile
from unittest import mock

import aiosqlite
import pytest

from world_model_server import audit_keys, tamper_evident
from world_model_server.config import Config
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event
from world_model_server.tools import WorldModelTools

pytestmark = pytest.mark.asyncio


async def _log_tool_call(kg: KnowledgeGraph, event_id: str, tool_name: str) -> None:
    """Log a single agent tool-call event into events.db + tamper log."""
    event = Event(
        id=event_id,
        session_id="agent-session-1",
        event_type="tool_call",
        tool_name=tool_name,
        success=True,
    )
    await kg.create_event(event)


class TestMidRunInterventionEndToEnd:
    """Full mid-run intervention workflow: agent starts, human pins an
    annotation across a range of tool calls, agent continues, epoch
    closes, offline reference verifier confirms all four properties
    ADR-0001 §5 mandates."""

    async def test_intervention_flow_verifies(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "WORLD_MODEL_AUDIT_LOG": "on",
                "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "100",
            },
        ):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                tools = WorldModelTools(kg, Config(db_path=tmp))

                # (1) Agent runs the first stretch of tool calls.
                for i in range(1, 6):
                    await _log_tool_call(kg, f"tool_call_{i}", f"tool_{i}")

                # (2) Human intervenes mid-run and pins an
                # override_justification annotation on the span
                # [tool_call_2, tool_call_5].
                raw = await tools.pin_annotation(
                    session_id="agent-session-1",
                    event_range_start="tool_call_2",
                    event_range_end="tool_call_5",
                    author="compliance-reviewer@ops",
                    rationale=(
                        "Halting the agent — proposed tool_call_5 would "
                        "leak PII into logs. Manual override applied and "
                        "re-planned with restricted scope. See PIR-2026-07."
                    ),
                    annotation_type="human_intervention",
                )
                annotation_id = json.loads(raw)["annotation_id"]

                # (3) Agent resumes and logs more tool calls after the
                # intervention.
                for i in range(6, 9):
                    await _log_tool_call(kg, f"tool_call_{i}", f"tool_{i}")

                # (4) Close the epoch (in production the 1024-entry
                # threshold triggers automatically; here we force-close
                # for a bounded test).
                signer = audit_keys.load_or_create_signer(kg.db_path)
                async with aiosqlite.connect(kg.audit_db) as db:
                    await tamper_evident.close_epoch(db, signer)
                    await db.commit()

                # (5) Reference verifier produces a full bundle for the
                # annotation.
                bundle = await kg.prove_annotation_inclusion(annotation_id)

                # (6a) Annotation is present in the verified output
                # with the correct kind.
                assert bundle["entry_kind"] == "annotation_create"
                assert bundle["row_id"] == annotation_id

                # (6b) Span reference resolves — both endpoints are real
                # logged tool_calls at seqs preceding the annotation.
                span = bundle["span_consistency"]
                assert span["verdict"] == "consistent"
                assert span["start_verified"] is True
                assert span["end_verified"] is True
                assert span["start_seq"] < span["annotation_seq"]
                assert span["end_seq"] < span["annotation_seq"]

                # (6c) Epoch signature verifies under the operator's
                # hybrid public keys via the same code path a standalone
                # reference verifier (Python or TypeScript) would
                # reproduce.
                ok, reason = tamper_evident.verify_inclusion_bundle(
                    bundle,
                    ed25519_public_key=signer.ed25519_public_key_bytes(),
                    slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
                )
                assert ok, (
                    f"E2E verifier rejected the annotation bundle: {reason}"
                )

                # (6d) Content-lock check: reconstructed payload matches
                # what the chain locked in. Tampering with the row
                # rationale/author/range after this point would fail
                # this recompute (Day 3 + Day 4 tamper suites cover
                # each field explicitly).
                recomputed = tamper_evident.row_hash(
                    bundle["reconstructed_payload"]
                )
                assert recomputed == bundle["row_hash"]

                # Bonus assertion: the annotation reconstructed_payload
                # carries the human-authored rationale hash and the
                # intervention type, so an auditor reading the bundle
                # can see BOTH the "what" (annotation_type) and the
                # "how it was verified" (rationale_hash proves the DB
                # row is byte-for-byte the reviewed text).
                rp = bundle["reconstructed_payload"]
                assert rp["annotation_type"] == "human_intervention"
                assert rp["author"] == "compliance-reviewer@ops"
                assert rp["rationale_hash"].startswith("sha256:")
