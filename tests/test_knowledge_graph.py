"""
Tests for knowledge graph operations.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Entity, Fact, Constraint


@pytest.fixture
async def kg():
    """Create a temporary knowledge graph for testing."""
    temp_dir = tempfile.mkdtemp()
    kg = KnowledgeGraph(temp_dir)
    await kg.initialize()
    yield kg
    shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_create_entity(kg):
    """Test creating an entity."""
    entity = Entity(
        entity_type="api",
        name="POST /api/users",
        file_path="src/api/users.ts",
        signature="(req, res) => Promise<void>",
    )

    entity_id = await kg.create_entity(entity)
    assert entity_id == entity.id

    # Retrieve it
    retrieved = await kg.get_entity(entity_id)
    assert retrieved is not None
    assert retrieved.name == "POST /api/users"
    assert retrieved.entity_type == "api"


@pytest.mark.asyncio
async def test_create_fact(kg):
    """Test creating a fact."""
    fact = Fact(
        fact_text="API endpoint /api/users requires JWT authentication",
        valid_at=datetime.now(),
        status="canonical",
        evidence_type="source_code",
        evidence_path="src/api/auth.ts:42-58",
    )

    fact_id = await kg.create_fact(fact)
    assert fact_id == fact.id


@pytest.mark.asyncio
async def test_query_facts(kg):
    """Test querying facts with full-text search."""
    # Create a fact
    fact = Fact(
        fact_text="User model has findByEmail method",
        valid_at=datetime.now(),
        status="canonical",
        evidence_type="source_code",
        evidence_path="src/models/User.ts:20-30",
    )
    await kg.create_fact(fact)

    # Query for it
    result = await kg.query_facts("findByEmail")
    assert result.exists
    assert len(result.facts) > 0
    assert "findByEmail" in result.facts[0].fact_text


@pytest.mark.asyncio
async def test_constraint_creation(kg):
    """Test creating and updating constraints."""
    constraint = Constraint(
        constraint_type="linting",
        rule_name="no-console",
        file_pattern="src/**/*.ts",
        description="Use logger instead of console.log",
        violation_count=1,
        examples=[{"incorrect": "console.log", "correct": "logger.debug"}],
        severity="error",
    )

    constraint_id = await kg.create_or_update_constraint(constraint)
    assert constraint_id == constraint.id

    # Retrieve it
    constraints = await kg.get_constraints("src/api/auth.ts")
    assert len(constraints) > 0
    assert constraints[0].rule_name == "no-console"


@pytest.mark.asyncio
async def test_constraint_increment(kg):
    """Test that constraint violation count increments."""
    constraint1 = Constraint(
        constraint_type="linting",
        rule_name="no-var",
        description="Use const or let instead of var",
        violation_count=1,
        examples=[{"incorrect": "var x = 1", "correct": "const x = 1"}],
        severity="error",
    )

    # Create first time
    id1 = await kg.create_or_update_constraint(constraint1)

    # Create again (should increment)
    constraint2 = Constraint(
        constraint_type="linting",
        rule_name="no-var",
        description="Use const or let instead of var",
        violation_count=1,
        examples=[{"incorrect": "var y = 2", "correct": "const y = 2"}],
        severity="error",
    )

    id2 = await kg.create_or_update_constraint(constraint2)

    # Should be same constraint
    assert id1 == id2

    # Violation count should be 2
    constraints = await kg.get_constraints()
    no_var = [c for c in constraints if c.rule_name == "no-var"][0]
    assert no_var.violation_count == 2
    assert len(no_var.examples) == 2  # Both examples preserved


@pytest.mark.asyncio
async def test_find_entities(kg):
    """Test finding entities by type and name."""
    # Create multiple entities
    entity1 = Entity(entity_type="api", name="GET /api/users", file_path="src/api/users.ts")
    entity2 = Entity(entity_type="api", name="POST /api/users", file_path="src/api/users.ts")
    entity3 = Entity(entity_type="function", name="validateUser", file_path="src/utils/validation.ts")

    await kg.create_entity(entity1)
    await kg.create_entity(entity2)
    await kg.create_entity(entity3)

    # Find all APIs
    apis = await kg.find_entities(entity_type="api")
    assert len(apis) == 2

    # Find by name
    users_apis = await kg.find_entities(name="users")
    assert len(users_apis) == 2


@pytest.mark.asyncio
async def test_get_bugs_for_file(kg):
    """Test retrieving bug fixes for a file."""
    # Create a bug fix fact
    bug_fact = Fact(
        fact_text="Fixed null pointer exception in auth middleware",
        valid_at=datetime.now(),
        status="canonical",
        evidence_type="bug_fix",
        evidence_path="src/api/auth.ts:42-45",
    )
    await kg.create_fact(bug_fact)

    # Retrieve bugs for this file
    bugs = await kg.get_bugs_for_file("src/api/auth.ts")
    assert len(bugs) == 1
    assert "null pointer" in bugs[0].description
    assert len(bugs[0].critical_regions) > 0
