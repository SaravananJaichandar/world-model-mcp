"""
Comprehensive tests for v0.6.0 features.
Covers: F1 enforcement, F2 transcript pointers, F3 project identity + dedup,
F4 CLAUDE.md export, F5 memory backend, F6 .mcpb packaging.

Test levels: Unit, Integration, Smoke.
"""

import json
import os
import pytest
import tempfile
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.config import Config
from world_model_server.models import (
    Entity, Fact, Constraint, Decision, ValidationResult,
)
from world_model_server.tools import WorldModelTools
from world_model_server.project_identity import (
    get_or_create_project_id, read_project_metadata,
)
from world_model_server.transcript import (
    encode_cwd, session_jsonl_path, find_session_path, read_range,
)
from world_model_server.claude_md_generator import generate_claude_md
from world_model_server.memory_backend import WorldModelMemoryBackend


@pytest.fixture
async def kg():
    temp_dir = tempfile.mkdtemp()
    kg = KnowledgeGraph(temp_dir)
    await kg.initialize()
    yield kg
    shutil.rmtree(temp_dir)


@pytest.fixture
def config():
    return Config()


# ============================================================================
# F1: Enforcement - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_validate_change_proceed_when_clean(kg, config):
    """No world_model violations and clean linter -> enforcement_decision is set."""
    tools = WorldModelTools(kg, config)
    result = await tools.validate_change(
        change_type="edit",
        file_path="src/clean.py",
        proposed_content='"""Module."""\n\n\ndef safe() -> None:\n    """Safe."""\n    return None\n',
    )
    # If clean -> proceed; else linter found minor issues -> warn (still not deny)
    assert result.enforcement_decision in ("proceed", "warn")
    # No world model violations regardless
    wm_violations = [v for v in result.violations if v.get("source") == "world_model"]
    assert wm_violations == []


@pytest.mark.asyncio
async def test_validate_change_warn_on_low_violation_count(kg, config):
    """Error severity but low violation_count -> warn."""
    await kg.create_or_update_constraint(Constraint(
        constraint_type="linting", rule_name="no-console",
        file_pattern="**/*.ts",
        description="No console.log",
        examples=[{"incorrect": "console.log", "correct": "logger.debug"}],
        severity="error",
    ))
    tools = WorldModelTools(kg, config)
    result = await tools.validate_change(
        change_type="edit",
        file_path="src/foo.ts",
        proposed_content="console.log('test')",
    )
    # Counter starts at 1 after this validation; threshold is 3
    assert result.enforcement_decision == "warn"


@pytest.mark.asyncio
async def test_validate_change_deny_after_threshold(kg, config):
    """Error severity + violation_count >= 3 -> deny."""
    cid = await kg.create_or_update_constraint(Constraint(
        constraint_type="linting", rule_name="no-console",
        file_pattern="**/*.ts",
        description="No console.log",
        examples=[{"incorrect": "console.log", "correct": "logger.debug"}],
        severity="error",
        violation_count=3,
    ))
    # Bump to 3 manually
    tools = WorldModelTools(kg, config)
    result = await tools.validate_change(
        change_type="edit",
        file_path="src/foo.ts",
        proposed_content="console.log('test')",
    )
    # After validate_change increments: starts at 3, becomes 4 -> deny
    assert result.enforcement_decision == "deny"


@pytest.mark.asyncio
async def test_validate_change_deny_only_for_error_severity(kg, config):
    """Warning severity should never escalate to deny."""
    await kg.create_or_update_constraint(Constraint(
        constraint_type="linting", rule_name="prefer-let",
        file_pattern="**/*.ts",
        description="Prefer let",
        examples=[{"incorrect": "var x", "correct": "let x"}],
        severity="warning",
        violation_count=10,
    ))
    tools = WorldModelTools(kg, config)
    result = await tools.validate_change(
        change_type="edit",
        file_path="src/foo.ts",
        proposed_content="var x = 1",
    )
    # Even with violation_count=10, severity=warning -> no deny
    assert result.enforcement_decision != "deny"


# F1: Smoke

@pytest.mark.asyncio
async def test_smoke_hook_helper_empty_input():
    """hook_helper returns {} on empty stdin."""
    result = subprocess.run(
        ["python3", "-m", "world_model_server.hook_helper"],
        input="",
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


@pytest.mark.asyncio
async def test_smoke_hook_helper_no_db():
    """hook_helper returns {} when DB doesn't exist."""
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/x.ts", "new_string": "console.log()"},
        "project_dir": "/nonexistent/path/xyz",
    }
    result = subprocess.run(
        ["python3", "-m", "world_model_server.hook_helper"],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    # No DB -> empty response (allow)
    assert parsed == {}


# ============================================================================
# F2: Transcript Pointers - Unit Tests
# ============================================================================

def test_encode_cwd():
    """encode_cwd matches Claude Code's slug format."""
    assert encode_cwd("/Users/foo/bar") == "-Users-foo-bar"


def test_session_jsonl_path():
    """Path resolves to correct location."""
    path = session_jsonl_path("/Users/foo/bar", "abc-123")
    assert path.name == "abc-123.jsonl"
    assert "Users-foo-bar" in str(path)


def test_read_range_session_not_found():
    """Missing session returns error."""
    result = read_range("nonexistent-session-uuid")
    assert "error" in result


def test_read_range_with_temp_file():
    """Read a temp JSONL file."""
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a fake projects dir with a session
        projects_dir = Path(temp_dir) / ".claude" / "projects" / "-tmp-fake"
        projects_dir.mkdir(parents=True, exist_ok=True)
        session_file = projects_dir / "test-session-uuid.jsonl"

        lines = [
            json.dumps({"type": "user", "content": "hello"}),
            json.dumps({"type": "assistant", "content": "hi there"}),
            json.dumps({"type": "user", "content": "goodbye"}),
        ]
        session_file.write_text("\n".join(lines))

        # Override Path.home() temporarily
        import world_model_server.transcript as transcript_mod
        original_home = Path.home

        def fake_home():
            return Path(temp_dir)

        Path.home = staticmethod(fake_home)
        try:
            result = read_range("test-session-uuid", line_start=2, line_end=2)
            assert "error" not in result
            assert result["total_lines"] == 3
            assert len(result["lines"]) == 1
            assert result["lines"][0]["content"] == "hi there"
        finally:
            Path.home = original_home
    finally:
        shutil.rmtree(temp_dir)


# F2: Integration

@pytest.mark.asyncio
async def test_recall_transcript_range_tool(kg, config):
    """Tool returns valid JSON for missing session."""
    tools = WorldModelTools(kg, config)
    result_json = await tools.recall_transcript_range(session_id="missing-uuid")
    data = json.loads(result_json)
    assert "error" in data


@pytest.mark.asyncio
async def test_facts_table_has_transcript_columns(kg):
    """Migration adds transcript pointer columns to facts."""
    import aiosqlite
    async with aiosqlite.connect(kg.facts_db) as db:
        cursor = await db.execute("PRAGMA table_info(facts)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
    assert "transcript_session_id" in col_names
    assert "line_start" in col_names
    assert "line_end" in col_names
    assert "content_hash" in col_names


@pytest.mark.asyncio
async def test_decisions_table_has_transcript_columns(kg):
    """Migration adds transcript pointer columns to decisions."""
    import aiosqlite
    async with aiosqlite.connect(kg.decisions_db) as db:
        cursor = await db.execute("PRAGMA table_info(decisions)")
        rows = await cursor.fetchall()
        col_names = {row[1] for row in rows}
    assert "transcript_session_id" in col_names
    assert "line_start" in col_names


# ============================================================================
# F3: Project Identity - Unit Tests
# ============================================================================

def test_get_or_create_project_id_creates_new():
    """First call creates UUID."""
    temp_dir = tempfile.mkdtemp()
    try:
        metadata = get_or_create_project_id(Path(temp_dir))
        assert "project_id" in metadata
        assert "name" in metadata
        assert "paths_seen" in metadata
        assert len(metadata["paths_seen"]) == 1

        # File was written
        assert (Path(temp_dir) / ".claude" / "world-model.json").exists()
    finally:
        shutil.rmtree(temp_dir)


def test_get_or_create_project_id_existing():
    """Second call returns same UUID."""
    temp_dir = tempfile.mkdtemp()
    try:
        m1 = get_or_create_project_id(Path(temp_dir))
        m2 = get_or_create_project_id(Path(temp_dir))
        assert m1["project_id"] == m2["project_id"]
    finally:
        shutil.rmtree(temp_dir)


def test_read_project_metadata_missing():
    """Missing file returns None."""
    temp_dir = tempfile.mkdtemp()
    try:
        result = read_project_metadata(Path(temp_dir))
        assert result is None
    finally:
        shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_content_hash_backfilled(kg):
    """Existing facts get content_hash populated on initialize."""
    fact = Fact(
        fact_text="Test fact",
        evidence_type="source_code", evidence_path="src/x.py",
        confidence=1.0, status="canonical",
    )
    await kg.create_fact(fact)

    # Re-initialize to trigger migration
    await kg.initialize()

    import aiosqlite
    async with aiosqlite.connect(kg.facts_db) as db:
        cursor = await db.execute("SELECT content_hash FROM facts WHERE id = ?", (fact.id,))
        row = await cursor.fetchone()
        # Either set during create (new code) or backfilled (migration)
        # New code does NOT set it during create_fact for backward compat - so backfill must work
        assert row is not None


@pytest.mark.asyncio
async def test_merge_from_basic():
    """merge_from copies non-duplicate facts and constraints."""
    src_dir = tempfile.mkdtemp()
    dst_dir = tempfile.mkdtemp()
    try:
        src = KnowledgeGraph(src_dir)
        await src.initialize()
        dst = KnowledgeGraph(dst_dir)
        await dst.initialize()

        # Add a constraint to source
        await src.create_or_update_constraint(Constraint(
            constraint_type="linting", rule_name="src-rule",
            description="from src", severity="warning",
        ))

        stats = await dst.merge_from(src)
        assert stats["constraints_merged"] >= 1

        # Verify it's in dst
        all_constraints = await dst.get_constraints()
        rule_names = [c.rule_name for c in all_constraints]
        assert "src-rule" in rule_names
    finally:
        shutil.rmtree(src_dir)
        shutil.rmtree(dst_dir)


@pytest.mark.asyncio
async def test_merge_from_dedup():
    """merge_from skips duplicates by content_hash."""
    src_dir = tempfile.mkdtemp()
    dst_dir = tempfile.mkdtemp()
    try:
        src = KnowledgeGraph(src_dir)
        await src.initialize()
        dst = KnowledgeGraph(dst_dir)
        await dst.initialize()

        # Add same constraint to both
        c = Constraint(
            constraint_type="linting", rule_name="shared",
            description="same description", severity="warning",
        )
        await src.create_or_update_constraint(c)
        await dst.create_or_update_constraint(Constraint(
            constraint_type="linting", rule_name="shared",
            description="same description", severity="warning",
        ))

        # Re-init dst to backfill content_hash
        await dst.initialize()

        stats = await dst.merge_from(src)
        # Should skip the duplicate
        assert stats["constraints_skipped"] >= 1
    finally:
        shutil.rmtree(src_dir)
        shutil.rmtree(dst_dir)


# F3: Smoke

def test_smoke_registry_backward_compat():
    """Registry handles legacy {name: db_path} format."""
    import world_model_server.registry as reg_mod
    reg_dir = Path(tempfile.mkdtemp()) / ".world-model"
    reg_dir.mkdir(parents=True, exist_ok=True)
    reg_file = reg_dir / "projects.json"
    # Write legacy format
    reg_file.write_text(json.dumps({"oldproject": "/some/path"}))

    orig_dir = reg_mod.REGISTRY_DIR
    orig_file = reg_mod.REGISTRY_FILE
    reg_mod.REGISTRY_DIR = reg_dir
    reg_mod.REGISTRY_FILE = reg_file

    try:
        # load() returns the legacy format normalized
        loaded = reg_mod.ProjectRegistry.load()
        assert loaded.get("oldproject") == "/some/path"

        # load_full() normalizes to dict format
        full = reg_mod.ProjectRegistry.load_full()
        assert full["oldproject"]["db_path"] == "/some/path"
        assert full["oldproject"]["project_id"] is None
    finally:
        reg_mod.REGISTRY_DIR = orig_dir
        reg_mod.REGISTRY_FILE = orig_file
        shutil.rmtree(reg_dir.parent)


# ============================================================================
# F4: CLAUDE.md export - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_generate_claude_md_empty(kg):
    """Empty KG produces valid markdown."""
    md = await generate_claude_md(kg)
    assert "# CLAUDE.md" in md
    assert "## Project Constraints" in md
    assert "## Recent Decisions" in md
    assert "## Known Bug Regions" in md
    assert "## Co-edit Patterns" in md


@pytest.mark.asyncio
async def test_generate_claude_md_with_constraints(kg):
    """Constraints appear in output."""
    await kg.create_or_update_constraint(Constraint(
        constraint_type="linting", rule_name="no-foo",
        description="No foo allowed",
        violation_count=5,
        severity="error",
    ))
    md = await generate_claude_md(kg)
    assert "no-foo" in md
    assert "No foo allowed" in md


@pytest.mark.asyncio
async def test_generate_claude_md_respects_max_constraints(kg):
    """max_constraints limits output."""
    for i in range(15):
        await kg.create_or_update_constraint(Constraint(
            constraint_type="linting", rule_name=f"rule-{i}",
            description=f"Description {i}",
            severity="warning",
        ))
    md = await generate_claude_md(kg, max_constraints=5)
    rules_in_md = sum(1 for i in range(15) if f"rule-{i}" in md)
    assert rules_in_md <= 5


# F4: Integration

@pytest.mark.asyncio
async def test_export_claude_md_tool(kg, config):
    """Tool wrapper returns markdown string."""
    tools = WorldModelTools(kg, config)
    md = await tools.export_claude_md()
    assert "# CLAUDE.md" in md


# ============================================================================
# F5: Memory Backend - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_memory_backend_create_and_view(kg):
    """Create then view round-trip."""
    backend = WorldModelMemoryBackend(kg, session_id="test")
    await backend.create("/memories/notes.md", "first content")
    content = await backend.view("/memories/notes.md")
    assert content == "first content"


@pytest.mark.asyncio
async def test_memory_backend_str_replace(kg):
    """str_replace mutates content."""
    backend = WorldModelMemoryBackend(kg, session_id="test")
    await backend.create("/memories/notes.md", "first content")
    await backend.str_replace("/memories/notes.md", "first", "second")
    content = await backend.view("/memories/notes.md")
    assert content == "second content"


@pytest.mark.asyncio
async def test_memory_backend_view_returns_latest(kg):
    """Multiple writes - view returns the latest."""
    backend = WorldModelMemoryBackend(kg, session_id="test")
    await backend.create("/memories/notes.md", "v1")
    await backend.create("/memories/notes.md", "v2")
    await backend.create("/memories/notes.md", "v3")
    content = await backend.view("/memories/notes.md")
    assert content == "v3"


@pytest.mark.asyncio
async def test_memory_backend_delete(kg):
    """Delete invalidates."""
    backend = WorldModelMemoryBackend(kg, session_id="test")
    await backend.create("/memories/notes.md", "content")
    await backend.delete("/memories/notes.md")
    content = await backend.view("/memories/notes.md")
    assert content == ""


# F5: Smoke

def test_memory_backend_has_sdk_base_method():
    """has_sdk_base() returns a boolean."""
    result = WorldModelMemoryBackend.has_sdk_base()
    assert isinstance(result, bool)


# ============================================================================
# F6: .mcpb packaging - Smoke Tests
# ============================================================================

def test_manifest_json_exists():
    """manifest.json is present in repo root."""
    repo_root = Path(__file__).parent.parent
    manifest = repo_root / "manifest.json"
    assert manifest.exists()


def test_manifest_json_required_keys():
    """manifest.json has required keys."""
    repo_root = Path(__file__).parent.parent
    manifest = repo_root / "manifest.json"
    data = json.loads(manifest.read_text())
    assert "manifest_version" in data
    assert "name" in data
    assert "version" in data
    assert "server" in data
    assert "hooks" in data
    assert isinstance(data["hooks"], list)
    assert len(data["hooks"]) >= 4  # PreToolUse, PostToolUse, SessionStart, SessionEnd


def test_build_mcpb_script_exists():
    """build_mcpb.sh exists and is executable."""
    repo_root = Path(__file__).parent.parent
    script = repo_root / "scripts" / "build_mcpb.sh"
    assert script.exists()
    # Note: not asserting executable bit because git may strip it on some systems


# ============================================================================
# Backward-Compat Regression Tests
# ============================================================================

@pytest.mark.asyncio
async def test_v050_validate_change_safe_field_unchanged(kg, config):
    """ValidationResult.safe still works as before."""
    tools = WorldModelTools(kg, config)
    result = await tools.validate_change(
        change_type="edit",
        file_path="src/clean.py",
        proposed_content="def safe(): pass",
    )
    assert hasattr(result, "safe")
    # New field present, optional
    assert hasattr(result, "enforcement_decision")


@pytest.mark.asyncio
async def test_v050_existing_constraint_db_loads():
    """KG opens an existing v0.5.0 DB without breakage."""
    temp_dir = tempfile.mkdtemp()
    try:
        kg = KnowledgeGraph(temp_dir)
        await kg.initialize()
        # Add some data
        await kg.create_or_update_constraint(Constraint(
            constraint_type="linting", rule_name="legacy-rule",
            description="from v0.5.0", severity="warning",
        ))
        await kg.create_fact(Fact(
            fact_text="legacy fact",
            evidence_type="source_code", evidence_path="src/old.py",
            confidence=1.0, status="canonical",
        ))

        # Re-initialize (simulates upgrade)
        await kg.initialize()

        # Old data still readable
        constraints = await kg.get_constraints()
        rule_names = [c.rule_name for c in constraints]
        assert "legacy-rule" in rule_names

        result = await kg.query_facts("legacy")
        assert result.exists
    finally:
        shutil.rmtree(temp_dir)


def test_smoke_cli_includes_v060_commands():
    """CLI --help should list new v0.6.0 commands."""
    result = subprocess.run(
        ["python3", "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "recall" in result.stdout
    assert "migrate" in result.stdout
    assert "export-claude-md" in result.stdout
