"""
Project registry for cross-project entity search.

Maintains a list of world-model-mcp projects at ~/.world-model/projects.json
and provides global search across all registered project databases.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)

REGISTRY_DIR = Path.home() / ".world-model"
REGISTRY_FILE = REGISTRY_DIR / "projects.json"


class ProjectRegistry:
    """Manages the list of world-model-mcp projects."""

    @classmethod
    def load(cls) -> Dict[str, str]:
        """Load registry: {project_name: db_path}."""
        if not REGISTRY_FILE.exists():
            return {}
        try:
            return json.loads(REGISTRY_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    @classmethod
    def register(cls, project_name: str, db_path: str) -> None:
        """Add a project to the registry."""
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        registry = cls.load()
        registry[project_name] = db_path
        REGISTRY_FILE.write_text(json.dumps(registry, indent=2))
        logger.info(f"Registered project: {project_name} -> {db_path}")

    @classmethod
    def unregister(cls, project_name: str) -> None:
        """Remove a project from the registry."""
        registry = cls.load()
        if project_name in registry:
            del registry[project_name]
            REGISTRY_FILE.write_text(json.dumps(registry, indent=2))
            logger.info(f"Unregistered project: {project_name}")

    @classmethod
    def list_projects(cls) -> List[Dict[str, str]]:
        """List all registered projects."""
        registry = cls.load()
        return [
            {"name": name, "db_path": path}
            for name, path in registry.items()
        ]


async def search_global(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Search entities across all registered projects."""
    registry = ProjectRegistry.load()
    if not registry:
        return []

    results = []

    async def search_project(project_name: str, db_path: str):
        entities_db = Path(db_path) / "entities.db"
        if not entities_db.exists():
            return []

        try:
            async with aiosqlite.connect(entities_db) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM entities WHERE name LIKE ? OR file_path LIKE ? LIMIT ?",
                    (f"%{query}%", f"%{query}%", limit),
                )
                rows = await cursor.fetchall()
                return [
                    {
                        "project": project_name,
                        "entity_type": row["entity_type"],
                        "name": row["name"],
                        "file_path": row["file_path"],
                        "signature": row["signature"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.warning(f"Failed to search {project_name}: {e}")
            return []

    # Search all projects in parallel
    tasks = [
        search_project(name, path)
        for name, path in list(registry.items())[:20]  # Cap at 20 projects
    ]
    all_results = await asyncio.gather(*tasks)

    for project_results in all_results:
        results.extend(project_results)

    return results[:limit]
