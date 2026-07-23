"""
v0.15.0 pin_annotation (ADR-0001) — Day 3: chain integration tests.

Exercises the tamper-evident log entries emitted by
KnowledgeGraph.insert_annotation() when the audit chain is enabled:

  - annotation_create is a registered ENTRY_KIND
  - DOMAIN_ANNOTATION is exported and matches the ADR-0001 spec value
  - a pin_annotation append lands exactly one entry with the right
    kind, row_id, and canonical payload shape
  - the payload embeds DOMAIN_ANNOTATION so leaf-hash space is
    disjoint from event/decision/fact leaves
  - the rationale text is NOT stored verbatim in the log payload
    (PII discipline), but a SHA-256 rationale_hash IS
  - modifying the rationale text in annotations.db after write
    changes what a fresh rationale_hash would be — the log's stored
    rationale_hash no longer matches, which is the tamper-detection
    contract
  - when tamper_evident is disabled, no log entry is written and the
    row still lands (the audit chain is opt-in and MUST NOT gate
    core writes)
  - existing entry kinds (fact_create, event_create, decision_create)
    are still registered — regression guard

Verifier-side proofs (prove_annotation_inclusion, span consistency)
land in Day 4+ per ADR-0001 and get their own tests.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import pytest_asyncio

from world_model_server import tamper_evident
from world_model_server.knowledge_graph import KnowledgeGraph

EXPECTED_DOMAIN = "world-model-mcp/transparency-log/annotation/v1"

EXPECTED_PAYLOAD_FIELDS = frozenset(
    {
        "domain",
        "id",
        "session_id",
        "event_range_start",
        "event_range_end",
        "author",
        "annotation_type",
        "rationale_hash",
    }
)


@pytest_asyncio.fixture
async def kg_with_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeGraph:
    """KnowledgeGraph with the tamper-evident audit chain enabled."""
    monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "1")
    g = KnowledgeGraph(str(tmp_path))
    await g.initialize()
    assert g.tamper_evident_enabled, "audit chain fixture must have it enabled"
    return g


@pytest_asyncio.fixture
async def kg_no_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeGraph:
    """KnowledgeGraph with the tamper-evident audit chain OFF (default)."""
    monkeypatch.delenv("WORLD_MODEL_AUDIT_LOG", raising=False)
    g = KnowledgeGraph(str(tmp_path))
    await g.initialize()
    assert not g.tamper_evident_enabled
    return g


async def _fetch_all_log_rows(kg: KnowledgeGraph) -> list[tuple[Any, ...]]:
    async with aiosqlite.connect(kg.audit_db) as db:
        cursor = await db.execute(
            "SELECT seq, kind, row_id, row_hash, prev_hash, entry_hash "
            "FROM tamper_evident_log ORDER BY seq ASC"
        )
        return list(await cursor.fetchall())


class TestEntryKindRegistered:
    """annotation_create is a canonical ENTRY_KIND. Verifiers walking
    the log rely on this set to filter by write path."""

    def test_annotation_create_in_entry_kinds(self) -> None:
        assert "annotation_create" in tamper_evident.ENTRY_KINDS

    @pytest.mark.parametrize(
        "kind",
        [
            "fact_create",
            "fact_update",
            "constraint_create",
            "constraint_update",
            "event_create",
            "decision_create",
            "correction_create",
        ],
    )
    def test_pre_existing_kinds_still_registered(self, kind: str) -> None:
        assert kind in tamper_evident.ENTRY_KINDS


class TestDomainConstant:
    """DOMAIN_ANNOTATION is exported and matches ADR-0001 verbatim.
    Downstream verifiers hard-code this string; changing it is a
    breaking change and must fail this test."""

    def test_domain_constant_exported(self) -> None:
        assert hasattr(tamper_evident, "DOMAIN_ANNOTATION")

    def test_domain_constant_matches_adr_spec(self) -> None:
        assert tamper_evident.DOMAIN_ANNOTATION == EXPECTED_DOMAIN


class TestChainAppendOnAnnotationWrite:
    """A pin_annotation write lands exactly one entry in the log with
    the expected shape."""

    async def test_write_produces_exactly_one_log_entry(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        annotation_id = await kg_with_audit.insert_annotation(
            session_id="sess-1",
            event_range_start="evt-1",
            event_range_end="evt-2",
            author="alice",
            rationale="reviewed and approved",
            annotation_type="human_note",
        )
        rows = await _fetch_all_log_rows(kg_with_audit)
        assert len(rows) == 1
        seq, kind, row_id, _row_hash, _prev, _entry = rows[0]
        assert seq == 1
        assert kind == "annotation_create"
        assert row_id == annotation_id

    async def test_multiple_writes_chain_sequentially(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        ids = []
        for i in range(3):
            aid = await kg_with_audit.insert_annotation(
                session_id=f"sess-{i}",
                event_range_start=f"evt-{i}",
                event_range_end=f"evt-{i}",
                author="alice",
                rationale=f"rationale {i}",
                annotation_type="human_note",
            )
            ids.append(aid)
        rows = await _fetch_all_log_rows(kg_with_audit)
        assert len(rows) == 3
        # chain prev_hash of entry n+1 == entry_hash of entry n
        for i in range(1, 3):
            _, _, _, _, prev_hash_n_plus_1, _ = rows[i]
            _, _, _, _, _, entry_hash_n = rows[i - 1]
            assert prev_hash_n_plus_1 == entry_hash_n, (
                f"chain broken between seq {i} and {i + 1}"
            )
        # row_ids in the log match returned annotation_ids in insertion order
        assert [r[2] for r in rows] == ids


class TestPayloadShape:
    """The canonical payload written to the log for an annotation has
    exactly the fields ADR-0001 mandates — no PII rationale leak, no
    accidental extras, no missing IDs."""

    async def _capture_payload(
        self, kg: KnowledgeGraph, **overrides: str,
    ) -> dict[str, Any]:
        """Insert one annotation and rebuild the canonical payload the
        log entry was hashed against. We recompute row_hash for each
        candidate payload shape and compare to the log's stored
        row_hash — that pins down what the log actually saw."""
        base = {
            "session_id": "sess-X",
            "event_range_start": "evt-A",
            "event_range_end": "evt-B",
            "author": "bob",
            "rationale": "test rationale text",
            "annotation_type": "human_intervention",
        }
        base.update(overrides)
        annotation_id = await kg.insert_annotation(**base)  # type: ignore[arg-type]
        rows = await _fetch_all_log_rows(kg)
        assert len(rows) == 1
        _, _, _, row_hash_from_log, _, _ = rows[0]

        rationale_hash = (
            "sha256:"
            + hashlib.sha256(base["rationale"].encode("utf-8")).hexdigest()
        )
        expected_payload = {
            "domain": EXPECTED_DOMAIN,
            "id": annotation_id,
            "session_id": base["session_id"],
            "event_range_start": base["event_range_start"],
            "event_range_end": base["event_range_end"],
            "author": base["author"],
            "annotation_type": base["annotation_type"],
            "rationale_hash": rationale_hash,
        }
        recomputed = tamper_evident.row_hash(expected_payload)
        assert recomputed == row_hash_from_log, (
            "log's stored row_hash does not match a payload built from "
            "EXPECTED_PAYLOAD_FIELDS — payload shape drifted from ADR-0001"
        )
        return expected_payload

    async def test_payload_has_exactly_expected_fields(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        payload = await self._capture_payload(kg_with_audit)
        assert set(payload.keys()) == EXPECTED_PAYLOAD_FIELDS

    async def test_payload_embeds_domain_annotation(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        payload = await self._capture_payload(kg_with_audit)
        assert payload["domain"] == EXPECTED_DOMAIN

    async def test_payload_carries_annotation_type_verbatim(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        payload = await self._capture_payload(
            kg_with_audit, annotation_type="override_justification",
        )
        assert payload["annotation_type"] == "override_justification"

    async def test_payload_does_not_contain_rationale_text(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        secret_marker = "SECRET-PII-MARKER-8b6f2a"
        await kg_with_audit.insert_annotation(
            session_id="sess-1",
            event_range_start="evt-1",
            event_range_end="evt-2",
            author="alice",
            rationale=f"reviewed {secret_marker} and approved",
            annotation_type="human_note",
        )
        # Read the entire tamper_evident_log.db as raw bytes and confirm
        # the secret marker does not appear anywhere. This is a stronger
        # guarantee than checking the recomputed payload keys — it
        # catches any accidental logging path that might dump rationale
        # into the audit DB.
        with open(kg_with_audit.audit_db, "rb") as f:
            audit_bytes = f.read()
        assert secret_marker.encode("utf-8") not in audit_bytes, (
            "rationale text leaked into audit_db"
        )

    async def test_payload_rationale_hash_is_sha256_of_utf8_bytes(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        rationale = "manual override — dropped the migration for safety"
        payload = await self._capture_payload(
            kg_with_audit, rationale=rationale,
        )
        expected = (
            "sha256:"
            + hashlib.sha256(rationale.encode("utf-8")).hexdigest()
        )
        assert payload["rationale_hash"] == expected


class TestTamperDetection:
    """The chain must detect post-write mutation of the annotations
    row. Day 3's stored artifact is the rationale_hash inside the log
    payload — modifying rationale in annotations.db changes what a
    fresh hash would be, so the log's rationale_hash no longer matches
    the DB's current contents. That is the compliance-facing contract:
    'the row you see now is byte-for-byte what was signed.'
    """

    async def test_modified_rationale_no_longer_matches_logged_hash(
        self, kg_with_audit: KnowledgeGraph,
    ) -> None:
        original = "the original rationale we signed"
        annotation_id = await kg_with_audit.insert_annotation(
            session_id="sess-1",
            event_range_start="evt-1",
            event_range_end="evt-2",
            author="alice",
            rationale=original,
            annotation_type="human_note",
        )
        # Extract the rationale_hash the chain locked in.
        rows = await _fetch_all_log_rows(kg_with_audit)
        _, _, _, row_hash_from_log, _, _ = rows[0]
        expected_payload = {
            "domain": EXPECTED_DOMAIN,
            "id": annotation_id,
            "session_id": "sess-1",
            "event_range_start": "evt-1",
            "event_range_end": "evt-2",
            "author": "alice",
            "annotation_type": "human_note",
            "rationale_hash": (
                "sha256:" + hashlib.sha256(original.encode("utf-8")).hexdigest()
            ),
        }
        assert tamper_evident.row_hash(expected_payload) == row_hash_from_log

        # Now tamper with the rationale in annotations.db out-of-band.
        tampered = "tampered rationale that was NEVER signed"
        async with aiosqlite.connect(kg_with_audit.annotations_db) as db:
            await db.execute(
                "UPDATE annotations SET rationale = ? WHERE id = ?",
                (tampered, annotation_id),
            )
            await db.commit()

        # A verifier building a payload from the current DB row would
        # compute a different rationale_hash, so the recomputed row_hash
        # would no longer match the one the log locked in.
        tampered_payload = dict(expected_payload)
        tampered_payload["rationale_hash"] = (
            "sha256:" + hashlib.sha256(tampered.encode("utf-8")).hexdigest()
        )
        assert (
            tamper_evident.row_hash(tampered_payload) != row_hash_from_log
        ), "tamper detection failed: recomputed hash matches log"


class TestDomainSeparation:
    """A leaf hash computed for an annotation payload must not match a
    leaf hash computed for an event payload with structurally similar
    fields. ADR-0001 §5 test 5 mandates this."""

    def test_annotation_leaf_differs_from_event_leaf_with_same_fields(
        self,
    ) -> None:
        common = {
            "id": "shared-id",
            "session_id": "sess-x",
        }
        annotation_payload = dict(common)
        annotation_payload["domain"] = tamper_evident.DOMAIN_ANNOTATION
        annotation_payload["annotation_type"] = "human_note"

        event_payload = dict(common)
        event_payload["event_type"] = "tool_call"

        assert tamper_evident.row_hash(annotation_payload) != tamper_evident.row_hash(
            event_payload
        ), "domain separation failed: annotation and event leaves collide"

    def test_annotation_payload_without_domain_hashes_differently(
        self,
    ) -> None:
        # An attacker who submits a payload identical to a real
        # annotation but WITHOUT the domain field must not be able to
        # reproduce the same leaf hash the real annotation produced.
        with_domain = {
            "domain": tamper_evident.DOMAIN_ANNOTATION,
            "id": "id-1",
            "session_id": "s",
            "annotation_type": "human_note",
        }
        without_domain = dict(with_domain)
        del without_domain["domain"]
        assert tamper_evident.row_hash(with_domain) != tamper_evident.row_hash(
            without_domain
        )


class TestAuditChainOptIn:
    """The chain is opt-in. When disabled, insert_annotation still
    writes the row (data plane MUST NOT gate on audit config) and
    NO tamper_evident_log entry appears.
    """

    async def test_row_written_when_audit_disabled(
        self, kg_no_audit: KnowledgeGraph,
    ) -> None:
        annotation_id = await kg_no_audit.insert_annotation(
            session_id="sess-1",
            event_range_start="evt-1",
            event_range_end="evt-2",
            author="alice",
            rationale="opt-in-off",
            annotation_type="human_note",
        )
        async with aiosqlite.connect(kg_no_audit.annotations_db) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM annotations WHERE id = ?",
                (annotation_id,),
            )
            (count,) = await cursor.fetchone()
        assert count == 1

    async def test_no_log_entry_when_audit_disabled(
        self, kg_no_audit: KnowledgeGraph,
    ) -> None:
        await kg_no_audit.insert_annotation(
            session_id="sess-1",
            event_range_start="evt-1",
            event_range_end="evt-2",
            author="alice",
            rationale="opt-in-off",
            annotation_type="human_note",
        )
        # audit_db + tamper_evident_log schema may not even exist here;
        # the opt-off path returns early before touching the audit DB.
        # We just confirm no chain rows exist by checking the audit_db
        # is absent OR the log table is empty.
        if not os.path.exists(kg_no_audit.audit_db):
            return
        async with aiosqlite.connect(kg_no_audit.audit_db) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='tamper_evident_log'"
            )
            if await cursor.fetchone() is None:
                return
            cursor = await db.execute(
                "SELECT COUNT(*) FROM tamper_evident_log"
            )
            (count,) = await cursor.fetchone()
        assert count == 0
