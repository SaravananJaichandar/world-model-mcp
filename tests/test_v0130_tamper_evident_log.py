"""
v0.13 — tamper-evident append-only log (schema-only PR).

Covers:
- Canonical serialization is stable across dict key orders
- Genesis hash is deterministic and versioned
- `append_entry` chains prev_hash correctly, produces monotonic seq
- Append-only triggers reject UPDATE and DELETE
- `verify_chain` detects tampering at each field
- Schema is opt-in (only created when WORLD_MODEL_AUDIT_LOG=on)
"""

import os
import tempfile
from datetime import datetime, timezone
from unittest import mock

import aiosqlite
import pytest

from world_model_server import tamper_evident
from world_model_server.knowledge_graph import KnowledgeGraph


class TestCanonicalSerialization:
    def test_key_order_does_not_affect_output(self):
        a = tamper_evident.canonical_json({"z": 1, "a": 2, "m": 3})
        b = tamper_evident.canonical_json({"a": 2, "m": 3, "z": 1})
        assert a == b

    def test_nested_key_order_does_not_affect_output(self):
        a = tamper_evident.canonical_json({"outer": {"z": 1, "a": 2}})
        b = tamper_evident.canonical_json({"outer": {"a": 2, "z": 1}})
        assert a == b

    def test_no_whitespace_variance(self):
        # Whitespace in JSON output would break byte-level hash stability.
        blob = tamper_evident.canonical_json({"x": 1, "y": 2})
        assert b" " not in blob
        assert b"\n" not in blob
        assert blob == b'{"x":1,"y":2}'

    def test_datetime_normalization_utc_millisecond_precision(self):
        # Two datetimes for the "same" instant serialize identically.
        naive = datetime(2026, 7, 11, 12, 0, 0, 123000)
        utc = datetime(2026, 7, 11, 12, 0, 0, 123000, tzinfo=timezone.utc)
        assert tamper_evident.canonical_json({"ts": naive}) == tamper_evident.canonical_json({"ts": utc})

    def test_set_serialized_as_sorted_list(self):
        blob = tamper_evident.canonical_json({"tags": {"b", "a", "c"}})
        assert blob == b'{"tags":["a","b","c"]}'

    def test_exotic_type_raises_typeerror(self):
        # Anything the default handler does not recognize must raise, not
        # silently produce a non-canonical serialization.
        with pytest.raises(TypeError):
            tamper_evident.canonical_json({"bad": object()})


class TestGenesisHash:
    def test_genesis_is_deterministic(self):
        # Recomputing the constant module-side must produce the same value.
        # Guards against accidental mutation of the seed.
        import hashlib
        expected = "sha256:" + hashlib.sha256(
            b"world-model-mcp tamper-evident log v1"
        ).hexdigest()
        assert tamper_evident.GENESIS_HASH == expected

    def test_genesis_is_prefixed(self):
        assert tamper_evident.GENESIS_HASH.startswith("sha256:")


class TestRowHash:
    def test_same_input_produces_same_hash(self):
        row = {"id": "fact-1", "text": "x is y", "confidence": 0.9}
        assert tamper_evident.row_hash(row) == tamper_evident.row_hash(row)

    def test_different_input_produces_different_hash(self):
        h1 = tamper_evident.row_hash({"id": "fact-1"})
        h2 = tamper_evident.row_hash({"id": "fact-2"})
        assert h1 != h2

    def test_key_order_does_not_affect_hash(self):
        h1 = tamper_evident.row_hash({"a": 1, "b": 2})
        h2 = tamper_evident.row_hash({"b": 2, "a": 1})
        assert h1 == h2


@pytest.mark.asyncio
class TestAppendEntry:
    async def _fresh_db(self, tmp_path):
        db_path = tmp_path / "audit.db"
        db = await aiosqlite.connect(db_path)
        await tamper_evident.create_schema(db)
        return db

    async def test_first_entry_prev_hash_is_genesis(self, tmp_path):
        db = await self._fresh_db(tmp_path)
        try:
            entry = await tamper_evident.append_entry(
                db, "fact_create", "fact-1", {"id": "fact-1", "text": "x"}
            )
            await db.commit()
            assert entry["prev_hash"] == tamper_evident.GENESIS_HASH
            assert entry["seq"] == 1
        finally:
            await db.close()

    async def test_subsequent_entry_chains_to_prior_entry_hash(self, tmp_path):
        db = await self._fresh_db(tmp_path)
        try:
            first = await tamper_evident.append_entry(
                db, "fact_create", "fact-1", {"id": "fact-1"}
            )
            second = await tamper_evident.append_entry(
                db, "fact_create", "fact-2", {"id": "fact-2"}
            )
            await db.commit()
            assert second["prev_hash"] == first["entry_hash"]
            assert second["seq"] == 2
        finally:
            await db.close()

    async def test_seq_is_monotonic(self, tmp_path):
        db = await self._fresh_db(tmp_path)
        try:
            entries = []
            for i in range(5):
                entries.append(
                    await tamper_evident.append_entry(
                        db, "event_create", f"event-{i}", {"i": i}
                    )
                )
            await db.commit()
            assert [e["seq"] for e in entries] == [1, 2, 3, 4, 5]
        finally:
            await db.close()

    async def test_row_hash_computed_from_payload(self, tmp_path):
        db = await self._fresh_db(tmp_path)
        try:
            payload = {"id": "fact-1", "text": "hello"}
            entry = await tamper_evident.append_entry(
                db, "fact_create", "fact-1", payload
            )
            await db.commit()
            assert entry["row_hash"] == tamper_evident.row_hash(payload)
        finally:
            await db.close()


@pytest.mark.asyncio
class TestAppendOnlyEnforcement:
    async def _seeded_db(self, tmp_path):
        db_path = tmp_path / "audit.db"
        db = await aiosqlite.connect(db_path)
        await tamper_evident.create_schema(db)
        await tamper_evident.append_entry(db, "fact_create", "fact-1", {"id": "fact-1"})
        await db.commit()
        return db

    async def test_update_is_rejected(self, tmp_path):
        db = await self._seeded_db(tmp_path)
        try:
            with pytest.raises(aiosqlite.IntegrityError) as exc:
                await db.execute(
                    "UPDATE tamper_evident_log SET row_hash = 'sha256:tampered' WHERE seq = 1"
                )
                await db.commit()
            assert "append-only" in str(exc.value).lower()
        finally:
            await db.close()

    async def test_delete_is_rejected(self, tmp_path):
        db = await self._seeded_db(tmp_path)
        try:
            with pytest.raises(aiosqlite.IntegrityError) as exc:
                await db.execute("DELETE FROM tamper_evident_log WHERE seq = 1")
                await db.commit()
            assert "append-only" in str(exc.value).lower()
        finally:
            await db.close()


@pytest.mark.asyncio
class TestVerifyChain:
    async def _seeded_entries(self, tmp_path, n=3):
        db_path = tmp_path / "audit.db"
        db = await aiosqlite.connect(db_path)
        await tamper_evident.create_schema(db)
        for i in range(n):
            await tamper_evident.append_entry(
                db, "event_create", f"e-{i}", {"i": i}
            )
        await db.commit()
        cursor = await db.execute(
            "SELECT seq, kind, row_id, row_hash, prev_hash, entry_hash, ts FROM tamper_evident_log ORDER BY seq"
        )
        rows = await cursor.fetchall()
        await db.close()
        return [
            dict(seq=r[0], kind=r[1], row_id=r[2], row_hash=r[3], prev_hash=r[4], entry_hash=r[5], ts=r[6])
            for r in rows
        ]

    async def test_intact_chain_verifies(self, tmp_path):
        entries = await self._seeded_entries(tmp_path, n=3)
        ok, reason = tamper_evident.verify_chain(entries)
        assert ok, reason
        assert reason is None

    async def test_tampered_row_hash_detected(self, tmp_path):
        entries = await self._seeded_entries(tmp_path, n=3)
        entries[1]["row_hash"] = "sha256:evil"
        ok, reason = tamper_evident.verify_chain(entries)
        assert not ok
        assert "entry_hash mismatch" in reason

    async def test_tampered_kind_detected(self, tmp_path):
        entries = await self._seeded_entries(tmp_path, n=3)
        entries[1]["kind"] = "event_delete"
        ok, reason = tamper_evident.verify_chain(entries)
        assert not ok
        assert "entry_hash mismatch" in reason

    async def test_reordered_entries_detected(self, tmp_path):
        entries = await self._seeded_entries(tmp_path, n=3)
        entries[1], entries[2] = entries[2], entries[1]
        ok, reason = tamper_evident.verify_chain(entries)
        assert not ok
        # Reorder shows as seq gap because we walk in list order.
        assert "seq" in reason.lower() or "prev_hash" in reason


@pytest.mark.asyncio
class TestOptInSchema:
    async def test_schema_absent_when_env_var_off(self):
        with mock.patch.dict(os.environ, {"WORLD_MODEL_AUDIT_LOG": ""}, clear=False):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                assert not kg.tamper_evident_enabled
                async with aiosqlite.connect(kg.audit_db) as db:
                    cursor = await db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='tamper_evident_log'"
                    )
                    row = await cursor.fetchone()
                    assert row is None, "table must not exist without opt-in"

    async def test_schema_created_when_env_var_on(self):
        with mock.patch.dict(os.environ, {"WORLD_MODEL_AUDIT_LOG": "on"}, clear=False):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                assert kg.tamper_evident_enabled
                async with aiosqlite.connect(kg.audit_db) as db:
                    cursor = await db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='tamper_evident_log'"
                    )
                    row = await cursor.fetchone()
                    assert row is not None, "table must exist with opt-in"
