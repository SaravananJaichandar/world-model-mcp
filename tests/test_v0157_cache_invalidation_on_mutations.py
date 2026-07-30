"""
Regression lock for the mutation-path cache-invalidation fix shipped in v0.15.7.

Verified finding from the second measurement pass after v0.15.6 landed:
`KnowledgeGraph` caches `query_facts` results in `_cache` with a TTL. Three
mutation methods commit their write and return without invalidating the
cache. Concretely for a right-to-erasure flow:

    create → query (warms cache) → purge → query (SAME instance) still returns
    the cached row until TTL expires.

`purge_fact()`'s return string says "row removed from disk; not retrievable"
while the very next in-process query resolves to the pre-purge cached
result. Same shape as the v0.15.5 return-string-lies bug, one layer inside.

Root cause: `create_fact()` and `apply_fact_decay()` both call
`_cache_invalidate("facts:")` after commit. `purge_fact()`,
`invalidate_fact()`, and `supersede_fact()` did not. The pattern existed;
it was skipped on three of the five mutation methods.

Fix (this release):
1. `invalidate_fact()` calls `_cache_invalidate("facts:")` after commit
2. `purge_fact()` calls `_cache_invalidate("facts:")` after commit
3. `supersede_fact()` calls `_cache_invalidate("facts:")` after commit

These tests lock all three by exercising the same-instance query-mutate-query
sequence that the v0.15.6 tests missed (v0.15.6 opened fresh aiosqlite
connections to verify the SQL layer, bypassing the in-memory cache).
"""

from __future__ import annotations

import pytest

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.memory_backend import WorldModelMemoryBackend


SENTINEL = "cacheinvsentinelworthfixing"


@pytest.fixture
async def kg(tmp_path):
    graph = KnowledgeGraph(str(tmp_path))
    await graph.initialize()
    yield graph


@pytest.fixture
async def backend(tmp_path):
    graph = KnowledgeGraph(str(tmp_path))
    await graph.initialize()
    yield WorldModelMemoryBackend(graph)


class TestPurgeFactInvalidatesCacheOnSameInstance:
    """purge_fact must invalidate the query cache. Same-instance repro."""

    @pytest.mark.asyncio
    async def test_purge_result_visible_to_next_same_instance_query(self, backend):
        # 1. Populate a fact through the public path
        await backend._write("erasure/subject.md", f"secret {SENTINEL}")
        fact = await backend._latest_fact_for("erasure/subject.md")
        assert fact is not None

        # 2. Warm the cache with a query (this is the compliance-flow shape:
        #    a right-to-erasure caller usually searches for the subject before
        #    purging and verifies after)
        before = await backend.kg.query_facts(SENTINEL)
        assert len(before.facts) >= 1, "cache-warm query must find the row pre-purge"

        # 3. Purge on the SAME instance
        removed = await backend.kg.purge_fact(fact.id)
        assert removed is True

        # 4. Re-query on the SAME instance. This is where v0.15.6 lied:
        #    the cache still held the pre-purge QueryFactResult.
        after = await backend.kg.query_facts(SENTINEL)
        assert len(after.facts) == 0, (
            "purge_fact must invalidate _cache; if this fails, the same-instance "
            "query returns the cached pre-purge row while the return string "
            "claims 'not retrievable'. Repeats the v0.15.5 return-string-lies bug "
            "one layer in."
        )

    @pytest.mark.asyncio
    async def test_public_purge_return_string_is_honest_same_instance(self, backend):
        """The purge() return string must not lie about in-process retrievability."""
        await backend._write("erasure/subject.md", f"secret {SENTINEL}")
        # Warm cache first
        await backend.kg.query_facts(SENTINEL)

        msg = await backend.purge("erasure/subject.md")
        assert "not retrievable" in msg or "removed from disk" in msg

        # Same-instance query must agree with the return string
        after = await backend.kg.query_facts(SENTINEL)
        assert len(after.facts) == 0, (
            "return string claims 'not retrievable'; a same-instance query MUST "
            "agree, otherwise the return string is lying (v0.15.7 regression)"
        )


class TestInvalidateFactInvalidatesCacheOnSameInstance:
    """invalidate_fact must invalidate the query cache under the default
    current_only=True filter (which excludes rows with invalid_at set)."""

    @pytest.mark.asyncio
    async def test_invalidate_hides_row_under_default_filter_same_instance(
        self, backend,
    ):
        await backend._write("audit/subject.md", f"decision {SENTINEL}")
        fact = await backend._latest_fact_for("audit/subject.md")
        assert fact is not None

        # Warm the cache
        before = await backend.kg.query_facts(SENTINEL)
        assert len(before.facts) >= 1

        # Invalidate on same instance
        await backend.kg.invalidate_fact(fact.id)

        # Default current_only=True filter should now hide the row.
        # Without cache-invalidate this would still surface the stale row.
        after = await backend.kg.query_facts(SENTINEL, current_only=True)
        assert len(after.facts) == 0, (
            "invalidate_fact must invalidate _cache so the default current_only "
            "query filter takes effect immediately on the same instance"
        )

    @pytest.mark.asyncio
    async def test_invalidate_leaves_row_retrievable_under_current_only_false(
        self, backend,
    ):
        """Documents the audit-preserving behavior: invalidated rows are still
        reachable through current_only=False. Complements the cache test above."""
        await backend._write("audit/subject.md", f"decision {SENTINEL}")
        fact = await backend._latest_fact_for("audit/subject.md")
        assert fact is not None

        await backend.kg.invalidate_fact(fact.id)

        # current_only=False should still see the invalidated row
        # (this is the audit-chain semantic).
        after = await backend.kg.query_facts(SENTINEL, current_only=False)
        assert len(after.facts) >= 1, (
            "invalidate_fact is soft-delete by design; current_only=False must "
            "still surface the row for audit-chain reconstruction"
        )


class TestSupersedeFactInvalidatesCacheOnSameInstance:
    """supersede_fact must invalidate the query cache. Sets both status and
    invalid_at, so under current_only=True the row should disappear from
    the same-instance query."""

    @pytest.mark.asyncio
    async def test_supersede_hides_row_under_default_filter_same_instance(self, kg):
        # Seed a fact directly through the low-level create_fact
        # (this is what the v0.7.0 F3 supersession path expects)
        from world_model_server.models import Fact

        fact = Fact(fact_text=f"prior belief {SENTINEL}", confidence=0.9, evidence_path="test/v0157/supersede.md")
        fact_id = await kg.create_fact(fact)

        # Warm cache
        before = await kg.query_facts(SENTINEL)
        assert len(before.facts) >= 1

        # Supersede on same instance
        superseded = await kg.supersede_fact(fact_id, reason="v0157-test")
        assert superseded is True

        # current_only=True must exclude the superseded row on this instance
        after = await kg.query_facts(SENTINEL, current_only=True)
        assert len(after.facts) == 0, (
            "supersede_fact must invalidate _cache; without the fix, the "
            "same-instance current_only query returns the pre-supersede row "
            "and the F3 resolution path is invisible in-process"
        )


class TestExistingCacheInvalidateCallsStillFire:
    """The v0.15.6 fix relied on create_fact + apply_fact_decay already
    invalidating the cache. Lock that this is still true, so a future refactor
    of the cache-invalidate scheme doesn't silently re-open the write-path
    invalidation gap."""

    @pytest.mark.asyncio
    async def test_create_fact_invalidates_cache(self, kg):
        from world_model_server.models import Fact

        # Warm cache with a miss result
        empty = await kg.query_facts("brandnewmarkertoken")
        assert len(empty.facts) == 0

        # Insert a matching fact
        f = Fact(fact_text="a fresh fact brandnewmarkertoken here", confidence=0.9, evidence_path="test/v0157/createcache.md")
        await kg.create_fact(f)

        # Same instance must see it
        hit = await kg.query_facts("brandnewmarkertoken")
        assert len(hit.facts) == 1, (
            "create_fact was expected to call _cache_invalidate('facts:'); if "
            "this fails, the write-path cache-invalidate has regressed and the "
            "v0.15.7 fix loses one of its known-good precedent calls"
        )


class TestCacheInvalidatePrefixScopeCorrect:
    """All mutation paths pass the 'facts:' prefix rather than clearing the
    entire cache. Lock that this narrow scope is preserved so we don't over-clear
    unrelated caches (decisions, constraints)."""

    @pytest.mark.asyncio
    async def test_purge_fact_invalidates_only_facts_prefix(self, kg):
        """Populate a non-facts cache key manually, ensure purge does not clear it."""
        # Set an arbitrary non-facts key
        kg._cache_set("decisions:xyz", "sentinel-value")
        assert kg._cache_get("decisions:xyz") == "sentinel-value"

        # Do a fact mutation
        from world_model_server.models import Fact

        f = Fact(fact_text="unrelated content", confidence=0.9, evidence_path="test/v0157/prefix.md")
        fid = await kg.create_fact(f)
        await kg.purge_fact(fid)

        # The non-facts key must survive the narrow prefix invalidation
        assert kg._cache_get("decisions:xyz") == "sentinel-value", (
            "purge_fact must invalidate ONLY the 'facts:' prefix; if this fails "
            "the mutation is over-clearing unrelated cache lanes"
        )
