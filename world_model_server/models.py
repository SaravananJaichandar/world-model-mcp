"""
Data models for the world model knowledge graph.

Defines Pydantic models for entities, facts, relationships, constraints,
sessions, and events.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
import uuid


def generate_id() -> str:
    """Generate a unique ID for entities."""
    return str(uuid.uuid4())


class Entity(BaseModel):
    """
    An entity in the codebase (file, API, function, class, constraint).

    Entities are resolved identities that persist across references.
    """

    id: str = Field(default_factory=generate_id)
    entity_type: Literal["file", "api", "function", "class", "constraint", "package"]
    name: str
    file_path: Optional[str] = None
    signature: Optional[str] = None  # For functions/APIs
    first_seen: datetime = Field(default_factory=datetime.now)
    last_updated: datetime = Field(default_factory=datetime.now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        json_schema_extra = {
            "example": {
                "id": "ent_abc123",
                "entity_type": "api",
                "name": "POST /api/users",
                "file_path": "src/api/users.ts",
                "signature": "(req: Request, res: Response) => Promise<void>",
                "metadata": {"authentication": "JWT", "rate_limited": True},
            }
        }


class Fact(BaseModel):
    """
    A temporal assertion about the codebase.

    Facts capture what was true at a specific time, with evidence chains.
    """

    id: str = Field(default_factory=generate_id)
    fact_text: str = Field(..., description="Human-readable assertion")
    valid_at: datetime = Field(
        default_factory=datetime.now, description="When this became true"
    )
    invalid_at: Optional[datetime] = Field(None, description="When this stopped being true")
    status: Literal["canonical", "corroborated", "superseded", "synthesized"] = Field(
        "canonical", description="Fact status"
    )
    entity_ids: List[str] = Field(default_factory=list, description="Entities mentioned")
    evidence_type: Literal["source_code", "test", "session", "user_correction", "bug_fix"] = (
        "source_code"
    )
    evidence_path: str = Field(..., description="Path to evidence (file:lines or session_id)")
    derived_from: Optional[List[str]] = Field(
        None, description="Fact IDs this was synthesized from"
    )
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence score")
    session_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)

    class Config:
        json_schema_extra = {
            "example": {
                "id": "fact_xyz789",
                "fact_text": "API endpoint /api/users requires JWT authentication",
                "valid_at": "2024-03-15T10:30:00Z",
                "invalid_at": None,
                "status": "canonical",
                "entity_ids": ["ent_api_users", "ent_jwt_auth"],
                "evidence_type": "source_code",
                "evidence_path": "src/api/auth.ts:42-58",
                "confidence": 1.0,
            }
        }


class Relationship(BaseModel):
    """
    A directional relationship between two entities.

    Relationships capture how entities connect (calls, imports, depends_on, etc.).
    """

    id: str = Field(default_factory=generate_id)
    source_entity_id: str
    target_entity_id: str
    relationship_type: Literal["calls", "imports", "depends_on", "fixes", "violates", "uses"]
    weight: float = Field(1.0, ge=0.0, description="Relationship strength")
    first_seen: datetime = Field(default_factory=datetime.now)
    last_seen: datetime = Field(default_factory=datetime.now)
    evidence_count: int = Field(1, description="Number of times observed")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "rel_abc123",
                "source_entity_id": "ent_auth_middleware",
                "target_entity_id": "ent_jwt_verify",
                "relationship_type": "calls",
                "weight": 0.9,
                "evidence_count": 15,
            }
        }


class Constraint(BaseModel):
    """
    A learned rule or pattern from user corrections.

    Constraints are high-priority knowledge learned when users correct Claude's output.
    """

    id: str = Field(default_factory=generate_id)
    constraint_type: Literal["linting", "architecture", "testing", "api_contract", "style"]
    rule_name: str
    file_pattern: Optional[str] = Field(
        None, description="Glob pattern like 'src/**/*.ts'"
    )
    description: str
    violation_count: int = Field(0, description="Times this constraint was violated")
    last_violated: Optional[datetime] = None
    examples: List[Dict[str, str]] = Field(
        default_factory=list, description="List of {incorrect, correct} examples"
    )
    severity: Literal["error", "warning", "info"] = "error"
    created_at: datetime = Field(default_factory=datetime.now)

    class Config:
        json_schema_extra = {
            "example": {
                "id": "const_abc123",
                "constraint_type": "linting",
                "rule_name": "no-console",
                "file_pattern": "src/**/*.ts",
                "description": "Use logger.debug() instead of console.log() in src/",
                "violation_count": 12,
                "examples": [
                    {"incorrect": "console.log('debug')", "correct": "logger.debug('debug')"}
                ],
                "severity": "error",
            }
        }


class Session(BaseModel):
    """
    A Claude Code development session.

    Sessions capture the trajectory of a coding session with outcomes.
    """

    session_id: str = Field(default_factory=generate_id)
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None
    user_request: Optional[str] = None
    outcome: Optional[Literal["success", "partial", "failure"]] = None
    entities_touched: List[str] = Field(default_factory=list)
    facts_generated: int = 0
    constraints_learned: int = 0

    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "sess_abc123",
                "started_at": "2024-03-15T10:00:00Z",
                "ended_at": "2024-03-15T10:45:00Z",
                "user_request": "Add rate limiting to the API",
                "outcome": "success",
                "entities_touched": ["src/api/server.ts", "package.json"],
                "facts_generated": 5,
                "constraints_learned": 1,
            }
        }


class Event(BaseModel):
    """
    An activity event during a session (file edit, test run, etc.).

    Events are the atomic building blocks of session trajectories.
    """

    id: str = Field(default_factory=generate_id)
    session_id: str
    event_type: Literal[
        "file_edit",
        "file_create",
        "file_delete",
        "test_run",
        "lint_run",
        "user_correction",
        "tool_call",
    ]
    timestamp: datetime = Field(default_factory=datetime.now)
    entity_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Dict[str, Any] = Field(default_factory=dict)
    tool_output: Dict[str, Any] = Field(default_factory=dict)
    reasoning: Optional[str] = None
    success: bool = True

    class Config:
        json_schema_extra = {
            "example": {
                "id": "evt_abc123",
                "session_id": "sess_xyz789",
                "event_type": "file_edit",
                "timestamp": "2024-03-15T10:15:00Z",
                "entity_id": "ent_auth_file",
                "tool_name": "Edit",
                "tool_input": {"file_path": "src/api/auth.ts", "old_string": "...", "new_string": "..."},
                "tool_output": {"success": True},
                "reasoning": "Added JWT authentication middleware",
                "success": True,
            }
        }


class ValidationResult(BaseModel):
    """Result of pre-execution validation."""

    safe: bool
    violations: List[Dict[str, Any]] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    enforcement_history: Dict[str, int] = Field(
        default_factory=dict,
        description="rule_name -> total times violated since the rule was learned",
    )


class QueryFactResult(BaseModel):
    """Result of querying facts about an entity."""

    exists: bool
    facts: List[Fact] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    alternatives: List[str] = Field(default_factory=list)


class BugInfo(BaseModel):
    """Information about a bug fix."""

    bug_id: str
    description: str
    fixed_at: datetime
    critical_regions: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_path: str


class Decision(BaseModel):
    """A decision trace capturing agent proposal and human response."""

    class Config:
        arbitrary_types_allowed = True

    id: str = Field(default_factory=generate_id)
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    tool_name: Optional[str] = None
    agent_proposal: Dict[str, Any] = Field(default_factory=dict)
    human_correction: Dict[str, Any] = Field(default_factory=dict)
    constraint_learned_id: Optional[str] = None
    file_path: Optional[str] = None
    reasoning: Optional[str] = None
    decision_type: Literal["correction", "approval", "rejection"] = "correction"


class TestOutcome(BaseModel):
    """A test result linked to code changes."""

    class Config:
        arbitrary_types_allowed = True

    id: str = Field(default_factory=generate_id)
    session_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
    test_name: str
    test_file: Optional[str] = None
    passed: bool
    error_message: Optional[str] = None
    linked_event_ids: List[str] = Field(default_factory=list)
    linked_file_paths: List[str] = Field(default_factory=list)


class RegressionPrediction(BaseModel):
    """Risk score for a proposed change."""

    file_path: str
    change_description: Optional[str] = None
    risk_score: float = Field(0.0, ge=0.0, le=1.0)
    risk_level: Literal["low", "medium", "high"] = "low"
    factors: Dict[str, int] = Field(default_factory=dict)


class SimulationResult(BaseModel):
    """Projected impact of a proposed change."""

    file_path: str
    change_description: str
    blast_radius: List[Dict[str, str]] = Field(default_factory=list)
    historical_outcomes: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class TestFailurePrediction(BaseModel):
    """Tests likely to fail given file edits."""

    file_paths: List[str] = Field(default_factory=list)
    likely_failing_tests: List[Dict[str, Any]] = Field(default_factory=list)


class ContradictionPair(BaseModel):
    """Two facts that contradict each other."""

    fact_a_id: str
    fact_b_id: str
    fact_a_text: str
    fact_b_text: str
    similarity_score: float = Field(0.0, ge=0.0, le=1.0)
    both_valid: bool
    reason: str


class HealthReport(BaseModel):
    """Memory health diagnostics."""

    class Config:
        arbitrary_types_allowed = True

    orphaned_entities: List[Dict[str, Any]] = Field(default_factory=list)
    stale_facts: List[Dict[str, Any]] = Field(default_factory=list)
    conflicting_facts: List[Dict[str, Any]] = Field(default_factory=list)
    constraint_decay_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    db_sizes: Dict[str, int] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=datetime.now)
    summary: Dict[str, int] = Field(default_factory=dict)
