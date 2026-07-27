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
    """Manages the list of world-model-mcp projects.

    Storage format (v0.6.0+):
        {project_name: {"db_path": str, "project_id": str|None}}

    Backward compat: legacy format {project_name: db_path_string} is auto-normalized.
    """

    @classmethod
    def _raw_load(cls) -> Dict[str, Any]:
        """Load raw JSON without normalization.

        Returns an empty dict on ANY failure that would leave a caller
        with no usable state anyway: missing file, unreadable JSON,
        or file-system errors (PermissionError, IsADirectoryError,
        FileNotFoundError, etc. all inherit from OSError). Callers
        must treat "empty registry" as a valid state.

        Suppressing errors is safe here because the registry is
        best-effort discovery metadata, not authoritative data.
        """
        if not REGISTRY_FILE.exists():
            return {}
        try:
            return json.loads(REGISTRY_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            # OSError covers PermissionError, IsADirectoryError,
            # NotADirectoryError, FileNotFoundError, and any other
            # transient FS issue. Log at DEBUG so operators can trace
            # if they need to, but never crash the caller.
            logger.debug(
                "registry raw_load failed, returning empty",
                exc_info=True,
            )
            return {}

    @classmethod
    def load(cls) -> Dict[str, str]:
        """Load registry as {project_name: db_path} for backward compat."""
        raw = cls._raw_load()
        result: Dict[str, str] = {}
        for name, value in raw.items():
            if isinstance(value, str):
                result[name] = value
            elif isinstance(value, dict) and "db_path" in value:
                result[name] = value["db_path"]
        return result

    @classmethod
    def load_full(cls) -> Dict[str, Dict[str, Any]]:
        """Load registry with all metadata (db_path, project_id)."""
        raw = cls._raw_load()
        result: Dict[str, Dict[str, Any]] = {}
        for name, value in raw.items():
            if isinstance(value, str):
                result[name] = {"db_path": value, "project_id": None}
            elif isinstance(value, dict):
                result[name] = {
                    "db_path": value.get("db_path", ""),
                    "project_id": value.get("project_id"),
                }
        return result

    @classmethod
    def register(
        cls, project_name: str, db_path: str, project_id: Optional[str] = None
    ) -> None:
        """Add a project to the registry.

        Fails soft (logs a warning, returns) on any file-system error
        so that a locked-down HOME or read-only mount doesn't crash
        the caller. Registry is best-effort discovery metadata.
        """
        try:
            REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
            raw = cls._raw_load()
            raw[project_name] = {"db_path": db_path, "project_id": project_id}
            REGISTRY_FILE.write_text(json.dumps(raw, indent=2))
        except OSError:
            logger.warning(
                "registry register failed for %s (registry write refused); "
                "project will not appear in cross-project search",
                project_name,
                exc_info=True,
            )
            return
        logger.info(f"Registered project: {project_name} -> {db_path} (id={project_id})")

    @classmethod
    def unregister(cls, project_name: str) -> None:
        """Remove a project from the registry.

        Fails soft on file-system errors, same as register().
        """
        raw = cls._raw_load()
        if project_name not in raw:
            return
        try:
            del raw[project_name]
            REGISTRY_FILE.write_text(json.dumps(raw, indent=2))
        except OSError:
            logger.warning(
                "registry unregister failed for %s (registry write refused)",
                project_name,
                exc_info=True,
            )
            return
        logger.info(f"Unregistered project: {project_name}")

    @classmethod
    def list_projects(cls) -> List[Dict[str, Any]]:
        """List all registered projects with full metadata."""
        full = cls.load_full()
        return [
            {"name": name, "db_path": meta["db_path"], "project_id": meta.get("project_id")}
            for name, meta in full.items()
        ]

    @classmethod
    def find_by_project_id(cls, project_id: str) -> List[Dict[str, Any]]:
        """Find all registered projects with a matching project_id."""
        full = cls.load_full()
        return [
            {"name": name, "db_path": meta["db_path"], "project_id": meta.get("project_id")}
            for name, meta in full.items()
            if meta.get("project_id") == project_id
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
