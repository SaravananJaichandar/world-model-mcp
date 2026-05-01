"""
Cross-project constraint promotion.

Read a constraint from the source project's KG, INSERT it into target
projects' constraints.db files. Skips duplicates by rule_name.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from .knowledge_graph import KnowledgeGraph
from .registry import ProjectRegistry

logger = logging.getLogger(__name__)


async def promote_constraint(
    kg: KnowledgeGraph,
    constraint_id: str,
    target_projects: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """
    Promote a constraint from source KG to one or more registered projects.

    Args:
        kg: source KnowledgeGraph (the project where the constraint lives)
        constraint_id: the constraint to promote
        target_projects: optional list of project names; defaults to all registered

    Returns:
        list of {project, status, reason?} dicts.
    """
    constraint = await kg.get_constraint_by_id(constraint_id)
    if constraint is None:
        return [{"project": "<source>", "status": "error", "reason": "constraint not found"}]

    registry = ProjectRegistry.load()
    if not registry:
        return [{"project": "<none>", "status": "skipped", "reason": "no projects registered"}]

    source_path = str(kg.db_path)
    if target_projects:
        targets = {
            name: path for name, path in registry.items()
            if name in target_projects
        }
    else:
        targets = {
            name: path for name, path in registry.items()
            if path != source_path
        }

    if not targets:
        return [{"project": "<none>", "status": "skipped", "reason": "no valid targets"}]

    results: List[Dict[str, str]] = []
    for project_name, project_db_path in targets.items():
        constraints_db = Path(project_db_path) / "constraints.db"
        if not constraints_db.exists():
            results.append({
                "project": project_name,
                "status": "error",
                "reason": "constraints.db not found",
            })
            continue

        try:
            async with aiosqlite.connect(constraints_db) as db:
                # Check for existing rule_name
                cursor = await db.execute(
                    "SELECT 1 FROM constraints WHERE rule_name = ? LIMIT 1",
                    (constraint.rule_name,),
                )
                if await cursor.fetchone():
                    results.append({
                        "project": project_name,
                        "status": "skipped",
                        "reason": f"rule_name '{constraint.rule_name}' already exists",
                    })
                    continue

                # INSERT a copy with a new id (so source and target are independent)
                import uuid as _uuid
                await db.execute(
                    """INSERT INTO constraints
                       (id, constraint_type, rule_name, file_pattern, description,
                        violation_count, last_violated, examples, severity, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(_uuid.uuid4()),
                        constraint.constraint_type,
                        constraint.rule_name,
                        constraint.file_pattern,
                        constraint.description,
                        0,  # reset violation count for promoted copy
                        None,
                        json.dumps(constraint.examples),
                        constraint.severity,
                        datetime.now().isoformat(),
                    ),
                )
                await db.commit()
                results.append({
                    "project": project_name,
                    "status": "success",
                    "reason": f"promoted as '{constraint.rule_name}'",
                })
        except Exception as e:
            logger.warning(f"Failed to promote to {project_name}: {e}")
            results.append({
                "project": project_name,
                "status": "error",
                "reason": str(e),
            })

    return results
