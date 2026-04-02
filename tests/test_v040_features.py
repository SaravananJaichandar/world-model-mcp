"""
Comprehensive tests for v0.4.0 features.
Covers: Decision Trace, Outcome Linkage, Trajectory Learning,
Cross-Project Search.

Test levels: Unit, Integration, E2E, Smoke.
"""

import json
import os
import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.config import Config
from world_model_server.models import Decision, TestOutcome, Event, Fact
from world_model_server.registry import ProjectRegistry, search_global


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
# F9: Decision Trace - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_record_decision(kg):
    """Should persist a decision."""
    d = Decision(session_id="s1", decision_type="correction", file_path="auth.py", reasoning="Use logger")
    did = await kg.record_decision(d)
    assert did == d.id


@pytest.mark.asyncio
async def test_get_decisions_all(kg):
    """Should retrieve all decisions."""
    await kg.record_decision(Decision(session_id="s1", decision_type="correction"))
    await kg.record_decision(Decision(session_id="s2", decision_type="approval"))

    decisions = await kg.get_decisions()
    assert len(decisions) == 2


@pytest.mark.asyncio
async def test_get_decisions_filter_session(kg):
    """Should filter by session_id."""
    await kg.record_decision(Decision(session_id="s1", decision_type="correction"))
    await kg.record_decision(Decision(session_id="s2", decision_type="approval"))

    decisions = await kg.get_decisions(session_id="s1")
    assert len(decisions) == 1
    assert decisions[0].session_id == "s1"


@pytest.mark.asyncio
async def test_get_decisions_filter_file(kg):
    """Should filter by file_path."""
    await kg.record_decision(Decision(session_id="s1", decision_type="correction", file_path="auth.py"))
    await kg.record_decision(Decision(session_id="s1", decision_type="correction", file_path="db.py"))

    decisions = await kg.get_decisions(file_path="auth")
    assert len(decisions) == 1


@pytest.mark.asyncio
async def test_get_decisions_filter_type(kg):
    """Should filter by decision_type."""
    await kg.record_decision(Decision(session_id="s1", decision_type="correction"))
    await kg.record_decision(Decision(session_id="s1", decision_type="approval"))

    decisions = await kg.get_decisions(decision_type="approval")
    assert len(decisions) == 1


@pytest.mark.asyncio
async def test_get_decisions_respects_limit(kg):
    """Should respect limit parameter."""
    for i in range(10):
        await kg.record_decision(Decision(session_id=f"s{i}", decision_type="correction"))

    decisions = await kg.get_decisions(limit=3)
    assert len(decisions) == 3


@pytest.mark.asyncio
async def test_get_decision_count(kg):
    """Should return correct count."""
    assert await kg.get_decision_count() == 0

    await kg.record_decision(Decision(session_id="s1", decision_type="correction"))
    await kg.record_decision(Decision(session_id="s2", decision_type="approval"))

    assert await kg.get_decision_count() == 2


# F9: Integration Tests

@pytest.mark.asyncio
async def test_record_correction_writes_decision(kg, config):
    """record_correction should also write a decision trace."""
    from world_model_server.tools import WorldModelTools

    tools = WorldModelTools(kg, config)
    await tools.record_correction(
        session_id="s1",
        claude_action={"content": "console.log('test')", "file_path": "app.js"},
        user_correction={"content": "logger.debug('test')", "file_path": "app.js"},
        reasoning="Use logger",
    )

    decisions = await kg.get_decisions(session_id="s1")
    assert len(decisions) >= 1
    assert decisions[0].decision_type == "correction"


@pytest.mark.asyncio
async def test_record_decision_tool(kg, config):
    """record_decision tool should work standalone."""
    from world_model_server.tools import WorldModelTools

    tools = WorldModelTools(kg, config)
    result_json = await tools.record_decision(
        session_id="s1",
        decision_type="approval",
        file_path="api.py",
        reasoning="Approved refactoring approach",
    )
    result = json.loads(result_json)
    assert "decision_id" in result
    assert result["decision_type"] == "approval"


# F9: E2E Test

@pytest.mark.asyncio
async def test_e2e_decision_workflow(kg, config):
    """Full workflow: correction -> decision logged -> queryable."""
    from world_model_server.tools import WorldModelTools

    tools = WorldModelTools(kg, config)

    # Record correction
    await tools.record_correction(
        session_id="sess-1",
        claude_action={"content": "var x = 1"},
        user_correction={"content": "const x = 1"},
        reasoning="Use const",
    )

    # Get decision log
    log_json = await tools.get_decision_log(session_id="sess-1")
    log = json.loads(log_json)
    assert log["count"] >= 1
    assert log["decisions"][0]["decision_type"] == "correction"


# F9: Smoke Tests

@pytest.mark.asyncio
async def test_smoke_empty_decisions(kg):
    """Empty decision log should return empty list."""
    decisions = await kg.get_decisions()
    assert decisions == []


@pytest.mark.asyncio
async def test_smoke_decision_count_zero(kg):
    """Fresh DB should have zero decisions."""
    assert await kg.get_decision_count() == 0


# ============================================================================
# F7: Outcome Linkage - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_create_test_outcome(kg):
    """Should persist a test outcome."""
    outcome = TestOutcome(
        session_id="s1", test_name="test_login", test_file="tests/test_auth.py",
        passed=False, error_message="AssertionError",
        linked_file_paths=["src/auth.py"],
    )
    oid = await kg.create_test_outcome(outcome)
    assert oid == outcome.id


@pytest.mark.asyncio
async def test_get_outcomes_for_file(kg):
    """Should find outcomes linked to a file."""
    await kg.create_test_outcome(TestOutcome(
        session_id="s1", test_name="test_login", passed=False,
        linked_file_paths=["src/auth.py"],
    ))
    await kg.create_test_outcome(TestOutcome(
        session_id="s1", test_name="test_db", passed=True,
        linked_file_paths=["src/db.py"],
    ))

    outcomes = await kg.get_outcomes_for_file("auth.py")
    assert len(outcomes) == 1
    assert outcomes[0].test_name == "test_login"


@pytest.mark.asyncio
async def test_get_recent_file_edit_events(kg):
    """Should return only file_edit events for a session."""
    await kg.create_event(Event(session_id="s1", event_type="file_edit", tool_input={"file_path": "a.py"}))
    await kg.create_event(Event(session_id="s1", event_type="tool_call", tool_name="Read"))
    await kg.create_event(Event(session_id="s1", event_type="file_edit", tool_input={"file_path": "b.py"}))

    events = await kg.get_recent_file_edit_events("s1")
    assert len(events) == 2
    assert all(e.event_type == "file_edit" for e in events)


@pytest.mark.asyncio
async def test_outcome_links_events(kg):
    """Test outcomes should link to event IDs."""
    outcome = TestOutcome(
        session_id="s1", test_name="test_x", passed=False,
        linked_event_ids=["evt-1", "evt-2"],
        linked_file_paths=["src/x.py"],
    )
    await kg.create_test_outcome(outcome)

    outcomes = await kg.get_outcomes_for_file("x.py")
    assert len(outcomes[0].linked_event_ids) == 2


# F7: Integration Tests

@pytest.mark.asyncio
async def test_record_test_outcome_creates_fact(kg, config):
    """Failed test should create a fact linking change to failure."""
    from world_model_server.tools import WorldModelTools

    tools = WorldModelTools(kg, config)

    # Record a file edit first
    await tools.record_event(
        event_type="file_edit", session_id="s1",
        entities=["auth.py"], description="Edited auth",
        evidence={"tool_name": "Edit", "tool_input": {"file_path": "src/auth.py"}},
    )

    # Record test failure
    result_json = await tools.record_test_outcome(
        session_id="s1",
        test_results=[{"name": "test_login", "file": "tests/test_auth.py", "passed": False, "error": "Failed"}],
    )
    result = json.loads(result_json)
    assert result["failures_linked"] >= 1


@pytest.mark.asyncio
async def test_validate_change_warns_past_failures(kg, config):
    """validate_change should warn about past test failures."""
    from world_model_server.tools import WorldModelTools

    # Create a past failure linked to auth.py
    await kg.create_test_outcome(TestOutcome(
        session_id="s1", test_name="test_login", passed=False,
        linked_file_paths=["src/auth.py"],
    ))

    tools = WorldModelTools(kg, config)
    result = await tools.validate_change("edit", "src/auth.py", "new code")
    has_warning = any("test failures" in s for s in result.suggestions)
    assert has_warning


# F7: E2E Test

@pytest.mark.asyncio
async def test_e2e_edit_test_fail_link(kg, config):
    """Full: edit file -> test fails -> fact created -> queryable."""
    from world_model_server.tools import WorldModelTools

    tools = WorldModelTools(kg, config)

    await tools.record_event(
        event_type="file_edit", session_id="s1",
        entities=["api.ts"], description="Changed API",
        evidence={"tool_name": "Edit", "tool_input": {"file_path": "src/api.ts"}},
    )

    await tools.record_test_outcome(
        session_id="s1",
        test_results=[{"name": "test_api", "passed": False, "error": "timeout"}],
    )

    facts = await kg.query_facts("caused")
    assert facts.exists


# F7: Smoke Test

@pytest.mark.asyncio
async def test_smoke_empty_outcomes(kg):
    """Empty outcomes DB should return empty list."""
    outcomes = await kg.get_outcomes_for_file("anything.py")
    assert outcomes == []


# ============================================================================
# F8: Trajectory Learning - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_record_co_edits_basic(kg):
    """Should record co-edit pairs from session events."""
    await kg.create_event(Event(session_id="s1", event_type="file_edit", tool_input={"file_path": "a.py"}))
    await kg.create_event(Event(session_id="s1", event_type="file_edit", tool_input={"file_path": "b.py"}))
    await kg.create_event(Event(session_id="s1", event_type="file_edit", tool_input={"file_path": "c.py"}))

    pairs = await kg.record_co_edits("s1")
    assert pairs == 3  # (a,b), (a,c), (b,c)


@pytest.mark.asyncio
async def test_record_co_edits_increments(kg):
    """Multiple sessions editing same files should increment count."""
    for sid in ["s1", "s2"]:
        await kg.create_event(Event(session_id=sid, event_type="file_edit", tool_input={"file_path": "a.py"}))
        await kg.create_event(Event(session_id=sid, event_type="file_edit", tool_input={"file_path": "b.py"}))
        await kg.record_co_edits(sid)

    co_edits = await kg.get_co_edited_files("a.py")
    assert len(co_edits) >= 1
    assert co_edits[0]["co_edit_count"] == 2


@pytest.mark.asyncio
async def test_get_co_edited_files(kg):
    """Should return co-edited files sorted by count."""
    # Create 2 sessions both editing a+b, 1 session editing a+c
    for sid in ["s1", "s2"]:
        await kg.create_event(Event(session_id=sid, event_type="file_edit", tool_input={"file_path": "a.py"}))
        await kg.create_event(Event(session_id=sid, event_type="file_edit", tool_input={"file_path": "b.py"}))
        await kg.record_co_edits(sid)

    await kg.create_event(Event(session_id="s3", event_type="file_edit", tool_input={"file_path": "a.py"}))
    await kg.create_event(Event(session_id="s3", event_type="file_edit", tool_input={"file_path": "b.py"}))
    await kg.create_event(Event(session_id="s3", event_type="file_edit", tool_input={"file_path": "c.py"}))
    await kg.record_co_edits("s3")

    co_edits = await kg.get_co_edited_files("a.py")
    assert len(co_edits) >= 1
    # b.py should have count 3, c.py count 1 (filtered out by >= 2 threshold)
    assert co_edits[0]["file_path"] == "b.py"
    assert co_edits[0]["co_edit_count"] == 3


@pytest.mark.asyncio
async def test_co_edits_canonical_order(kg):
    """file_a should always be < file_b lexicographically."""
    await kg.create_event(Event(session_id="s1", event_type="file_edit", tool_input={"file_path": "z.py"}))
    await kg.create_event(Event(session_id="s1", event_type="file_edit", tool_input={"file_path": "a.py"}))
    await kg.record_co_edits("s1")

    # Query from either side should work
    co_z = await kg.get_co_edited_files("z.py")
    co_a = await kg.get_co_edited_files("a.py")
    # Both won't return results yet because co_edit_count = 1 (< 2 threshold)
    # But we can verify the pair was recorded by doing another session
    await kg.create_event(Event(session_id="s2", event_type="file_edit", tool_input={"file_path": "z.py"}))
    await kg.create_event(Event(session_id="s2", event_type="file_edit", tool_input={"file_path": "a.py"}))
    await kg.record_co_edits("s2")

    co_z = await kg.get_co_edited_files("z.py")
    assert len(co_z) == 1
    assert co_z[0]["file_path"] == "a.py"


@pytest.mark.asyncio
async def test_co_edits_single_file_no_pairs(kg):
    """Session with only one file edit should produce no pairs."""
    await kg.create_event(Event(session_id="s1", event_type="file_edit", tool_input={"file_path": "a.py"}))
    pairs = await kg.record_co_edits("s1")
    assert pairs == 0


# F8: Integration Tests

@pytest.mark.asyncio
async def test_co_edit_suggestions_tool(kg, config):
    """get_co_edit_suggestions tool should return suggestions."""
    from world_model_server.tools import WorldModelTools

    for sid in ["s1", "s2"]:
        await kg.create_event(Event(session_id=sid, event_type="file_edit", tool_input={"file_path": "auth.py"}))
        await kg.create_event(Event(session_id=sid, event_type="file_edit", tool_input={"file_path": "auth.test.py"}))
        await kg.record_co_edits(sid)

    tools = WorldModelTools(kg, config)
    result_json = await tools.get_co_edit_suggestions("auth.py")
    result = json.loads(result_json)
    assert len(result["suggestions"]) >= 1


# F8: E2E Test

@pytest.mark.asyncio
async def test_e2e_trajectory_learning(kg, config):
    """Full: multiple sessions editing same files -> suggestions work."""
    from world_model_server.tools import WorldModelTools

    tools = WorldModelTools(kg, config)

    for sid in ["s1", "s2", "s3"]:
        await tools.record_event(
            event_type="file_edit", session_id=sid,
            entities=["app.ts"], description="Edit",
            evidence={"tool_name": "Edit", "tool_input": {"file_path": "src/app.ts"}},
        )
        await tools.record_event(
            event_type="file_edit", session_id=sid,
            entities=["app.test.ts"], description="Edit",
            evidence={"tool_name": "Edit", "tool_input": {"file_path": "src/app.test.ts"}},
        )
        await kg.record_co_edits(sid)

    result_json = await tools.get_co_edit_suggestions("src/app.ts")
    result = json.loads(result_json)
    assert len(result["suggestions"]) >= 1


# F8: Smoke Test

@pytest.mark.asyncio
async def test_smoke_co_edits_empty(kg):
    """Empty trajectories DB should return empty list."""
    co_edits = await kg.get_co_edited_files("anything.py")
    assert co_edits == []


# ============================================================================
# F11: Cross-Project Search - Unit Tests
# ============================================================================

def test_registry_register_and_load():
    """Should register and load a project."""
    import tempfile
    reg_dir = Path(tempfile.mkdtemp()) / ".world-model"
    reg_file = reg_dir / "projects.json"

    # Temporarily override registry path
    import world_model_server.registry as reg_mod
    orig_dir = reg_mod.REGISTRY_DIR
    orig_file = reg_mod.REGISTRY_FILE
    reg_mod.REGISTRY_DIR = reg_dir
    reg_mod.REGISTRY_FILE = reg_file

    try:
        ProjectRegistry.register("test-project", "/tmp/test-db")
        registry = ProjectRegistry.load()
        assert "test-project" in registry
        assert registry["test-project"] == "/tmp/test-db"
    finally:
        reg_mod.REGISTRY_DIR = orig_dir
        reg_mod.REGISTRY_FILE = orig_file
        shutil.rmtree(reg_dir.parent)


def test_registry_unregister():
    """Should unregister a project."""
    import tempfile
    reg_dir = Path(tempfile.mkdtemp()) / ".world-model"
    reg_file = reg_dir / "projects.json"

    import world_model_server.registry as reg_mod
    orig_dir = reg_mod.REGISTRY_DIR
    orig_file = reg_mod.REGISTRY_FILE
    reg_mod.REGISTRY_DIR = reg_dir
    reg_mod.REGISTRY_FILE = reg_file

    try:
        ProjectRegistry.register("proj-a", "/tmp/a")
        ProjectRegistry.register("proj-b", "/tmp/b")
        ProjectRegistry.unregister("proj-a")
        registry = ProjectRegistry.load()
        assert "proj-a" not in registry
        assert "proj-b" in registry
    finally:
        reg_mod.REGISTRY_DIR = orig_dir
        reg_mod.REGISTRY_FILE = orig_file
        shutil.rmtree(reg_dir.parent)


def test_registry_list_projects():
    """Should list all registered projects."""
    import tempfile
    reg_dir = Path(tempfile.mkdtemp()) / ".world-model"
    reg_file = reg_dir / "projects.json"

    import world_model_server.registry as reg_mod
    orig_dir = reg_mod.REGISTRY_DIR
    orig_file = reg_mod.REGISTRY_FILE
    reg_mod.REGISTRY_DIR = reg_dir
    reg_mod.REGISTRY_FILE = reg_file

    try:
        ProjectRegistry.register("a", "/tmp/a")
        ProjectRegistry.register("b", "/tmp/b")
        projects = ProjectRegistry.list_projects()
        assert len(projects) == 2
    finally:
        reg_mod.REGISTRY_DIR = orig_dir
        reg_mod.REGISTRY_FILE = orig_file
        shutil.rmtree(reg_dir.parent)


@pytest.mark.asyncio
async def test_search_global_across_projects():
    """Should find entities across multiple project databases."""
    import tempfile
    import world_model_server.registry as reg_mod

    # Create 2 temp KGs
    tmp1 = tempfile.mkdtemp()
    tmp2 = tempfile.mkdtemp()
    kg1 = KnowledgeGraph(tmp1)
    kg2 = KnowledgeGraph(tmp2)
    await kg1.initialize()
    await kg2.initialize()

    from world_model_server.models import Entity
    await kg1.create_entity(Entity(entity_type="class", name="AuthService", file_path="src/auth.py"))
    await kg2.create_entity(Entity(entity_type="class", name="AuthController", file_path="src/auth.java"))

    # Mock registry
    reg_dir = Path(tempfile.mkdtemp()) / ".world-model"
    reg_file = reg_dir / "projects.json"
    orig_dir = reg_mod.REGISTRY_DIR
    orig_file = reg_mod.REGISTRY_FILE
    reg_mod.REGISTRY_DIR = reg_dir
    reg_mod.REGISTRY_FILE = reg_file

    try:
        ProjectRegistry.register("project1", tmp1)
        ProjectRegistry.register("project2", tmp2)

        results = await search_global("Auth", limit=10)
        assert len(results) == 2
        projects = {r["project"] for r in results}
        assert "project1" in projects
        assert "project2" in projects
    finally:
        reg_mod.REGISTRY_DIR = orig_dir
        reg_mod.REGISTRY_FILE = orig_file
        shutil.rmtree(tmp1)
        shutil.rmtree(tmp2)
        shutil.rmtree(reg_dir.parent)


# F11: Smoke Tests

@pytest.mark.asyncio
async def test_smoke_empty_registry():
    """Search with no registered projects should return empty."""
    import tempfile
    import world_model_server.registry as reg_mod

    reg_dir = Path(tempfile.mkdtemp()) / ".world-model"
    reg_file = reg_dir / "projects.json"
    orig_dir = reg_mod.REGISTRY_DIR
    orig_file = reg_mod.REGISTRY_FILE
    reg_mod.REGISTRY_DIR = reg_dir
    reg_mod.REGISTRY_FILE = reg_file

    try:
        results = await search_global("anything")
        assert results == []
    finally:
        reg_mod.REGISTRY_DIR = orig_dir
        reg_mod.REGISTRY_FILE = orig_file


@pytest.mark.asyncio
async def test_smoke_missing_db():
    """Registered project with deleted DB should be skipped."""
    import tempfile
    import world_model_server.registry as reg_mod

    reg_dir = Path(tempfile.mkdtemp()) / ".world-model"
    reg_file = reg_dir / "projects.json"
    orig_dir = reg_mod.REGISTRY_DIR
    orig_file = reg_mod.REGISTRY_FILE
    reg_mod.REGISTRY_DIR = reg_dir
    reg_mod.REGISTRY_FILE = reg_file

    try:
        ProjectRegistry.register("ghost", "/tmp/nonexistent-path")
        results = await search_global("anything")
        assert results == []
    finally:
        reg_mod.REGISTRY_DIR = orig_dir
        reg_mod.REGISTRY_FILE = orig_file
        shutil.rmtree(reg_dir.parent)


# ============================================================================
# F10: VS Code Extension - CLI JSON Tests
# ============================================================================

@pytest.mark.asyncio
async def test_smoke_cli_includes_new_commands():
    """CLI should show all new commands."""
    import subprocess
    result = subprocess.run(
        ["python3", "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "decisions" in result.stdout
    assert "register" in result.stdout
    assert "projects" in result.stdout
    assert "search-global" in result.stdout
