"""
Knowledge graph storage and operations using SQLite.

Implements temporal fact storage, entity resolution, and relationship tracking.
"""

import aiosqlite
import json
import os
import re
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
    Decision,
    TestOutcome,
)


# FTS5 metacharacters that break MATCH expressions when they appear in raw
# user queries: ? * " - + ( ) : ^ (and the reserved AND/OR/NOT/NEAR keywords).
# Strip them, then quote-wrap each remaining token so anything we missed
# is treated as a literal phrase rather than parsed as an operator.
_FTS5_META = re.compile(r'[?*"()\-+:^]')


def sanitize_fts5_query(query: str) -> str:
    """
    Turn an arbitrary user query into a valid FTS5 MATCH expression.

    Removes FTS5 metacharacters and quote-wraps each remaining token. Reserved
    operator words (AND/OR/NOT/NEAR) are lowercased before quoting so they
    match content instead of being parsed as operators. Returns an empty
    quoted phrase for empty input so the caller still gets a valid expression.
    """
    if not query or not query.strip():
        return '""'
    cleaned = _FTS5_META.sub(' ', query)
    tokens = [t for t in cleaned.split() if t]
    if not tokens:
        return '""'
    reserved = {"AND", "OR", "NOT", "NEAR"}
    quoted: List[str] = []
    for t in tokens:
        if t in reserved:
            t = t.lower()
        # Defensively escape any surviving double quotes by doubling them,
        # per FTS5 phrase-literal escaping rules.
        quoted.append(f'"{t.replace(chr(34), chr(34) + chr(34))}"')
    return ' '.join(quoted)


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
        self.decisions_db = self.db_path / "decisions.db"
        self.outcomes_db = self.db_path / "outcomes.db"
        self.trajectories_db = self.db_path / "trajectories.db"
        self.audit_db = self.db_path / "audit.db"

        # Query cache with TTL (seconds)
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._cache_ttl = 60.0  # 1 minute default

    def _cache_get(self, key: str) -> Optional[Any]:
        """Get a value from cache if not expired."""
        import time as _time
        if key in self._cache:
            ts, val = self._cache[key]
            if _time.time() - ts < self._cache_ttl:
                return val
            del self._cache[key]
        return None

    def _cache_set(self, key: str, value: Any) -> None:
        """Store a value in cache."""
        import time as _time
        self._cache[key] = (_time.time(), value)

    def _cache_invalidate(self, prefix: str = "") -> None:
        """Invalidate cache entries matching prefix, or all if empty."""
        if not prefix:
            self._cache.clear()
        else:
            keys = [k for k in self._cache if k.startswith(prefix)]
            for k in keys:
                del self._cache[k]

    async def initialize(self) -> None:
        """Create database schemas if they don't exist."""
        await self._create_entities_schema()
        await self._create_facts_schema()
        await self._create_relationships_schema()
        await self._create_constraints_schema()
        await self._create_sessions_schema()
        await self._create_events_schema()
        await self._create_decisions_schema()
        await self._create_outcomes_schema()
        await self._create_trajectories_schema()
        await self._create_audit_schema()
        # v0.13 opt-in tamper-evident log. Off by default so existing users
        # do not gain a new table on upgrade without asking. Enable with
        # WORLD_MODEL_AUDIT_LOG=on. See docs/AUDIT_LOG.md.
        if self.tamper_evident_enabled:
            await self._create_tamper_evident_schema()
        await self._run_migrations()

    @property
    def tamper_evident_enabled(self) -> bool:
        """True when v0.13 tamper-evident append-only log is opted in."""
        return os.environ.get("WORLD_MODEL_AUDIT_LOG", "").lower() in {
            "on",
            "1",
            "true",
            "yes",
        }

    async def _create_tamper_evident_schema(self) -> None:
        """Create the v0.13 tamper-evident append-only log schema.

        Table lives in `audit.db` alongside the v0.7.0 `compaction_audit`
        table. Append-only enforced via SQLite BEFORE UPDATE / BEFORE DELETE
        triggers that RAISE(ABORT, ...). The primary tamper defense is still
        the hash chain — this trigger is belt-and-braces at the storage
        layer.
        """
        from . import tamper_evident

        async with aiosqlite.connect(self.audit_db) as db:
            await tamper_evident.create_schema(db)

    async def _maybe_audit_write(
        self, kind: str, row_id: str, payload: dict
    ) -> None:
        """
        Append a tamper-evident log entry for a durable write; close the
        current epoch if the append pushed the unclosed-entry count over
        the threshold.

        No-op when opt-in is off (the common case for existing users). When
        opt-in is on, opens a connection to `audit_db`, appends via the
        chain-hashed primitive in `tamper_evident.append_entry`, and — if
        the epoch is now full — Merkle-trees the epoch's entries, signs
        the root with the on-disk HybridSigner (creating fresh keys on
        first close), and persists the epoch row. Everything happens in a
        single connection so the append + epoch close either both persist
        or neither does.

        Called AFTER the primary write commits. If this raises, the primary
        write has already persisted — the caller sees the exception and can
        surface it. A verifier walking the log later will detect a gap (the
        persisted row has no corresponding audit entry), which is the
        compliance-correct outcome: "a write happened that we cannot prove."

        Payload should be a stable subset of the persisted row — identity
        + purpose-shaped fields, excluding volatile server-side timestamps
        and free-text fields that may contain PII.
        """
        if not self.tamper_evident_enabled:
            return

        from . import tamper_evident

        async with aiosqlite.connect(self.audit_db) as db:
            await tamper_evident.append_entry(db, kind, row_id, payload)
            # Check-on-append epoch close. Time-based close is a v0.14
            # addition; v0.13 is size-based only. The threshold is
            # 1024 by default (env-overridable for tests and operators).
            if await tamper_evident.should_close_epoch(db):
                from . import audit_keys

                signer = audit_keys.load_or_create_signer(self.db_path)
                await tamper_evident.close_epoch(db, signer)
            await db.commit()

    async def _existing_columns(self, db, table: str) -> set:
        """Return set of column names for a table via PRAGMA table_info."""
        cursor = await db.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        return {row[1] for row in rows}

    async def _run_migrations(self) -> None:
        """Apply backward-compatible schema migrations.

        v0.6.0 added transcript pointers (F2) and content_hash dedup (F3).
        v0.7.0 adds source_count and last_confirmed_at for confidence-weighted
        contradiction resolution.
        All ALTERs are idempotent via column existence checks.
        """
        import hashlib as _hashlib

        # facts: add transcript pointers + content_hash + v0.7 confidence weighting
        async with aiosqlite.connect(self.facts_db) as db:
            cols = await self._existing_columns(db, "facts")
            if "transcript_session_id" not in cols:
                await db.execute("ALTER TABLE facts ADD COLUMN transcript_session_id TEXT")
            if "line_start" not in cols:
                await db.execute("ALTER TABLE facts ADD COLUMN line_start INTEGER")
            if "line_end" not in cols:
                await db.execute("ALTER TABLE facts ADD COLUMN line_end INTEGER")
            if "content_hash" not in cols:
                await db.execute("ALTER TABLE facts ADD COLUMN content_hash TEXT")
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_facts_hash ON facts(content_hash)"
                )
            # v0.7.0: confidence weighting fields
            if "source_count" not in cols:
                await db.execute(
                    "ALTER TABLE facts ADD COLUMN source_count INTEGER DEFAULT 1"
                )
            if "last_confirmed_at" not in cols:
                await db.execute(
                    "ALTER TABLE facts ADD COLUMN last_confirmed_at TIMESTAMP"
                )
            # v0.8.0: provenance + decay fields. All NULL-defaulted; no
            # backfill. Behavior on rows with NULL values is identical to
            # v0.7 (see world_model_server/decay.py for the on-read decay
            # function and the spec sketch on anthropics/claude-code#47023).
            if "source_tool" not in cols:
                await db.execute(
                    "ALTER TABLE facts ADD COLUMN source_tool TEXT"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_facts_source_tool ON facts(source_tool)"
                )
            if "confirmer" not in cols:
                await db.execute(
                    "ALTER TABLE facts ADD COLUMN confirmer TEXT"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_facts_confirmer ON facts(confirmer)"
                )
            if "last_decay_at" not in cols:
                await db.execute(
                    "ALTER TABLE facts ADD COLUMN last_decay_at TIMESTAMP"
                )
            # v0.11.1: content-type axis for routing rules vs facts vs
            # procedures. NULL default = legacy row / unclassified; no
            # backfill. See adapters/hermes-memory-provider/README.md and
            # the write-side routing discussion in Hermes #47349 for the
            # architectural motivation.
            if "content_type" not in cols:
                await db.execute(
                    "ALTER TABLE facts ADD COLUMN content_type TEXT"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_facts_content_type ON facts(content_type)"
                )
            # v0.12.2: enterprise memory-governance additions.
            #   influence_state — storage vs planning-influence policy,
            #     distinct from status. NULL = legacy row, treated as
            #     'approved' by planning consumers.
            #   expires_at — hard drop-dead timestamp complementing the
            #     continuous last_decay_at erosion. NULL = never expires.
            # Both additive and NULL-tolerant. See models.py Fact for
            # semantics; consumer wiring (planning-query filter, expiry
            # sweep) lands in a follow-up.
            if "influence_state" not in cols:
                await db.execute(
                    "ALTER TABLE facts ADD COLUMN influence_state TEXT"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_facts_influence_state ON facts(influence_state)"
                )
            if "expires_at" not in cols:
                await db.execute(
                    "ALTER TABLE facts ADD COLUMN expires_at TIMESTAMP"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_facts_expires_at ON facts(expires_at)"
                )
            # Always backfill any NULL content_hash rows (covers post-migration inserts too)
            cursor = await db.execute(
                "SELECT id, fact_text, evidence_path FROM facts WHERE content_hash IS NULL"
            )
            rows = await cursor.fetchall()
            for row_id, fact_text, evidence_path in rows:
                h = _hashlib.sha256(
                    f"{fact_text}|{evidence_path or ''}".encode()
                ).hexdigest()
                await db.execute(
                    "UPDATE facts SET content_hash = ? WHERE id = ?", (h, row_id)
                )
            await db.commit()

        # decisions: add transcript pointers
        async with aiosqlite.connect(self.decisions_db) as db:
            cols = await self._existing_columns(db, "decisions")
            if "transcript_session_id" not in cols:
                await db.execute("ALTER TABLE decisions ADD COLUMN transcript_session_id TEXT")
            if "line_start" not in cols:
                await db.execute("ALTER TABLE decisions ADD COLUMN line_start INTEGER")
            if "line_end" not in cols:
                await db.execute("ALTER TABLE decisions ADD COLUMN line_end INTEGER")
            await db.commit()

        # constraints: add content_hash + backfill
        async with aiosqlite.connect(self.constraints_db) as db:
            cols = await self._existing_columns(db, "constraints")
            if "content_hash" not in cols:
                await db.execute("ALTER TABLE constraints ADD COLUMN content_hash TEXT")
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_constraints_hash ON constraints(content_hash)"
                )
            # Always backfill any NULL content_hash rows
            cursor = await db.execute(
                "SELECT id, rule_name, description FROM constraints WHERE content_hash IS NULL"
            )
            rows = await cursor.fetchall()
            for row_id, rule_name, description in rows:
                h = _hashlib.sha256(
                    f"{rule_name}|{description or ''}".encode()
                ).hexdigest()
                await db.execute(
                    "UPDATE constraints SET content_hash = ? WHERE id = ?", (h, row_id)
                )
            await db.commit()

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
        """Find entities by type and/or name. Also searches file_path for module-level matching."""
        async with aiosqlite.connect(self.entities_db) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM entities WHERE 1=1"
            params: List[Any] = []

            if entity_type:
                query += " AND entity_type = ?"
                params.append(entity_type)
            if name:
                query += " AND (name LIKE ? OR file_path LIKE ?)"
                params.append(f"%{name}%")
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

    async def find_entities_fuzzy(
        self, name: str, threshold: float = 0.6, limit: int = 10
    ) -> List[Entity]:
        """Find entities with approximate name matching using sequence similarity."""
        from difflib import SequenceMatcher

        async with aiosqlite.connect(self.entities_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM entities")
            rows = await cursor.fetchall()

            scored = []
            name_lower = name.lower()
            for row in rows:
                entity_name = row["name"].lower()
                file_path = (row["file_path"] or "").lower()

                # Check name similarity
                name_ratio = SequenceMatcher(None, name_lower, entity_name).ratio()
                # Check if query is a substring
                if name_lower in entity_name or name_lower in file_path:
                    name_ratio = max(name_ratio, 0.8)

                if name_ratio >= threshold:
                    entity = Entity(
                        id=row["id"],
                        entity_type=row["entity_type"],
                        name=row["name"],
                        file_path=row["file_path"],
                        signature=row["signature"],
                        first_seen=datetime.fromisoformat(row["first_seen"]),
                        last_updated=datetime.fromisoformat(row["last_updated"]),
                        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    )
                    scored.append((name_ratio, entity))

            scored.sort(key=lambda x: x[0], reverse=True)
            return [entity for _, entity in scored[:limit]]

    async def entity_exists_for_file(self, file_path: str) -> bool:
        """Check if any entity exists with the given file_path."""
        async with aiosqlite.connect(self.entities_db) as db:
            cursor = await db.execute(
                "SELECT 1 FROM entities WHERE file_path = ? LIMIT 1", (file_path,)
            )
            return await cursor.fetchone() is not None

    async def get_file_entity_updated(self, file_path: str) -> Optional[datetime]:
        """Get the last_updated timestamp for a file entity. Returns None if not found."""
        async with aiosqlite.connect(self.entities_db) as db:
            cursor = await db.execute(
                "SELECT last_updated FROM entities WHERE file_path = ? AND entity_type = 'file' LIMIT 1",
                (file_path,),
            )
            row = await cursor.fetchone()
            if row:
                return datetime.fromisoformat(row[0])
            return None

    async def get_entity_count(self) -> int:
        """Get total number of entities in the graph."""
        async with aiosqlite.connect(self.entities_db) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM entities")
            row = await cursor.fetchone()
            return row[0] if row else 0

    # ============================================================================
    # Relationship Operations
    # ============================================================================

    async def create_relationship(self, relationship: Relationship) -> str:
        """Create or update a relationship between entities."""
        async with aiosqlite.connect(self.relationships_db) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO relationships
                (id, source_entity_id, target_entity_id, relationship_type,
                 weight, first_seen, last_seen, evidence_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    relationship.id,
                    relationship.source_entity_id,
                    relationship.target_entity_id,
                    relationship.relationship_type,
                    relationship.weight,
                    relationship.first_seen.isoformat(),
                    relationship.last_seen.isoformat(),
                    relationship.evidence_count,
                ),
            )
            await db.commit()
        return relationship.id

    # ============================================================================
    # Fact Operations
    # ============================================================================

    async def create_fact(self, fact: Fact) -> str:
        """Create a new fact.

        v0.12.3 extends the INSERT to persist content_type (added in v0.11.1)
        plus the v0.12.2 governance fields (influence_state, expires_at).
        Prior to v0.12.3 these fields lived on the Fact model but never
        reached the DB, so callers that set them saw the value silently
        dropped. All three are nullable and existing callers are unaffected.
        """
        async with aiosqlite.connect(self.facts_db) as db:
            await db.execute(
                """
                INSERT INTO facts
                (id, fact_text, valid_at, invalid_at, status, entity_ids, evidence_type,
                 evidence_path, derived_from, confidence, session_id, created_at,
                 source_count, last_confirmed_at, source_tool, confirmer,
                 content_type, influence_state, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    fact.source_count,
                    fact.last_confirmed_at.isoformat() if fact.last_confirmed_at else None,
                    fact.source_tool,
                    fact.confirmer,
                    fact.content_type,
                    fact.influence_state,
                    fact.expires_at.isoformat() if fact.expires_at else None,
                ),
            )
            await db.commit()
        self._cache_invalidate("facts:")
        await self._maybe_audit_write(
            "fact_create",
            fact.id,
            {
                "id": fact.id,
                "fact_text": fact.fact_text,
                "evidence_type": fact.evidence_type,
                "evidence_path": fact.evidence_path,
                "confidence": fact.confidence,
                "status": fact.status,
                "session_id": fact.session_id,
                "content_type": fact.content_type,
            },
        )
        return fact.id

    async def query_facts(
        self,
        query: str,
        entity_type: Optional[str] = None,
        current_only: bool = True,
        content_type: Optional[str] = None,
    ) -> QueryFactResult:
        """
        Query facts using full-text search. Results are cached for performance.

        Args:
            query: Search query
            entity_type: Filter by entity type
            current_only: Only return facts where invalid_at IS NULL
            content_type: Optional filter by content_type ('rule' / 'fact' /
                'procedure'). NULL rows are always excluded when this is set
                — a NULL row is unclassified and cannot answer a typed query.

        Returns:
            QueryFactResult with matching facts and confidence
        """
        cache_key = f"facts:{query}:{entity_type}:{current_only}:{content_type}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        async with aiosqlite.connect(self.facts_db) as db:
            db.row_factory = aiosqlite.Row

            # Full-text search. Try the raw query first so intentional FTS5
            # operator syntax (Legacy OR async, "phrase match", etc) still
            # works for internal callers. Fall back to a sanitized version
            # if FTS5 rejects the raw query — this covers user-facing queries
            # with metacharacters like `?` that would otherwise crash
            # (v0.12.14 — bug found via TS SDK dogfooding).
            sql_query = """
                SELECT f.* FROM facts f
                JOIN facts_fts fts ON f.rowid = fts.rowid
                WHERE facts_fts MATCH ?
            """
            extra = ""
            extra_params: List[Any] = []
            if current_only:
                extra += " AND f.invalid_at IS NULL"
            if content_type is not None:
                extra += " AND f.content_type = ?"
                extra_params.append(content_type)
            extra += " ORDER BY f.confidence DESC, f.created_at DESC LIMIT 10"

            try:
                cursor = await db.execute(
                    sql_query + extra, [query] + extra_params
                )
                rows = await cursor.fetchall()
            except aiosqlite.OperationalError as e:
                if "fts5" not in str(e).lower():
                    raise
                cursor = await db.execute(
                    sql_query + extra,
                    [sanitize_fts5_query(query)] + extra_params,
                )
                rows = await cursor.fetchall()

            # v0.8.0 F1: apply domain-aware decay on read. Pure
            # computation, no DB writes here; row-level last_decay_at
            # update happens lazily when a fact is materially read by
            # downstream code (e.g. resolve_contradiction).
            from .decay import apply_decay_to_row

            facts = []
            for row in rows:
                row_dict = dict(row) if not isinstance(row, dict) else row
                decayed = apply_decay_to_row(row_dict)
                facts.append(
                    Fact(
                        id=decayed["id"],
                        fact_text=decayed["fact_text"],
                        valid_at=datetime.fromisoformat(decayed["valid_at"]),
                        invalid_at=(
                            datetime.fromisoformat(decayed["invalid_at"])
                            if decayed["invalid_at"]
                            else None
                        ),
                        status=decayed["status"],
                        entity_ids=(
                            json.loads(decayed["entity_ids"])
                            if decayed["entity_ids"]
                            else []
                        ),
                        evidence_type=decayed["evidence_type"],
                        evidence_path=decayed["evidence_path"],
                        derived_from=(
                            json.loads(decayed["derived_from"])
                            if decayed["derived_from"]
                            else None
                        ),
                        confidence=decayed["confidence"],
                        session_id=decayed["session_id"],
                        created_at=datetime.fromisoformat(decayed["created_at"]),
                        source_tool=decayed.get("source_tool"),
                        confirmer=decayed.get("confirmer"),
                        content_type=decayed.get("content_type"),
                        influence_state=decayed.get("influence_state"),
                        expires_at=(
                            datetime.fromisoformat(decayed["expires_at"])
                            if decayed.get("expires_at")
                            else None
                        ),
                    )
                )

            # Calculate overall confidence
            if facts:
                max_confidence = max(f.confidence for f in facts)
                exists = any(f.status in ["canonical", "corroborated"] for f in facts)
            else:
                max_confidence = 0.0
                exists = False

            result = QueryFactResult(exists=exists, facts=facts, confidence=max_confidence)
            self._cache_set(cache_key, result)
            return result

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
                await self._maybe_audit_write(
                    "constraint_update",
                    existing_id,
                    {
                        "id": existing_id,
                        "rule_name": constraint.rule_name,
                        "violation_count": new_count,
                        "description": constraint.description,
                    },
                )
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
                await self._maybe_audit_write(
                    "constraint_create",
                    constraint.id,
                    {
                        "id": constraint.id,
                        "rule_name": constraint.rule_name,
                        "constraint_type": constraint.constraint_type,
                        "description": constraint.description,
                        "severity": constraint.severity,
                    },
                )
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
        await self._maybe_audit_write(
            "event_create",
            event.id,
            {
                "id": event.id,
                "event_type": event.event_type,
                "session_id": event.session_id,
                "entity_id": event.entity_id,
                "tool_name": event.tool_name,
                "success": event.success,
            },
        )
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

    # ============================================================================
    # Decision Operations (v0.4.0)
    # ============================================================================

    async def _create_decisions_schema(self) -> None:
        """Create decisions table and indexes."""
        async with aiosqlite.connect(self.decisions_db) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tool_name TEXT,
                    agent_proposal JSON,
                    human_correction JSON,
                    constraint_learned_id TEXT,
                    file_path TEXT,
                    reasoning TEXT,
                    decision_type TEXT NOT NULL
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_decisions_session ON decisions(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_decisions_file ON decisions(file_path)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_decisions_type ON decisions(decision_type)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_decisions_time ON decisions(timestamp DESC)"
            )
            await db.commit()

    async def record_decision(self, decision: Decision) -> str:
        """Record a decision trace."""
        async with aiosqlite.connect(self.decisions_db) as db:
            await db.execute(
                """
                INSERT INTO decisions
                (id, session_id, timestamp, tool_name, agent_proposal, human_correction,
                 constraint_learned_id, file_path, reasoning, decision_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    decision.id,
                    decision.session_id,
                    decision.timestamp.isoformat(),
                    decision.tool_name,
                    json.dumps(decision.agent_proposal),
                    json.dumps(decision.human_correction),
                    decision.constraint_learned_id,
                    decision.file_path,
                    decision.reasoning,
                    decision.decision_type,
                ),
            )
            await db.commit()
        await self._maybe_audit_write(
            "decision_create",
            decision.id,
            {
                "id": decision.id,
                "session_id": decision.session_id,
                "tool_name": decision.tool_name,
                "decision_type": decision.decision_type,
                "file_path": decision.file_path,
            },
        )
        return decision.id

    async def get_decisions(
        self,
        session_id: Optional[str] = None,
        file_path: Optional[str] = None,
        decision_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Decision]:
        """Get decisions with optional filters."""
        async with aiosqlite.connect(self.decisions_db) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM decisions WHERE 1=1"
            params: List[Any] = []

            if session_id:
                query += " AND session_id = ?"
                params.append(session_id)
            if file_path:
                query += " AND file_path LIKE ?"
                params.append(f"%{file_path}%")
            if decision_type:
                query += " AND decision_type = ?"
                params.append(decision_type)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            return [
                Decision(
                    id=row["id"],
                    session_id=row["session_id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    tool_name=row["tool_name"],
                    agent_proposal=json.loads(row["agent_proposal"]) if row["agent_proposal"] else {},
                    human_correction=json.loads(row["human_correction"]) if row["human_correction"] else {},
                    constraint_learned_id=row["constraint_learned_id"],
                    file_path=row["file_path"],
                    reasoning=row["reasoning"],
                    decision_type=row["decision_type"],
                )
                for row in rows
            ]

    async def get_decision_count(self) -> int:
        """Get total number of decisions."""
        async with aiosqlite.connect(self.decisions_db) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM decisions")
            row = await cursor.fetchone()
            return row[0] if row else 0

    # ============================================================================
    # Test Outcome Operations (v0.4.0)
    # ============================================================================

    async def _create_outcomes_schema(self) -> None:
        """Create test outcomes table."""
        async with aiosqlite.connect(self.outcomes_db) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS test_outcomes (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    test_name TEXT NOT NULL,
                    test_file TEXT,
                    passed BOOLEAN NOT NULL,
                    error_message TEXT,
                    linked_event_ids JSON,
                    linked_file_paths JSON
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_outcomes_session ON test_outcomes(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_outcomes_file ON test_outcomes(test_file)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_outcomes_passed ON test_outcomes(passed)"
            )
            await db.commit()

    async def create_test_outcome(self, outcome: TestOutcome) -> str:
        """Record a test outcome."""
        async with aiosqlite.connect(self.outcomes_db) as db:
            await db.execute(
                """
                INSERT INTO test_outcomes
                (id, session_id, timestamp, test_name, test_file, passed,
                 error_message, linked_event_ids, linked_file_paths)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    outcome.id,
                    outcome.session_id,
                    outcome.timestamp.isoformat(),
                    outcome.test_name,
                    outcome.test_file,
                    outcome.passed,
                    outcome.error_message,
                    json.dumps(outcome.linked_event_ids),
                    json.dumps(outcome.linked_file_paths),
                ),
            )
            await db.commit()
        return outcome.id

    async def get_outcomes_for_file(self, file_path: str, limit: int = 20) -> List[TestOutcome]:
        """Get test outcomes linked to a file."""
        async with aiosqlite.connect(self.outcomes_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM test_outcomes
                   WHERE linked_file_paths LIKE ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (f"%{file_path}%", limit),
            )
            rows = await cursor.fetchall()
            return [
                TestOutcome(
                    id=row["id"],
                    session_id=row["session_id"],
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    test_name=row["test_name"],
                    test_file=row["test_file"],
                    passed=bool(row["passed"]),
                    error_message=row["error_message"],
                    linked_event_ids=json.loads(row["linked_event_ids"]) if row["linked_event_ids"] else [],
                    linked_file_paths=json.loads(row["linked_file_paths"]) if row["linked_file_paths"] else [],
                )
                for row in rows
            ]

    async def get_recent_file_edit_events(self, session_id: str, limit: int = 10) -> List[Event]:
        """Get recent file_edit events for a session."""
        async with aiosqlite.connect(self.events_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM events
                   WHERE session_id = ? AND event_type = 'file_edit'
                   ORDER BY timestamp DESC LIMIT ?""",
                (session_id, limit),
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
    # Trajectory / Co-edit Operations (v0.4.0)
    # ============================================================================

    async def _create_audit_schema(self) -> None:
        """Create compaction audit log table (v0.7.0 F5)."""
        async with aiosqlite.connect(self.audit_db) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS compaction_audit (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    compacted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    pre_compact_tokens INTEGER,
                    post_compact_tokens INTEGER,
                    facts_injected INTEGER DEFAULT 0,
                    constraints_injected INTEGER DEFAULT 0,
                    injection_event TEXT,
                    raw_summary TEXT,
                    metadata JSON
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_session ON compaction_audit(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_when ON compaction_audit(compacted_at)"
            )
            await db.commit()

    async def _create_trajectories_schema(self) -> None:
        """Create co-edit tracking table."""
        async with aiosqlite.connect(self.trajectories_db) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS co_edits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_a TEXT NOT NULL,
                    file_b TEXT NOT NULL,
                    co_edit_count INTEGER DEFAULT 1,
                    last_co_edited TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    session_ids JSON DEFAULT '[]',
                    UNIQUE(file_a, file_b)
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_coedits_a ON co_edits(file_a)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_coedits_b ON co_edits(file_b)"
            )
            await db.commit()

    async def record_co_edits(self, session_id: str) -> int:
        """Analyze session events and record co-edit patterns. Returns pair count."""
        events = await self.get_session_events(session_id)
        edited_files = list(dict.fromkeys(
            e.tool_input.get("file_path", "") or e.entity_id or ""
            for e in events
            if e.event_type == "file_edit" and (e.tool_input.get("file_path") or e.entity_id)
        ))

        # Cap at 20 files to avoid combinatorial explosion
        edited_files = edited_files[:20]

        if len(edited_files) < 2:
            return 0

        pairs_recorded = 0
        async with aiosqlite.connect(self.trajectories_db) as db:
            for i in range(len(edited_files)):
                for j in range(i + 1, len(edited_files)):
                    # Canonical order
                    file_a, file_b = sorted([edited_files[i], edited_files[j]])

                    # Upsert
                    cursor = await db.execute(
                        "SELECT co_edit_count, session_ids FROM co_edits WHERE file_a = ? AND file_b = ?",
                        (file_a, file_b),
                    )
                    row = await cursor.fetchone()

                    if row:
                        count = row[0] + 1
                        session_ids = json.loads(row[1]) if row[1] else []
                        if session_id not in session_ids:
                            session_ids.append(session_id)
                        await db.execute(
                            """UPDATE co_edits
                               SET co_edit_count = ?, last_co_edited = ?, session_ids = ?
                               WHERE file_a = ? AND file_b = ?""",
                            (count, datetime.now().isoformat(), json.dumps(session_ids), file_a, file_b),
                        )
                    else:
                        await db.execute(
                            """INSERT INTO co_edits (file_a, file_b, co_edit_count, last_co_edited, session_ids)
                               VALUES (?, ?, 1, ?, ?)""",
                            (file_a, file_b, datetime.now().isoformat(), json.dumps([session_id])),
                        )
                    pairs_recorded += 1

            await db.commit()
        return pairs_recorded

    async def get_co_edited_files(self, file_path: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get files commonly edited alongside the given file."""
        async with aiosqlite.connect(self.trajectories_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM co_edits
                   WHERE (file_a = ? OR file_b = ?) AND co_edit_count >= 2
                   ORDER BY co_edit_count DESC LIMIT ?""",
                (file_path, file_path, limit),
            )
            rows = await cursor.fetchall()

            results = []
            for row in rows:
                other_file = row["file_b"] if row["file_a"] == file_path else row["file_a"]
                results.append({
                    "file_path": other_file,
                    "co_edit_count": row["co_edit_count"],
                    "last_co_edited": row["last_co_edited"],
                })
            return results

    # ============================================================================
    # v0.5.0: Health, Decay, Contradictions, Promotion helpers
    # ============================================================================

    async def get_orphaned_entities(self, limit: int = 100) -> List[Entity]:
        """Find entities with no facts or relationships referencing them."""
        # Build set of referenced entity IDs from facts.entity_ids and relationships
        referenced: set = set()

        async with aiosqlite.connect(self.facts_db) as db:
            cursor = await db.execute("SELECT entity_ids FROM facts WHERE entity_ids IS NOT NULL")
            rows = await cursor.fetchall()
            for row in rows:
                try:
                    ids = json.loads(row[0]) if row[0] else []
                    referenced.update(ids)
                except (json.JSONDecodeError, TypeError):
                    pass

        async with aiosqlite.connect(self.relationships_db) as db:
            cursor = await db.execute("SELECT source_entity_id, target_entity_id FROM relationships")
            rows = await cursor.fetchall()
            for row in rows:
                referenced.add(row[0])
                referenced.add(row[1])

        # Find entities not in the referenced set
        async with aiosqlite.connect(self.entities_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM entities LIMIT ?", (limit * 5,))
            rows = await cursor.fetchall()

            orphans = []
            for row in rows:
                if row["id"] not in referenced:
                    orphans.append(Entity(
                        id=row["id"],
                        entity_type=row["entity_type"],
                        name=row["name"],
                        file_path=row["file_path"],
                        signature=row["signature"],
                        first_seen=datetime.fromisoformat(row["first_seen"]),
                        last_updated=datetime.fromisoformat(row["last_updated"]),
                        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    ))
                    if len(orphans) >= limit:
                        break
            return orphans

    async def get_stale_facts(self, days: int = 30, limit: int = 100) -> List[Fact]:
        """Find facts older than N days with no re-observation."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        async with aiosqlite.connect(self.facts_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM facts WHERE invalid_at IS NULL AND valid_at < ? ORDER BY valid_at ASC LIMIT ?",
                (cutoff, limit * 3),
            )
            rows = await cursor.fetchall()

            # Filter out facts that have a newer fact with the same evidence_path
            stale = []
            seen_paths: Dict[str, str] = {}
            for row in rows:
                ep = row["evidence_path"]
                if ep in seen_paths:
                    # An older fact for same path, skip
                    continue
                # Check for newer facts with the same evidence_path
                cursor2 = await db.execute(
                    "SELECT 1 FROM facts WHERE evidence_path = ? AND valid_at > ? AND id != ? LIMIT 1",
                    (ep, row["valid_at"], row["id"]),
                )
                if await cursor2.fetchone():
                    continue
                seen_paths[ep] = row["id"]
                stale.append(Fact(
                    id=row["id"],
                    fact_text=row["fact_text"],
                    valid_at=datetime.fromisoformat(row["valid_at"]),
                    invalid_at=None,
                    status=row["status"],
                    entity_ids=json.loads(row["entity_ids"]) if row["entity_ids"] else [],
                    evidence_type=row["evidence_type"],
                    evidence_path=ep,
                    confidence=row["confidence"],
                    session_id=row["session_id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                ))
                if len(stale) >= limit:
                    break
            return stale

    async def find_contradictions(
        self, query: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Find pairs of facts that contradict each other."""
        from difflib import SequenceMatcher

        # Pull candidate facts
        async with aiosqlite.connect(self.facts_db) as db:
            db.row_factory = aiosqlite.Row
            if query:
                fts_sql = """SELECT f.* FROM facts f
                             JOIN facts_fts fts ON f.rowid = fts.rowid
                             WHERE facts_fts MATCH ? LIMIT 200"""
                try:
                    cursor = await db.execute(fts_sql, (query,))
                except aiosqlite.OperationalError as e:
                    if "fts5" not in str(e).lower():
                        raise
                    cursor = await db.execute(fts_sql, (sanitize_fts5_query(query),))
            else:
                cursor = await db.execute("SELECT * FROM facts ORDER BY created_at DESC LIMIT 200")
            rows = await cursor.fetchall()

        # Cap to 200 for O(n^2) scan
        facts_data = [dict(r) for r in rows[:200]]

        contradictions = []
        for i in range(len(facts_data)):
            for j in range(i + 1, len(facts_data)):
                a, b = facts_data[i], facts_data[j]
                # Skip identical facts
                if a["fact_text"] == b["fact_text"]:
                    continue
                ratio = SequenceMatcher(None, a["fact_text"], b["fact_text"]).ratio()
                if ratio < 0.7:
                    continue

                # Check for contradiction signals
                a_entity_ids = set(json.loads(a["entity_ids"]) if a["entity_ids"] else [])
                b_entity_ids = set(json.loads(b["entity_ids"]) if b["entity_ids"] else [])
                entity_overlap = bool(a_entity_ids & b_entity_ids)

                differ_status = a["status"] != b["status"]
                differ_validity = (a["invalid_at"] is None) != (b["invalid_at"] is None)

                if differ_status or differ_validity or (entity_overlap and ratio >= 0.85):
                    reasons = []
                    if differ_status:
                        reasons.append(f"status: {a['status']} vs {b['status']}")
                    if differ_validity:
                        reasons.append("one invalidated, other still valid")
                    if entity_overlap and ratio >= 0.85:
                        reasons.append("same entity, similar text")

                    # v0.7.0: surface confidence + source counts for weighting
                    conf_a = float(a["confidence"]) if a["confidence"] is not None else 1.0
                    conf_b = float(b["confidence"]) if b["confidence"] is not None else 1.0
                    src_a = int(a["source_count"]) if "source_count" in a.keys() and a["source_count"] is not None else 1
                    src_b = int(b["source_count"]) if "source_count" in b.keys() and b["source_count"] is not None else 1

                    contradictions.append({
                        "fact_a_id": a["id"],
                        "fact_b_id": b["id"],
                        "fact_a_text": a["fact_text"],
                        "fact_b_text": b["fact_text"],
                        "similarity_score": round(ratio, 3),
                        "both_valid": a["invalid_at"] is None and b["invalid_at"] is None,
                        "reason": "; ".join(reasons) or "high similarity",
                        "confidence_a": conf_a,
                        "confidence_b": conf_b,
                        "source_count_a": src_a,
                        "source_count_b": src_b,
                    })
                    if len(contradictions) >= limit:
                        return contradictions
        return contradictions

    async def supersede_fact(self, fact_id: str, reason: Optional[str] = None) -> bool:
        """Mark a fact as superseded (v0.7.0 F3 resolution path).

        Sets status='superseded' and invalid_at=now. Returns True if any row was updated.
        """
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.facts_db) as db:
            cursor = await db.execute(
                "UPDATE facts SET status = 'superseded', invalid_at = ? WHERE id = ?",
                (now, fact_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def get_fact_by_id(self, fact_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single fact row by id, returns None if missing."""
        async with aiosqlite.connect(self.facts_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM facts WHERE id = ?", (fact_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def apply_fact_decay(self, days: int = 90) -> int:
        """Mark facts with no re-observation in N days as invalid."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        now = datetime.now().isoformat()

        async with aiosqlite.connect(self.facts_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, evidence_path, entity_ids, valid_at FROM facts WHERE invalid_at IS NULL AND valid_at < ?",
                (cutoff,),
            )
            candidates = await cursor.fetchall()

            decayed = 0
            for row in candidates:
                ep = row["evidence_path"]
                # Check for re-observation by evidence_path
                cursor2 = await db.execute(
                    "SELECT 1 FROM facts WHERE evidence_path = ? AND valid_at > ? AND id != ? LIMIT 1",
                    (ep, row["valid_at"], row["id"]),
                )
                if await cursor2.fetchone():
                    continue

                # Mark invalid
                await db.execute(
                    "UPDATE facts SET invalid_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                decayed += 1

            await db.commit()

        self._cache_invalidate("facts:")
        return decayed

    async def increment_violation_count(self, constraint_id: str) -> int:
        """Increment a constraint's violation count and update last_violated."""
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.constraints_db) as db:
            await db.execute(
                "UPDATE constraints SET violation_count = violation_count + 1, last_violated = ? WHERE id = ?",
                (now, constraint_id),
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT violation_count FROM constraints WHERE id = ?", (constraint_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_constraint_decay_candidates(self, days: int = 30) -> List[Constraint]:
        """Constraints not violated in N days but with non-zero violation count."""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        async with aiosqlite.connect(self.constraints_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM constraints
                   WHERE violation_count > 0
                   AND (last_violated IS NULL OR last_violated < ?)
                   ORDER BY last_violated ASC""",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            return [
                Constraint(
                    id=row["id"],
                    constraint_type=row["constraint_type"],
                    rule_name=row["rule_name"],
                    file_pattern=row["file_pattern"],
                    description=row["description"],
                    violation_count=row["violation_count"],
                    last_violated=datetime.fromisoformat(row["last_violated"]) if row["last_violated"] else None,
                    examples=json.loads(row["examples"]) if row["examples"] else [],
                    severity=row["severity"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
                for row in rows
            ]

    async def get_constraint_by_id(self, constraint_id: str) -> Optional[Constraint]:
        """Fetch a single constraint by id."""
        async with aiosqlite.connect(self.constraints_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM constraints WHERE id = ?", (constraint_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return Constraint(
                id=row["id"],
                constraint_type=row["constraint_type"],
                rule_name=row["rule_name"],
                file_pattern=row["file_pattern"],
                description=row["description"],
                violation_count=row["violation_count"],
                last_violated=datetime.fromisoformat(row["last_violated"]) if row["last_violated"] else None,
                examples=json.loads(row["examples"]) if row["examples"] else [],
                severity=row["severity"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    async def constraint_exists_by_rule_name(self, rule_name: str) -> bool:
        """Check if a constraint with this rule_name exists."""
        async with aiosqlite.connect(self.constraints_db) as db:
            cursor = await db.execute(
                "SELECT 1 FROM constraints WHERE rule_name = ? LIMIT 1", (rule_name,)
            )
            return await cursor.fetchone() is not None

    async def get_recent_decisions_for_file(
        self, file_path: str, limit: int = 5
    ) -> List[Decision]:
        """Get recent decisions touching this file."""
        return await self.get_decisions(file_path=file_path, limit=limit)

    async def get_test_failure_rates(
        self, file_paths: List[str], min_runs: int = 1
    ) -> List[Dict[str, Any]]:
        """Aggregate failure rates per test for the given files."""
        # Collect all outcomes touching any of the files
        outcomes_by_test: Dict[str, List[Dict[str, Any]]] = {}
        for fp in file_paths:
            outs = await self.get_outcomes_for_file(fp, limit=100)
            for o in outs:
                outcomes_by_test.setdefault(o.test_name, []).append({
                    "passed": o.passed,
                    "timestamp": o.timestamp.isoformat(),
                    "test_file": o.test_file,
                })

        results = []
        for test_name, runs in outcomes_by_test.items():
            if len(runs) < min_runs:
                continue
            failures = sum(1 for r in runs if not r["passed"])
            failure_rate = failures / len(runs)
            last_failure = max(
                (r["timestamp"] for r in runs if not r["passed"]),
                default=None,
            )
            results.append({
                "test_name": test_name,
                "failure_rate": round(failure_rate, 3),
                "sample_size": len(runs),
                "last_failure": last_failure,
                "test_file": runs[0].get("test_file"),
            })
        return sorted(results, key=lambda r: r["failure_rate"], reverse=True)

    async def get_db_sizes(self) -> Dict[str, int]:
        """Get file sizes of all 9 databases."""
        dbs = {
            "entities.db": self.entities_db,
            "facts.db": self.facts_db,
            "relationships.db": self.relationships_db,
            "constraints.db": self.constraints_db,
            "sessions.db": self.sessions_db,
            "events.db": self.events_db,
            "decisions.db": self.decisions_db,
            "outcomes.db": self.outcomes_db,
            "trajectories.db": self.trajectories_db,
        }
        return {
            name: path.stat().st_size if path.exists() else 0
            for name, path in dbs.items()
        }

    # ============================================================================
    # v0.6.0: Cross-DB merge for project identity migration (F3)
    # ============================================================================

    async def merge_from(self, other: "KnowledgeGraph") -> Dict[str, int]:
        """Merge another KG's data into this one, deduplicating by content_hash.

        Used by `world-model migrate` to consolidate DBs split across path variants.
        Returns counts of merged vs skipped rows.
        """
        import hashlib

        stats = {
            "facts_merged": 0,
            "facts_skipped": 0,
            "constraints_merged": 0,
            "constraints_skipped": 0,
        }

        # Merge facts (dedup by content_hash)
        async with aiosqlite.connect(other.facts_db) as src_db:
            src_db.row_factory = aiosqlite.Row
            cursor = await src_db.execute("SELECT * FROM facts")
            other_rows = await cursor.fetchall()

        async with aiosqlite.connect(self.facts_db) as dst_db:
            cursor = await dst_db.execute("SELECT content_hash FROM facts WHERE content_hash IS NOT NULL")
            existing_hashes = {row[0] for row in await cursor.fetchall()}

            for row in other_rows:
                fact_text = row["fact_text"]
                evidence_path = row["evidence_path"] or ""
                content_hash = row["content_hash"] or hashlib.sha256(
                    f"{fact_text}|{evidence_path}".encode()
                ).hexdigest()

                if content_hash in existing_hashes:
                    stats["facts_skipped"] += 1
                    continue

                await dst_db.execute(
                    """INSERT OR IGNORE INTO facts
                    (id, fact_text, valid_at, invalid_at, status, entity_ids, evidence_type,
                     evidence_path, derived_from, confidence, session_id, created_at,
                     transcript_session_id, line_start, line_end, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["id"], row["fact_text"], row["valid_at"], row["invalid_at"],
                        row["status"], row["entity_ids"], row["evidence_type"],
                        row["evidence_path"], row["derived_from"], row["confidence"],
                        row["session_id"], row["created_at"],
                        row["transcript_session_id"] if "transcript_session_id" in row.keys() else None,
                        row["line_start"] if "line_start" in row.keys() else None,
                        row["line_end"] if "line_end" in row.keys() else None,
                        content_hash,
                    ),
                )
                existing_hashes.add(content_hash)
                stats["facts_merged"] += 1
            await dst_db.commit()

        # Merge constraints (dedup by content_hash)
        async with aiosqlite.connect(other.constraints_db) as src_db:
            src_db.row_factory = aiosqlite.Row
            cursor = await src_db.execute("SELECT * FROM constraints")
            other_rows = await cursor.fetchall()

        async with aiosqlite.connect(self.constraints_db) as dst_db:
            cursor = await dst_db.execute(
                "SELECT content_hash FROM constraints WHERE content_hash IS NOT NULL"
            )
            existing_hashes = {row[0] for row in await cursor.fetchall()}

            for row in other_rows:
                rule_name = row["rule_name"]
                description = row["description"] or ""
                content_hash = row["content_hash"] or hashlib.sha256(
                    f"{rule_name}|{description}".encode()
                ).hexdigest()

                if content_hash in existing_hashes:
                    stats["constraints_skipped"] += 1
                    continue

                await dst_db.execute(
                    """INSERT OR IGNORE INTO constraints
                    (id, constraint_type, rule_name, file_pattern, description,
                     violation_count, last_violated, examples, severity, created_at, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["id"], row["constraint_type"], row["rule_name"],
                        row["file_pattern"], row["description"], row["violation_count"],
                        row["last_violated"], row["examples"], row["severity"],
                        row["created_at"], content_hash,
                    ),
                )
                existing_hashes.add(content_hash)
                stats["constraints_merged"] += 1
            await dst_db.commit()

        return stats
