"""
v0.13 — epoch-close pipeline.

Covers:
- Epoch table is created alongside the log table when opt-in is on
- Below threshold: no epoch is closed
- At threshold: an epoch closes with correct merkle_root, prev_epoch_root
  (genesis for first), first/last entry seq, entry_count, signed envelope
- Second epoch chains prev_epoch_root to first epoch's merkle_root
- Epoch signature verifies under the operator's persisted public keys
- Epoch table is append-only (UPDATE / DELETE forbidden)
- Keys persist across process restarts (second KnowledgeGraph reads
  same signer without regenerating)
- Non-opt-in path never triggers epoch close and never creates keys
- Threshold override via WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE env var
"""

import json
import os
import stat
import tempfile
from unittest import mock

import aiosqlite
import pytest

from world_model_server import (
    audit_keys,
    hybrid_signer as hs,
    merkle,
    tamper_evident,
)
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event


async def _fetch_epochs(audit_db_path):
    async with aiosqlite.connect(audit_db_path) as db:
        cursor = await db.execute(
            "SELECT seq, merkle_root, prev_epoch_root, first_entry_seq, "
            "last_entry_seq, entry_count, signature_envelope, closed_at "
            "FROM tamper_evident_epochs ORDER BY seq"
        )
        rows = await cursor.fetchall()
    return [
        dict(
            seq=r[0], merkle_root=r[1], prev_epoch_root=r[2],
            first_entry_seq=r[3], last_entry_seq=r[4], entry_count=r[5],
            signature_envelope=r[6], closed_at=r[7],
        )
        for r in rows
    ]


async def _fetch_entry_row_hashes(audit_db_path):
    """Return all entry row_hashes in seq order."""
    async with aiosqlite.connect(audit_db_path) as db:
        cursor = await db.execute(
            "SELECT row_hash FROM tamper_evident_log ORDER BY seq"
        )
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


def _make_event(i: int) -> Event:
    return Event(
        session_id="epoch-test",
        event_type="file_edit",
        tool_name="Edit",
        entity_id=f"file-{i}",
        success=True,
    )


@pytest.mark.asyncio
class TestEpochSchema:
    async def test_epochs_table_created_alongside_log_table(self):
        with mock.patch.dict(os.environ, {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                async with aiosqlite.connect(kg.audit_db) as db:
                    cursor = await db.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='tamper_evident_epochs'"
                    )
                    assert await cursor.fetchone() is not None

    async def test_epochs_table_absent_when_opt_in_off(self):
        env = {k: v for k, v in os.environ.items() if k != "WORLD_MODEL_AUDIT_LOG"}
        with mock.patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                async with aiosqlite.connect(kg.audit_db) as db:
                    cursor = await db.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='tamper_evident_epochs'"
                    )
                    assert await cursor.fetchone() is None


@pytest.mark.asyncio
class TestEpochCloseTrigger:
    async def test_below_threshold_no_epoch_closes(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "5",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                # 3 entries — below threshold 5.
                for i in range(3):
                    await kg.create_event(_make_event(i))
                epochs = await _fetch_epochs(kg.audit_db)
                assert len(epochs) == 0

    async def test_at_threshold_closes_epoch(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "4",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                for i in range(4):
                    await kg.create_event(_make_event(i))
                epochs = await _fetch_epochs(kg.audit_db)
                assert len(epochs) == 1
                e = epochs[0]
                assert e["entry_count"] == 4
                assert e["first_entry_seq"] == 1
                assert e["last_entry_seq"] == 4
                # Genesis prev_epoch_root anchors the first epoch.
                assert e["prev_epoch_root"] == tamper_evident.EPOCH_GENESIS_ROOT

    async def test_second_epoch_chains_to_first_merkle_root(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                # 6 entries → two epochs of 3 each.
                for i in range(6):
                    await kg.create_event(_make_event(i))
                epochs = await _fetch_epochs(kg.audit_db)
                assert len(epochs) == 2
                assert epochs[1]["prev_epoch_root"] == epochs[0]["merkle_root"]
                assert epochs[1]["first_entry_seq"] == 4
                assert epochs[1]["last_entry_seq"] == 6

    async def test_merkle_root_matches_leaves_over_epoch_entries(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "4",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                for i in range(4):
                    await kg.create_event(_make_event(i))
                epochs = await _fetch_epochs(kg.audit_db)
                row_hashes = await _fetch_entry_row_hashes(kg.audit_db)
                # Recompute the Merkle root from the persisted row_hashes;
                # must match the stored merkle_root.
                leaves = [
                    merkle.leaf_hash(bytes.fromhex(rh.split(":", 1)[1]))
                    for rh in row_hashes
                ]
                expected_root = "sha256:" + merkle.merkle_root(leaves).hex()
                assert epochs[0]["merkle_root"] == expected_root


@pytest.mark.asyncio
class TestEpochSignature:
    async def test_signature_verifies_with_persisted_public_keys(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                for i in range(3):
                    await kg.create_event(_make_event(i))
                epochs = await _fetch_epochs(kg.audit_db)
                envelope = json.loads(epochs[0]["signature_envelope"])

                # Read the operator's public keys from disk.
                pk_payload = audit_keys.read_public_keys(kg.db_path)
                assert pk_payload is not None
                ed_pub = bytes.fromhex(pk_payload["ed25519"]["public_key_hex"])
                slh_pub = bytes.fromhex(pk_payload["slh_dsa"]["public_key_hex"])

                # Reconstruct the exact signed message: canonical JSON of
                # the epoch fields, in the order the close_epoch function
                # produced.
                signed_payload = {
                    "merkle_root": epochs[0]["merkle_root"],
                    "prev_epoch_root": epochs[0]["prev_epoch_root"],
                    "first_entry_seq": epochs[0]["first_entry_seq"],
                    "last_entry_seq": epochs[0]["last_entry_seq"],
                    "entry_count": epochs[0]["entry_count"],
                    "closed_at": epochs[0]["closed_at"],
                }
                signed_bytes = tamper_evident.canonical_json(signed_payload)
                assert hs.verify_hybrid(
                    envelope=envelope,
                    message=signed_bytes,
                    ed25519_public_key=ed_pub,
                    slh_dsa_public_key=slh_pub,
                )

    async def test_signature_fails_if_merkle_root_tampered_in_verification(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                for i in range(3):
                    await kg.create_event(_make_event(i))
                epochs = await _fetch_epochs(kg.audit_db)
                envelope = json.loads(epochs[0]["signature_envelope"])
                pk_payload = audit_keys.read_public_keys(kg.db_path)
                ed_pub = bytes.fromhex(pk_payload["ed25519"]["public_key_hex"])
                slh_pub = bytes.fromhex(pk_payload["slh_dsa"]["public_key_hex"])

                tampered_payload = {
                    "merkle_root": "sha256:00" * 32,  # attacker rewrote the root
                    "prev_epoch_root": epochs[0]["prev_epoch_root"],
                    "first_entry_seq": epochs[0]["first_entry_seq"],
                    "last_entry_seq": epochs[0]["last_entry_seq"],
                    "entry_count": epochs[0]["entry_count"],
                    "closed_at": epochs[0]["closed_at"],
                }
                assert not hs.verify_hybrid(
                    envelope=envelope,
                    message=tamper_evident.canonical_json(tampered_payload),
                    ed25519_public_key=ed_pub,
                    slh_dsa_public_key=slh_pub,
                )


@pytest.mark.asyncio
class TestEpochTableAppendOnly:
    async def _seed_one_epoch(self, tmp):
        for i in range(3):
            kg = None  # unused; loop-scoped
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            kg = KnowledgeGraph(tmp)
            await kg.initialize()
            for i in range(3):
                await kg.create_event(_make_event(i))
            return kg.audit_db

    async def test_epoch_update_forbidden(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                for i in range(3):
                    await kg.create_event(_make_event(i))
                async with aiosqlite.connect(kg.audit_db) as db:
                    with pytest.raises(aiosqlite.IntegrityError) as exc:
                        await db.execute(
                            "UPDATE tamper_evident_epochs "
                            "SET merkle_root = 'sha256:ff' WHERE seq = 1"
                        )
                        await db.commit()
                    assert "append-only" in str(exc.value).lower()

    async def test_epoch_delete_forbidden(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "3",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                for i in range(3):
                    await kg.create_event(_make_event(i))
                async with aiosqlite.connect(kg.audit_db) as db:
                    with pytest.raises(aiosqlite.IntegrityError) as exc:
                        await db.execute(
                            "DELETE FROM tamper_evident_epochs WHERE seq = 1"
                        )
                        await db.commit()
                    assert "append-only" in str(exc.value).lower()


@pytest.mark.asyncio
class TestKeyPersistence:
    async def test_keys_created_on_first_epoch_close(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "2",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                # Below threshold: no keys yet.
                await kg.create_event(_make_event(0))
                assert audit_keys.read_public_keys(kg.db_path) is None
                # At threshold: keys are generated + public_keys.json written.
                await kg.create_event(_make_event(1))
                pk = audit_keys.read_public_keys(kg.db_path)
                assert pk is not None
                assert "ed25519" in pk
                assert "slh_dsa" in pk

    async def test_private_key_files_have_mode_0600(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "2",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                for i in range(2):
                    await kg.create_event(_make_event(i))
                keys_dir = kg.db_path / "keys"
                for name in ("ed25519_private.key", "slh_dsa_secret.key"):
                    p = keys_dir / name
                    assert p.exists()
                    mode_bits = stat.S_IMODE(os.stat(p).st_mode)
                    assert mode_bits == 0o600, (
                        f"{name} must be 0600, got {oct(mode_bits)}"
                    )

    async def test_signer_survives_process_restart(self):
        with mock.patch.dict(os.environ, {
            "WORLD_MODEL_AUDIT_LOG": "on",
            "WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE": "2",
        }):
            with tempfile.TemporaryDirectory() as tmp:
                # First "process" — generate keys, close first epoch.
                kg1 = KnowledgeGraph(tmp)
                await kg1.initialize()
                for i in range(2):
                    await kg1.create_event(_make_event(i))
                first_signer = audit_keys.load_or_create_signer(kg1.db_path)
                first_ed_pub = first_signer.ed25519_public_key_bytes()
                first_slh_pub = first_signer.slh_dsa_public_key_bytes()

                # Second "process" on the SAME DB path — must reuse keys.
                kg2 = KnowledgeGraph(tmp)
                await kg2.initialize()
                for i in range(2, 4):
                    await kg2.create_event(_make_event(i))
                second_signer = audit_keys.load_or_create_signer(kg2.db_path)
                assert second_signer.ed25519_public_key_bytes() == first_ed_pub
                assert second_signer.slh_dsa_public_key_bytes() == first_slh_pub


@pytest.mark.asyncio
class TestNonOptInPathUnchanged:
    """
    Defensive coverage: with opt-in off, no audit table, no epoch, no
    keys, no matter how many entries the write path handles.
    """

    async def test_no_epoch_no_keys_no_audit_when_opt_in_off(self):
        env = {k: v for k, v in os.environ.items() if k != "WORLD_MODEL_AUDIT_LOG"}
        with mock.patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                # Write enough that opt-in would have triggered an epoch.
                for i in range(50):
                    await kg.create_event(_make_event(i))
                # No keys directory.
                assert not (kg.db_path / "keys").exists()
                # No tables.
                async with aiosqlite.connect(kg.audit_db) as db:
                    cursor = await db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND (name='tamper_evident_log' OR name='tamper_evident_epochs')"
                    )
                    rows = await cursor.fetchall()
                    assert rows == []
