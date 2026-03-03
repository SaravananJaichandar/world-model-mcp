"""
Knowledge graph storage and operations using SQLite.

Implements temporal fact storage, entity resolution, and relationship tracking.
"""

import aiosqlite
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from .models import (
    Entity,
    Fact,
    Relationship,
    Constraint,
    Session,
    Event,
    QueryFactResult,
    BugInfo,
)


class KnowledgeGraph:
    """
    SQLite-based knowledge graph with temporal fact support.

    Manages entities, facts, relationships, constraints, sessions, and events.
    """

    def __init__(self, db_path: str):
        """
        Initialize the knowledge graph.

        Args:
            db_path: Path to the .claude/world-model/ directory
        """
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

        self.entities_db = self.db_path / "entities.db"
        self.facts_db = self.db_path / "facts.db"
        self.relationships_db = self.db_path / "relationships.db"
        self.constraints_db = self.db_path / "constraints.db"
        self.sessions_db = self.db_path / "sessions.db"
        self.events_db = self.db_path / "events.db"

    async def initialize(self) -> None:
        """Create database schemas if they don't exist."""
        await self._create_entities_schema()
        await self._create_facts_schema()
        await self._create_relationships_schema()
        await self._create_constraints_schema()
        await self._create_sessions_schema()
        await self._create_events_schema()

    async def _create_entities_schema(self) -> None:
        """Create entities table and indexes."""
        async with aiosqlite.connect(self.entities_db) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    file_path TEXT,
                    signature TEXT,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata JSON
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_file ON entities(file_path)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)"
            )
            await db.commit()

    async def _create_facts_schema(self) -> None:
        """Create facts table, FTS5 index, and indexes."""
        async with aiosqlite.connect(self.facts_db) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY,
                    fact_text TEXT NOT NULL,
                    valid_at TIMESTAMP NOT NULL,
                    invalid_at TIMESTAMP,
                    status TEXT NOT NULL,
                    entity_ids JSON,
                    evidence_type TEXT,
                    evidence_path TEXT,
                    derived_from JSON,
                    confidence REAL DEFAULT 1.0,
                    session_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_valid ON facts(valid_at, invalid_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_session ON facts(session_id)"
            )

            # Create FTS5 virtual table for full-text search
            await db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                    fact_text,
                    content='facts',
                    content_rowid='rowid'
                )
            """
            )

            # Trigger to keep FTS5 in sync
            await db.execute(
                """
                CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                    INSERT INTO facts_fts(rowid, fact_text) VALUES (new.rowid, new.fact_text);
                END
            """
            )
            await db.execute(
                """
                CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                    DELETE FROM facts_fts WHERE rowid = old.rowid;
                END
            """
            )
            await db.execute(
                """
                CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
                    UPDATE facts_fts SET fact_text = new.fact_text WHERE rowid = new.rowid;
                END
            """
            )

            await db.commit()

    async def _create_relationships_schema(self) -> None:
        """Create relationships table and indexes."""
        async with aiosqlite.connect(self.relationships_db) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS relationships (
                    id TEXT PRIMARY KEY,
                    source_entity_id TEXT NOT NULL,
                    target_entity_id TEXT NOT NULL,
                    relationship_type TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    evidence_count INTEGER DEFAULT 1
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_entity_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_entity_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(relationship_type)"
            )
            await db.commit()

    async def _create_constraints_schema(self) -> None:
        """Create constraints table and indexes."""
        async with aiosqlite.connect(self.constraints_db) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS constraints (
                    id TEXT PRIMARY KEY,
                    constraint_type TEXT NOT NULL,
                    rule_name TEXT NOT NULL,
                    file_pattern TEXT,
                    description TEXT,
                    violation_count INTEGER DEFAULT 0,
                    last_violated TIMESTAMP,
                    examples JSON,
                    severity TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_constraints_type ON constraints(constraint_type)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_constraints_violations ON constraints(violation_count DESC)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_constraints_rule ON constraints(rule_name)"
            )
            await db.commit()

    async def _create_sessions_schema(self) -> None:
        """Create sessions table and indexes."""
        async with aiosqlite.connect(self.sessions_db) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP,
                    user_request TEXT,
                    outcome TEXT,
                    entities_touched JSON,
                    facts_generated INTEGER DEFAULT 0,
                    constraints_learned INTEGER DEFAULT 0
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_time ON sessions(started_at)"
            )
            await db.commit()

    async def _create_events_schema(self) -> None:
        """Create events table and indexes."""
        async with aiosqlite.connect(self.events_db) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    entity_id TEXT,
                    tool_name TEXT,
                    tool_input JSON,
                    tool_output JSON,
                    reasoning TEXT,
                    success BOOLEAN
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id)"
            )
            await db.commit()

    # ============================================================================
    # Entity Operations
    # ============================================================================

    async def create_entity(self, entity: Entity) -> str:
        """Create or update an entity."""
        async with aiosqlite.connect(self.entities_db) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO entities
                (id, entity_type, name, file_path, signature, first_seen, last_updated, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    entity.id,
                    entity.entity_type,
                    entity.name,
                    entity.file_path,
                    entity.signature,
                    entity.first_seen.isoformat(),
                    entity.last_updated.isoformat(),
                    json.dumps(entity.metadata),
                ),
            )
            await db.commit()
        return entity.id

    async def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Retrieve an entity by ID."""
        async with aiosqlite.connect(self.entities_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM entities WHERE id = ?", (entity_id,))
            row = await cursor.fetchone()
            if row:
                return Entity(
                    id=row["id"],
                    entity_type=row["entity_type"],
                    name=row["name"],
                    file_path=row["file_path"],
                    signature=row["signature"],
                    first_seen=datetime.fromisoformat(row["first_seen"]),
                    last_updated=datetime.fromisoformat(row["last_updated"]),
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
        return None

    async def find_entities(
        self, entity_type: Optional[str] = None, name: Optional[str] = None
    ) -> List[Entity]:
        """Find entities by type and/or name."""
        async with aiosqlite.connect(self.entities_db) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM entities WHERE 1=1"
            params: List[Any] = []

            if entity_type:
                query += " AND entity_type = ?"
                params.append(entity_type)
            if name:
                query += " AND name LIKE ?"
                params.append(f"%{name}%")

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            return [
                Entity(
                    id=row["id"],
                    entity_type=row["entity_type"],
                    name=row["name"],
                    file_path=row["file_path"],
                    signature=row["signature"],
                    first_seen=datetime.fromisoformat(row["first_seen"]),
                    last_updated=datetime.fromisoformat(row["last_updated"]),
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
                for row in rows
            ]

    # ============================================================================
    # Fact Operations
    # ============================================================================

    async def create_fact(self, fact: Fact) -> str:
        """Create a new fact."""
        async with aiosqlite.connect(self.facts_db) as db:
            await db.execute(
                """
                INSERT INTO facts
                (id, fact_text, valid_at, invalid_at, status, entity_ids, evidence_type,
                 evidence_path, derived_from, confidence, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    fact.id,
                    fact.fact_text,
                    fact.valid_at.isoformat(),
                    fact.invalid_at.isoformat() if fact.invalid_at else None,
                    fact.status,
                    json.dumps(fact.entity_ids),
                    fact.evidence_type,
                    fact.evidence_path,
                    json.dumps(fact.derived_from) if fact.derived_from else None,
                    fact.confidence,
                    fact.session_id,
                    fact.created_at.isoformat(),
                ),
            )
            await db.commit()
        return fact.id

    async def query_facts(
        self,
        query: str,
        entity_type: Optional[str] = None,
        current_only: bool = True,
    ) -> QueryFactResult:
        """
        Query facts using full-text search.

        Args:
            query: Search query
            entity_type: Filter by entity type
            current_only: Only return facts where invalid_at IS NULL

        Returns:
            QueryFactResult with matching facts and confidence
        """
        async with aiosqlite.connect(self.facts_db) as db:
            db.row_factory = aiosqlite.Row

            # Full-text search
            sql_query = """
                SELECT f.* FROM facts f
                JOIN facts_fts fts ON f.rowid = fts.rowid
                WHERE facts_fts MATCH ?
            """
            params: List[Any] = [query]

            if current_only:
                sql_query += " AND f.invalid_at IS NULL"

            sql_query += " ORDER BY f.confidence DESC, f.created_at DESC LIMIT 10"

            cursor = await db.execute(sql_query, params)
            rows = await cursor.fetchall()

            facts = [
                Fact(
                    id=row["id"],
                    fact_text=row["fact_text"],
                    valid_at=datetime.fromisoformat(row["valid_at"]),
                    invalid_at=(
                        datetime.fromisoformat(row["invalid_at"]) if row["invalid_at"] else None
                    ),
                    status=row["status"],
                    entity_ids=json.loads(row["entity_ids"]) if row["entity_ids"] else [],
                    evidence_type=row["evidence_type"],
                    evidence_path=row["evidence_path"],
                    derived_from=(
                        json.loads(row["derived_from"]) if row["derived_from"] else None
                    ),
                    confidence=row["confidence"],
                    session_id=row["session_id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

            # Calculate overall confidence
            if facts:
                max_confidence = max(f.confidence for f in facts)
                exists = any(f.status in ["canonical", "corroborated"] for f in facts)
            else:
                max_confidence = 0.0
                exists = False

            return QueryFactResult(exists=exists, facts=facts, confidence=max_confidence)

    async def invalidate_fact(self, fact_id: str, invalid_at: Optional[datetime] = None) -> None:
        """Mark a fact as no longer true."""
        if invalid_at is None:
            invalid_at = datetime.now()

        async with aiosqlite.connect(self.facts_db) as db:
            await db.execute(
                "UPDATE facts SET invalid_at = ? WHERE id = ?", (invalid_at.isoformat(), fact_id)
            )
            await db.commit()

    # ============================================================================
    # Constraint Operations
    # ============================================================================

    async def create_or_update_constraint(self, constraint: Constraint) -> str:
        """Create or update a constraint."""
        async with aiosqlite.connect(self.constraints_db) as db:
            # Check if constraint exists
            cursor = await db.execute(
                "SELECT id, violation_count, examples FROM constraints WHERE rule_name = ?",
                (constraint.rule_name,),
            )
            existing = await cursor.fetchone()

            if existing:
                # Update existing constraint
                existing_id, existing_count, existing_examples = existing
                new_count = existing_count + 1
                all_examples = json.loads(existing_examples) if existing_examples else []
                all_examples.extend(constraint.examples)

                await db.execute(
                    """
                    UPDATE constraints
                    SET violation_count = ?,
                        last_violated = ?,
                        examples = ?,
                        description = ?
                    WHERE id = ?
                """,
                    (
                        new_count,
                        datetime.now().isoformat(),
                        json.dumps(all_examples),
                        constraint.description,
                        existing_id,
                    ),
                )
                await db.commit()
                return existing_id
            else:
                # Create new constraint
                await db.execute(
                    """
                    INSERT INTO constraints
                    (id, constraint_type, rule_name, file_pattern, description,
                     violation_count, last_violated, examples, severity, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        constraint.id,
                        constraint.constraint_type,
                        constraint.rule_name,
                        constraint.file_pattern,
                        constraint.description,
                        constraint.violation_count,
                        (
                            constraint.last_violated.isoformat()
                            if constraint.last_violated
                            else None
                        ),
                        json.dumps(constraint.examples),
                        constraint.severity,
                        constraint.created_at.isoformat(),
                    ),
                )
                await db.commit()
                return constraint.id

    @staticmethod
    def _glob_match(path: str, pattern: str) -> bool:
        """Match a file path against a glob pattern, supporting ** for recursive dirs."""
        from fnmatch import fnmatch
        if '**' in pattern:
            # ** matches zero or more directory levels
            # e.g. src/api/**/*.ts should match src/api/users.ts AND src/api/v2/users.ts
            flat_pattern = pattern.replace('**/', '')
            recursive_pattern = pattern.replace('**', '*')
            return fnmatch(path, flat_pattern) or fnmatch(path, recursive_pattern)
        return fnmatch(path, pattern)

    async def get_constraints(self, file_path: Optional[str] = None) -> List[Constraint]:
        """Get constraints, optionally filtered by file pattern using glob matching."""

        async with aiosqlite.connect(self.constraints_db) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute(
                "SELECT * FROM constraints ORDER BY violation_count DESC"
            )

            rows = await cursor.fetchall()
            constraints = [
                Constraint(
                    id=row["id"],
                    constraint_type=row["constraint_type"],
                    rule_name=row["rule_name"],
                    file_pattern=row["file_pattern"],
                    description=row["description"],
                    violation_count=row["violation_count"],
                    last_violated=(
                        datetime.fromisoformat(row["last_violated"]) if row["last_violated"] else None
                    ),
                    examples=json.loads(row["examples"]) if row["examples"] else [],
                    severity=row["severity"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

            if file_path:
                constraints = [
                    c for c in constraints
                    if c.file_pattern is None or self._glob_match(file_path, c.file_pattern)
                ]

            return constraints

    # ============================================================================
    # Session Operations
    # ============================================================================

    async def create_session(self, session: Session) -> str:
        """Create a new session."""
        async with aiosqlite.connect(self.sessions_db) as db:
            await db.execute(
                """
                INSERT INTO sessions
                (session_id, started_at, ended_at, user_request, outcome,
                 entities_touched, facts_generated, constraints_learned)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    session.session_id,
                    session.started_at.isoformat(),
                    session.ended_at.isoformat() if session.ended_at else None,
                    session.user_request,
                    session.outcome,
                    json.dumps(session.entities_touched),
                    session.facts_generated,
                    session.constraints_learned,
                ),
            )
            await db.commit()
        return session.session_id

    async def update_session(self, session: Session) -> None:
        """Update an existing session."""
        async with aiosqlite.connect(self.sessions_db) as db:
            await db.execute(
                """
                UPDATE sessions
                SET ended_at = ?,
                    outcome = ?,
                    entities_touched = ?,
                    facts_generated = ?,
                    constraints_learned = ?
                WHERE session_id = ?
            """,
                (
                    session.ended_at.isoformat() if session.ended_at else None,
                    session.outcome,
                    json.dumps(session.entities_touched),
                    session.facts_generated,
                    session.constraints_learned,
                    session.session_id,
                ),
            )
            await db.commit()

    # ============================================================================
    # Event Operations
    # ============================================================================

    async def create_event(self, event: Event) -> str:
        """Create a new event."""
        async with aiosqlite.connect(self.events_db) as db:
            await db.execute(
                """
                INSERT INTO events
                (id, session_id, event_type, timestamp, entity_id, tool_name,
                 tool_input, tool_output, reasoning, success)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    event.id,
                    event.session_id,
                    event.event_type,
                    event.timestamp.isoformat(),
                    event.entity_id,
                    event.tool_name,
                    json.dumps(event.tool_input),
                    json.dumps(event.tool_output),
                    event.reasoning,
                    event.success,
                ),
            )
            await db.commit()
        return event.id

    async def get_session_events(self, session_id: str) -> List[Event]:
        """Get all events for a session."""
        async with aiosqlite.connect(self.events_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp",
                (session_id,),
            )
            rows = await cursor.fetchall()

            return [
                Event(
                    id=row["id"],
                    session_id=row["session_id"],
                    event_type=row["event_type"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    entity_id=row["entity_id"],
                    tool_name=row["tool_name"],
                    tool_input=json.loads(row["tool_input"]) if row["tool_input"] else {},
                    tool_output=json.loads(row["tool_output"]) if row["tool_output"] else {},
                    reasoning=row["reasoning"],
                    success=bool(row["success"]),
                )
                for row in rows
            ]

    # ============================================================================
    # Bug/Regression Tracking
    # ============================================================================

    async def get_bugs_for_file(self, file_path: str) -> List[BugInfo]:
        """Get all bug fixes for a file."""
        async with aiosqlite.connect(self.facts_db) as db:
            db.row_factory = aiosqlite.Row

            # Find facts with evidence_type = 'bug_fix' that mention this file
            cursor = await db.execute(
                """
                SELECT * FROM facts
                WHERE evidence_type = 'bug_fix'
                  AND evidence_path LIKE ?
                  AND invalid_at IS NULL
                ORDER BY created_at DESC
            """,
                (f"%{file_path}%",),
            )
            rows = await cursor.fetchall()

            bugs = []
            for row in rows:
                # Extract critical regions from evidence_path (format: file:lines)
                evidence_path = row["evidence_path"]
                critical_regions = []
                if ":" in evidence_path:
                    _, lines = evidence_path.split(":", 1)
                    critical_regions.append({"file": file_path, "lines": lines})

                bugs.append(
                    BugInfo(
                        bug_id=row["id"],
                        description=row["fact_text"],
                        fixed_at=datetime.fromisoformat(row["valid_at"]),
                        critical_regions=critical_regions,
                        evidence_path=evidence_path,
                    )
                )

            return bugs
