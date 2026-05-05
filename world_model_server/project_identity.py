"""
Project identity management for path-stable world-model storage.

Stores a stable UUID in <project_dir>/.claude/world-model.json so that
knowledge graphs can be merged when directories are renamed or aliased.
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PROJECT_CONFIG_FILENAME = "world-model.json"


def _config_path(project_dir: Path) -> Path:
    return project_dir / ".claude" / PROJECT_CONFIG_FILENAME


def read_project_metadata(project_dir: Path) -> Optional[Dict[str, Any]]:
    """Read existing world-model.json if present."""
    path = _config_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read project metadata at {path}: {e}")
        return None


def get_or_create_project_id(project_dir: Path) -> Dict[str, Any]:
    """
    Read or create the project identity file.

    On first call, generates a UUID and writes the file.
    Subsequent calls return the existing UUID and append the current path
    to paths_seen if it's new.
    """
    project_dir = Path(project_dir).resolve()
    path = _config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = read_project_metadata(project_dir)
    now = datetime.now().isoformat()
    current_path = str(project_dir)

    if existing is None:
        metadata = {
            "project_id": str(uuid.uuid4()),
            "name": project_dir.name,
            "paths_seen": [current_path],
            "created_at": now,
            "updated_at": now,
        }
        path.write_text(json.dumps(metadata, indent=2))
        logger.info(f"Created new project identity {metadata['project_id']} at {path}")
        return metadata

    # Append current path if not already tracked
    paths_seen = existing.get("paths_seen", [])
    if current_path not in paths_seen:
        paths_seen.append(current_path)
        existing["paths_seen"] = paths_seen
        existing["updated_at"] = now
        path.write_text(json.dumps(existing, indent=2))
        logger.info(f"Tracked new path for project {existing.get('project_id')}: {current_path}")

    return existing
