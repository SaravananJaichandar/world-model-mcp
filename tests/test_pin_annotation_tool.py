"""
v0.15.0 pin_annotation (ADR-0001) — Day 2: MCP tool unit tests.

Exercises WorldModelTools.pin_annotation() in isolation:

  - happy path returns a valid PinAnnotationResult JSON payload
  - annotation_id is a well-formed UUID
  - row lands in annotations.db with expected columns
  - annotation_type validation rejects unknown values
  - the five required string fields reject empty strings
  - rationale byte-length limit (8 KB) is enforced

Chain integration (Merkle epoch signing, DOMAIN_ANNOTATION prefix,
epoch_id + signature_hash population) is Day 3+ per ADR-0001 and
lives in its own test file.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from world_model_server.config import Config
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.tools import (
    PIN_ANNOTATION_MAX_RATIONALE_BYTES,
    WorldModelTools,
)

VALID_TYPES = (
    "human_intervention",
    "human_note",
    "override_justification",
)


@pytest_asyncio.fixture
async def tools(tmp_path: Path) -> WorldModelTools:
    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()
    return WorldModelTools(kg, Config())


def _kwargs(**overrides: object) -> dict[str, object]:
    """Baseline valid arguments; overrides replace individual fields."""
    base: dict[str, object] = {
        "session_id": "sess-1",
        "event_range_start": "evt-1",
        "event_range_end": "evt-2",
        "author": "alice@example.com",
        "rationale": "reviewed migration diff and approved manually",
        "annotation_type": "human_note",
    }
    base.update(overrides)
    return base


class TestPinAnnotationHappyPath:
    """A well-formed call persists a row and returns a valid result."""

    async def test_returns_json_payload_with_expected_fields(
        self, tools: WorldModelTools,
    ) -> None:
        raw = await tools.pin_annotation(**_kwargs())
        payload = json.loads(raw)
        assert set(payload.keys()) == {
            "annotation_id",
            "epoch_id",
            "signed",
            "signature_hash",
        }
        assert payload["signed"] is False
        assert payload["epoch_id"] is None
        assert payload["signature_hash"] is None

    async def test_annotation_id_is_valid_uuid(
        self, tools: WorldModelTools,
    ) -> None:
        raw = await tools.pin_annotation(**_kwargs())
        payload = json.loads(raw)
        uuid.UUID(payload["annotation_id"])

    @pytest.mark.parametrize("annotation_type", list(VALID_TYPES))
    async def test_all_three_valid_types_accepted(
        self, tools: WorldModelTools, annotation_type: str,
    ) -> None:
        raw = await tools.pin_annotation(
            **_kwargs(annotation_type=annotation_type),
        )
        payload = json.loads(raw)
        assert "annotation_id" in payload

    async def test_row_persisted_to_annotations_db(
        self, tools: WorldModelTools,
    ) -> None:
        raw = await tools.pin_annotation(**_kwargs(author="carol"))
        payload = json.loads(raw)
        async with aiosqlite.connect(tools.kg.annotations_db) as db:
            cursor = await db.execute(
                "SELECT session_id, event_range_start, event_range_end, "
                "author, rationale, annotation_type, epoch_id, signature "
                "FROM annotations WHERE id = ?",
                (payload["annotation_id"],),
            )
            row = await cursor.fetchone()
        assert row is not None
        (
            session_id,
            range_start,
            range_end,
            author,
            rationale,
            annotation_type,
            epoch_id,
            signature,
        ) = row
        assert session_id == "sess-1"
        assert range_start == "evt-1"
        assert range_end == "evt-2"
        assert author == "carol"
        assert rationale == "reviewed migration diff and approved manually"
        assert annotation_type == "human_note"
        assert epoch_id is None
        assert signature is None


class TestPinAnnotationValidation:
    """Tool-layer validators run before storage. A bad request never
    reaches insert_annotation() and never allocates an annotation_id."""

    async def test_unknown_annotation_type_rejected(
        self, tools: WorldModelTools,
    ) -> None:
        with pytest.raises(ValueError, match="annotation_type"):
            await tools.pin_annotation(**_kwargs(annotation_type="garbage"))

    async def test_annotation_type_wrong_case_rejected(
        self, tools: WorldModelTools,
    ) -> None:
        with pytest.raises(ValueError, match="annotation_type"):
            await tools.pin_annotation(**_kwargs(annotation_type="HUMAN_NOTE"))

    @pytest.mark.parametrize(
        "field",
        [
            "session_id",
            "event_range_start",
            "event_range_end",
            "author",
            "rationale",
        ],
    )
    async def test_empty_required_field_rejected(
        self, tools: WorldModelTools, field: str,
    ) -> None:
        with pytest.raises(ValueError, match=field):
            await tools.pin_annotation(**_kwargs(**{field: ""}))

    async def test_rationale_exactly_at_limit_accepted(
        self, tools: WorldModelTools,
    ) -> None:
        rationale = "a" * PIN_ANNOTATION_MAX_RATIONALE_BYTES
        raw = await tools.pin_annotation(**_kwargs(rationale=rationale))
        payload = json.loads(raw)
        assert "annotation_id" in payload

    async def test_rationale_one_byte_over_limit_rejected(
        self, tools: WorldModelTools,
    ) -> None:
        rationale = "a" * (PIN_ANNOTATION_MAX_RATIONALE_BYTES + 1)
        with pytest.raises(ValueError, match="rationale"):
            await tools.pin_annotation(**_kwargs(rationale=rationale))

    async def test_rationale_multibyte_utf8_counted_by_bytes_not_chars(
        self, tools: WorldModelTools,
    ) -> None:
        # Each U+00E9 encodes to 2 bytes in UTF-8. Half the byte limit's
        # worth of these characters equals the byte limit; one more
        # character trips the check.
        char = "é"
        half = PIN_ANNOTATION_MAX_RATIONALE_BYTES // 2
        at_limit = char * half
        assert len(at_limit.encode("utf-8")) == PIN_ANNOTATION_MAX_RATIONALE_BYTES
        raw = await tools.pin_annotation(**_kwargs(rationale=at_limit))
        assert "annotation_id" in json.loads(raw)

        over_limit = char * (half + 1)
        assert len(over_limit.encode("utf-8")) > PIN_ANNOTATION_MAX_RATIONALE_BYTES
        with pytest.raises(ValueError, match="rationale"):
            await tools.pin_annotation(**_kwargs(rationale=over_limit))

    async def test_no_row_written_when_validation_fails(
        self, tools: WorldModelTools,
    ) -> None:
        with pytest.raises(ValueError):
            await tools.pin_annotation(**_kwargs(annotation_type="garbage"))
        async with aiosqlite.connect(tools.kg.annotations_db) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM annotations")
            (count,) = await cursor.fetchone()
        assert count == 0
