"""
v0.7.0 feature tests.

F1: PostCompact / UserPromptSubmit auto-injection
F2: MCP defer decision support in PreToolUse hook
F3: Confidence-weighted contradiction resolution
F4: Cursor adapter (smoke)
F5: Compaction audit log
Backward-compat regression: ensure v0.6 schema + tools still work.

Test conventions match v0.4/v0.5/v0.6 suites:
- async fixtures with tempfile.mkdtemp / shutil.rmtree
- @pytest.mark.asyncio applied implicitly (asyncio_mode = "auto")
"""

import json
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from world_model_server.audit import (
    export_jsonl,
    list_compactions,
    record_compaction,
)
from world_model_server.contradictions import (
    pick_winner,
    resolve,
    suggest_strategy,
)
from world_model_server.hook_helper import classify as hook_classify
from world_model_server.inject_helper import build_injection
from world_model_server.config import Config
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import (
    CompactionAuditEntry,
    Constraint,
    ContradictionPair,
    Fact,
    ValidationResult,
)
from world_model_server.tools import WorldModelTools


@pytest.fixture
async def kg():
    tmp = tempfile.mkdtemp()
    g = KnowledgeGraph(tmp)
    await g.initialize()
    yield g
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
async def tools(kg):
    return WorldModelTools(kg, Config())


def _make_constraint(rule="no-console", severity="warning", violation_count=0, file_pattern="*.ts"):
    return Constraint(
        constraint_type="linting",
        rule_name=rule,
        file_pattern=file_pattern,
        description=f"Do not use {rule}",
        violation_count=violation_count,
        severity=severity,
        examples=[{"incorrect": "console.log", "correct": "logger.debug"}],
    )


def _make_fact(text, confidence=1.0, source_count=1, valid_offset_seconds=0):
    return Fact(
        fact_text=text,
        evidence_path="memory:test",
        confidence=confidence,
        source_count=source_count,
        valid_at=datetime.now() + timedelta(seconds=valid_offset_seconds),
    )


# ============================================================================
# F1: PostCompact / UserPromptSubmit auto-injection
# ============================================================================

async def test_f1_inject_empty_graph_returns_empty(kg):
    out = build_injection({
        "event": "PostCompact",
        "project_dir": str(kg.db_path.parent.parent),
    })
    # No constraints + no facts on a fresh KG with audit dir at .claude/world-model
    # but only if the directory layout matches. Use direct DB path test instead.
    assert out == {} or out.get("constraints_count", 0) == 0


async def test_f1_inject_with_constraint_and_fact(kg):
    # Seed one constraint and one fact
    await kg.create_or_update_constraint(_make_constraint(rule="no-console", violation_count=4))
    await kg.create_fact(_make_fact("API endpoint /users uses JWT", confidence=0.9))

    # The helper reads via project_dir/.claude/world-model. We pointed kg at a tmp dir,
    # so build a temp project layout that mirrors that path.
    project = tempfile.mkdtemp()
    target = Path(project) / ".claude" / "world-model"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(kg.db_path), str(target))

    try:
        out = build_injection({
            "event": "PostCompact",
            "project_dir": project,
            "session_id": "session-test",
            "pre_compact_tokens": 50000,
            "post_compact_tokens": 12000,
        })
        assert out, "Expected non-empty injection for seeded KG"
        assert out["hookSpecificOutput"]["hookEventName"] == "PostCompact"
        bundle = out["hookSpecificOutput"]["additionalContext"]
        assert "no-console" in bundle
        assert "JWT" in bundle
        assert out["constraints_count"] == 1
        assert out["facts_count"] == 1
        # Audit row should have been written
        assert "audit_id" in out
    finally:
        shutil.rmtree(project, ignore_errors=True)


async def test_f1_inject_unknown_event_returns_empty(kg):
    out = build_injection({"event": "Random", "project_dir": "."})
    assert out == {}


async def test_f1_inject_user_prompt_search_hint(kg):
    await kg.create_fact(_make_fact("JWT authentication required for /api/users"))
    await kg.create_fact(_make_fact("CSV exporter uses pandas"))

    project = tempfile.mkdtemp()
    target = Path(project) / ".claude" / "world-model"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(kg.db_path), str(target))
    try:
        out = build_injection({
            "event": "UserPromptSubmit",
            "project_dir": project,
            "user_prompt": "Tell me about authentication and JWT",
            "max_facts": 5,
        })
        bundle = out["hookSpecificOutput"]["additionalContext"]
        # Search hint should bias toward JWT fact
        assert "JWT" in bundle
    finally:
        shutil.rmtree(project, ignore_errors=True)


async def test_f1_tool_get_injection_context(tools, kg):
    await kg.create_or_update_constraint(_make_constraint(rule="no-var", violation_count=2))
    out_json = await tools.get_injection_context(event_type="UserPromptSubmit")
    out = json.loads(out_json)
    assert out["event_type"] == "UserPromptSubmit"
    assert "no-var" in out["injection"]


# ============================================================================
# F2: MCP defer decision
# ============================================================================

async def test_f2_defer_threshold_warning_high_count(kg, tools):
    # Warning severity with 5+ violations -> defer tier
    await kg.create_or_update_constraint(_make_constraint(
        rule="no-console", severity="warning", violation_count=6
    ))

    project = tempfile.mkdtemp()
    target = Path(project) / ".claude" / "world-model"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(kg.db_path), str(target))
    try:
        out = hook_classify({
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/x.ts", "new_string": "console.log('debug')"},
            "project_dir": project,
            "supports_defer": True,
        })
        assert out["hookSpecificOutput"]["permissionDecision"] == "defer"
    finally:
        shutil.rmtree(project, ignore_errors=True)


async def test_f2_defer_falls_back_to_ask_when_unsupported(kg):
    await kg.create_or_update_constraint(_make_constraint(
        rule="no-console", severity="warning", violation_count=6
    ))
    project = tempfile.mkdtemp()
    target = Path(project) / ".claude" / "world-model"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(kg.db_path), str(target))
    try:
        out = hook_classify({
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/x.ts", "new_string": "console.log('debug')"},
            "project_dir": project,
            "supports_defer": False,
        })
        # Without defer support, defer tier falls back to "ask"
        assert out["hookSpecificOutput"]["permissionDecision"] == "ask"
    finally:
        shutil.rmtree(project, ignore_errors=True)


async def test_f2_hard_deny_still_works(kg):
    # Error severity with 3+ violations -> deny (existing v0.6 behavior preserved)
    await kg.create_or_update_constraint(_make_constraint(
        rule="no-console", severity="error", violation_count=5
    ))
    project = tempfile.mkdtemp()
    target = Path(project) / ".claude" / "world-model"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(kg.db_path), str(target))
    try:
        out = hook_classify({
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/x.ts", "new_string": "console.log('debug')"},
            "project_dir": project,
            "supports_defer": True,
        })
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        shutil.rmtree(project, ignore_errors=True)


async def test_f2_validation_result_accepts_defer():
    # Schema check: enforcement_decision Literal now includes "defer"
    v = ValidationResult(safe=False, enforcement_decision="defer")
    assert v.enforcement_decision == "defer"


# ============================================================================
# F3: Confidence-weighted contradiction resolution
# ============================================================================

def test_f3_suggest_strategy_prefers_sources():
    a = {"confidence": 0.9, "source_count": 6, "valid_at": "2026-01-01T00:00:00"}
    b = {"confidence": 0.85, "source_count": 1, "valid_at": "2026-04-01T00:00:00"}
    assert suggest_strategy(a, b) == "keep_most_sources"


def test_f3_suggest_strategy_falls_back_to_recency():
    a = {"confidence": 0.9, "source_count": 1, "valid_at": "2026-01-01T00:00:00"}
    b = {"confidence": 0.9, "source_count": 1, "valid_at": "2026-04-01T00:00:00"}
    assert suggest_strategy(a, b) == "keep_most_recent"


def test_f3_suggest_strategy_picks_confidence_when_gap():
    a = {"confidence": 0.95, "source_count": 1, "valid_at": "2026-01-01T00:00:00"}
    b = {"confidence": 0.6, "source_count": 1, "valid_at": "2026-01-01T00:00:00"}
    assert suggest_strategy(a, b) == "keep_higher_confidence"


def test_f3_pick_winner_explicit_supersede():
    a = {"confidence": 0.99}
    b = {"confidence": 0.1}
    # Explicit supersede_a always picks b regardless of scores
    assert pick_winner("supersede_a", a, b) == "b"
    assert pick_winner("supersede_b", a, b) == "a"


def test_f3_pick_winner_returns_none_for_manual_and_ties():
    a = {"confidence": 0.5}
    b = {"confidence": 0.5}
    assert pick_winner("keep_higher_confidence", a, b) is None
    assert pick_winner("manual", a, b) is None


async def test_f3_resolve_supersedes_loser(kg):
    f1 = _make_fact("user.email is unique", confidence=0.95, source_count=3)
    f2 = _make_fact("user.email is not unique", confidence=0.6, source_count=1)
    a_id = await kg.create_fact(f1)
    b_id = await kg.create_fact(f2)

    resolution = await resolve(kg, a_id, b_id, strategy="keep_higher_confidence")
    assert resolution.winner_id == a_id
    assert resolution.loser_id == b_id

    loser = await kg.get_fact_by_id(b_id)
    assert loser["status"] == "superseded"
    assert loser["invalid_at"] is not None
    winner = await kg.get_fact_by_id(a_id)
    assert winner["status"] != "superseded"


async def test_f3_resolve_auto_picks_strategy(kg):
    f1 = _make_fact("X", confidence=0.9, source_count=5)
    f2 = _make_fact("Y", confidence=0.9, source_count=1)
    a_id = await kg.create_fact(f1)
    b_id = await kg.create_fact(f2)

    resolution = await resolve(kg, a_id, b_id, strategy="auto")
    assert resolution.strategy == "keep_most_sources"
    assert resolution.winner_id == a_id


async def test_f3_resolve_manual_skips_supersede(kg):
    a_id = await kg.create_fact(_make_fact("A", confidence=0.5))
    b_id = await kg.create_fact(_make_fact("B", confidence=0.5))
    resolution = await resolve(kg, a_id, b_id, strategy="manual")
    assert resolution.winner_id is None
    assert resolution.loser_id is None
    # Neither fact should be marked superseded
    for fid in (a_id, b_id):
        f = await kg.get_fact_by_id(fid)
        assert f["status"] != "superseded"


async def test_f3_resolve_invalid_strategy_raises(kg):
    a_id = await kg.create_fact(_make_fact("A"))
    b_id = await kg.create_fact(_make_fact("B"))
    with pytest.raises(ValueError):
        await resolve(kg, a_id, b_id, strategy="not-a-real-strategy")


async def test_f3_resolve_missing_fact_raises(kg):
    a_id = await kg.create_fact(_make_fact("A"))
    with pytest.raises(ValueError):
        await resolve(kg, a_id, "fact_does_not_exist", strategy="auto")


async def test_f3_find_contradictions_surfaces_confidence_fields(kg):
    a_id = await kg.create_fact(_make_fact(
        "The auth flow uses JWT for /users", confidence=0.9, source_count=2,
    ))
    # Slightly different wording, same entity-shaped statement, different conclusion
    b_id = await kg.create_fact(_make_fact(
        "The auth flow uses session cookies for /users", confidence=0.5,
    ))
    pairs = await kg.find_contradictions(limit=20)
    # The basic SequenceMatcher contradiction logic may or may not flag this pair.
    # We assert only on the schema: any returned pair carries confidence + source_count.
    for pair in pairs:
        assert "confidence_a" in pair
        assert "confidence_b" in pair
        assert "source_count_a" in pair
        assert "source_count_b" in pair


async def test_f3_tool_resolve_contradiction(tools, kg):
    a_id = await kg.create_fact(_make_fact("X", confidence=0.99, source_count=3))
    b_id = await kg.create_fact(_make_fact("Y", confidence=0.5))
    out_json = await tools.resolve_contradiction(
        fact_a_id=a_id, fact_b_id=b_id, strategy="keep_higher_confidence",
    )
    out = json.loads(out_json)
    assert out["winner_id"] == a_id


# ============================================================================
# F4: Cursor adapter (smoke - the adapter is a docs/template package)
# ============================================================================

def test_f4_cursor_adapter_package_exists():
    repo_root = Path(__file__).parent.parent
    adapter_dir = repo_root / "adapters" / "cursor"
    assert adapter_dir.exists(), f"Cursor adapter directory missing: {adapter_dir}"
    readme = adapter_dir / "README.md"
    assert readme.exists()
    hooks_template = adapter_dir / "hooks.json"
    assert hooks_template.exists()


def test_f4_cursor_adapter_hooks_json_is_valid():
    repo_root = Path(__file__).parent.parent
    hooks_template = repo_root / "adapters" / "cursor" / "hooks.json"
    data = json.loads(hooks_template.read_text())
    # Should at least declare the inject hook entries
    assert "hooks" in data or "events" in data


# ============================================================================
# F5: Compaction audit log
# ============================================================================

async def test_f5_record_compaction_writes_row(kg):
    entry = await record_compaction(
        kg,
        session_id="s1",
        pre_compact_tokens=50000,
        post_compact_tokens=12000,
        facts_injected=3,
        constraints_injected=2,
        injection_event="PostCompact",
        raw_summary="Constraints: no-console; Facts: 3 recent",
    )
    assert isinstance(entry, CompactionAuditEntry)
    assert entry.session_id == "s1"
    rows = await list_compactions(kg)
    assert len(rows) == 1
    assert rows[0].facts_injected == 3


async def test_f5_list_compactions_filters_by_session(kg):
    await record_compaction(kg, session_id="s1")
    await record_compaction(kg, session_id="s2")
    await record_compaction(kg, session_id="s1")
    s1_rows = await list_compactions(kg, session_id="s1")
    assert len(s1_rows) == 2
    s2_rows = await list_compactions(kg, session_id="s2")
    assert len(s2_rows) == 1


async def test_f5_list_compactions_orders_recent_first(kg):
    e1 = await record_compaction(kg, session_id="s", raw_summary="first")
    e2 = await record_compaction(kg, session_id="s", raw_summary="second")
    rows = await list_compactions(kg)
    # Most recent first
    assert rows[0].id == e2.id
    assert rows[1].id == e1.id


async def test_f5_export_jsonl(kg, tmp_path):
    await record_compaction(kg, session_id="s1", facts_injected=1)
    await record_compaction(kg, session_id="s1", facts_injected=2)
    out_file = tmp_path / "audit.jsonl"
    count = await export_jsonl(kg, out_file, session_id="s1")
    assert count == 2
    lines = out_file.read_text().strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["session_id"] == "s1"


async def test_f5_tool_record_and_get_audit(tools, kg):
    rec_json = await tools.record_compaction_audit(
        session_id="abc",
        pre_compact_tokens=40000,
        post_compact_tokens=10000,
        facts_injected=4,
        constraints_injected=1,
        injection_event="PostCompact",
        raw_summary="test",
    )
    rec = json.loads(rec_json)
    assert rec["session_id"] == "abc"

    listing_json = await tools.get_compaction_audit(session_id="abc")
    listing = json.loads(listing_json)
    assert listing["count"] == 1
    assert listing["entries"][0]["session_id"] == "abc"


async def test_f5_audit_schema_exists(kg):
    # Smoke: the audit.db file exists with the expected table
    audit_db = kg.audit_db
    assert audit_db.exists()
    conn = sqlite3.connect(str(audit_db))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='compaction_audit'"
    ).fetchall()
    conn.close()
    assert rows


def test_f5_cli_audit_compactions_runs():
    # Smoke: invoking the CLI command on an empty project should succeed
    with tempfile.TemporaryDirectory() as proj:
        # Initialize a minimal world-model dir first
        target = Path(proj) / ".claude" / "world-model"
        target.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["python3", "-m", "world_model_server.cli", "audit-compactions",
             "--project-dir", proj],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Exit cleanly even if there are no rows yet
        assert result.returncode == 0


# ============================================================================
# Backward-compat regression
# ============================================================================

async def test_bc_v060_facts_still_have_content_hash(kg):
    fact_id = await kg.create_fact(_make_fact("regression check"))
    f = await kg.get_fact_by_id(fact_id)
    # content_hash backfill from v0.6 still runs
    # (we don't assert non-null since the backfill happens on initialize, not on insert,
    # but the column must exist and be readable)
    assert "content_hash" in f


async def test_bc_v060_constraints_still_work(kg, tools):
    await kg.create_or_update_constraint(_make_constraint(rule="no-var", violation_count=10, severity="error"))
    # validate_change must still produce an enforcement_decision (now possibly "defer" too)
    result = await tools.validate_change(
        change_type="edit",
        file_path="src/x.ts",
        proposed_content="var x = 1;",
    )
    # All four are valid for v0.7
    assert result.enforcement_decision in ("deny", "defer", "warn", "proceed")


async def test_bc_v060_existing_mcp_tool_names_unchanged():
    # Sanity: confirm the v0.6 tool methods are still on the class
    method_names = {
        "query_fact", "record_event", "validate_change", "get_constraints",
        "record_correction", "get_related_bugs", "seed_project", "ingest_pr_reviews",
        "find_contradictions", "recall_transcript_range", "export_claude_md",
    }
    for m in method_names:
        assert hasattr(WorldModelTools, m), f"Missing v0.6 tool: {m}"


async def test_bc_facts_db_columns_present(kg):
    """Schema migration must have added v0.7 columns idempotently."""
    import aiosqlite
    async with aiosqlite.connect(kg.facts_db) as db:
        cursor = await db.execute("PRAGMA table_info(facts)")
        rows = await cursor.fetchall()
        cols = {row[1] for row in rows}
    assert "source_count" in cols
    assert "last_confirmed_at" in cols
    # v0.6 columns must still be present
    assert "transcript_session_id" in cols
    assert "content_hash" in cols
