#!/usr/bin/env python3
"""
demo_seed.py - reproducible demonstration data for world-model-mcp.

What this does
--------------
Seeds the world-model knowledge graph in the current project with a small,
realistic set of constraints, facts, and a compaction audit row. The data
models what would naturally accumulate after one to two weeks of real
development with Claude Code and world-model-mcp's hooks installed:

  - A no-console-log constraint with violation_count=5 (severity=error)
    that triggers hard-deny at the PreToolUse boundary
  - A bug-fix fact pinned to a critical file region that
    get_related_bugs uses to warn on refactors
  - Two contradicting facts about the HTTP transport port, with a
    shared entity, so find_contradictions surfaces them and
    resolve_contradiction picks a winner via the auto strategy
  - A handful of curated canonical facts that appear in the
    PostCompact injection bundle
  - A check-twine-before-tag constraint with violation_count=5
    (severity=warning) that demonstrates the defer enforcement tier
  - One compaction audit row demonstrating the audit log

What this does NOT do
---------------------
This does not simulate actual Claude Code sessions, capture real
corrections, or fabricate data the system could not produce. Every row
inserted here is the shape the PostToolUse / record_correction hooks
would write if the agent were corrected in your real workflow - this
script just inserts the rows directly so you do not have to wait two
weeks of organic use to see what the primitives look like.

Usage
-----
  python scripts/demo_seed.py                # seed the current project
  python scripts/demo_seed.py --project-dir /path/to/repo
  python scripts/demo_seed.py --dry-run      # show what would be inserted
  python scripts/demo_seed.py --reset        # delete demo rows first
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Make this script runnable from anywhere by inserting the repo root on the path.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from world_model_server.audit import record_compaction  # noqa: E402
from world_model_server.knowledge_graph import KnowledgeGraph  # noqa: E402
from world_model_server.models import (  # noqa: E402
    Constraint,
    Entity,
    Fact,
)


# Stable identifiers used so reruns are idempotent: a re-run sees these
# already exist and updates rather than appending new rows.
DEMO_CONSTRAINTS = [
    {
        "constraint_type": "linting",
        "rule_name": "no-console-log",
        "file_pattern": "*.ts",
        "description": (
            "Use logger.debug() not console.log() in TypeScript source. "
            "Production logs route through pino; console.log bypasses formatting "
            "and breaks downstream parsers."
        ),
        "violation_count": 5,
        "severity": "error",
        "examples": [{"incorrect": "console.log", "correct": "logger.debug"}],
        "last_violated_hours_ago": 6,
    },
    {
        "constraint_type": "style",
        "rule_name": "tag-before-upload",
        "file_pattern": "*",
        "description": (
            "Always run git tag + git push --tags before twine upload. PyPI is "
            "permanent; an untagged upload pins a wheel to no git ref."
        ),
        "violation_count": 2,
        "severity": "warning",
        "examples": [
            {
                "incorrect": "twine upload dist/*",
                "correct": "git tag -a v0.7.x && git push --tags && twine upload dist/*",
            }
        ],
        "last_violated_hours_ago": 48,
    },
    {
        "constraint_type": "style",
        "rule_name": "check-twine-before-tag",
        "file_pattern": "*",
        "description": (
            "Run `python3 -m twine check dist/*` before tagging. Catches PyPI "
            "metadata errors before the tag is pushed; saves a retraction."
        ),
        "violation_count": 5,
        "severity": "warning",
        "examples": [
            {
                "incorrect": "git tag -a v0.7.x",
                "correct": "python3 -m twine check dist/* && git tag -a v0.7.x",
            }
        ],
        "last_violated_hours_ago": 4,
    },
]


DEMO_BUG_FIX_FACT = {
    "fact_text": (
        "Bug fix: NULL content_hash backfill must run on every initialize() to "
        "cover post-migration inserts. Earlier code only backfilled when the "
        "column was created, which left merge_from rows un-hashed and broke dedup."
    ),
    "evidence_path": "world_model_server/knowledge_graph.py:120-135",
    "evidence_type": "bug_fix",
    "confidence": 0.95,
    "source_count": 2,
    "days_ago": 11,
}


DEMO_CANONICAL_FACTS = [
    (
        "Never run twine upload before git tag. Always tag, push, then upload to "
        "PyPI so the published wheel maps to a real git ref.",
        "RELEASE_NOTES.md:1",
    ),
    (
        "Cursor hooks.json uses object-keyed schema with version: 1 (integer), "
        "preToolUse / preCompact / beforeSubmitPrompt event names, failClosed "
        "(not fail_open), timeout in seconds.",
        "adapters/cursor/hooks.json:1",
    ),
    (
        "PostCompact and UserPromptSubmit hooks emit additionalContext to splice "
        "constraints + recent facts back into agent context after compaction.",
        "world_model_server/inject_helper.py:1",
    ),
    (
        "HTTP transport defaults to port 8765 in Dockerfile.http; do not change "
        "without updating docs/deployment/mcp-tunnel.md and docker-compose.yml together.",
        "Dockerfile.http:18",
    ),
    (
        "BetaAbstractMemoryTool subclass lives at world_model_server/memory_backend.py; "
        "required by the Anthropic SDK Managed Agents memory path.",
        "world_model_server/memory_backend.py:1",
    ),
]


DEMO_CONTRADICTION_PAIR = {
    "entity_name": "http_transport_port",
    "common_prefix": "HTTP transport listen port default is ",
    "a": {
        "suffix": "8080",
        "evidence_path": "session:early-prototype-2026-05-08",
        "confidence": 0.7,
        "source_count": 1,
        "days_ago": 3,
    },
    "b": {
        "suffix": "8765",
        "evidence_path": "Dockerfile.http:18",
        "confidence": 0.95,
        "source_count": 3,
        "hours_ago": 2,
    },
}


DEMO_AUDIT_ROW = {
    "session_id": "demo-session-1",
    "pre_compact_tokens": 84320,
    "post_compact_tokens": 22150,
    "facts_injected": 10,
    "constraints_injected": 3,
    "injection_event": "PostCompact",
    "raw_summary": (
        "## Active constraints (top by violation count)\n"
        "- no-console-log: Use logger.debug() not console.log() (violated 5x)\n"
        "- check-twine-before-tag: Run twine check before tag (violated 5x)\n"
        "- tag-before-upload: Always tag before twine upload (violated 2x)"
    ),
}


def _now_offset(days: int = 0, hours: int = 0, seconds: int = 0) -> datetime:
    return datetime.now() - timedelta(days=days, hours=hours, seconds=seconds)


async def seed(kg: KnowledgeGraph, dry_run: bool = False) -> dict[str, Any]:
    """Insert demo constraints, facts, contradiction pair, and audit row.

    Returns a summary dict of what was inserted (or would be inserted, in dry-run).
    """
    summary: dict[str, Any] = {"constraints": [], "facts": [], "contradiction": None, "audit": None}

    # ---- constraints --------------------------------------------------------
    for c_spec in DEMO_CONSTRAINTS:
        constraint = Constraint(
            constraint_type=c_spec["constraint_type"],
            rule_name=c_spec["rule_name"],
            file_pattern=c_spec["file_pattern"],
            description=c_spec["description"],
            violation_count=c_spec["violation_count"],
            severity=c_spec["severity"],
            examples=c_spec["examples"],
            last_violated=_now_offset(hours=c_spec["last_violated_hours_ago"]),
        )
        if dry_run:
            summary["constraints"].append({"rule_name": c_spec["rule_name"], "would_insert": True})
        else:
            cid = await kg.create_or_update_constraint(constraint)
            summary["constraints"].append({"rule_name": c_spec["rule_name"], "id": cid})

    # ---- bug-fix fact ------------------------------------------------------
    bug_fact = Fact(
        fact_text=DEMO_BUG_FIX_FACT["fact_text"],
        evidence_path=DEMO_BUG_FIX_FACT["evidence_path"],
        evidence_type=DEMO_BUG_FIX_FACT["evidence_type"],
        confidence=DEMO_BUG_FIX_FACT["confidence"],
        source_count=DEMO_BUG_FIX_FACT["source_count"],
        valid_at=_now_offset(days=DEMO_BUG_FIX_FACT["days_ago"]),
    )
    if dry_run:
        summary["facts"].append({"kind": "bug_fix", "would_insert": True})
    else:
        bid = await kg.create_fact(bug_fact)
        summary["facts"].append({"kind": "bug_fix", "id": bid})

    # ---- canonical facts (recent, so they appear in injection bundle) -------
    now = datetime.now()
    for i, (text, path) in enumerate(DEMO_CANONICAL_FACTS):
        f = Fact(
            fact_text=text,
            evidence_path=path,
            confidence=0.92,
            source_count=2,
            valid_at=now - timedelta(seconds=i),
        )
        if dry_run:
            summary["facts"].append({"kind": "canonical", "text_preview": text[:60], "would_insert": True})
        else:
            fid = await kg.create_fact(f)
            summary["facts"].append({"kind": "canonical", "id": fid, "text_preview": text[:60]})

    # ---- contradiction pair (shared entity, similar text) -------------------
    pair = DEMO_CONTRADICTION_PAIR
    entity = Entity(
        entity_type="constraint",
        name=pair["entity_name"],
        file_path="Dockerfile.http",
    )
    if dry_run:
        summary["contradiction"] = {"would_insert": True, "entity": pair["entity_name"]}
    else:
        ent_id = await kg.create_entity(entity)
        fact_a = Fact(
            fact_text=pair["common_prefix"] + pair["a"]["suffix"],
            evidence_path=pair["a"]["evidence_path"],
            confidence=pair["a"]["confidence"],
            source_count=pair["a"]["source_count"],
            valid_at=_now_offset(days=pair["a"]["days_ago"]),
            entity_ids=[ent_id],
        )
        fact_b = Fact(
            fact_text=pair["common_prefix"] + pair["b"]["suffix"],
            evidence_path=pair["b"]["evidence_path"],
            confidence=pair["b"]["confidence"],
            source_count=pair["b"]["source_count"],
            valid_at=_now_offset(hours=pair["b"]["hours_ago"]),
            entity_ids=[ent_id],
        )
        a_id = await kg.create_fact(fact_a)
        b_id = await kg.create_fact(fact_b)
        summary["contradiction"] = {"entity_id": ent_id, "fact_a_id": a_id, "fact_b_id": b_id}

    # ---- compaction audit row ----------------------------------------------
    if dry_run:
        summary["audit"] = {"would_insert": True, "session_id": DEMO_AUDIT_ROW["session_id"]}
    else:
        audit = await record_compaction(
            kg,
            session_id=DEMO_AUDIT_ROW["session_id"],
            pre_compact_tokens=DEMO_AUDIT_ROW["pre_compact_tokens"],
            post_compact_tokens=DEMO_AUDIT_ROW["post_compact_tokens"],
            facts_injected=DEMO_AUDIT_ROW["facts_injected"],
            constraints_injected=DEMO_AUDIT_ROW["constraints_injected"],
            injection_event=DEMO_AUDIT_ROW["injection_event"],
            raw_summary=DEMO_AUDIT_ROW["raw_summary"],
        )
        summary["audit"] = {"id": audit.id, "session_id": audit.session_id}

    return summary


async def reset(kg: KnowledgeGraph) -> dict[str, int]:
    """Delete demo rows from a previous run. Identified by stable rule_name /
    evidence_path / session_id values defined above."""
    import aiosqlite

    deleted = {"constraints": 0, "facts": 0, "audit": 0}

    rule_names = [c["rule_name"] for c in DEMO_CONSTRAINTS]
    async with aiosqlite.connect(kg.constraints_db) as db:
        for rn in rule_names:
            cur = await db.execute("DELETE FROM constraints WHERE rule_name = ?", (rn,))
            deleted["constraints"] += cur.rowcount or 0
        await db.commit()

    demo_evidence_paths = (
        [DEMO_BUG_FIX_FACT["evidence_path"]]
        + [p for _, p in DEMO_CANONICAL_FACTS]
        + [
            DEMO_CONTRADICTION_PAIR["a"]["evidence_path"],
            DEMO_CONTRADICTION_PAIR["b"]["evidence_path"],
        ]
    )
    async with aiosqlite.connect(kg.facts_db) as db:
        for p in demo_evidence_paths:
            cur = await db.execute("DELETE FROM facts WHERE evidence_path = ?", (p,))
            deleted["facts"] += cur.rowcount or 0
        await db.commit()

    async with aiosqlite.connect(kg.audit_db) as db:
        cur = await db.execute(
            "DELETE FROM compaction_audit WHERE session_id = ?",
            (DEMO_AUDIT_ROW["session_id"],),
        )
        deleted["audit"] = cur.rowcount or 0
        await db.commit()

    return deleted


async def main_async(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).resolve()
    db_dir = project_dir / ".claude" / "world-model"
    if not db_dir.exists():
        sys.stderr.write(
            f"world-model directory not found at {db_dir}\n"
            "Run `python -m world_model_server.cli setup` first.\n"
        )
        sys.exit(2)

    kg = KnowledgeGraph(str(db_dir))
    await kg.initialize()

    if args.reset:
        deleted = await reset(kg)
        print(
            f"Reset: removed {deleted['constraints']} constraints, "
            f"{deleted['facts']} facts, {deleted['audit']} audit rows."
        )
        if not args.seed_after_reset:
            return

    summary = await seed(kg, dry_run=args.dry_run)

    if args.dry_run:
        print("Dry run - nothing inserted. Would insert:")
    else:
        print("Seed complete. Inserted:")
    for rn in summary["constraints"]:
        print(f"  constraint: {rn['rule_name']}")
    print(f"  facts: {len(summary['facts'])} ({sum(1 for f in summary['facts'] if f['kind']=='canonical')} canonical, 1 bug_fix)")
    print(f"  contradiction pair: {summary['contradiction'].get('entity_id', 'dry-run')}")
    print(f"  audit row: {summary['audit'].get('id', 'dry-run')}")
    if not args.dry_run:
        print()
        print("Now try:")
        print("  python -m world_model_server.cli audit-compactions --limit 5")
        print("  python -m world_model_server.cli health")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed reproducible demo data into world-model-mcp",
    )
    parser.add_argument(
        "--project-dir",
        default=os.getcwd(),
        help="Project directory containing .claude/world-model/ (default: cwd)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be inserted, do not write",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete demo rows from a previous run before seeding",
    )
    parser.add_argument(
        "--seed-after-reset",
        action="store_true",
        help="When combined with --reset, seed again after deleting (default: stop after reset)",
    )
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
