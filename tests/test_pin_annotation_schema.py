"""
v0.15.0 pin_annotation (ADR-0001) — Day 1: schema tests.

Tests the annotations table + indexes + constraints created by
KnowledgeGraph._create_annotations_schema(). Scoped strictly to
storage-layer correctness:

  - annotations.db file is created
  - annotations table exists after initialize()
  - 4 indexes exist (session, epoch, range, type)
  - annotation_type CHECK constraint rejects unknown values
  - annotation_type CHECK constraint accepts the three documented values
  - NOT NULL constraints fire on session_id, event_range_start,
    event_range_end, author, rationale
  - get_db_sizes() reports annotations.db

The MCP tool + verifier + Merkle chain integration are Day 2+ per
docs/decisions/0001-pin-annotation-mcp-tool.md. This file does NOT
exercise those paths — they get their own tests as they land.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from world_model_server.knowledge_graph import KnowledgeGraph

VALID_ANNOTATION_TYPES = (
    "human_intervention",
    "human_note",
    "override_justification",
)

EXPECTED_INDEXES = {
    "idx_annotations_session",
    "idx_annotations_epoch",
    "idx_annotations_range",
    "idx_annotations_type",
}


@pytest_asyncio.fixture
async def kg(tmp_path: Path) -> KnowledgeGraph:
    """Fresh KnowledgeGraph in a temp directory, fully initialized."""
    g = KnowledgeGraph(str(tmp_path))
    await g.initialize()
    return g


async def _insert_annotation(
    db: aiosqlite.Connection,
    *,
    id: str | None = None,
    session_id: str | None = "s1",
    event_range_start: str | None = "e1",
    event_range_end: str | None = "e2",
    author: str | None = "alice",
    rationale: str | None = "test rationale",
    annotation_type: str | None = "human_note",
) -> None:
    """Insert a single annotation row. Any field passed as None is
    inserted as NULL so NOT NULL constraint tests can exercise the
    exact rejection path."""
    await db.execute(
        """
        INSERT INTO annotations
        (id, session_id, event_range_start, event_range_end,
         author, rationale, annotation_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            id if id is not None else str(uuid.uuid4()),
            session_id,
            event_range_start,
            event_range_end,
            author,
            rationale,
            annotation_type,
        ),
    )


class TestAnnotationsSchemaExists:
    """After initialize(), the annotations table, its 4 indexes, and
    the on-disk .db file all exist."""

    async def test_annotations_db_file_created(self, kg: KnowledgeGraph) -> None:
        assert kg.annotations_db.exists(), (
            f"annotations.db not created at {kg.annotations_db}"
        )

    async def test_annotations_table_created(
        self, kg: KnowledgeGraph,
    ) -> None:
        async with aiosqlite.connect(kg.annotations_db) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='annotations'"
            )
            row = await cursor.fetchone()
        assert row is not None, (
            "annotations table not created by initialize()"
        )

    async def test_expected_indexes_exist(self, kg: KnowledgeGraph) -> None:
        async with aiosqlite.connect(kg.annotations_db) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='annotations'"
            )
            rows = await cursor.fetchall()
        index_names = {r[0] for r in rows}
        missing = EXPECTED_INDEXES - index_names
        assert not missing, (
            f"missing indexes: {sorted(missing)}. "
            f"Present: {sorted(index_names)}"
        )


class TestAnnotationTypeCheckConstraint:
    """The annotation_type column enforces the closed vocabulary at
    the storage layer. A bad type never reaches the Merkle chain path
    even if a caller-side validator is bypassed."""

    @pytest.mark.parametrize("valid_type", list(VALID_ANNOTATION_TYPES))
    async def test_check_constraint_accepts_known_type(
        self, kg: KnowledgeGraph, valid_type: str,
    ) -> None:
        async with aiosqlite.connect(kg.annotations_db) as db:
            await _insert_annotation(db, annotation_type=valid_type)
            await db.commit()

    @pytest.mark.parametrize(
        "invalid_type",
        [
            "",
            "note",  # abbreviated
            "HUMAN_NOTE",  # wrong case
            "human intervention",  # space instead of underscore
            "human-intervention",  # dash instead of underscore
            "garbage",
            "correction",  # decision_type value, not annotation_type
        ],
    )
    async def test_check_constraint_rejects_unknown_type(
        self, kg: KnowledgeGraph, invalid_type: str,
    ) -> None:
        async with aiosqlite.connect(kg.annotations_db) as db:
            with pytest.raises(sqlite3.IntegrityError):
                await _insert_annotation(db, annotation_type=invalid_type)


class TestAnnotationNotNullConstraints:
    """The five NOT NULL constraints all fire when their field is
    missing. An annotation without any of these five fields is not
    a meaningful audit-trail entry — storage must refuse it."""

    @pytest.mark.parametrize(
        "missing_field",
        [
            "session_id",
            "event_range_start",
            "event_range_end",
            "author",
            "rationale",
        ],
    )
    async def test_not_null_constraint_enforced(
        self, kg: KnowledgeGraph, missing_field: str,
    ) -> None:
        kwargs: dict[str, str | None] = {
            "session_id": "s1",
            "event_range_start": "e1",
            "event_range_end": "e2",
            "author": "alice",
            "rationale": "test rationale",
            "annotation_type": "human_note",
        }
        kwargs[missing_field] = None
        async with aiosqlite.connect(kg.annotations_db) as db:
            with pytest.raises(sqlite3.IntegrityError):
                await _insert_annotation(db, **kwargs)


class TestGetDbSizesIncludesAnnotations:
    """get_db_sizes() now reports 10 databases including annotations.db.
    Locks in the invariant that adding a new DB requires updating both
    the source method AND the annotations count in downstream consumers."""

    async def test_annotations_in_db_sizes(
        self, kg: KnowledgeGraph,
    ) -> None:
        sizes = await kg.get_db_sizes()
        assert "annotations.db" in sizes, (
            f"annotations.db not in get_db_sizes(): {sorted(sizes.keys())}"
        )

    async def test_db_sizes_count_is_10(
        self, kg: KnowledgeGraph,
    ) -> None:
        sizes = await kg.get_db_sizes()
        assert len(sizes) == 10, (
            f"expected 10 DBs in get_db_sizes(), got {len(sizes)}: "
            f"{sorted(sizes.keys())}"
        )


class TestExistingSchemasStillWork:
    """Adding annotations.db must not break the schemas that came
    before. Sanity check that events, decisions, and facts tables
    still initialize cleanly alongside the new table."""

    async def test_events_table_still_exists(
        self, kg: KnowledgeGraph,
    ) -> None:
        async with aiosqlite.connect(kg.events_db) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='events'"
            )
            assert await cursor.fetchone() is not None

    async def test_decisions_table_still_exists(
        self, kg: KnowledgeGraph,
    ) -> None:
        async with aiosqlite.connect(kg.decisions_db) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='decisions'"
            )
            assert await cursor.fetchone() is not None

    async def test_facts_table_still_exists(
        self, kg: KnowledgeGraph,
    ) -> None:
        async with aiosqlite.connect(kg.facts_db) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='facts'"
            )
            assert await cursor.fetchone() is not None
