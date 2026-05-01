"""
Memory health diagnostics for world-model-mcp.

Aggregates orphans, stale facts, contradictions, decay candidates,
and DB sizes into a single health report.
"""

import logging
from typing import Optional

from .knowledge_graph import KnowledgeGraph
from .models import HealthReport

logger = logging.getLogger(__name__)


async def build_health_report(
    kg: KnowledgeGraph,
    stale_days: int = 30,
    decay_days: int = 30,
    contradiction_query: Optional[str] = None,
    list_limit: int = 50,
) -> HealthReport:
    """Build a comprehensive memory health report."""

    orphans = await kg.get_orphaned_entities(limit=list_limit)
    stale = await kg.get_stale_facts(days=stale_days, limit=list_limit)
    contradictions = await kg.find_contradictions(query=contradiction_query, limit=20)
    decay_candidates = await kg.get_constraint_decay_candidates(days=decay_days)
    db_sizes = await kg.get_db_sizes()

    summary = {
        "orphan_count": len(orphans),
        "stale_fact_count": len(stale),
        "contradiction_count": len(contradictions),
        "decay_candidate_count": len(decay_candidates),
        "total_db_bytes": sum(db_sizes.values()),
    }

    return HealthReport(
        orphaned_entities=[
            {
                "id": e.id,
                "entity_type": e.entity_type,
                "name": e.name,
                "file_path": e.file_path,
            }
            for e in orphans
        ],
        stale_facts=[
            {
                "id": f.id,
                "fact_text": f.fact_text[:200],
                "valid_at": f.valid_at.isoformat(),
                "evidence_path": f.evidence_path,
                "confidence": f.confidence,
            }
            for f in stale
        ],
        conflicting_facts=contradictions,
        constraint_decay_candidates=[
            {
                "id": c.id,
                "rule_name": c.rule_name,
                "violation_count": c.violation_count,
                "last_violated": c.last_violated.isoformat() if c.last_violated else None,
            }
            for c in decay_candidates[:list_limit]
        ],
        db_sizes=db_sizes,
        summary=summary,
    )
