"""
v0.15.0 pin_annotation (ADR-0001 §5) — integration tests.

Exercises pin_annotation through the MCP tool boundary
(`WorldModelTools.pin_annotation`) with the audit chain enabled,
end-to-end into the tamper-evident log and back out through the
verifier:

  - Round trip: MCP call → annotations.db row + tamper log entry →
    prove_annotation_inclusion after epoch close returns a valid
    bundle with matching row_hash and consistent span verdict.
  - Multiple annotations pinned to the same epoch: all five land in
    the same closed epoch when it seals.
  - Epoch close semantics: pin (epoch_size − 1) events + 1
    annotation, epoch closes exactly at threshold, both events and
    the annotation are in the signed root, prove_annotation_inclusion
    returns a valid Merkle proof.
  - Chain continuity across epochs: pin annotation, close epoch, pin
    a second annotation in the next epoch. The second epoch's
    prev_epoch_root chains to the first epoch's merkle_root — no
    fork, no rewrite.

Matches the WorldModelTools-boundary convention established by
tests/test_v0130_proof_mcp_tools.py. A true stdio-subprocess
JSON-RPC harness for pin_annotation lives with the MCPJam
conformance suite and stays there.
"""

from __future__ import annotations

import json
import tempfile
from typing import Any
from unittest import mock

import aiosqlite
import pytest

from world_model_server import audit_keys, tamper_evident
from world_model_server.config import Config
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event
from world_model_server.tools import WorldModelTools

pytestmark = pytest.mark.asyncio


async def _build(tmp_path: str) -> tuple[KnowledgeGraph, WorldModelTools]:
    kg = KnowledgeGraph(tmp_path)
    await kg.initialize()
    tools = WorldModelTools(kg, Config(db_path=tmp_path))
    return kg, tools


async def _pin(tools: WorldModelTools, **overrides: Any) -> dict:
    """Call tools.pin_annotation as the MCP JSON-RPC dispatcher does,
    parse the JSON payload, return it as a dict."""
    args = {
        "session_id": "sess-1",
        "event_range_start": "evt-1",
        "event_range_end": "evt-2",
        "author": "alice",
        "rationale": "reviewed and approved",
        "annotation_type": "human_note",
    }
    args.update(overrides)
    raw = await tools.pin_annotation(**args)
    return json.loads(raw)


async def _close_epoch(kg: KnowledgeGraph) -> None:
    signer = audit_keys.load_or_create_signer(kg.db_path)
    async with aiosqlite.connect(kg.audit_db) as db:
        await tamper_evident.close_epoch(db, signer)
        await db.commit()


async def _log_event(kg: KnowledgeGraph, event_id: str) -> None:
    event = Event(
        id=event_id,
        session_id="sess-1",
        event_type="tool_call",
        tool_name="run_tests",
        success=True,
    )
    await kg.create_event(event)


class TestEndToEndViaMCP:
    """MCP call → tool → annotations.db + tamper log → prove
    round-trips cleanly."""

    async def test_pin_then_prove_after_epoch_close(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"WORLD_MODEL_AUDIT_LOG": "on", "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "100"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build(tmp)
                await _log_event(kg, "evt-1")
                await _log_event(kg, "evt-2")

                result = await _pin(
                    tools,
                    rationale="round-trip proof rationale",
                )
                annotation_id = result["annotation_id"]

                await _close_epoch(kg)

                bundle = await kg.prove_annotation_inclusion(annotation_id)
                assert bundle["entry_kind"] == "annotation_create"
                assert bundle["row_id"] == annotation_id
                assert bundle["span_consistency"]["verdict"] == "consistent"
                # Bundle row_hash matches a reconstruction of the payload
                # rebuilt purely from the annotations.db row.
                recomputed = tamper_evident.row_hash(
                    bundle["reconstructed_payload"]
                )
                assert recomputed == bundle["row_hash"]


class TestMultipleAnnotationsSameEpoch:
    """Five annotations pinned before an epoch close land in the same
    closed epoch. ADR-0001 §5 integration test 2."""

    async def test_five_annotations_share_one_epoch(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"WORLD_MODEL_AUDIT_LOG": "on", "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "100"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build(tmp)
                # Log the two range endpoints once; every annotation
                # references them so span_consistency stays "consistent"
                # (event.seq < annotation.seq for all five).
                await _log_event(kg, "evt-1")
                await _log_event(kg, "evt-2")

                ids = []
                for i in range(5):
                    result = await _pin(
                        tools,
                        session_id=f"sess-{i}",
                        rationale=f"annotation number {i}",
                    )
                    ids.append(result["annotation_id"])

                await _close_epoch(kg)

                epochs_seen = set()
                for aid in ids:
                    bundle = await kg.prove_annotation_inclusion(aid)
                    epochs_seen.add(bundle["epoch"]["seq"])
                    assert bundle["span_consistency"]["verdict"] == "consistent"

                assert len(epochs_seen) == 1, (
                    f"expected all five annotations in one epoch, "
                    f"got epochs {sorted(epochs_seen)}"
                )


class TestEpochCloseSemantics:
    """With epoch_size=5, pin 4 events + 1 annotation. Epoch closes
    exactly at threshold. Both events and the annotation prove out
    against the same signed Merkle root. ADR-0001 §5 integration test 3
    scaled down (1024 → 5) so it runs in a reasonable time."""

    async def test_epoch_closes_at_threshold_with_events_and_annotation(
        self,
    ) -> None:
        with mock.patch.dict(
            "os.environ",
            {"WORLD_MODEL_AUDIT_LOG": "on", "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "5"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build(tmp)
                # 4 events + 1 annotation = 5 entries → auto-close at
                # the annotation write via _maybe_audit_write's threshold
                # check. No manual _close_epoch() call needed.
                for i in range(4):
                    await _log_event(kg, f"evt-{i}")

                result = await _pin(
                    tools,
                    event_range_start="evt-0",
                    event_range_end="evt-3",
                    rationale="close-boundary annotation",
                )
                annotation_id = result["annotation_id"]

                # Auto-close should have fired. Prove annotation
                # inclusion — should succeed immediately without a
                # manual epoch close.
                bundle = await kg.prove_annotation_inclusion(annotation_id)
                assert bundle["entry_kind"] == "annotation_create"
                assert bundle["epoch"]["entry_count"] == 5

                # Every event that was in that epoch also has a Merkle
                # inclusion proof against the same root.
                async with aiosqlite.connect(kg.audit_db) as db:
                    for i in range(4):
                        event_bundle = await tamper_evident.get_inclusion_proof(
                            db, f"evt-{i}"
                        )
                        assert event_bundle["epoch"]["merkle_root"] == (
                            bundle["epoch"]["merkle_root"]
                        ), (
                            f"evt-{i} not in the same Merkle root as "
                            "the annotation"
                        )


class TestChainContinuityAcrossEpochs:
    """Pin annotation in epoch N, close, pin another in epoch N+1.
    Epoch N+1's prev_epoch_root == epoch N's merkle_root — the
    signed epoch chain is unbroken. ADR-0001 §5 integration test 4."""

    async def test_second_epoch_chains_to_first(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"WORLD_MODEL_AUDIT_LOG": "on", "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "100"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build(tmp)
                await _log_event(kg, "evt-1")
                await _log_event(kg, "evt-2")

                first = await _pin(tools, rationale="epoch-1 annotation")
                await _close_epoch(kg)

                # Second annotation lands in a fresh unclosed epoch
                # after the first closed. Closing again seals epoch 2.
                second = await _pin(tools, rationale="epoch-2 annotation")
                await _close_epoch(kg)

                bundle_1 = await kg.prove_annotation_inclusion(
                    first["annotation_id"]
                )
                bundle_2 = await kg.prove_annotation_inclusion(
                    second["annotation_id"]
                )

                assert bundle_1["epoch"]["seq"] < bundle_2["epoch"]["seq"], (
                    "second annotation should be in a strictly later epoch"
                )
                assert bundle_2["epoch"]["prev_epoch_root"] == (
                    bundle_1["epoch"]["merkle_root"]
                ), (
                    "epoch-2 prev_epoch_root must match epoch-1 merkle_root — "
                    "chain broken across annotation-carrying epochs"
                )
