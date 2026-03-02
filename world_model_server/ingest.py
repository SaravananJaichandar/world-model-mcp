"""
Bridge between hook flat files and the SQLite knowledge graph.

Hooks write events to .jsonl files for speed. This module ingests
those queued events into the SQLite databases so the MCP server
can query them.
"""

import json
import logging
import os
from pathlib import Path
from typing import List, Dict, Any

from .knowledge_graph import KnowledgeGraph
from .models import Event, Session

logger = logging.getLogger(__name__)


async def ingest_queued_events(kg: KnowledgeGraph, db_path: str) -> int:
    """
    Read events from the hooks' events-queue.jsonl and insert them
    into the SQLite events database.

    Args:
        kg: Initialized KnowledgeGraph instance
        db_path: Path to the .claude/world-model/ directory

    Returns:
        Number of events ingested
    """
    queue_file = Path(db_path) / "events-queue.jsonl"

    if not queue_file.exists():
        return 0

    ingested = 0
    failed = 0

    try:
        lines = queue_file.read_text().strip().split("\n")
        events_data = []

        for line in lines:
            if not line.strip():
                continue
            try:
                events_data.append(json.loads(line))
            except json.JSONDecodeError:
                failed += 1
                logger.warning(f"Skipping malformed event line: {line[:100]}")

        for event_data in events_data:
            try:
                event = Event(
                    session_id=event_data.get("session_id", "unknown"),
                    event_type=event_data.get("event_type", "tool_call"),
                    tool_name=event_data.get("evidence", {}).get("tool_name"),
                    tool_input=event_data.get("evidence", {}).get("tool_input", {}),
                    tool_output=event_data.get("evidence", {}).get("tool_output", {}),
                    reasoning=event_data.get("reasoning"),
                    success=event_data.get("success", True),
                )
                await kg.create_event(event)
                ingested += 1
            except Exception as e:
                failed += 1
                logger.warning(f"Failed to ingest event: {e}")

        # Clear the queue after successful ingestion
        queue_file.unlink()
        logger.info(f"Ingested {ingested} events from queue ({failed} failed)")

    except Exception as e:
        logger.error(f"Failed to process events queue: {e}")

    return ingested


async def ingest_session_files(kg: KnowledgeGraph, db_path: str) -> int:
    """
    Read session metadata files written by hooks and insert them
    into the SQLite sessions database.

    Args:
        kg: Initialized KnowledgeGraph instance
        db_path: Path to the .claude/world-model/ directory

    Returns:
        Number of sessions ingested
    """
    world_model_dir = Path(db_path)
    ingested = 0

    for session_file in world_model_dir.glob("session-*.json"):
        try:
            data = json.loads(session_file.read_text())

            session = Session(
                session_id=data.get("session_id", session_file.stem),
                user_request=data.get("user_request"),
                outcome=data.get("outcome"),
            )

            if data.get("ended_at"):
                from datetime import datetime
                session.ended_at = datetime.fromisoformat(data["ended_at"])

            await kg.create_session(session)
            ingested += 1

            # Remove the file after ingestion
            session_file.unlink()

        except Exception as e:
            logger.warning(f"Failed to ingest session file {session_file}: {e}")

    if ingested > 0:
        logger.info(f"Ingested {ingested} session files")

    return ingested
