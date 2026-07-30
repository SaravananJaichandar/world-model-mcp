"""
Regression lock for the delete/purge two-primitive design shipped in
response to DanceNitra in #37 on this repo.

Measured finding (their reproduction, we replicate the same shape here):
`WorldModelMemoryBackend.delete()` marked facts invalid but left rows on
disk and retrievable. Return string said "Deleted memory at {path}" while
the docstring said `(invalidates the Fact)` — the docstring was accurate;
the return string and two field descriptions in models.py were not.

Fix landed in this same release:
1. `delete()` return string now says "Invalidated" and explicitly points
   at `purge()` for row-level erasure
2. New `WorldModelMemoryBackend.purge(path)` primitive that hard-deletes
   via `KnowledgeGraph.purge_fact` (`DELETE FROM facts` + FTS sync)
3. `models.py` field descriptions for `influence_state` and `expires_at`
   now note the consumer-wiring gap that made the original descriptions
   over-promise

These tests lock all three so the drift can't silently re-open.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import aiosqlite
import pytest

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.memory_backend import WorldModelMemoryBackend
from world_model_server.models import Fact


@pytest.fixture
async def backend(tmp_path):
    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()
    b = WorldModelMemoryBackend(kg)
    # Seed a fact so we have something to delete/purge
    await b._write("test-key", "sentinel-value-worth-erasing")
    yield b


class TestDeleteReturnStringHonest:
    """The return string was the biggest lie in the old surface. Lock it."""

    @pytest.mark.asyncio
    async def test_return_string_no_longer_says_deleted(self, backend):
        msg = await backend.delete("test-key")
        assert "Deleted memory" not in msg, (
            "return string must not claim 'Deleted' when only invalidating; "
            "that was the exact regression DanceNitra flagged in #37"
        )

    @pytest.mark.asyncio
    async def test_return_string_says_invalidated(self, backend):
        msg = await backend.delete("test-key")
        assert "Invalidated" in msg, (
            "return string must accurately name the operation; "
            "docstring says invalidate, return string should agree"
        )

    @pytest.mark.asyncio
    async def test_return_string_points_at_purge_for_erasure(self, backend):
        msg = await backend.delete("test-key")
        assert "purge" in msg.lower(), (
            "callers who expected 'delete' to actually erase must be told "
            "explicitly where to go for that semantic"
        )

    @pytest.mark.asyncio
    async def test_delete_missing_key_returns_no_memory(self, backend):
        msg = await backend.delete("never-existed")
        assert msg == "No memory at never-existed"


class TestDeleteLeavesRowOnDiskByDesign:
    """This is now documented behavior, not a bug. Lock it as intended."""

    @pytest.mark.asyncio
    async def test_delete_does_not_remove_row_from_facts_table(
        self, backend, tmp_path,
    ):
        # Get the fact_id before delete
        fact = await backend._latest_fact_for("test-key")
        assert fact is not None
        fact_id = fact.id

        await backend.delete("test-key")

        # Row should still exist in facts.db (soft-delete)
        async with aiosqlite.connect(backend.kg.facts_db) as db:
            cursor = await db.execute(
                "SELECT invalid_at FROM facts WHERE id = ?", (fact_id,),
            )
            row = await cursor.fetchone()
            assert row is not None, (
                "delete() must leave the row on disk (audit-preserving); "
                "callers who need actual erasure must call purge()"
            )
            assert row[0] is not None, (
                "delete() must set invalid_at as the soft-delete marker"
            )


class TestPurgePrimitive:
    """The new purpose-built erase primitive. Must actually erase."""

    @pytest.mark.asyncio
    async def test_purge_removes_row_from_facts_table(self, backend):
        fact = await backend._latest_fact_for("test-key")
        assert fact is not None
        fact_id = fact.id

        msg = await backend.purge("test-key")
        assert "Purged" in msg

        async with aiosqlite.connect(backend.kg.facts_db) as db:
            cursor = await db.execute(
                "SELECT id FROM facts WHERE id = ?", (fact_id,),
            )
            row = await cursor.fetchone()
            assert row is None, (
                "purge() must physically remove the row; if this fails, "
                "the fix for #37 has regressed"
            )

    @pytest.mark.asyncio
    async def test_purge_removes_row_from_fts_index(self, backend):
        """The `AFTER DELETE ON facts` trigger must fire on the purge."""
        fact = await backend._latest_fact_for("test-key")
        assert fact is not None
        fact_id = fact.id

        # Verify FTS row exists before purge
        async with aiosqlite.connect(backend.kg.facts_db) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM facts_fts WHERE rowid = "
                "(SELECT rowid FROM facts WHERE id = ?)",
                (fact_id,),
            )
            (fts_before,) = await cursor.fetchone()
            assert fts_before >= 1, "sanity: FTS row must exist before purge"

        await backend.purge("test-key")

        async with aiosqlite.connect(backend.kg.facts_db) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM facts_fts WHERE rowid = "
                "(SELECT rowid FROM facts WHERE id = ?)",
                (fact_id,),
            )
            (fts_after,) = await cursor.fetchone()
            assert fts_after == 0, (
                "AFTER DELETE ON facts trigger must have fired; "
                "if fts_after > 0 the fact is still findable via full-text "
                "search which defeats the purge semantics"
            )

    @pytest.mark.asyncio
    async def test_purge_missing_key_returns_no_memory(self, backend):
        msg = await backend.purge("never-existed")
        assert msg == "No memory at never-existed"

    @pytest.mark.asyncio
    async def test_purge_return_string_mentions_erasure(self, backend):
        msg = await backend.purge("test-key")
        assert "Purged" in msg
        assert "not retrievable" in msg or "removed from disk" in msg


class TestKnowledgeGraphPurgeFactReturnCode:
    """purge_fact returns True/False for signed-audit downstream callers."""

    @pytest.mark.asyncio
    async def test_purge_fact_returns_true_when_row_removed(self, backend):
        fact = await backend._latest_fact_for("test-key")
        assert fact is not None
        removed = await backend.kg.purge_fact(fact.id)
        assert removed is True

    @pytest.mark.asyncio
    async def test_purge_fact_returns_false_when_absent(self, backend):
        removed = await backend.kg.purge_fact("nonexistent-fact-id")
        assert removed is False


class TestModelsFieldDescriptionHonestyLock:
    """The field descriptions in models.py were over-promising. Lock the
    honest version so a future edit can't silently regress it."""

    def test_expires_at_description_notes_consumer_wiring_gap(self):
        from world_model_server.models import Fact
        desc = Fact.model_fields["expires_at"].description or ""
        assert "expiry sweep" in desc.lower() or "purge" in desc.lower(), (
            "expires_at description must reference the consumer wiring "
            "(sweep worker) so a reader with a retention requirement is "
            "not misled about what the field guarantees on its own"
        )
        assert "not guaranteed on-by-default" in desc.lower() or (
            "verify the sweep is enabled" in desc.lower()
        ), (
            "expires_at description must be honest that the sweep is "
            "not automatic in the shipped wheel"
        )

    def test_influence_state_description_notes_consumer_wiring_gap(self):
        from world_model_server.models import Fact
        desc = Fact.model_fields["influence_state"].description or ""
        assert "consumer wiring" in desc.lower() or (
            "verify their retrieval path" in desc.lower()
        ), (
            "influence_state description must reference the consumer "
            "wiring so a reader does not assume the filter is automatic"
        )
