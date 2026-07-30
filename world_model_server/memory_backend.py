"""
WorldModelMemoryBackend - drop-in memory backend for Anthropic SDK.

Subclasses anthropic's BetaAbstractMemoryTool when available, allowing
world-model-mcp to be plugged in as the structured memory layer underneath
Anthropic's pluggable memory tool API.

If the SDK version doesn't ship BetaAbstractMemoryTool, this module exposes
the implementation logic via a plain class so callers can adapt. Bumping the
anthropic dependency to a version with BetaAbstractMemoryTool is optional.

Storage convention:
    path = "/memories/<key>"  ->  Fact with evidence_path = "memory:<key>"
"""

import json
import logging
from datetime import datetime
from typing import Any, List, Optional

from .knowledge_graph import KnowledgeGraph
from .models import Fact

logger = logging.getLogger(__name__)


# Try to import the SDK base class. Fall back to object if unavailable
# so the module always imports cleanly.
try:
    from anthropic.lib.tools import BetaAbstractMemoryTool  # type: ignore
    HAS_SDK_BASE = True
except ImportError:
    BetaAbstractMemoryTool = object  # type: ignore
    HAS_SDK_BASE = False


def _normalize_path(path: str) -> str:
    """Normalize a memory path to a stable key."""
    p = path.strip()
    if p.startswith("/memories/"):
        p = p[len("/memories/"):]
    return p


class WorldModelMemoryBackend(BetaAbstractMemoryTool):  # type: ignore
    """Memory backend backed by world-model-mcp KG.

    Implements the file-style memory tool interface (view/create/str_replace/
    insert/delete/rename). Memory entries are persisted as Facts in facts.db
    with evidence_path = "memory:<key>".

    Multiple writes to the same path create a new canonical Fact and invalidate
    the previous one (temporal versioning). `view` returns the most recent.
    """

    def __init__(self, kg: KnowledgeGraph, session_id: Optional[str] = None):
        if HAS_SDK_BASE:
            super().__init__()
        self.kg = kg
        self.session_id = session_id or "memory-backend"

    @staticmethod
    def has_sdk_base() -> bool:
        """Return True if the underlying anthropic SDK provides BetaAbstractMemoryTool."""
        return HAS_SDK_BASE

    async def _latest_fact_for(self, key: str) -> Optional[Fact]:
        """Return the most recent valid Fact at this memory key."""
        evidence_path = f"memory:{key}"
        try:
            result = await self.kg.query_facts(evidence_path, current_only=True)
            for f in result.facts:
                if f.evidence_path == evidence_path:
                    return f
        except Exception:
            pass
        # Fallback: direct DB scan
        import aiosqlite
        async with aiosqlite.connect(self.kg.facts_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM facts
                   WHERE evidence_path = ? AND invalid_at IS NULL
                   ORDER BY valid_at DESC LIMIT 1""",
                (evidence_path,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return Fact(
                id=row["id"],
                fact_text=row["fact_text"],
                valid_at=datetime.fromisoformat(row["valid_at"]),
                invalid_at=None,
                status=row["status"],
                entity_ids=json.loads(row["entity_ids"]) if row["entity_ids"] else [],
                evidence_type=row["evidence_type"],
                evidence_path=row["evidence_path"],
                confidence=row["confidence"],
                session_id=row["session_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )

    async def _write(self, key: str, content: str) -> str:
        """Create a new Fact at this key, invalidating any existing one."""
        existing = await self._latest_fact_for(key)
        if existing:
            await self.kg.invalidate_fact(existing.id)

        fact = Fact(
            fact_text=content,
            evidence_type="session",
            evidence_path=f"memory:{key}",
            confidence=1.0,
            status="canonical",
            session_id=self.session_id,
        )
        await self.kg.create_fact(fact)
        return fact.id

    async def view(self, path: str, view_range: Optional[List[int]] = None) -> str:
        """Read the memory at a path. view_range is [start_line, end_line], 1-indexed."""
        key = _normalize_path(path)
        fact = await self._latest_fact_for(key)
        if not fact:
            return ""

        content = fact.fact_text
        if view_range:
            lines = content.splitlines(keepends=True)
            start = max(0, (view_range[0] or 1) - 1)
            end = view_range[1] if len(view_range) > 1 else len(lines)
            content = "".join(lines[start:end])
        return content

    async def create(self, path: str, file_text: str) -> str:
        """Create or overwrite memory at a path."""
        key = _normalize_path(path)
        fid = await self._write(key, file_text)
        return f"Created memory at {path} (fact_id={fid})"

    async def str_replace(self, path: str, old_str: str, new_str: str) -> str:
        """Replace a string in memory."""
        key = _normalize_path(path)
        fact = await self._latest_fact_for(key)
        if not fact:
            raise FileNotFoundError(f"No memory at {path}")
        if old_str not in fact.fact_text:
            raise ValueError(f"old_str not found in {path}")
        new_content = fact.fact_text.replace(old_str, new_str, 1)
        await self._write(key, new_content)
        return f"Replaced in {path}"

    async def insert(self, path: str, insert_line: int, insert_text: str) -> str:
        """Insert text at a 1-indexed line."""
        key = _normalize_path(path)
        fact = await self._latest_fact_for(key)
        existing = fact.fact_text if fact else ""
        lines = existing.splitlines(keepends=True)
        idx = max(0, min(insert_line - 1, len(lines)))
        if not insert_text.endswith("\n") and idx < len(lines):
            insert_text += "\n"
        lines.insert(idx, insert_text)
        new_content = "".join(lines)
        await self._write(key, new_content)
        return f"Inserted into {path} at line {insert_line}"

    async def delete(self, path: str) -> str:
        """Invalidate memory at a path (soft-delete).

        Marks the underlying Fact as no longer true (`invalid_at` timestamp
        is set) but leaves the row on disk. The fact remains retrievable via
        any query that doesn't filter on `invalid_at`, which is intentional
        for audit-preserving lifecycle semantics: the chain of what the
        system ever believed should not be silently rewritten.

        Callers who need actual row-level erasure (GDPR right-to-erasure,
        HIPAA purge, ephemeral credential cleanup) must call `purge`
        instead. See DanceNitra/agora openclaw#37 for the measured
        distinction that motivated the two-primitive design.
        """
        key = _normalize_path(path)
        fact = await self._latest_fact_for(key)
        if not fact:
            return f"No memory at {path}"
        await self.kg.invalidate_fact(fact.id)
        return f"Invalidated memory at {path} (fact remains retrievable; call purge() for row-level erasure)"

    async def purge(self, path: str) -> str:
        """Physically remove memory at a path (hard-delete).

        Distinct from `delete` / `invalidate_fact`. This calls
        `knowledge_graph.purge_fact` which issues `DELETE FROM facts` and
        fires the `facts_fts` sync trigger, removing the row from both
        the primary table AND the full-text-search index. After this
        returns, no query path can surface the fact again.

        Use for GDPR Article 17 right-to-erasure requests, HIPAA retention
        purges, ephemeral credential cleanup, or any case where "the fact
        must not remain retrievable" is the compliance requirement rather
        than "mark invalid."

        Regression lock: DanceNitra/agora openclaw#37 measured that
        `delete()` was retrievability-after-delete because it only
        invalidated. This method is the purpose-built erase primitive
        that matches their `forget_subject` shape.
        """
        key = _normalize_path(path)
        fact = await self._latest_fact_for(key)
        if not fact:
            return f"No memory at {path}"
        removed = await self.kg.purge_fact(fact.id)
        if removed:
            return f"Purged memory at {path} (row removed from disk; not retrievable)"
        return f"No memory at {path} (already absent)"

    async def rename(self, old_path: str, new_path: str) -> str:
        """Move memory from old_path to new_path."""
        old_key = _normalize_path(old_path)
        new_key = _normalize_path(new_path)
        fact = await self._latest_fact_for(old_key)
        if not fact:
            raise FileNotFoundError(f"No memory at {old_path}")
        await self._write(new_key, fact.fact_text)
        await self.kg.invalidate_fact(fact.id)
        return f"Renamed {old_path} -> {new_path}"
