"""
PreToolUse hook helper. Synchronous Python script invoked via subprocess
from the JS hook. Reads JSON from stdin, queries constraints.db (read-only),
classifies violations as hard/soft, returns JSON on stdout.

Designed to fail open: any error returns empty {} (allow) so it never
blocks legitimate edits when the world model is unavailable.

Input shape (stdin):
{
    "tool_name": "Edit" | "Write",
    "tool_input": {"file_path": str, "new_string"|"content": str, ...},
    "project_dir": str,
    "session_id": str (optional),
    "hard_threshold": int (optional, default 3),
    "defer_threshold": int (optional, default 5),
    "supports_defer": bool (optional, default false)
}

Output shape (stdout):
{
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny" | "defer" | "ask" | "allow",
        "permissionDecisionReason": str
    },
    "violations": [...]
}

When the client does not advertise `supports_defer`, defer-tier violations
downgrade to `ask` for backward compatibility.
"""

import json
import logging
import sqlite3
import sys
from fnmatch import fnmatch
from pathlib import Path

logger = logging.getLogger(__name__)


def _glob_match(path: str, pattern: str) -> bool:
    """Match a file path against a glob pattern, supporting **."""
    if not pattern:
        return True
    if "**" in pattern:
        flat_pattern = pattern.replace("**/", "")
        recursive_pattern = pattern.replace("**", "*")
        return fnmatch(path, flat_pattern) or fnmatch(path, recursive_pattern)
    return fnmatch(path, pattern)


def _violates_constraint(content: str, constraint: dict) -> bool:
    """Simple pattern-based check ported from tools.py:_violates_constraint."""
    rule_name = constraint.get("rule_name", "")

    # Built-in patterns for common rules
    if rule_name == "no-console" and "console.log" in content:
        return True
    if rule_name == "no-var" and "var " in content:
        return True

    # Check examples for incorrect patterns
    examples_json = constraint.get("examples", "[]")
    try:
        examples = json.loads(examples_json) if isinstance(examples_json, str) else examples_json
    except (json.JSONDecodeError, TypeError):
        examples = []

    for example in examples:
        if isinstance(example, dict):
            incorrect = example.get("incorrect", "")
            if incorrect and incorrect in content:
                return True

    return False


def _load_constraints(db_path: str) -> list:
    """Load constraints from constraints.db read-only."""
    constraints_db = Path(db_path) / "constraints.db"
    if not constraints_db.exists():
        return []

    try:
        # Read-only mode
        conn = sqlite3.connect(f"file:{constraints_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM constraints")
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows
    except sqlite3.Error as e:
        logger.warning(f"Failed to load constraints: {e}")
        return []


def classify(payload: dict) -> dict:
    """Classify a tool invocation against learned constraints."""
    tool_input = payload.get("tool_input", {})
    project_dir = payload.get("project_dir", ".")
    hard_threshold = payload.get("hard_threshold", 3)
    defer_threshold = payload.get("defer_threshold", 5)
    supports_defer = bool(payload.get("supports_defer", False))

    file_path = tool_input.get("file_path", "")
    content = (
        tool_input.get("new_string")
        or tool_input.get("content")
        or ""
    )

    if not file_path or not content:
        # Nothing to check
        return {}

    db_path = str(Path(project_dir) / ".claude" / "world-model")
    all_constraints = _load_constraints(db_path)

    if not all_constraints:
        return {}

    violations = []
    hard_count = 0
    defer_count = 0

    for c in all_constraints:
        # Filter by file pattern
        pattern = c.get("file_pattern")
        if pattern and not _glob_match(file_path, pattern):
            continue

        if _violates_constraint(content, c):
            severity = c.get("severity", "warning")
            violation_count = c.get("violation_count", 0)
            is_hard = severity == "error" and violation_count >= hard_threshold
            is_defer = (
                not is_hard
                and severity == "warning"
                and violation_count >= defer_threshold
            )

            violations.append({
                "rule": c.get("rule_name"),
                "severity": severity,
                "description": c.get("description"),
                "violation_count": violation_count,
                "is_hard": is_hard,
                "is_defer": is_defer,
            })
            if is_hard:
                hard_count += 1
            elif is_defer:
                defer_count += 1

    if not violations:
        return {}

    if hard_count > 0:
        decision = "deny"
        reason = f"Hard constraint violation: {violations[0]['rule']} ({violations[0]['description']}). Violated {violations[0]['violation_count']} times previously."
    elif defer_count > 0:
        # defer tier: prefer 'defer' if the client supports it, else fall back to 'ask'
        decision = "defer" if supports_defer else "ask"
        defer_rules = ", ".join(
            v["rule"] for v in violations if v.get("is_defer")
        )[:200]
        reason = f"Recurring warning-level violations ({defer_rules}). Headless agents should pause for confirmation."
    else:
        decision = "ask"
        rules = ", ".join(v["rule"] for v in violations[:3])
        reason = f"Soft constraint violations: {rules}. Continue?"

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        },
        "violations": violations,
    }


def main():
    """Entry point: read stdin, classify, write stdout."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.stdout.write("{}")
            return
        payload = json.loads(raw)
        result = classify(payload)
        sys.stdout.write(json.dumps(result))
    except Exception as e:
        # Fail open
        logger.warning(f"hook_helper failed: {e}")
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
