"""
Compaction audit log (v0.7.0 F5).

Records each context-compaction event the agent passes through, with token
counts pre/post, the number of facts/constraints re-injected by the
PostCompact hook, and a short summary blob. Lets developers see what was
remembered and what was lost across compaction boundaries.

Public surface:
- record_compaction(kg, ...)     : insert a row
- list_compactions(kg, ...)      : query recent rows
- export_jsonl(kg, out_path, ...): dump rows to a JSONL file
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from .models import CompactionAuditEntry, generate_id


async def record_compaction(
    kg,
    session_id: Optional[str] = None,
    pre_compact_tokens: Optional[int] = None,
    post_compact_tokens: Optional[int] = None,
    facts_injected: int = 0,
    constraints_injected: int = 0,
    injection_event: Optional[str] = None,
    raw_summary: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> CompactionAuditEntry:
    """Insert a compaction audit row. Returns the inserted entry."""
    entry = CompactionAuditEntry(
        id=generate_id(),
        session_id=session_id,
        compacted_at=datetime.now(),
        pre_compact_tokens=pre_compact_tokens,
        post_compact_tokens=post_compact_tokens,
        facts_injected=facts_injected,
        constraints_injected=constraints_injected,
        injection_event=injection_event,
        raw_summary=raw_summary,
        metadata=metadata or {},
    )
    async with aiosqlite.connect(kg.audit_db) as db:
        await db.execute(
            """
            INSERT INTO compaction_audit
              (id, session_id, compacted_at, pre_compact_tokens, post_compact_tokens,
               facts_injected, constraints_injected, injection_event, raw_summary, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry.session_id,
                entry.compacted_at.isoformat(),
                entry.pre_compact_tokens,
                entry.post_compact_tokens,
                entry.facts_injected,
                entry.constraints_injected,
                entry.injection_event,
                entry.raw_summary,
                json.dumps(entry.metadata),
            ),
        )
        await db.commit()
    return entry


async def list_compactions(
    kg,
    session_id: Optional[str] = None,
    limit: int = 50,
) -> List[CompactionAuditEntry]:
    """List recent compaction audit rows. Most-recent first."""
    query = "SELECT * FROM compaction_audit"
    params: tuple = ()
    if session_id:
        query += " WHERE session_id = ?"
        params = (session_id,)
    query += " ORDER BY compacted_at DESC LIMIT ?"
    params = params + (limit,)

    async with aiosqlite.connect(kg.audit_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

    entries: List[CompactionAuditEntry] = []
    for row in rows:
        meta_raw = row["metadata"] if "metadata" in row.keys() else None
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        entries.append(
            CompactionAuditEntry(
                id=row["id"],
                session_id=row["session_id"],
                compacted_at=datetime.fromisoformat(row["compacted_at"]),
                pre_compact_tokens=row["pre_compact_tokens"],
                post_compact_tokens=row["post_compact_tokens"],
                facts_injected=row["facts_injected"] or 0,
                constraints_injected=row["constraints_injected"] or 0,
                injection_event=row["injection_event"],
                raw_summary=row["raw_summary"],
                metadata=meta,
            )
        )
    return entries


async def export_jsonl(
    kg,
    out_path: Path,
    session_id: Optional[str] = None,
    limit: int = 1000,
) -> int:
    """Dump compaction audit rows to a JSONL file. Returns row count."""
    entries = await list_compactions(kg, session_id=session_id, limit=limit)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for e in entries:
            f.write(e.model_dump_json() + "\n")
    return len(entries)
