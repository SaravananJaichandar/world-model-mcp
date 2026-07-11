"""
v0.13 — inclusion-proof + head-fetch APIs and reference verifier.

Covers:
- get_inclusion_proof returns a full bundle for a persisted row_id
- Bundle inclusion proof verifies against the stored epoch merkle_root
- Bundle epoch_chain has intact prev_epoch_root chaining from genesis
- verify_inclusion_bundle: intact bundle → (True, None)
- verify_inclusion_bundle catches every tampering path:
    - rewritten epoch merkle_root breaks the signature
    - swapped prev_epoch_root breaks the chain link
    - forged inclusion proof breaks the leaf verification
    - forged row_hash on the entry breaks the leaf verification
    - envelope stripped SLH-DSA half is rejected
- Bundle for a row_id that does not exist raises ValueError
- Bundle for an entry in the unclosed backlog raises ValueError with a
  retry-after-next-close hint
- get_audit_log_head returns head state + full chain (used by periodic
  external audits without a specific entry to prove)
- Bundle for a row_id in a MIDDLE epoch (not the first, not the last)
  still verifies — leaf_index is relative to the containing epoch
"""

import json
import os
import tempfile
from copy import deepcopy
from unittest import mock

import aiosqlite
import pytest

from world_model_server import (
    audit_keys,
    tamper_evident,
)
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event


def _make_event(i: int) -> Event:
    return Event(
        session_id="proof-test",
        event_type="file_edit",
        tool_name="Edit",
        entity_id=f"file-{i}",
        success=True,
    )


async def _seed_two_epochs_of_three(tmp: str) -> KnowledgeGraph:
    """Six events → two full epochs of 3 entries each."""
    kg = KnowledgeGraph(tmp)
    await kg.initialize()
    for i in range(6):
        await kg.create_event(_make_event(i))
    return kg


def _load_pubkeys(kg: KnowledgeGraph) -> tuple[bytes, bytes]:
    pk = audit_keys.read_public_keys(kg.db_path)
    ed_pub = bytes.fromhex(pk["ed25519"]["public_key_hex"])
    slh_pub = bytes.fromhex(pk["slh_dsa"]["public_key_hex"])
    return ed_pub, slh_pub


@pytest.mark.asyncio
class TestGetInclusionProof:
    async def test_bundle_shape_first_epoch(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                # Capture the event IDs BEFORE writing so we can query
                # for the one at a known position (Event() auto-generates
                # a UUID at construction time).
                events = [_make_event(i) for i in range(6)]
                for e in events:
                    await kg.create_event(e)
                # events[1] is the second entry → seq=2, in epoch 1
                # (which spans seq 1..3 given threshold=3).
                async with aiosqlite.connect(kg.audit_db) as db:
                    bundle = await tamper_evident.get_inclusion_proof(
                        db, events[1].id
                    )
                assert bundle["row_id"] == events[1].id
                assert bundle["entry_kind"] == "event_create"
                assert bundle["row_hash"].startswith("sha256:")
                assert bundle["epoch"]["seq"] == 1
                assert bundle["inclusion"]["tree_size"] == 3
                assert bundle["inclusion"]["leaf_index"] == 1  # seq 2 - first_seq 1

    async def test_bundle_for_row_in_middle_epoch(self):
        """
        Nine events with epoch size 3 → three epochs. Fetch a bundle for
        an event in the MIDDLE epoch and confirm leaf_index is relative
        to that epoch (i.e. 0..2) and epoch_chain has exactly two entries
        (genesis→ep1→ep2).
        """
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                events = [_make_event(i) for i in range(9)]
                for e in events:
                    await kg.create_event(e)
                # The middle epoch contains entries seq 4..6 → events[3..5].
                target = events[4]
                async with aiosqlite.connect(kg.audit_db) as db:
                    bundle = await tamper_evident.get_inclusion_proof(
                        db, target.id
                    )
                assert bundle["epoch"]["seq"] == 2
                assert bundle["inclusion"]["leaf_index"] == 1  # 5 - 4
                assert len(bundle["epoch_chain"]) == 2

    async def test_row_id_not_found_raises(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = await _seed_two_epochs_of_three(tmp)
                async with aiosqlite.connect(kg.audit_db) as db:
                    with pytest.raises(ValueError) as exc:
                        await tamper_evident.get_inclusion_proof(
                            db, "nonexistent-row-id"
                        )
                    assert "not found" in str(exc.value).lower()

    async def test_unclosed_entry_raises_with_retry_hint(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "10",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                # Only 3 entries; threshold 10 → nothing sealed.
                events = [_make_event(i) for i in range(3)]
                for e in events:
                    await kg.create_event(e)
                async with aiosqlite.connect(kg.audit_db) as db:
                    with pytest.raises(ValueError) as exc:
                        await tamper_evident.get_inclusion_proof(
                            db, events[0].id
                        )
                    msg = str(exc.value).lower()
                    assert "not yet sealed" in msg
                    assert "retry" in msg


@pytest.mark.asyncio
class TestVerifyInclusionBundle:
    async def test_intact_bundle_verifies(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                events = [_make_event(i) for i in range(6)]
                for e in events:
                    await kg.create_event(e)
                async with aiosqlite.connect(kg.audit_db) as db:
                    bundle = await tamper_evident.get_inclusion_proof(
                        db, events[2].id
                    )
                ed_pub, slh_pub = _load_pubkeys(kg)
                ok, reason = tamper_evident.verify_inclusion_bundle(
                    bundle, ed_pub, slh_pub
                )
                assert ok, reason
                assert reason is None

    async def test_tampered_epoch_merkle_root_rejected(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                events = [_make_event(i) for i in range(6)]
                for e in events:
                    await kg.create_event(e)
                async with aiosqlite.connect(kg.audit_db) as db:
                    bundle = await tamper_evident.get_inclusion_proof(
                        db, events[2].id
                    )
                ed_pub, slh_pub = _load_pubkeys(kg)
                # Rewrite the merkle_root in the epoch chain. The signature
                # covers this field, so verification MUST fail on the
                # signature check.
                bundle["epoch_chain"][0]["merkle_root"] = "sha256:" + "00" * 32
                ok, reason = tamper_evident.verify_inclusion_bundle(
                    bundle, ed_pub, slh_pub
                )
                assert not ok
                assert "signature" in reason.lower() or "chain" in reason.lower()

    async def test_broken_prev_epoch_root_chain_rejected(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                events = [_make_event(i) for i in range(6)]
                for e in events:
                    await kg.create_event(e)
                async with aiosqlite.connect(kg.audit_db) as db:
                    bundle = await tamper_evident.get_inclusion_proof(
                        db, events[5].id
                    )
                ed_pub, slh_pub = _load_pubkeys(kg)
                # Bundle spans two epochs. Rewrite the SECOND epoch's
                # prev_epoch_root so it no longer chains to the first.
                bundle["epoch_chain"][1]["prev_epoch_root"] = "sha256:" + "ff" * 32
                ok, reason = tamper_evident.verify_inclusion_bundle(
                    bundle, ed_pub, slh_pub
                )
                assert not ok
                assert "chain" in reason.lower() or "prev_epoch_root" in reason.lower()

    async def test_forged_inclusion_proof_rejected(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                events = [_make_event(i) for i in range(6)]
                for e in events:
                    await kg.create_event(e)
                async with aiosqlite.connect(kg.audit_db) as db:
                    bundle = await tamper_evident.get_inclusion_proof(
                        db, events[2].id
                    )
                ed_pub, slh_pub = _load_pubkeys(kg)
                # Corrupt one sibling in the inclusion proof.
                if bundle["inclusion"]["proof"]:
                    bundle["inclusion"]["proof"][0] = "00" * 32
                ok, reason = tamper_evident.verify_inclusion_bundle(
                    bundle, ed_pub, slh_pub
                )
                assert not ok
                assert "inclusion" in reason.lower()

    async def test_swapped_row_hash_rejected(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                events = [_make_event(i) for i in range(6)]
                for e in events:
                    await kg.create_event(e)
                async with aiosqlite.connect(kg.audit_db) as db:
                    bundle = await tamper_evident.get_inclusion_proof(
                        db, events[2].id
                    )
                ed_pub, slh_pub = _load_pubkeys(kg)
                # Attacker swaps in a different row_hash — inclusion
                # verification of this leaf into the epoch's tree MUST fail.
                bundle["row_hash"] = "sha256:" + "ab" * 32
                ok, reason = tamper_evident.verify_inclusion_bundle(
                    bundle, ed_pub, slh_pub
                )
                assert not ok
                assert "inclusion" in reason.lower()

    async def test_envelope_stripped_pq_half_rejected(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                events = [_make_event(i) for i in range(6)]
                for e in events:
                    await kg.create_event(e)
                async with aiosqlite.connect(kg.audit_db) as db:
                    bundle = await tamper_evident.get_inclusion_proof(
                        db, events[2].id
                    )
                ed_pub, slh_pub = _load_pubkeys(kg)
                # Attacker strips the SLH-DSA half from the envelope,
                # hoping the verifier falls back to Ed25519-only.
                bundle["epoch_chain"][0]["signature_envelope"]["slh_dsa"] = None
                ok, reason = tamper_evident.verify_inclusion_bundle(
                    bundle, ed_pub, slh_pub
                )
                assert not ok
                assert "signature" in reason.lower()


@pytest.mark.asyncio
class TestGetAuditLogHead:
    async def test_head_empty_when_nothing_written(self):
        with mock.patch.dict(os.environ, {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                async with aiosqlite.connect(kg.audit_db) as db:
                    head = await tamper_evident.get_audit_log_head(db)
                assert head["head_entry_seq"] == 0
                assert head["head_epoch_seq"] == 0
                assert head["unclosed_entry_count"] == 0
                assert head["epoch_chain"] == []
                assert head["genesis_entry_hash"] == tamper_evident.GENESIS_HASH
                assert head["genesis_epoch_root"] == tamper_evident.EPOCH_GENESIS_ROOT

    async def test_head_reflects_closed_epochs_and_unclosed_tail(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                for i in range(7):  # 2 closed epochs of 3 + 1 unclosed
                    await kg.create_event(_make_event(i))
                async with aiosqlite.connect(kg.audit_db) as db:
                    head = await tamper_evident.get_audit_log_head(db)
                assert head["head_entry_seq"] == 7
                assert head["head_epoch_seq"] == 2
                assert head["unclosed_entry_count"] == 1
                assert len(head["epoch_chain"]) == 2
