"""
v0.11.1: content-type routing schema field tests.

The v0.11.1 patch adds a nullable `content_type` field to the Fact model
and the facts table, distinguishing rules (always-inject), facts
(search-on-demand), and procedures (multi-step workflows). Motivation is
the write-side routing gap surfaced by Hermes #47349: a MemoryProvider
needs an intelligent routing rule per write, and the schema is where
that rule lives.

Regression discipline:
  - Nullable column, NULL default. Existing rows (which have NULL) are
    unaffected. No backfill. Existing code paths that don't consume
    content_type keep working exactly as before.
  - Migration is idempotent: running `_run_migrations` twice on a DB
    that already has the column is a no-op.
  - Full v0.11 pydantic Fact model accepts existing serialized rows
    without the new field.
  - Full v0.11 pydantic Fact model accepts new rows with each of the
    three enum values.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Fact


# ============================================================================
# F1: Fact model accepts and rejects content_type values correctly
# ============================================================================


def test_f1_fact_model_content_type_defaults_to_none():
    """Legacy code that constructs Fact without content_type must still work."""
    f = Fact(fact_text="X", evidence_path="p")
    assert f.content_type is None


def test_f1_fact_model_content_type_accepts_rule():
    f = Fact(fact_text="X", evidence_path="p", content_type="rule")
    assert f.content_type == "rule"


def test_f1_fact_model_content_type_accepts_fact():
    f = Fact(fact_text="X", evidence_path="p", content_type="fact")
    assert f.content_type == "fact"


def test_f1_fact_model_content_type_accepts_procedure():
    f = Fact(fact_text="X", evidence_path="p", content_type="procedure")
    assert f.content_type == "procedure"


def test_f1_fact_model_rejects_unknown_content_type():
    """Literal type enforces the enum; unknown values raise."""
    with pytest.raises(Exception):
        Fact(fact_text="X", evidence_path="p", content_type="not-a-real-type")


def test_f1_fact_model_serialization_round_trip():
    """v0.11 Fact instances round-trip through JSON without losing content_type."""
    original = Fact(fact_text="X", evidence_path="p", content_type="rule")
    serialized = original.model_dump()
    round_tripped = Fact(**serialized)
    assert round_tripped.content_type == "rule"


# ============================================================================
# F2: Migration adds the column exactly once and is idempotent
# ============================================================================


@pytest.mark.asyncio
async def test_f2_migration_adds_content_type_column(tmp_path):
    """A fresh DB gets the content_type column via _run_migrations."""
    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()

    async with aiosqlite.connect(kg.facts_db) as db:
        cursor = await db.execute("PRAGMA table_info(facts)")
        rows = await cursor.fetchall()
        cols = {row[1] for row in rows}

    assert "content_type" in cols


@pytest.mark.asyncio
async def test_f2_migration_is_idempotent(tmp_path):
    """Running initialize twice on the same path does not error and does
    not create duplicate columns."""
    kg1 = KnowledgeGraph(str(tmp_path / "wm"))
    await kg1.initialize()

    # Second initialize: should be a no-op for the content_type column
    kg2 = KnowledgeGraph(str(tmp_path / "wm"))
    await kg2.initialize()

    async with aiosqlite.connect(kg2.facts_db) as db:
        cursor = await db.execute("PRAGMA table_info(facts)")
        rows = await cursor.fetchall()
        matches = [row for row in rows if row[1] == "content_type"]

    assert len(matches) == 1


@pytest.mark.asyncio
async def test_f2_migration_creates_content_type_index(tmp_path):
    """Index on content_type is created (needed for future routing filters)."""
    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()

    async with aiosqlite.connect(kg.facts_db) as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='index'")
        rows = await cursor.fetchall()
        indexes = {row[0] for row in rows}

    assert "idx_facts_content_type" in indexes


# ============================================================================
# F3: NULL content_type on read is well-behaved (backward compatibility)
# ============================================================================


@pytest.mark.asyncio
async def test_f3_existing_rows_without_content_type_still_readable(tmp_path):
    """Legacy rows (inserted without content_type) can be read back and the
    field surfaces as None. This is the load-bearing backward-compat guarantee."""
    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()

    fact = Fact(
        id="legacy-1",
        fact_text="A legacy fact from before v0.11.1",
        evidence_path="p",
    )
    await kg.create_fact(fact)

    round_tripped = await kg.get_fact_by_id("legacy-1")
    assert round_tripped is not None
    # The row exists, and any consumer reading content_type sees None or the
    # absence of the field. The knowledge_graph.get_fact_by_id returns a dict
    # (not a pydantic model), so we check for the key gracefully.
    assert round_tripped.get("content_type") is None
