"""
v0.15.0 pin_annotation (ADR-0001) — Day 4: offline verifier tests.

Exercises the reference verifier surface added for pinned annotations:

  - `tamper_evident.reconstruct_annotation_payload` is a pure function
    of the annotations.db row content and matches what the writer
    locked into the log byte-for-byte
  - `KnowledgeGraph.prove_annotation_inclusion` returns a bundle with
    Merkle inclusion proof + reconstructed payload + span_consistency
    verdict once the annotation's epoch closes
  - kind assertion: passing a row_id that lives in the log under a
    non-annotation kind (e.g. event_create) is rejected clearly
  - tamper detection via row_hash reconstruction: any post-hoc
    mutation of the annotations.db row (rationale, author, range,
    annotation_type) causes prove_annotation_inclusion to raise
  - span consistency verdicts cover the five documented cases:
      * both endpoints resolve and precede/equal the annotation
        → "consistent"
      * start endpoint absent from the tamper log
        → "event_range_start_not_in_log"
      * end endpoint absent from the tamper log
        → "event_range_end_not_in_log"
      * start endpoint's seq > annotation seq
        → "event_range_start_after_annotation"
      * end endpoint's seq > annotation seq
        → "event_range_end_after_annotation"
  - audit-chain-disabled path raises with a clear message rather than
    silently returning an empty bundle

E2E-through-CLI coverage lives with the etch-verify integration
suite and is not in scope for Day 4 unit tests.
"""

from __future__ import annotations

import hashlib
from datetime import UTC
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from world_model_server import tamper_evident
from world_model_server.knowledge_graph import KnowledgeGraph


@pytest_asyncio.fixture
async def kg_with_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeGraph:
    monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "1")
    g = KnowledgeGraph(str(tmp_path))
    await g.initialize()
    assert g.tamper_evident_enabled
    return g


@pytest_asyncio.fixture
async def kg_no_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeGraph:
    monkeypatch.delenv("WORLD_MODEL_AUDIT_LOG", raising=False)
    g = KnowledgeGraph(str(tmp_path))
    await g.initialize()
    assert not g.tamper_evident_enabled
    return g


async def _force_epoch_close(kg: KnowledgeGraph) -> None:
    """Force any unclosed entries into a signed epoch.

    Default threshold is 1024 entries; tests don't want to write that
    many. We call close_epoch directly with the signer the writer path
    would use.
    """
    from world_model_server import audit_keys

    signer = audit_keys.load_or_create_signer(kg.db_path)
    async with aiosqlite.connect(kg.audit_db) as db:
        await tamper_evident.close_epoch(db, signer)
        await db.commit()


async def _insert_annotation(
    kg: KnowledgeGraph,
    *,
    session_id: str = "sess-1",
    event_range_start: str = "evt-1",
    event_range_end: str = "evt-2",
    author: str = "alice",
    rationale: str = "reviewed",
    annotation_type: str = "human_note",
) -> str:
    return await kg.insert_annotation(
        session_id=session_id,
        event_range_start=event_range_start,
        event_range_end=event_range_end,
        author=author,
        rationale=rationale,
        annotation_type=annotation_type,
    )


class TestReconstructPayloadIsPure:
    """The payload reconstruction function is a pure function of
    the annotations.db row content, so an offline dump-verifier
    (etch-verify) never touches the writer's state to prove the row
    matches what was signed."""

    def test_matches_writer_payload_shape(self) -> None:
        row = {
            "id": "anno-1",
            "session_id": "sess-1",
            "event_range_start": "evt-1",
            "event_range_end": "evt-2",
            "author": "alice",
            "rationale": "manual override for safety",
            "annotation_type": "override_justification",
        }
        payload = tamper_evident.reconstruct_annotation_payload(row)
        expected_hash = (
            "sha256:"
            + hashlib.sha256(b"manual override for safety").hexdigest()
        )
        assert payload == {
            "domain": tamper_evident.DOMAIN_ANNOTATION,
            "id": "anno-1",
            "session_id": "sess-1",
            "event_range_start": "evt-1",
            "event_range_end": "evt-2",
            "author": "alice",
            "annotation_type": "override_justification",
            "rationale_hash": expected_hash,
        }

    def test_is_deterministic_across_calls(self) -> None:
        row = {
            "id": "anno-1",
            "session_id": "sess-1",
            "event_range_start": "evt-1",
            "event_range_end": "evt-2",
            "author": "alice",
            "rationale": "note",
            "annotation_type": "human_note",
        }
        assert tamper_evident.reconstruct_annotation_payload(
            row
        ) == tamper_evident.reconstruct_annotation_payload(row)


class TestProveInclusionHappyPath:
    """After an epoch closes, prove_annotation_inclusion returns a
    Merkle inclusion proof + reconstructed payload + span verdict."""

    async def test_bundle_shape(self, kg_with_audit: KnowledgeGraph) -> None:
        annotation_id = await _insert_annotation(kg_with_audit)
        await _force_epoch_close(kg_with_audit)

        bundle = await kg_with_audit.prove_annotation_inclusion(annotation_id)

        assert bundle["row_id"] == annotation_id
        assert bundle["entry_kind"] == "annotation_create"
        assert "reconstructed_payload" in bundle
        assert "span_consistency" in bundle
        assert bundle["reconstructed_payload"]["domain"] == (
            tamper_evident.DOMAIN_ANNOTATION
        )
        assert bundle["epoch"]["merkle_root"].startswith("sha256:")
        assert bundle["inclusion"]["tree_size"] >= 1
        assert bundle["inclusion"]["leaf_index"] >= 0

    async def test_reconstructed_row_hash_matches_logged_hash(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        annotation_id = await _insert_annotation(
            kg_with_audit, rationale="unique rationale for this test",
        )
        await _force_epoch_close(kg_with_audit)

        bundle = await kg_with_audit.prove_annotation_inclusion(annotation_id)
        recomputed = tamper_evident.row_hash(bundle["reconstructed_payload"])
        assert recomputed == bundle["row_hash"]


class TestKindAssertion:
    """Passing a row_id that lives in the tamper log under a non-
    annotation kind must be rejected. Otherwise an event or fact
    could be smuggled through the annotation-verifier path."""

    async def test_rejects_event_row_id(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        from datetime import datetime

        from world_model_server.models import Event

        event = Event(
            id="evt-real",
            session_id="sess-1",
            event_type="tool_call",
            timestamp=datetime.now(UTC),
            tool_name="run_tests",
            success=True,
        )
        await kg_with_audit.create_event(event)
        await _force_epoch_close(kg_with_audit)

        # Passing an event's id through the annotation-inclusion path
        # must fail: the row is in annotations.db as absent, so the
        # KnowledgeGraph-level wrapper rejects with "not in
        # annotations.db" before even reaching the kind check.
        with pytest.raises(ValueError, match="annotation not found"):
            await kg_with_audit.prove_annotation_inclusion("evt-real")

    async def test_kind_assertion_fires_when_forged_row_supplied(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        # Simulate the case a tampered dump-verifier might construct:
        # the row_id exists in the tamper log under kind=event_create,
        # and the caller supplies a fabricated annotation_row that
        # matches (so the annotations.db lookup would return a row).
        # We drive tamper_evident.prove_annotation_inclusion directly
        # to exercise the kind check without the KnowledgeGraph
        # annotations.db pre-check.
        from datetime import datetime

        from world_model_server.models import Event

        event = Event(
            id="evt-forged",
            session_id="sess-1",
            event_type="tool_call",
            timestamp=datetime.now(UTC),
            tool_name="run_tests",
            success=True,
        )
        await kg_with_audit.create_event(event)
        await _force_epoch_close(kg_with_audit)

        forged_row = {
            "id": "evt-forged",
            "session_id": "sess-1",
            "event_range_start": "evt-1",
            "event_range_end": "evt-2",
            "author": "alice",
            "rationale": "not a real annotation",
            "annotation_type": "human_note",
        }
        async with aiosqlite.connect(kg_with_audit.audit_db) as db:
            with pytest.raises(ValueError, match="'annotation_create'"):
                await tamper_evident.prove_annotation_inclusion(
                    db, "evt-forged", forged_row,
                )


class TestTamperDetection:
    """A post-hoc mutation of the annotations.db row breaks
    prove_annotation_inclusion. This is the compliance-facing
    guarantee: the row you see now is byte-for-byte what was signed."""

    @pytest.mark.parametrize(
        "field,new_value",
        [
            ("rationale", "tampered rationale not signed"),
            ("author", "eve@example.com"),
            ("event_range_start", "evt-forged-start"),
            ("event_range_end", "evt-forged-end"),
            ("annotation_type", "override_justification"),
        ],
    )
    async def test_mutated_field_breaks_inclusion(
        self,
        kg_with_audit: KnowledgeGraph,
        field: str,
        new_value: str,
    ) -> None:
        annotation_id = await _insert_annotation(kg_with_audit)
        await _force_epoch_close(kg_with_audit)

        async with aiosqlite.connect(kg_with_audit.annotations_db) as db:
            await db.execute(
                f"UPDATE annotations SET {field} = ? WHERE id = ?",
                (new_value, annotation_id),
            )
            await db.commit()

        with pytest.raises(ValueError, match="does not match"):
            await kg_with_audit.prove_annotation_inclusion(annotation_id)


class TestSpanConsistency:
    """Given real events in the tamper log, span_consistency verdicts
    reflect chronological ordering by seq (equivalent to epoch order
    since epochs assign seqs monotonically)."""

    async def _log_event(self, kg: KnowledgeGraph, event_id: str) -> None:
        from datetime import datetime

        from world_model_server.models import Event

        event = Event(
            id=event_id,
            session_id="sess-1",
            event_type="tool_call",
            timestamp=datetime.now(UTC),
            tool_name="run_tests",
            success=True,
        )
        await kg.create_event(event)

    async def test_consistent_when_both_events_precede_annotation(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        await self._log_event(kg_with_audit, "evt-a")
        await self._log_event(kg_with_audit, "evt-b")
        annotation_id = await _insert_annotation(
            kg_with_audit,
            event_range_start="evt-a",
            event_range_end="evt-b",
        )
        await _force_epoch_close(kg_with_audit)

        bundle = await kg_with_audit.prove_annotation_inclusion(annotation_id)
        assert bundle["span_consistency"]["verdict"] == "consistent"
        assert bundle["span_consistency"]["start_verified"] is True
        assert bundle["span_consistency"]["end_verified"] is True

    async def test_start_not_in_log(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        await self._log_event(kg_with_audit, "evt-b")
        annotation_id = await _insert_annotation(
            kg_with_audit,
            event_range_start="evt-missing-start",
            event_range_end="evt-b",
        )
        await _force_epoch_close(kg_with_audit)

        bundle = await kg_with_audit.prove_annotation_inclusion(annotation_id)
        assert bundle["span_consistency"]["verdict"] == (
            "event_range_start_not_in_log"
        )

    async def test_end_not_in_log(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        await self._log_event(kg_with_audit, "evt-a")
        annotation_id = await _insert_annotation(
            kg_with_audit,
            event_range_start="evt-a",
            event_range_end="evt-missing-end",
        )
        await _force_epoch_close(kg_with_audit)

        bundle = await kg_with_audit.prove_annotation_inclusion(annotation_id)
        assert bundle["span_consistency"]["verdict"] == (
            "event_range_end_not_in_log"
        )

    async def test_start_after_annotation(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        # Log an event, then pin an annotation whose start range
        # points at a future event we log AFTER the annotation writes.
        # The annotation's seq predates the future event, so start_seq
        # > annotation_seq — chronologically inconsistent.
        annotation_id = await _insert_annotation(
            kg_with_audit,
            event_range_start="evt-future-start",
            event_range_end="evt-future-end",
        )
        await self._log_event(kg_with_audit, "evt-future-start")
        await self._log_event(kg_with_audit, "evt-future-end")
        await _force_epoch_close(kg_with_audit)

        bundle = await kg_with_audit.prove_annotation_inclusion(annotation_id)
        assert bundle["span_consistency"]["verdict"] == (
            "event_range_start_after_annotation"
        )

    async def test_end_after_annotation(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        await self._log_event(kg_with_audit, "evt-past")
        annotation_id = await _insert_annotation(
            kg_with_audit,
            event_range_start="evt-past",
            event_range_end="evt-future-end",
        )
        await self._log_event(kg_with_audit, "evt-future-end")
        await _force_epoch_close(kg_with_audit)

        bundle = await kg_with_audit.prove_annotation_inclusion(annotation_id)
        assert bundle["span_consistency"]["verdict"] == (
            "event_range_end_after_annotation"
        )


class TestErrorPaths:
    """Error branches surface actionable messages."""

    async def test_disabled_chain_raises(
        self, kg_no_audit: KnowledgeGraph,
    ) -> None:
        # An annotation still writes when audit is off; verifier
        # cannot produce a proof.
        annotation_id = await _insert_annotation(kg_no_audit)
        with pytest.raises(ValueError, match="audit chain is disabled"):
            await kg_no_audit.prove_annotation_inclusion(annotation_id)

    async def test_unknown_id_raises(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        with pytest.raises(ValueError, match="annotation not found"):
            await kg_with_audit.prove_annotation_inclusion(
                "does-not-exist"
            )

    async def test_before_epoch_close_raises_retry_signal(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        annotation_id = await _insert_annotation(kg_with_audit)
        # No epoch close. The underlying get_inclusion_proof raises
        # ValueError with a retry hint, and prove_annotation_inclusion
        # passes it through untouched.
        with pytest.raises(ValueError, match="not yet sealed"):
            await kg_with_audit.prove_annotation_inclusion(annotation_id)
