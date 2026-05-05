"""
Read Claude Code session transcripts (JSONL files).

Claude Code stores session transcripts at:
    ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl

Where <encoded-cwd> replaces / with - and prefixes -.
Each line is a JSON event with type, timestamp, message, etc.

This module provides utilities to read line ranges from these transcripts
to support the recall_transcript_range MCP tool (v0.6.0 F2).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def encode_cwd(cwd: str) -> str:
    """Encode a working directory path to Claude Code's project slug format."""
    # Claude Code replaces / with - and prefixes -
    return "-" + str(cwd).replace("/", "-").lstrip("-")


def session_jsonl_path(cwd: str, session_id: str) -> Path:
    """Compute the path to a session JSONL file."""
    return Path.home() / ".claude" / "projects" / encode_cwd(cwd) / f"{session_id}.jsonl"


def find_session_path(session_id: str) -> Optional[Path]:
    """Find a session JSONL file across all projects (UUIDs are globally unique)."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None

    target = f"{session_id}.jsonl"
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / target
        if candidate.exists():
            return candidate
    return None


def read_range(
    session_id: str,
    line_start: Optional[int] = None,
    line_end: Optional[int] = None,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Read a line range from a session transcript.

    Args:
        session_id: the session UUID
        line_start: 1-indexed start line (None = from beginning)
        line_end: 1-indexed end line inclusive (None = to end)
        cwd: optional working directory hint to speed up lookup

    Returns:
        dict with: session_id, path, total_lines, lines (parsed JSON entries)
        or {"error": str} if session not found
    """
    if cwd:
        path = session_jsonl_path(cwd, session_id)
        if not path.exists():
            path = find_session_path(session_id)
    else:
        path = find_session_path(session_id)

    if path is None or not path.exists():
        return {"error": f"Session not found: {session_id}"}

    try:
        all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return {"error": f"Failed to read transcript: {e}"}

    total = len(all_lines)
    start = (line_start - 1) if line_start else 0
    end = line_end if line_end else total

    selected = all_lines[start:end]

    parsed = []
    for i, line in enumerate(selected, start=start + 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            entry["_line"] = i
            parsed.append(entry)
        except json.JSONDecodeError:
            parsed.append({"_line": i, "_raw": line, "_error": "invalid_json"})

    return {
        "session_id": session_id,
        "path": str(path),
        "total_lines": total,
        "lines": parsed,
    }
