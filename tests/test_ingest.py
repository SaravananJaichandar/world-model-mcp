"""
Tests for the ingest bridge module (hooks flat files -> SQLite).
"""

import pytest
import tempfile
import shutil
import json
from pathlib import Path
from datetime import datetime

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.ingest import ingest_queued_events, ingest_session_files


@pytest.fixture
async def kg():
    """Create a temporary knowledge graph for testing."""
    temp_dir = tempfile.mkdtemp()
    kg = KnowledgeGraph(temp_dir)
    await kg.initialize()
    yield kg
    shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_ingest_no_queue_file(kg):
    """Returns 0 when no events-queue.jsonl exists."""
    result = await ingest_queued_events(kg, kg.db_path)
    assert result == 0


@pytest.mark.asyncio
async def test_ingest_empty_queue(kg):
    """Returns 0 when queue file is empty."""
    queue_file = kg.db_path / "events-queue.jsonl"
    queue_file.write_text("")
    result = await ingest_queued_events(kg, str(kg.db_path))
    assert result == 0


@pytest.mark.asyncio
async def test_ingest_valid_events(kg):
    """Ingests valid events from queue file into SQLite."""
    events = [
        {
            "session_id": "test-session-1",
            "event_type": "file_edit",
            "evidence": {
                "tool_name": "Edit",
                "tool_input": {"file_path": "src/app.ts"},
                "tool_output": {"success": True},
            },
            "reasoning": "Editing app file",
            "success": True,
        },
        {
            "session_id": "test-session-1",
            "event_type": "tool_call",
            "evidence": {
                "tool_name": "Bash",
                "tool_input": {"command": "npm test"},
                "tool_output": {"exit_code": 0},
            },
            "success": True,
        },
    ]

    queue_file = kg.db_path / "events-queue.jsonl"
    queue_file.write_text("\n".join(json.dumps(e) for e in events))

    result = await ingest_queued_events(kg, str(kg.db_path))
    assert result == 2

    # Queue file should be deleted after ingestion
    assert not queue_file.exists()

    # Events should be in SQLite
    session_events = await kg.get_session_events("test-session-1")
    assert len(session_events) == 2
    assert session_events[0].event_type == "file_edit"
    assert session_events[0].tool_name == "Edit"
    assert session_events[1].event_type == "tool_call"


@pytest.mark.asyncio
async def test_ingest_malformed_lines(kg):
    """Skips malformed JSON lines and ingests valid ones."""
    lines = [
        json.dumps({"session_id": "s1", "event_type": "file_edit"}),
        "this is not json",
        json.dumps({"session_id": "s1", "event_type": "tool_call"}),
        "{broken json",
    ]

    queue_file = kg.db_path / "events-queue.jsonl"
    queue_file.write_text("\n".join(lines))

    result = await ingest_queued_events(kg, str(kg.db_path))
    assert result == 2  # Only 2 valid events ingested


@pytest.mark.asyncio
async def test_ingest_events_default_fields(kg):
    """Events with missing fields get defaults."""
    events = [{"some_field": "some_value"}]

    queue_file = kg.db_path / "events-queue.jsonl"
    queue_file.write_text(json.dumps(events[0]))

    result = await ingest_queued_events(kg, str(kg.db_path))
    assert result == 1

    session_events = await kg.get_session_events("unknown")
    assert len(session_events) == 1
    assert session_events[0].session_id == "unknown"
    assert session_events[0].event_type == "tool_call"


@pytest.mark.asyncio
async def test_ingest_no_session_files(kg):
    """Returns 0 when no session files exist."""
    result = await ingest_session_files(kg, str(kg.db_path))
    assert result == 0


@pytest.mark.asyncio
async def test_ingest_session_files(kg):
    """Ingests session metadata files into SQLite."""
    session_data = {
        "session_id": "sess-abc-123",
        "user_request": "Build the auth module",
        "outcome": "success",
        "ended_at": "2026-03-01T10:30:00",
    }

    session_file = kg.db_path / "session-abc-123.json"
    session_file.write_text(json.dumps(session_data))

    result = await ingest_session_files(kg, str(kg.db_path))
    assert result == 1

    # Session file should be deleted after ingestion
    assert not session_file.exists()


@pytest.mark.asyncio
async def test_ingest_multiple_session_files(kg):
    """Ingests multiple session files."""
    for i in range(3):
        session_data = {
            "session_id": f"sess-{i}",
            "user_request": f"Task {i}",
        }
        session_file = kg.db_path / f"session-{i}.json"
        session_file.write_text(json.dumps(session_data))

    result = await ingest_session_files(kg, str(kg.db_path))
    assert result == 3


@pytest.mark.asyncio
async def test_ingest_session_without_ended_at(kg):
    """Sessions without ended_at are ingested without error."""
    session_data = {
        "session_id": "sess-active",
        "user_request": "Still working",
    }

    session_file = kg.db_path / "session-active.json"
    session_file.write_text(json.dumps(session_data))

    result = await ingest_session_files(kg, str(kg.db_path))
    assert result == 1


@pytest.mark.asyncio
async def test_ingest_corrupted_session_file(kg):
    """Corrupted session files are skipped."""
    # Valid session
    valid = kg.db_path / "session-good.json"
    valid.write_text(json.dumps({"session_id": "good"}))

    # Corrupted session
    bad = kg.db_path / "session-bad.json"
    bad.write_text("not valid json at all")

    result = await ingest_session_files(kg, str(kg.db_path))
    assert result == 1  # Only the valid one
