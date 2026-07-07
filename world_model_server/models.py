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
    source_count: int = Field(1, ge=1, description="Number of independent sources supporting this fact")
    last_confirmed_at: Optional[datetime] = Field(
        None, description="When this fact was most recently re-observed"
    )
    # v0.8.0 provenance fields. See anthropics/claude-code#47023.
    source_tool: Optional[str] = Field(
        None,
        description=(
            "Which tool wrote this fact (e.g. 'claude_code', 'codex', "
            "'cursor', 'pi', 'user'). NULL = unknown / legacy row."
        ),
    )
    confirmer: Optional[str] = Field(
        None,
        description=(
            "Who confirmed this fact, distinct from the asserter. NULL = "
            "pending (asserted but not confirmed); non-NULL = settled."
        ),
    )
    last_decay_at: Optional[datetime] = Field(
        None,
        description=(
            "When confidence decay was last applied. NULL = needs decay "
            "computed on next read."
        ),
    )
    # v0.11.1: content-type routing axis. Distinct from evidence_type
    # (which describes where the fact came from) — content_type
    # describes what shape of content this fact carries, so a
    # MemoryProvider can route writes intelligently:
    #   rule       — always-inject constraint (e.g., "always await async")
    #   fact       — search-on-demand knowledge (e.g., "endpoint /users needs JWT")
    #   procedure  — multi-step workflow (e.g., a runbook or skill definition)
    # NULL = legacy row / unclassified. NULL-tolerant on read; existing
    # code paths ignore it. See adapters/hermes-memory-provider/README.md.
    content_type: Optional[Literal["rule", "fact", "procedure"]] = Field(
        None,
        description=(
            "Content-type routing axis: 'rule' (always-inject), 'fact' "
            "(search-on-demand), 'procedure' (multi-step workflow). NULL = "
            "unclassified; write paths do not require this field."
        ),
    )
    # v0.12.2: enterprise memory-governance additions. Two independent
    # nullable fields covering gaps the existing status/severity/decay
    # axes don't address:
    #
    #   influence_state — separates *storage* from *influence on planning*.
    #     A fact can be stored as evidence (observed / pending_review)
    #     without being trusted by planning consumers, or explicitly
    #     blocked from planning while remaining in the audit trail.
    #     Distinct from `status` (which tracks canonical/superseded
    #     lineage). NULL = legacy row, treated as 'approved' by consumers
    #     for backward compatibility.
    #
    #   expires_at — hard drop-dead timestamp. Complements the continuous
    #     `last_decay_at` confidence erosion. Use for compliance data
    #     retention, ephemeral credentials, and rows that should be
    #     removed entirely rather than weighted down. NULL = never expires.
    #
    # Both are additive and NULL-tolerant; no read/write paths are
    # required to consume them. Consumer wiring (planning-query filter,
    # expiry sweep) is tracked separately.
    influence_state: Optional[Literal["observed", "pending_review", "approved", "blocked"]] = Field(
        None,
        description=(
            "Storage-vs-influence policy: 'observed' (logged but not "
            "trusted), 'pending_review' (queued for promotion), 'approved' "
            "(trusted for planning), 'blocked' (retained for audit but "
            "excluded from planning). NULL = legacy row / treated as approved."
        ),
    )
    expires_at: Optional[datetime] = Field(
        None,
        description=(
            "Hard expiry timestamp. Rows past this time should be dropped, "
            "not just decayed. NULL = never expires."
        ),
    )

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
                "source_tool": "codex",
                "confirmer": "user",
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
    enforcement_decision: Optional[Literal["deny", "warn", "proceed", "defer"]] = Field(
        default=None,
        description="hard violations -> deny, soft violations -> warn, no violations -> proceed, headless ambiguous -> defer",
    )


class QueryFactResult(BaseModel):
    """Result of querying facts about an entity."""

    exists: bool
    facts: List[Fact] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    alternatives: List[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    """Result of the Coach adversarial verification pass (v0.12.12).

    A Player synthesizes an answer citing facts from the graph; the Coach
    is an independent LLM call that checks each claim in the answer against
    the source facts and produces a confidence band + itemized claim breakdown.

    Confidence bands:
      HIGH   - 0 unverified claims; every material claim in the answer is
               backed by a supplied fact.
      MEDIUM - Some claims unverified, but the answer is mostly grounded
               (>=70% of claims verified). Caller should surface the
               unverified list, not the answer wholesale.
      LOW    - Majority of claims unverified, no facts supplied, or the
               Coach LLM call failed. Caller should NOT trust the answer.
               The `error` field is populated when LOW is due to Coach failure.

    Contract: never raises. On any failure the result is a LOW-confidence
    verdict with `error` populated, matching v0.12.9's best-effort hook
    convention.
    """

    query: str = Field(..., description="Original query the answer responds to")
    answer: str = Field(..., description="The answer under verification")
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    verified_claims: List[str] = Field(
        default_factory=list,
        description="Claims that Coach mapped to a supporting fact",
    )
    unverified_claims: List[str] = Field(
        default_factory=list,
        description="Claims Coach could not map to any supplied fact",
    )
    source_pointers: List[Dict[str, str]] = Field(
        default_factory=list,
        description="[{claim, fact_id}] mapping verified claims to source facts",
    )
    coach_reasoning: Optional[str] = Field(
        None, description="Coach's short rationale (non-load-bearing; for audit)"
    )
    error: Optional[str] = Field(
        None,
        description=(
            "Non-None if the Coach call failed (API error, malformed response, "
            "no API key). When set, confidence is always LOW."
        ),
    )


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
    confidence_a: float = Field(1.0, ge=0.0, le=1.0)
    confidence_b: float = Field(1.0, ge=0.0, le=1.0)
    source_count_a: int = Field(1, ge=1)
    source_count_b: int = Field(1, ge=1)
    suggested_winner: Optional[Literal["a", "b", "neither"]] = None
    suggested_strategy: Optional[str] = None


class ContradictionResolution(BaseModel):
    """Result of resolving a contradiction pair."""

    fact_a_id: str
    fact_b_id: str
    strategy: Literal["auto", "keep_higher_confidence", "keep_higher_confidence_decayed", "keep_most_recent", "keep_most_sources", "supersede_a", "supersede_b", "manual"]
    winner_id: Optional[str] = None
    loser_id: Optional[str] = None
    resolved_at: datetime = Field(default_factory=datetime.now)
    notes: Optional[str] = None


class CompactionAuditEntry(BaseModel):
    """A single compaction audit log entry."""

    id: str = Field(default_factory=generate_id)
    session_id: Optional[str] = None
    compacted_at: datetime = Field(default_factory=datetime.now)
    pre_compact_tokens: Optional[int] = None
    post_compact_tokens: Optional[int] = None
    facts_injected: int = 0
    constraints_injected: int = 0
    injection_event: Optional[Literal["PostCompact", "UserPromptSubmit", "SessionStart"]] = None
    raw_summary: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


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
