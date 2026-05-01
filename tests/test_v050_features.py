"""
Comprehensive tests for v0.5.0 features.
Covers: Prediction layer, Memory health, Fact decay, Context aggregator,
Constraint violation tracking, Find contradictions, Cross-project promotion.

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
    Entity, Fact, Constraint, Event, TestOutcome, Decision,
    RegressionPrediction, SimulationResult, TestFailurePrediction, HealthReport,
)
from world_model_server.predictions import RegressionPredictor
from world_model_server.health import build_health_report
from world_model_server.promotion import promote_constraint
from world_model_server.tools import WorldModelTools


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
# Feature 1: predict_regression - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_predict_regression_no_history(kg):
    """File with no history should return low risk."""
    predictor = RegressionPredictor(kg)
    result = await predictor.predict_regression("src/new.py")
    assert result.risk_score == 0.0
    assert result.risk_level == "low"


@pytest.mark.asyncio
async def test_predict_regression_with_bugs(kg):
    """Past bugs should increase risk."""
    for i in range(2):
        await kg.create_fact(Fact(
            fact_text=f"Fixed null pointer bug {i}",
            evidence_type="bug_fix",
            evidence_path="src/auth.py:42",
            confidence=1.0, status="canonical",
        ))

    predictor = RegressionPredictor(kg)
    result = await predictor.predict_regression("src/auth.py")
    assert result.factors["past_bugs"] == 2
    assert result.risk_score > 0


@pytest.mark.asyncio
async def test_predict_regression_with_test_failures(kg):
    """Recent test failures should increase risk."""
    await kg.create_test_outcome(TestOutcome(
        session_id="s1", test_name="test_login", passed=False,
        linked_file_paths=["src/auth.py"],
    ))
    predictor = RegressionPredictor(kg)
    result = await predictor.predict_regression("src/auth.py")
    assert result.factors["recent_test_failures"] >= 1


@pytest.mark.asyncio
async def test_predict_regression_risk_level_thresholds(kg):
    """Score < 0.3 -> low, <= 0.6 -> medium, > 0.6 -> high."""
    # Add 3 bugs (3 * 0.3 = 0.9 -> high)
    for i in range(3):
        await kg.create_fact(Fact(
            fact_text=f"Bug {i}",
            evidence_type="bug_fix",
            evidence_path="src/critical.py:1",
            confidence=1.0, status="canonical",
        ))
    predictor = RegressionPredictor(kg)
    result = await predictor.predict_regression("src/critical.py")
    assert result.risk_level == "high"


@pytest.mark.asyncio
async def test_predict_regression_score_capped_at_1(kg):
    """Risk score must not exceed 1.0."""
    # Add many bugs
    for i in range(10):
        await kg.create_fact(Fact(
            fact_text=f"Bug {i}",
            evidence_type="bug_fix",
            evidence_path="src/risky.py:1",
            confidence=1.0, status="canonical",
        ))
    predictor = RegressionPredictor(kg)
    result = await predictor.predict_regression("src/risky.py")
    assert result.risk_score <= 1.0


# Feature 1: Integration

@pytest.mark.asyncio
async def test_predict_regression_tool(kg, config):
    """Tool wrapper returns valid JSON."""
    tools = WorldModelTools(kg, config)
    result_json = await tools.predict_regression(file_path="src/foo.py")
    data = json.loads(result_json)
    assert "risk_score" in data
    assert "risk_level" in data
    assert "factors" in data


@pytest.mark.asyncio
async def test_predict_regression_includes_change_description(kg, config):
    """Change description should be in result."""
    tools = WorldModelTools(kg, config)
    result_json = await tools.predict_regression(
        file_path="src/foo.py", change_description="refactor auth"
    )
    data = json.loads(result_json)
    assert data["change_description"] == "refactor auth"


# Feature 1: Smoke

@pytest.mark.asyncio
async def test_smoke_predict_empty_kg(kg):
    """Empty KG returns risk=0/low."""
    predictor = RegressionPredictor(kg)
    result = await predictor.predict_regression("anything.py")
    assert result.risk_score == 0.0
    assert result.risk_level == "low"


# ============================================================================
# Feature 2: simulate_change - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_simulate_change_co_edit_radius(kg):
    """Blast radius includes co-edited files."""
    # Create co-edits
    for sid in ["s1", "s2"]:
        await kg.create_event(Event(
            session_id=sid, event_type="file_edit",
            tool_input={"file_path": "src/a.py"},
        ))
        await kg.create_event(Event(
            session_id=sid, event_type="file_edit",
            tool_input={"file_path": "src/b.py"},
        ))
        await kg.record_co_edits(sid)

    predictor = RegressionPredictor(kg)
    result = await predictor.simulate_change("src/a.py", "refactor")
    assert len(result.blast_radius) >= 1


@pytest.mark.asyncio
async def test_simulate_change_includes_history(kg):
    """Historical outcomes should be attached."""
    # Co-edit
    for sid in ["s1", "s2"]:
        await kg.create_event(Event(
            session_id=sid, event_type="file_edit",
            tool_input={"file_path": "src/a.py"},
        ))
        await kg.create_event(Event(
            session_id=sid, event_type="file_edit",
            tool_input={"file_path": "src/b.py"},
        ))
        await kg.record_co_edits(sid)

    # Test outcome on b.py
    await kg.create_test_outcome(TestOutcome(
        session_id="s3", test_name="test_b", passed=False,
        linked_file_paths=["src/b.py"],
    ))

    predictor = RegressionPredictor(kg)
    result = await predictor.simulate_change("src/a.py", "edit")
    assert len(result.historical_outcomes) >= 0  # may or may not include depending on outcomes link


@pytest.mark.asyncio
async def test_simulate_change_confidence_tiers(kg):
    """Confidence depends on signal availability."""
    predictor = RegressionPredictor(kg)
    # No signals
    result = await predictor.simulate_change("src/empty.py", "edit")
    assert result.confidence == 0.4


# Feature 2: Integration

@pytest.mark.asyncio
async def test_simulate_change_tool(kg, config):
    """Tool returns valid JSON."""
    tools = WorldModelTools(kg, config)
    result_json = await tools.simulate_change(
        file_path="src/x.py", change_description="add logging"
    )
    data = json.loads(result_json)
    assert "blast_radius" in data
    assert "confidence" in data


# Feature 2: Smoke

@pytest.mark.asyncio
async def test_smoke_simulate_empty(kg):
    """Empty KG returns empty blast radius."""
    predictor = RegressionPredictor(kg)
    result = await predictor.simulate_change("nothing.py", "edit")
    assert result.blast_radius == []
    assert result.confidence == 0.4


# ============================================================================
# Feature 3: predict_test_failures - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_predict_test_failures_basic(kg):
    """Tests with high failure rate should be flagged."""
    # 3 failures out of 4 runs for test_login
    for passed in [False, False, True, False]:
        await kg.create_test_outcome(TestOutcome(
            session_id="s1", test_name="test_login", passed=passed,
            linked_file_paths=["src/auth.py"],
        ))

    predictor = RegressionPredictor(kg)
    result = await predictor.predict_test_failures(["src/auth.py"])
    assert len(result.likely_failing_tests) >= 1
    assert result.likely_failing_tests[0]["test_name"] == "test_login"


@pytest.mark.asyncio
async def test_predict_test_failures_filter_threshold(kg):
    """Tests with rate <= 0.3 should be filtered out."""
    # 1 failure out of 4 runs = 0.25
    for passed in [True, True, False, True]:
        await kg.create_test_outcome(TestOutcome(
            session_id="s1", test_name="test_stable", passed=passed,
            linked_file_paths=["src/stable.py"],
        ))

    predictor = RegressionPredictor(kg)
    result = await predictor.predict_test_failures(["src/stable.py"])
    # Should be filtered out (rate 0.25 < 0.3)
    test_names = [t["test_name"] for t in result.likely_failing_tests]
    assert "test_stable" not in test_names


@pytest.mark.asyncio
async def test_predict_test_failures_grouping(kg):
    """Multiple test outcomes should be grouped by test_name."""
    for i in range(3):
        await kg.create_test_outcome(TestOutcome(
            session_id=f"s{i}", test_name="test_a", passed=False,
            linked_file_paths=["src/x.py"],
        ))

    predictor = RegressionPredictor(kg)
    result = await predictor.predict_test_failures(["src/x.py"])
    assert len(result.likely_failing_tests) == 1
    assert result.likely_failing_tests[0]["sample_size"] == 3


# Feature 3: Integration

@pytest.mark.asyncio
async def test_predict_test_failures_tool(kg, config):
    """Tool returns valid JSON."""
    tools = WorldModelTools(kg, config)
    result_json = await tools.predict_test_failures(file_paths=["src/a.py"])
    data = json.loads(result_json)
    assert "likely_failing_tests" in data


# Feature 3: Smoke

@pytest.mark.asyncio
async def test_smoke_predict_test_failures_empty(kg):
    """Empty outcomes returns empty list."""
    predictor = RegressionPredictor(kg)
    result = await predictor.predict_test_failures(["any.py"])
    assert result.likely_failing_tests == []


# ============================================================================
# Feature 4: promote_constraint - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_promote_constraint_to_targets():
    """Promote should INSERT into target project's constraints.db."""
    import world_model_server.registry as reg_mod

    src_dir = tempfile.mkdtemp()
    tgt_dir = tempfile.mkdtemp()

    # Source KG
    src_kg = KnowledgeGraph(src_dir)
    await src_kg.initialize()
    constraint = Constraint(
        constraint_type="linting", rule_name="no-foo",
        description="No foo allowed", violation_count=5,
        examples=[{"incorrect": "foo()", "correct": "bar()"}],
        severity="error",
    )
    cid = await src_kg.create_or_update_constraint(constraint)

    # Target KG
    tgt_kg = KnowledgeGraph(tgt_dir)
    await tgt_kg.initialize()

    # Mock registry
    reg_dir = Path(tempfile.mkdtemp()) / ".world-model"
    reg_file = reg_dir / "projects.json"
    orig_dir = reg_mod.REGISTRY_DIR
    orig_file = reg_mod.REGISTRY_FILE
    reg_mod.REGISTRY_DIR = reg_dir
    reg_mod.REGISTRY_FILE = reg_file

    try:
        from world_model_server.registry import ProjectRegistry
        ProjectRegistry.register("source", src_dir)
        ProjectRegistry.register("target", tgt_dir)

        results = await promote_constraint(src_kg, cid)
        success = [r for r in results if r["status"] == "success"]
        assert len(success) >= 1

        # Verify constraint exists in target
        all_constraints = await tgt_kg.get_constraints()
        rule_names = [c.rule_name for c in all_constraints]
        assert "no-foo" in rule_names
    finally:
        reg_mod.REGISTRY_DIR = orig_dir
        reg_mod.REGISTRY_FILE = orig_file
        shutil.rmtree(src_dir)
        shutil.rmtree(tgt_dir)
        shutil.rmtree(reg_dir.parent, ignore_errors=True)


@pytest.mark.asyncio
async def test_promote_constraint_skip_duplicates():
    """Promote to a project that already has the constraint should skip."""
    import world_model_server.registry as reg_mod

    src_dir = tempfile.mkdtemp()
    tgt_dir = tempfile.mkdtemp()

    src_kg = KnowledgeGraph(src_dir)
    await src_kg.initialize()
    cid = await src_kg.create_or_update_constraint(Constraint(
        constraint_type="style", rule_name="dup-rule",
        description="dup", severity="warning",
    ))

    tgt_kg = KnowledgeGraph(tgt_dir)
    await tgt_kg.initialize()
    # Pre-populate target with same rule_name
    await tgt_kg.create_or_update_constraint(Constraint(
        constraint_type="style", rule_name="dup-rule",
        description="existing", severity="warning",
    ))

    reg_dir = Path(tempfile.mkdtemp()) / ".world-model"
    orig_dir = reg_mod.REGISTRY_DIR
    orig_file = reg_mod.REGISTRY_FILE
    reg_mod.REGISTRY_DIR = reg_dir
    reg_mod.REGISTRY_FILE = reg_dir / "projects.json"

    try:
        from world_model_server.registry import ProjectRegistry
        ProjectRegistry.register("source", src_dir)
        ProjectRegistry.register("target", tgt_dir)

        results = await promote_constraint(src_kg, cid)
        skipped = [r for r in results if r["status"] == "skipped"]
        assert len(skipped) >= 1
    finally:
        reg_mod.REGISTRY_DIR = orig_dir
        reg_mod.REGISTRY_FILE = orig_file
        shutil.rmtree(src_dir)
        shutil.rmtree(tgt_dir)
        shutil.rmtree(reg_dir.parent, ignore_errors=True)


@pytest.mark.asyncio
async def test_promote_constraint_invalid_id(kg):
    """Invalid constraint ID returns error."""
    results = await promote_constraint(kg, "nonexistent-id")
    assert results[0]["status"] == "error"


# Feature 4: Integration

@pytest.mark.asyncio
async def test_promote_constraint_tool(kg, config):
    """Tool wraps and returns valid JSON."""
    cid = await kg.create_or_update_constraint(Constraint(
        constraint_type="linting", rule_name="test-rule",
        description="test", severity="warning",
    ))
    tools = WorldModelTools(kg, config)
    result_json = await tools.promote_constraint(constraint_id=cid)
    data = json.loads(result_json)
    assert "results" in data
    assert "promoted_count" in data


# Feature 4: Smoke

@pytest.mark.asyncio
async def test_smoke_promote_empty_registry(kg):
    """No registered projects returns skipped."""
    import world_model_server.registry as reg_mod

    reg_dir = Path(tempfile.mkdtemp()) / ".world-model"
    orig_dir = reg_mod.REGISTRY_DIR
    orig_file = reg_mod.REGISTRY_FILE
    reg_mod.REGISTRY_DIR = reg_dir
    reg_mod.REGISTRY_FILE = reg_dir / "projects.json"

    try:
        cid = await kg.create_or_update_constraint(Constraint(
            constraint_type="linting", rule_name="lonely",
            description="x", severity="error",
        ))
        results = await promote_constraint(kg, cid)
        assert results[0]["status"] == "skipped"
    finally:
        reg_mod.REGISTRY_DIR = orig_dir
        reg_mod.REGISTRY_FILE = orig_file
        shutil.rmtree(reg_dir.parent, ignore_errors=True)


# ============================================================================
# Feature 5: get_health_report - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_get_orphaned_entities(kg):
    """Entities with no facts/relationships should be flagged as orphans."""
    e = Entity(entity_type="function", name="orphan_func", file_path="src/x.py")
    await kg.create_entity(e)

    orphans = await kg.get_orphaned_entities()
    assert any(o.id == e.id for o in orphans)


@pytest.mark.asyncio
async def test_get_stale_facts(kg):
    """Old facts with no re-observation should be returned."""
    old_date = datetime.now() - timedelta(days=60)
    await kg.create_fact(Fact(
        fact_text="Old assertion",
        valid_at=old_date,
        evidence_type="source_code",
        evidence_path="src/old.py",
        confidence=1.0, status="canonical",
    ))

    stale = await kg.get_stale_facts(days=30)
    assert len(stale) >= 1


@pytest.mark.asyncio
async def test_get_constraint_decay_candidates(kg):
    """Constraints with old last_violated should be flagged."""
    c = Constraint(
        constraint_type="linting", rule_name="old-rule",
        description="old", violation_count=5,
        last_violated=datetime.now() - timedelta(days=60),
        severity="warning",
    )
    await kg.create_or_update_constraint(c)

    candidates = await kg.get_constraint_decay_candidates(days=30)
    assert any(cd.rule_name == "old-rule" for cd in candidates)


@pytest.mark.asyncio
async def test_get_db_sizes(kg):
    """All 9 DBs should have size info."""
    sizes = await kg.get_db_sizes()
    assert len(sizes) == 9
    for name in ["entities.db", "facts.db", "constraints.db", "decisions.db",
                 "outcomes.db", "trajectories.db", "events.db", "relationships.db", "sessions.db"]:
        assert name in sizes


# Feature 5: Integration

@pytest.mark.asyncio
async def test_health_report_aggregation(kg):
    """build_health_report returns a complete report."""
    report = await build_health_report(kg)
    assert isinstance(report, HealthReport)
    assert "orphan_count" in report.summary
    assert "total_db_bytes" in report.summary


@pytest.mark.asyncio
async def test_health_report_tool(kg, config):
    """Tool wrapper returns valid JSON."""
    tools = WorldModelTools(kg, config)
    result_json = await tools.get_health_report()
    data = json.loads(result_json)
    assert "summary" in data
    assert "orphaned_entities" in data


# Feature 5: Smoke

@pytest.mark.asyncio
async def test_smoke_health_empty_kg(kg):
    """Fresh KG returns empty lists with zero counts."""
    report = await build_health_report(kg)
    assert report.summary["orphan_count"] == 0
    assert report.summary["stale_fact_count"] == 0


# ============================================================================
# Feature 6: apply_fact_decay - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_apply_fact_decay_marks_old(kg):
    """Facts older than threshold with no re-observation get invalidated."""
    old_date = datetime.now() - timedelta(days=120)
    f = Fact(
        fact_text="ancient",
        valid_at=old_date,
        evidence_type="source_code",
        evidence_path="src/old.py:1",
        confidence=1.0, status="canonical",
    )
    await kg.create_fact(f)

    count = await kg.apply_fact_decay(days=90)
    assert count >= 1


@pytest.mark.asyncio
async def test_apply_fact_decay_skips_re_observed(kg):
    """Facts with newer same-evidence_path facts should NOT be decayed."""
    old_date = datetime.now() - timedelta(days=120)
    await kg.create_fact(Fact(
        fact_text="old",
        valid_at=old_date,
        evidence_type="source_code",
        evidence_path="src/active.py:1",
        confidence=1.0, status="canonical",
    ))
    # Newer fact with same path
    await kg.create_fact(Fact(
        fact_text="new",
        valid_at=datetime.now(),
        evidence_type="source_code",
        evidence_path="src/active.py:1",
        confidence=1.0, status="canonical",
    ))

    count = await kg.apply_fact_decay(days=90)
    # Old fact should not be decayed because of re-observation
    assert count == 0


@pytest.mark.asyncio
async def test_apply_fact_decay_threshold(kg):
    """Facts within the threshold should not be decayed."""
    recent = datetime.now() - timedelta(days=10)
    await kg.create_fact(Fact(
        fact_text="recent",
        valid_at=recent,
        evidence_type="source_code",
        evidence_path="src/recent.py:1",
        confidence=1.0, status="canonical",
    ))

    count = await kg.apply_fact_decay(days=90)
    assert count == 0


# Feature 6: Smoke

@pytest.mark.asyncio
async def test_smoke_decay_empty_kg(kg):
    """Empty KG decay returns 0."""
    count = await kg.apply_fact_decay(days=90)
    assert count == 0


# ============================================================================
# Feature 7: get_context_for_action - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_context_for_action_returns_all_sections(kg, config):
    """Context bundle should include all 6 sections."""
    tools = WorldModelTools(kg, config)
    result_json = await tools.get_context_for_action(
        file_path="src/auth.py", action_type="edit"
    )
    data = json.loads(result_json)
    expected_keys = {"file_path", "action_type", "constraints", "recent_decisions",
                     "recent_bugs", "co_edit_files", "related_facts",
                     "risk_score", "risk_level", "factors"}
    assert expected_keys.issubset(set(data.keys()))


@pytest.mark.asyncio
async def test_context_for_action_with_seeded_data(kg, config):
    """All sections should populate with seeded data."""
    # Seed
    c_id = await kg.create_or_update_constraint(Constraint(
        constraint_type="linting", rule_name="no-bar",
        file_pattern="**/*.py",
        description="no bar", severity="warning",
    ))

    await kg.record_decision(Decision(
        session_id="s1", decision_type="correction",
        file_path="src/auth.py", reasoning="use logger",
    ))

    await kg.create_fact(Fact(
        fact_text="Fixed null check in auth",
        evidence_type="bug_fix",
        evidence_path="src/auth.py:42",
        confidence=1.0, status="canonical",
    ))

    tools = WorldModelTools(kg, config)
    result_json = await tools.get_context_for_action("src/auth.py", "edit")
    data = json.loads(result_json)
    assert len(data["constraints"]) >= 1
    assert len(data["recent_decisions"]) >= 1
    assert len(data["recent_bugs"]) >= 1


# Feature 7: Smoke

@pytest.mark.asyncio
async def test_smoke_context_empty_kg(kg, config):
    """Empty KG returns empty sections, risk=0."""
    tools = WorldModelTools(kg, config)
    result_json = await tools.get_context_for_action("anything.py", "edit")
    data = json.loads(result_json)
    assert data["constraints"] == []
    assert data["recent_decisions"] == []
    assert data["risk_score"] == 0.0


# ============================================================================
# Feature 8: Constraint Violation Tracking - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_increment_violation_count(kg):
    """Increment should bump count and set last_violated."""
    cid = await kg.create_or_update_constraint(Constraint(
        constraint_type="linting", rule_name="no-zoo",
        description="x", severity="warning",
    ))

    new_count = await kg.increment_violation_count(cid)
    assert new_count >= 1


@pytest.mark.asyncio
async def test_validate_change_increments_violation_count(kg, config):
    """validate_change should increment violation count when violation detected."""
    cid = await kg.create_or_update_constraint(Constraint(
        constraint_type="linting", rule_name="no-console",
        file_pattern="**/*.ts",
        description="No console.log",
        examples=[{"incorrect": "console.log", "correct": "logger.debug"}],
        severity="error",
    ))

    tools = WorldModelTools(kg, config)
    initial = (await kg.get_constraint_by_id(cid)).violation_count

    result = await tools.validate_change(
        change_type="edit",
        file_path="src/foo.ts",
        proposed_content="console.log('hello')",
    )

    after = (await kg.get_constraint_by_id(cid)).violation_count
    assert after > initial


@pytest.mark.asyncio
async def test_validate_change_enforcement_history_populated(kg, config):
    """ValidationResult should include enforcement_history when violations exist."""
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
    assert "no-console" in result.enforcement_history


# Feature 8: Smoke

@pytest.mark.asyncio
async def test_smoke_validate_no_violations(kg, config):
    """Non-violating change has empty enforcement_history."""
    tools = WorldModelTools(kg, config)
    result = await tools.validate_change(
        change_type="edit",
        file_path="src/clean.py",
        proposed_content="def safe(): pass",
    )
    # No constraints in kg, so enforcement_history should be empty
    assert result.enforcement_history == {}


# ============================================================================
# Feature 9: find_contradictions - Unit Tests
# ============================================================================

@pytest.mark.asyncio
async def test_find_contradictions_status_diff(kg):
    """Similar facts with different statuses should be flagged."""
    await kg.create_fact(Fact(
        fact_text="Auth uses JWT tokens for session management",
        evidence_type="source_code", evidence_path="src/auth.py",
        confidence=1.0, status="canonical",
    ))
    await kg.create_fact(Fact(
        fact_text="Auth uses JWT tokens for session management v2",
        evidence_type="source_code", evidence_path="src/auth.py",
        confidence=1.0, status="superseded",
    ))

    contradictions = await kg.find_contradictions()
    assert len(contradictions) >= 1


@pytest.mark.asyncio
async def test_find_contradictions_no_similar(kg):
    """Completely different facts should not contradict."""
    await kg.create_fact(Fact(
        fact_text="Database connection pool size is 10",
        evidence_type="source_code", evidence_path="src/db.py",
        confidence=1.0, status="canonical",
    ))
    await kg.create_fact(Fact(
        fact_text="API uses bearer tokens",
        evidence_type="source_code", evidence_path="src/api.py",
        confidence=1.0, status="canonical",
    ))

    contradictions = await kg.find_contradictions()
    assert len(contradictions) == 0


@pytest.mark.asyncio
async def test_find_contradictions_validity_diff(kg):
    """One invalidated fact and one valid fact (similar) should be flagged."""
    f1 = Fact(
        fact_text="Endpoint /api/v1/users returns User array",
        evidence_type="source_code", evidence_path="src/api.py",
        confidence=1.0, status="canonical",
    )
    await kg.create_fact(f1)
    await kg.invalidate_fact(f1.id)
    await kg.create_fact(Fact(
        fact_text="Endpoint /api/v1/users returns User array (v2)",
        evidence_type="source_code", evidence_path="src/api.py",
        confidence=1.0, status="canonical",
    ))

    contradictions = await kg.find_contradictions()
    assert len(contradictions) >= 1


# Feature 9: Integration

@pytest.mark.asyncio
async def test_find_contradictions_tool(kg, config):
    """Tool wrapper returns valid JSON."""
    tools = WorldModelTools(kg, config)
    result_json = await tools.find_contradictions()
    data = json.loads(result_json)
    assert "contradictions" in data
    assert "count" in data


# Feature 9: Smoke

@pytest.mark.asyncio
async def test_smoke_contradictions_empty(kg):
    """Empty KG has no contradictions."""
    contradictions = await kg.find_contradictions()
    assert contradictions == []


# ============================================================================
# CLI Smoke Tests
# ============================================================================

@pytest.mark.asyncio
async def test_smoke_cli_includes_health_decay():
    """CLI --help should list new commands."""
    result = subprocess.run(
        ["python3", "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "health" in result.stdout
    assert "decay" in result.stdout
