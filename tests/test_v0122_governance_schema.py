"""
v0.12.2: enterprise memory-governance schema additions.

Adds two nullable, additive fields to the Fact model + facts table:

  influence_state - storage-vs-influence policy: 'observed' / 'pending_review'
                    / 'approved' / 'blocked'. Distinct from `status`, which
                    tracks canonical/superseded lineage. NULL = legacy row,
                    treated as approved by planning consumers.
  expires_at      - hard drop-dead timestamp complementing continuous
                    last_decay_at erosion. NULL = never expires.

Regression discipline (identical to the v0.11.1 content_type pattern):
  - Nullable columns, NULL default. Legacy rows unaffected. No backfill.
  - Migrations idempotent: running _run_migrations twice is a no-op.
  - Pydantic Fact model accepts existing serialized rows without the new
    fields.
  - Pydantic Fact model accepts new rows with each enum value.
  - Consumer wiring (planning-query filter, expiry sweep) is out of scope
    for this patch; only the schema surface is under test here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import aiosqlite
import pytest

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Fact


# ============================================================================
# F1: Fact model accepts and rejects influence_state values correctly
# ============================================================================


def test_f1_fact_model_influence_state_defaults_to_none():
    """Legacy code that constructs Fact without influence_state must still work."""
    f = Fact(fact_text="X", evidence_path="p")
    assert f.influence_state is None


def test_f1_fact_model_influence_state_accepts_observed():
    f = Fact(fact_text="X", evidence_path="p", influence_state="observed")
    assert f.influence_state == "observed"


def test_f1_fact_model_influence_state_accepts_pending_review():
    f = Fact(fact_text="X", evidence_path="p", influence_state="pending_review")
    assert f.influence_state == "pending_review"


def test_f1_fact_model_influence_state_accepts_approved():
    f = Fact(fact_text="X", evidence_path="p", influence_state="approved")
    assert f.influence_state == "approved"


def test_f1_fact_model_influence_state_accepts_blocked():
    f = Fact(fact_text="X", evidence_path="p", influence_state="blocked")
    assert f.influence_state == "blocked"


def test_f1_fact_model_rejects_unknown_influence_state():
    """Literal type enforces the enum; unknown values raise."""
    with pytest.raises(Exception):
        Fact(fact_text="X", evidence_path="p", influence_state="not-a-real-state")


def test_f1_fact_model_influence_state_round_trip():
    """Fact instances round-trip through JSON without losing influence_state."""
    original = Fact(fact_text="X", evidence_path="p", influence_state="pending_review")
    serialized = original.model_dump()
    round_tripped = Fact(**serialized)
    assert round_tripped.influence_state == "pending_review"


# ============================================================================
# F2: Fact model accepts expires_at correctly
# ============================================================================


def test_f2_fact_model_expires_at_defaults_to_none():
    f = Fact(fact_text="X", evidence_path="p")
    assert f.expires_at is None


def test_f2_fact_model_expires_at_accepts_future():
    future = datetime.now() + timedelta(days=30)
    f = Fact(fact_text="X", evidence_path="p", expires_at=future)
    assert f.expires_at == future


def test_f2_fact_model_expires_at_accepts_past():
    """A past expires_at is a valid model value; consumers decide what to do
    with expired rows. The schema does not enforce future-only."""
    past = datetime.now() - timedelta(days=1)
    f = Fact(fact_text="X", evidence_path="p", expires_at=past)
    assert f.expires_at == past


def test_f2_fact_model_expires_at_round_trip():
    when = datetime(2027, 1, 1, 0, 0, 0)
    original = Fact(fact_text="X", evidence_path="p", expires_at=when)
    serialized = original.model_dump()
    round_tripped = Fact(**serialized)
    assert round_tripped.expires_at == when


# ============================================================================
# F3: Migration adds both columns exactly once and is idempotent
# ============================================================================


@pytest.mark.asyncio
async def test_f3_migration_adds_influence_state_column(tmp_path):
    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()

    async with aiosqlite.connect(kg.facts_db) as db:
        cursor = await db.execute("PRAGMA table_info(facts)")
        rows = await cursor.fetchall()
        cols = {row[1] for row in rows}

    assert "influence_state" in cols


@pytest.mark.asyncio
async def test_f3_migration_adds_expires_at_column(tmp_path):
    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()

    async with aiosqlite.connect(kg.facts_db) as db:
        cursor = await db.execute("PRAGMA table_info(facts)")
        rows = await cursor.fetchall()
        cols = {row[1] for row in rows}

    assert "expires_at" in cols


@pytest.mark.asyncio
async def test_f3_migration_is_idempotent(tmp_path):
    """Second initialize is a no-op; column count for the new fields stays 1."""
    kg1 = KnowledgeGraph(str(tmp_path / "wm"))
    await kg1.initialize()

    kg2 = KnowledgeGraph(str(tmp_path / "wm"))
    await kg2.initialize()

    async with aiosqlite.connect(kg2.facts_db) as db:
        cursor = await db.execute("PRAGMA table_info(facts)")
        rows = await cursor.fetchall()

    assert sum(1 for r in rows if r[1] == "influence_state") == 1
    assert sum(1 for r in rows if r[1] == "expires_at") == 1


@pytest.mark.asyncio
async def test_f3_migration_creates_influence_state_index(tmp_path):
    """Index on influence_state is created (needed for the planning-query filter)."""
    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()

    async with aiosqlite.connect(kg.facts_db) as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        rows = await cursor.fetchall()
        indexes = {row[0] for row in rows}

    assert "idx_facts_influence_state" in indexes


@pytest.mark.asyncio
async def test_f3_migration_creates_expires_at_index(tmp_path):
    """Index on expires_at is created (needed for the expiry sweep)."""
    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()

    async with aiosqlite.connect(kg.facts_db) as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        rows = await cursor.fetchall()
        indexes = {row[0] for row in rows}

    assert "idx_facts_expires_at" in indexes


# ============================================================================
# F4: Legacy rows and existing write paths remain untouched
# ============================================================================


@pytest.mark.asyncio
async def test_f4_existing_rows_without_new_fields_still_readable(tmp_path):
    """Rows inserted without the new fields (which is every insert today, since
    create_fact does not write them) come back with NULL for both. Load-bearing
    backward-compat guarantee."""
    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()

    fact = Fact(
        id="legacy-1",
        fact_text="A legacy fact from before v0.12.2",
        evidence_path="p",
    )
    await kg.create_fact(fact)

    round_tripped = await kg.get_fact_by_id("legacy-1")
    assert round_tripped is not None
    assert round_tripped.get("influence_state") is None
    assert round_tripped.get("expires_at") is None


@pytest.mark.asyncio
async def test_f4_content_type_migration_still_intact(tmp_path):
    """v0.11.1's content_type migration must not have regressed when v0.12.2's
    ALTERs were added adjacent to it."""
    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()

    async with aiosqlite.connect(kg.facts_db) as db:
        cursor = await db.execute("PRAGMA table_info(facts)")
        rows = await cursor.fetchall()
        cols = {row[1] for row in rows}

    assert "content_type" in cols
    assert "influence_state" in cols
    assert "expires_at" in cols
