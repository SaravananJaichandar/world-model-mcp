#!/usr/bin/env python3
"""
dogfooding_snapshot.py -- produce a reproducible JSON snapshot of a
world-model-mcp fact graph, entities table, and constraints table.

Used by case-studies/v011-dogfooding/CASE_STUDY.md to make the reported
numbers checkable: anyone who runs `python scripts/dogfooding_snapshot.py
--db-path .claude/world-model` regenerates the exact figures cited in
the writeup.

Usage:
    python scripts/dogfooding_snapshot.py                        # default: .claude/world-model
    python scripts/dogfooding_snapshot.py --db-path <path>       # explicit path
    python scripts/dogfooding_snapshot.py --out snapshot.json    # write to file

The output is deterministic given the input DB. Two invocations on the
same DB produce byte-identical JSON.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def _q(db_path: Path, sql: str) -> list[dict[str, Any]]:
    """Run a query and return dict-shaped rows."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def _table_exists(db_path: Path, table: str) -> bool:
    if not db_path.exists():
        return False
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchall()
        return bool(rows)
    finally:
        con.close()


def _count_or_zero(db_dir: Path, db_file: str, table: str) -> int:
    p = db_dir / db_file
    if not _table_exists(p, table):
        return 0
    rows = _q(p, f"SELECT COUNT(*) c FROM {table}")
    return rows[0]["c"]


def snapshot(db_dir: Path) -> dict[str, Any]:
    """Produce a full snapshot dict for one world-model DB directory."""
    facts_db = db_dir / "facts.db"
    constraints_db = db_dir / "constraints.db"
    entities_db = db_dir / "entities.db"

    result: dict[str, Any] = {
        "db_dir": str(db_dir.resolve()),
        "totals": {
            "facts": _count_or_zero(db_dir, "facts.db", "facts"),
            "constraints": _count_or_zero(db_dir, "constraints.db", "constraints"),
            "entities": _count_or_zero(db_dir, "entities.db", "entities"),
            "events": _count_or_zero(db_dir, "events.db", "events"),
            "decisions": _count_or_zero(db_dir, "decisions.db", "decisions"),
            "sessions": _count_or_zero(db_dir, "sessions.db", "sessions"),
        },
    }

    if facts_db.exists() and _table_exists(facts_db, "facts"):
        result["facts_by_evidence_type"] = {
            (r["evidence_type"] or "NULL"): r["c"]
            for r in _q(
                facts_db,
                "SELECT evidence_type, COUNT(*) c FROM facts GROUP BY evidence_type ORDER BY c DESC",
            )
        }
        result["facts_by_status"] = {
            (r["status"] or "NULL"): r["c"]
            for r in _q(
                facts_db,
                "SELECT status, COUNT(*) c FROM facts GROUP BY status ORDER BY c DESC",
            )
        }
        result["facts_by_source_tool"] = {
            (r["source_tool"] or "NULL"): r["c"]
            for r in _q(
                facts_db,
                "SELECT source_tool, COUNT(*) c FROM facts GROUP BY source_tool ORDER BY c DESC",
            )
        }
        r = _q(
            facts_db,
            "SELECT SUM(CASE WHEN confirmer IS NOT NULL THEN 1 ELSE 0 END) settled, "
            "SUM(CASE WHEN confirmer IS NULL THEN 1 ELSE 0 END) pending FROM facts",
        )[0]
        result["facts_settled_vs_pending"] = {
            "settled": r["settled"] or 0,
            "pending": r["pending"] or 0,
        }
        r = _q(facts_db, "SELECT MIN(created_at) first, MAX(created_at) last FROM facts")[0]
        result["facts_time_range"] = {"first": r["first"], "last": r["last"]}
        result["facts_by_top_file"] = [
            {"file": r["evidence_path"], "count": r["c"]}
            for r in _q(
                facts_db,
                "SELECT evidence_path, COUNT(*) c FROM facts "
                "GROUP BY evidence_path ORDER BY c DESC, evidence_path ASC LIMIT 15",
            )
        ]
        result["bug_fix_facts"] = [
            {
                "fact_text": r["fact_text"],
                "evidence_path": r["evidence_path"],
                "created_at": r["created_at"],
            }
            for r in _q(
                facts_db,
                "SELECT fact_text, evidence_path, created_at FROM facts "
                "WHERE evidence_type = 'bug_fix' ORDER BY created_at ASC",
            )
        ]

    if constraints_db.exists() and _table_exists(constraints_db, "constraints"):
        result["constraints"] = [
            {
                "rule_name": r["rule_name"],
                "constraint_type": r["constraint_type"],
                "severity": r["severity"],
                "file_pattern": r["file_pattern"],
                "violation_count": r["violation_count"],
                "description": r["description"],
                "examples": r["examples"],
                "created_at": r["created_at"],
                "last_violated": r["last_violated"],
            }
            for r in _q(
                constraints_db,
                "SELECT * FROM constraints ORDER BY violation_count DESC, rule_name ASC",
            )
        ]

    if entities_db.exists() and _table_exists(entities_db, "entities"):
        result["entities_by_type"] = {
            r["entity_type"]: r["c"]
            for r in _q(
                entities_db,
                "SELECT entity_type, COUNT(*) c FROM entities "
                "GROUP BY entity_type ORDER BY c DESC, entity_type ASC",
            )
        }

    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--db-path",
        default=".claude/world-model",
        help="Directory holding the world-model .db files (default: .claude/world-model)",
    )
    p.add_argument("--out", default=None, help="Write JSON to this path instead of stdout")
    args = p.parse_args()

    db_dir = Path(args.db_path)
    if not db_dir.exists():
        print(f"error: DB directory not found: {db_dir}", file=sys.stderr)
        sys.exit(1)

    data = snapshot(db_dir)
    text = json.dumps(data, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote snapshot to {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
