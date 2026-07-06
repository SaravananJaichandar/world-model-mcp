"""
MCP tool implementations for the World Model server.

Implements the 6 core tools: query_fact, record_event, validate_change,
get_constraints, record_correction, get_related_bugs.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .knowledge_graph import KnowledgeGraph
from .models import (
    Entity,
    Fact,
    Event,
    Constraint,
    Session,
    QueryFactResult,
    ValidationResult,
    BugInfo,
    Decision,
    TestOutcome,
    RegressionPrediction,
    SimulationResult,
    TestFailurePrediction,
    HealthReport,
)
from .config import Config
from .extraction import EntityExtractor
from .linters import LinterIntegration

logger = logging.getLogger(__name__)


class WorldModelTools:
    """Implementation of all MCP tools for the world model."""

    def __init__(self, kg: KnowledgeGraph, config: Config):
        self.kg = kg
        self.config = config
        self.extractor = EntityExtractor(config)
        self.linter = LinterIntegration(config.db_path.rsplit("/.claude", 1)[0])

    # ============================================================================
    # Tool 1: query_fact
    # ============================================================================

    async def query_fact(
        self,
        query: str,
        entity_type: Optional[str] = None,
        context: Dict[str, Any] = None,
        content_type: Optional[str] = None,
    ) -> QueryFactResult:
        """
        Query the knowledge graph for facts about entities.

        Args:
            query: Search query (e.g., "User.findByEmail", "JWT authentication")
            entity_type: Optional filter by entity type
            context: Additional context for the query
            content_type: Optional filter — 'rule', 'fact', or 'procedure'. When
                set, only rows with that exact content_type are returned; NULL
                (unclassified) rows are excluded. This is the load-bearing path
                for explicitly summoning procedures, which are excluded from
                auto-injection by design (v0.12.3 content-type routing).

        Returns:
            QueryFactResult with exists flag, matching facts, and confidence score
        """
        logger.info(
            f"Querying fact: {query}, entity_type: {entity_type}, "
            f"content_type: {content_type}"
        )

        # Search facts using full-text search
        result = await self.kg.query_facts(
            query=query,
            entity_type=entity_type,
            current_only=True,
            content_type=content_type,
        )

        # If no facts found, try searching entities directly
        if not result.facts:
            entities = await self.kg.find_entities(entity_type=entity_type, name=query)
            if not entities:
                # Fuzzy fallback for typos and abbreviations
                entities = await self.kg.find_entities_fuzzy(name=query, threshold=0.6, limit=5)
                if entities:
                    result.confidence = 0.6  # Lower confidence for fuzzy matches
            if entities:
                result.exists = True
                if result.confidence == 0.0:
                    result.confidence = 0.8
                result.alternatives = [e.name for e in entities[:5]]
                for e in entities[:5]:
                    result.facts.append(Fact(
                        fact_text=f"{e.entity_type} {e.name} exists in {e.file_path or 'unknown'}",
                        evidence_type="source_code",
                        evidence_path=e.file_path or "",
                        confidence=result.confidence,
                        status="canonical",
                    ))

        logger.info(
            f"Query result: exists={result.exists}, facts={len(result.facts)}, confidence={result.confidence}"
        )

        return result

    # ============================================================================
    # Tool 2: record_event
    # ============================================================================

    async def record_event(
        self,
        event_type: str,
        session_id: str,
        entities: List[str],
        description: str,
        reasoning: Optional[str] = None,
        evidence: Dict[str, Any] = None,
        success: bool = True,
    ) -> str:
        """
        Record a development event (file edit, test run, etc.).

        Args:
            event_type: Type of event
            session_id: Session ID
            entities: Entity names/paths involved
            description: Description of what happened
            reasoning: Why this action was taken
            evidence: Tool inputs/outputs, file contents, etc.
            success: Whether the event succeeded

        Returns:
            Event ID
        """
        logger.info(f"Recording event: {event_type} in session {session_id}")

        if evidence is None:
            evidence = {}

        # Create event
        event = Event(
            session_id=session_id,
            event_type=event_type,
            tool_name=evidence.get("tool_name"),
            tool_input=evidence.get("tool_input", {}),
            tool_output=evidence.get("tool_output", {}),
            reasoning=reasoning or description,
            success=success,
        )

        event_id = await self.kg.create_event(event)

        # Extract entities and facts from file edits
        if event_type == "file_edit" and evidence.get("tool_input"):
            file_path = evidence["tool_input"].get("file_path")
            old_string = evidence["tool_input"].get("old_string", "")
            new_string = evidence["tool_input"].get("new_string", "")

            if file_path:
                extracted_entities, extracted_facts = await self.extractor.extract_from_file_edit(
                    file_path=file_path,
                    old_content=old_string,
                    new_content=new_string,
                    reasoning=reasoning
                )

                # Store extracted entities
                for entity in extracted_entities:
                    await self.kg.create_entity(entity)

                # Store extracted facts
                for fact in extracted_facts:
                    fact.session_id = session_id
                    await self.kg.create_fact(fact)

                logger.info(
                    f"Extracted {len(extracted_entities)} entities and {len(extracted_facts)} facts"
                )

        logger.info(f"Event recorded: {event_id}")
        return json.dumps({"event_id": event_id, "status": "recorded"})

    # ============================================================================
    # Tool 3: validate_change
    # ============================================================================

    async def validate_change(
        self, change_type: str, file_path: str, proposed_content: str
    ) -> ValidationResult:
        """
        Validate a proposed code change against known constraints.

        Args:
            change_type: Type of change (edit, create, delete)
            file_path: Path to the file
            proposed_content: New content to validate

        Returns:
            ValidationResult with safe flag, violations, and suggestions
        """
        logger.info(f"Validating {change_type} for {file_path}")

        violations = []
        suggestions = []
        enforcement_history: Dict[str, int] = {}

        # 1. Check against learned constraints from the world model
        constraints = await self.kg.get_constraints(file_path)

        for constraint in constraints:
            if self._violates_constraint(proposed_content, constraint):
                # Increment violation counter (v0.5.0)
                new_count = await self.kg.increment_violation_count(constraint.id)
                enforcement_history[constraint.rule_name] = new_count

                violations.append(
                    {
                        "rule": constraint.rule_name,
                        "type": constraint.constraint_type,
                        "severity": constraint.severity,
                        "description": constraint.description,
                        "violation_count": new_count,
                        "source": "world_model",
                        "enforcement_summary": f"violated {new_count} times since {constraint.created_at:%Y-%m-%d}",
                    }
                )

                # Add suggestion from examples
                if constraint.examples:
                    example = constraint.examples[0]
                    suggestions.append(
                        f"Use {example.get('correct')} instead of {example.get('incorrect')}"
                    )

        # 2. Run external linters (ESLint, Pylint, Ruff)
        linter_violations = await self.linter.validate_code(file_path, proposed_content)

        for lv in linter_violations:
            violations.append(
                {
                    "rule": lv["rule"],
                    "type": "linting",
                    "severity": lv["severity"],
                    "description": lv["message"],
                    "line": lv.get("line"),
                    "column": lv.get("column"),
                    "source": "linter",
                }
            )

        # Check past test failures linked to this file
        try:
            outcomes = await self.kg.get_outcomes_for_file(file_path, limit=5)
            failed_tests = [o for o in outcomes if not o.passed]
            if failed_tests:
                test_names = [o.test_name for o in failed_tests[:3]]
                suggestions.append(
                    f"Warning: past changes to this file caused test failures: {', '.join(test_names)}"
                )
        except Exception:
            pass  # outcomes.db may not exist yet

        safe = len(violations) == 0
        confidence = 1.0 if safe else 0.95 if any(v.get("source") == "linter" for v in violations) else 0.9

        # Classify enforcement_decision into deny / defer / warn / proceed.
        # deny: error-level violation seen >= hard_threshold times
        # defer: warning-level violation seen >= defer_threshold times (pauses headless agents)
        # warn:  any other violation
        # proceed: clean
        hard_threshold = 3
        defer_threshold = 5
        wm_violations = [v for v in violations if v.get("source") == "world_model"]
        hard_violations = [
            v for v in wm_violations
            if v.get("severity") == "error" and v.get("violation_count", 0) >= hard_threshold
        ]
        defer_violations = [
            v for v in wm_violations
            if v.get("severity") == "warning" and v.get("violation_count", 0) >= defer_threshold
            and v not in hard_violations
        ]
        if hard_violations:
            enforcement_decision = "deny"
        elif defer_violations:
            enforcement_decision = "defer"
        elif wm_violations:
            enforcement_decision = "warn"
        elif violations:
            enforcement_decision = "warn"
        else:
            enforcement_decision = "proceed"

        result = ValidationResult(
            safe=safe,
            violations=violations,
            suggestions=suggestions,
            confidence=confidence,
            enforcement_history=enforcement_history,
            enforcement_decision=enforcement_decision,
        )

        logger.info(f"Validation result: safe={safe}, violations={len(violations)}, decision={enforcement_decision}")
        return result

    def _violates_constraint(self, content: str, constraint: Constraint) -> bool:
        """
        Check if content violates a constraint.

        This is a simple pattern-based check. Can be enhanced with AST parsing.
        """
        # Simple string matching for common violations
        if constraint.rule_name == "no-console" and "console.log" in content:
            return True

        # Check examples for patterns
        for example in constraint.examples:
            incorrect = example.get("incorrect", "")
            if incorrect and incorrect in content:
                return True

        return False

    # ============================================================================
    # Tool 4: get_constraints
    # ============================================================================

    async def get_constraints(
        self, file_path: str, constraint_types: Optional[List[str]] = None
    ) -> str:
        """
        Get constraints (linting rules, patterns, conventions) for a file.

        Args:
            file_path: Path to the file
            constraint_types: Optional filter by constraint types

        Returns:
            JSON string with constraints and examples
        """
        logger.info(f"Getting constraints for {file_path}")

        constraints = await self.kg.get_constraints(file_path)

        # Filter by type if specified
        if constraint_types:
            constraints = [c for c in constraints if c.constraint_type in constraint_types]

        # Format as JSON
        result = {
            "file_path": file_path,
            "constraints": [
                {
                    "rule_name": c.rule_name,
                    "type": c.constraint_type,
                    "description": c.description,
                    "severity": c.severity,
                    "violation_count": c.violation_count,
                    "examples": c.examples[:2],  # Limit to 2 examples
                }
                for c in constraints
            ],
        }

        logger.info(f"Found {len(constraints)} constraints")
        return json.dumps(result, indent=2)

    # ============================================================================
    # Tool 5: record_correction
    # ============================================================================

    async def record_correction(
        self,
        session_id: str,
        claude_action: Dict[str, Any],
        user_correction: Dict[str, Any],
        reasoning: str = "",
    ) -> str:
        """
        Record a user correction to Claude's output (high-priority learning signal).

        Args:
            session_id: Session ID
            claude_action: What Claude did
            user_correction: How the user corrected it
            reasoning: Inferred reason for the correction

        Returns:
            Constraint ID and learned pattern
        """
        logger.info(f"Recording correction in session {session_id}")

        # Use LLM-powered constraint inference
        file_path = claude_action.get("file_path", "")
        claude_content = str(claude_action.get("content", ""))
        user_content = str(user_correction.get("content", ""))

        # Try LLM-powered inference first
        constraint = await self.extractor.infer_constraint_from_correction(
            claude_content=claude_content,
            user_content=user_content,
            file_path=file_path
        )

        # Fallback to simple inference if LLM failed
        if not constraint:
            constraint_type = "style"
            rule_name = "user_preference"

            # Try to infer specific constraint
            if "console.log" in claude_content and "logger" in user_content:
                constraint_type = "linting"
                rule_name = "no-console"
                if not reasoning:
                    reasoning = "Use logger instead of console.log"

            constraint = Constraint(
                constraint_type=constraint_type,
                rule_name=rule_name,
                file_pattern=file_path,
                description=reasoning or "User-corrected pattern",
                violation_count=1,
                last_violated=datetime.now(),
                examples=[
                    {
                        "incorrect": claude_content,
                        "correct": user_content,
                    }
                ],
                severity="error",
            )

        # Store constraint (will increment count if exists)
        constraint_id = await self.kg.create_or_update_constraint(constraint)

        # Also record as event
        event = Event(
            session_id=session_id,
            event_type="user_correction",
            tool_name="UserEdit",
            tool_input=claude_action,
            tool_output=user_correction,
            reasoning=reasoning,
            success=True,
        )
        await self.kg.create_event(event)

        # Also record as a decision trace
        decision = Decision(
            session_id=session_id,
            tool_name="UserEdit",
            agent_proposal=claude_action,
            human_correction=user_correction,
            constraint_learned_id=constraint_id,
            file_path=file_path,
            reasoning=reasoning,
            decision_type="correction",
        )
        await self.kg.record_decision(decision)

        logger.info(f"Correction recorded: constraint_id={constraint_id}, rule={constraint.rule_name}")

        return json.dumps(
            {
                "constraint_id": constraint_id,
                "learned_pattern": {
                    "rule": constraint.rule_name,
                    "type": constraint.constraint_type,
                    "description": constraint.description,
                },
            }
        )

    # ============================================================================
    # Tool 6: get_related_bugs
    # ============================================================================

    async def get_related_bugs(self, file_path: str, change_description: str = "") -> str:
        """
        Get bugs fixed in a file and assess regression risk.

        Args:
            file_path: Path to the file
            change_description: Brief description of proposed change

        Returns:
            JSON string with bugs, risk score, and warnings
        """
        logger.info(f"Getting related bugs for {file_path}")

        bugs = await self.kg.get_bugs_for_file(file_path)

        # Calculate risk score based on number of bugs and critical regions
        risk_score = 0.0
        if bugs:
            risk_score = min(0.9, len(bugs) * 0.3)  # Max 0.9

        warnings = []
        for bug in bugs:
            if bug.critical_regions:
                for region in bug.critical_regions:
                    warnings.append(
                        f"Lines {region.get('lines')} preserve fix for {bug.bug_id}: {bug.description}"
                    )

        result = {
            "file_path": file_path,
            "bugs": [
                {
                    "bug_id": bug.bug_id,
                    "description": bug.description,
                    "fixed_at": bug.fixed_at.isoformat(),
                    "critical_regions": bug.critical_regions,
                }
                for bug in bugs
            ],
            "risk_score": risk_score,
            "warnings": warnings,
        }

        logger.info(f"Found {len(bugs)} bugs, risk_score={risk_score}")
        return json.dumps(result, indent=2)

    # ============================================================================
    # Tool 7: seed_project
    # ============================================================================

    async def seed_project(
        self, project_dir: Optional[str] = None, force: bool = False
    ) -> str:
        """
        Scan the project codebase and populate the knowledge graph.

        Args:
            project_dir: Project directory (defaults to inferring from db_path)
            force: Re-seed already processed files

        Returns:
            JSON string with seeding statistics
        """
        from .seeder import ProjectSeeder

        if project_dir is None:
            # Infer project dir from db_path (strip .claude/world-model)
            project_dir = str(Path(self.config.db_path).parent.parent.parent)

        logger.info(f"Seeding project: {project_dir} (force={force})")

        seeder = ProjectSeeder(project_dir, self.kg, self.config)
        result = await seeder.seed(force=force)

        return json.dumps({
            "files_scanned": result.files_scanned,
            "files_seeded": result.files_seeded,
            "entities_created": result.entities_created,
            "relationships_created": result.relationships_created,
            "skipped_files": result.skipped_files,
            "duration_seconds": result.duration_seconds,
        })

    # ============================================================================
    # Tool 8: ingest_pr_reviews
    # ============================================================================

    async def ingest_pr_reviews(
        self, repo: Optional[str] = None, count: int = 10
    ) -> str:
        """
        Pull GitHub PR review comments and convert them into constraints.

        Args:
            repo: GitHub repo (owner/repo). Auto-detected if omitted.
            count: Number of recent PRs to scan (default 10, max 50)

        Returns:
            JSON string with ingestion statistics
        """
        from .pr_reviews import PRReviewIngester

        count = min(count, 50)  # Cap at 50
        logger.info(f"Ingesting PR reviews: repo={repo}, count={count}")

        ingester = PRReviewIngester(self.kg, self.config)
        result = await ingester.ingest(repo=repo, count=count)

        return json.dumps({
            "prs_scanned": result.prs_scanned,
            "prs_skipped": result.prs_skipped,
            "comments_analyzed": result.comments_analyzed,
            "constraints_created": result.constraints_created,
            "constraints_updated": result.constraints_updated,
            "duration_seconds": result.duration_seconds,
        })

    # ============================================================================
    # Tool 9: record_decision (v0.4.0)
    # ============================================================================

    async def record_decision(
        self,
        session_id: str,
        tool_name: Optional[str] = None,
        agent_proposal: Dict[str, Any] = None,
        human_correction: Dict[str, Any] = None,
        file_path: Optional[str] = None,
        reasoning: Optional[str] = None,
        decision_type: str = "correction",
    ) -> str:
        """Record a decision trace."""
        decision = Decision(
            session_id=session_id,
            tool_name=tool_name,
            agent_proposal=agent_proposal or {},
            human_correction=human_correction or {},
            file_path=file_path,
            reasoning=reasoning,
            decision_type=decision_type,
        )
        did = await self.kg.record_decision(decision)
        logger.info(f"Decision recorded: {did} ({decision_type})")
        return json.dumps({"decision_id": did, "decision_type": decision_type})

    # ============================================================================
    # Tool 10: get_decision_log (v0.4.0)
    # ============================================================================

    async def get_decision_log(
        self,
        session_id: Optional[str] = None,
        file_path: Optional[str] = None,
        decision_type: Optional[str] = None,
        limit: int = 50,
    ) -> str:
        """Get decision traces with optional filters."""
        decisions = await self.kg.get_decisions(
            session_id=session_id,
            file_path=file_path,
            decision_type=decision_type,
            limit=limit,
        )
        return json.dumps({
            "decisions": [
                {
                    "id": d.id,
                    "session_id": d.session_id,
                    "timestamp": d.timestamp.isoformat(),
                    "tool_name": d.tool_name,
                    "agent_proposal": d.agent_proposal,
                    "human_correction": d.human_correction,
                    "file_path": d.file_path,
                    "reasoning": d.reasoning,
                    "decision_type": d.decision_type,
                }
                for d in decisions
            ],
            "count": len(decisions),
        })

    # ============================================================================
    # Tool 11: record_test_outcome (v0.4.0)
    # ============================================================================

    async def record_test_outcome(
        self, session_id: str, test_results: List[Dict[str, Any]]
    ) -> str:
        """Record test outcomes and link to recent code changes."""
        recent_events = await self.kg.get_recent_file_edit_events(session_id, limit=10)
        edited_files = list(dict.fromkeys(
            e.tool_input.get("file_path", "") for e in recent_events if e.tool_input.get("file_path")
        ))
        event_ids = [e.id for e in recent_events]

        created = 0
        failed = 0
        for tr in test_results:
            outcome = TestOutcome(
                session_id=session_id,
                test_name=tr.get("name", "unknown"),
                test_file=tr.get("file"),
                passed=tr.get("passed", True),
                error_message=tr.get("error"),
                linked_event_ids=event_ids,
                linked_file_paths=edited_files,
            )
            await self.kg.create_test_outcome(outcome)
            created += 1

            if not outcome.passed and edited_files:
                fact = Fact(
                    fact_text=f"Change to {', '.join(edited_files[:3])} caused {outcome.test_name} to fail",
                    evidence_type="test",
                    evidence_path=outcome.test_file or "unknown",
                    confidence=0.85,
                    status="canonical",
                    session_id=session_id,
                )
                await self.kg.create_fact(fact)
                failed += 1

        logger.info(f"Recorded {created} test outcomes ({failed} failures linked)")
        return json.dumps({"outcomes_recorded": created, "failures_linked": failed})

    # ============================================================================
    # Tool 12: get_co_edit_suggestions (v0.4.0)
    # ============================================================================

    async def get_co_edit_suggestions(self, file_path: str, limit: int = 5) -> str:
        """Get files commonly edited alongside the given file."""
        co_edits = await self.kg.get_co_edited_files(file_path, limit=limit)
        return json.dumps({
            "file_path": file_path,
            "suggestions": co_edits,
            "message": f"When editing {file_path}, consider also updating: "
                       + ", ".join(c["file_path"] for c in co_edits) if co_edits else "No co-edit patterns found yet.",
        })

    # ============================================================================
    # Tool 13: search_global (v0.4.0)
    # ============================================================================

    async def search_global(self, query: str, limit: int = 20) -> str:
        """Search entities across all registered projects."""
        from .registry import search_global as _search_global
        results = await _search_global(query, limit)
        return json.dumps({
            "query": query,
            "results": results,
            "count": len(results),
        })

    # ============================================================================
    # v0.5.0: Prediction layer
    # ============================================================================

    async def predict_regression(
        self, file_path: str, change_description: Optional[str] = None
    ) -> str:
        """Score regression risk for a proposed change."""
        from .predictions import RegressionPredictor
        predictor = RegressionPredictor(self.kg)
        result = await predictor.predict_regression(file_path, change_description)
        return result.model_dump_json(indent=2)

    async def simulate_change(self, file_path: str, change_description: str) -> str:
        """Project blast radius and historical outcomes for a proposed change."""
        from .predictions import RegressionPredictor
        predictor = RegressionPredictor(self.kg)
        result = await predictor.simulate_change(file_path, change_description)
        return result.model_dump_json(indent=2)

    async def predict_test_failures(self, file_paths: List[str]) -> str:
        """Surface tests likely to fail given file edits."""
        from .predictions import RegressionPredictor
        predictor = RegressionPredictor(self.kg)
        result = await predictor.predict_test_failures(file_paths)
        return result.model_dump_json(indent=2)

    async def promote_constraint(
        self, constraint_id: str, target_projects: Optional[List[str]] = None
    ) -> str:
        """Promote a constraint from this project to other registered projects."""
        from .promotion import promote_constraint as _promote
        results = await _promote(self.kg, constraint_id, target_projects)
        return json.dumps({
            "constraint_id": constraint_id,
            "results": results,
            "promoted_count": sum(1 for r in results if r["status"] == "success"),
            "skipped_count": sum(1 for r in results if r["status"] == "skipped"),
            "error_count": sum(1 for r in results if r["status"] == "error"),
        })

    # ============================================================================
    # v0.5.0: Memory health
    # ============================================================================

    async def get_health_report(self) -> str:
        """Get a comprehensive memory health report."""
        from .health import build_health_report
        report = await build_health_report(self.kg)
        return report.model_dump_json(indent=2)

    # ============================================================================
    # v0.5.0: Pre-action context aggregator
    # ============================================================================

    async def get_context_for_action(self, file_path: str, action_type: str) -> str:
        """Bundle constraints, decisions, bugs, co-edits, facts, and risk into one call."""
        import asyncio as _asyncio
        from .predictions import RegressionPredictor

        constraints_task = self.kg.get_constraints(file_path)
        decisions_task = self.kg.get_recent_decisions_for_file(file_path, limit=5)
        bugs_task = self.kg.get_bugs_for_file(file_path)
        co_edits_task = self.kg.get_co_edited_files(file_path, limit=10)

        # query_facts can fail with FTS5 syntax errors on file paths with dots/slashes
        try:
            facts_result = await self.kg.query_facts(file_path)
            related_facts = facts_result.facts[:5]
        except Exception:
            related_facts = []

        constraints = await constraints_task
        decisions = await decisions_task
        bugs = await bugs_task
        co_edits = await co_edits_task

        predictor = RegressionPredictor(self.kg)
        risk = await predictor.predict_regression(file_path, change_description=action_type)

        return json.dumps({
            "file_path": file_path,
            "action_type": action_type,
            "constraints": [
                {
                    "rule": c.rule_name,
                    "type": c.constraint_type,
                    "severity": c.severity,
                    "description": c.description,
                    "violation_count": c.violation_count,
                }
                for c in constraints
            ],
            "recent_decisions": [
                {
                    "type": d.decision_type,
                    "timestamp": d.timestamp.isoformat(),
                    "reasoning": d.reasoning,
                }
                for d in decisions
            ],
            "recent_bugs": [
                {
                    "description": b.description,
                    "fixed_at": b.fixed_at.isoformat(),
                    "critical_regions": b.critical_regions,
                }
                for b in bugs[:5]
            ],
            "co_edit_files": co_edits,
            "related_facts": [
                {
                    "text": f.fact_text[:200],
                    "evidence_path": f.evidence_path,
                    "confidence": f.confidence,
                }
                for f in related_facts
            ],
            "risk_score": risk.risk_score,
            "risk_level": risk.risk_level,
            "factors": risk.factors,
        }, indent=2)

    # ============================================================================
    # v0.5.0: Contradiction detection
    # ============================================================================

    async def find_contradictions(
        self, query: Optional[str] = None, limit: int = 20
    ) -> str:
        """Find pairs of facts that contradict each other."""
        contradictions = await self.kg.find_contradictions(query=query, limit=limit)
        return json.dumps({
            "query": query,
            "contradictions": contradictions,
            "count": len(contradictions),
        })

    # ============================================================================
    # v0.6.0 F2: Indexed Transcript Pointers
    # ============================================================================

    async def recall_transcript_range(
        self,
        session_id: str,
        line_start: Optional[int] = None,
        line_end: Optional[int] = None,
    ) -> str:
        """Hydrate a session transcript line range from disk."""
        from .transcript import read_range
        result = read_range(session_id, line_start=line_start, line_end=line_end)
        return json.dumps(result, indent=2)

    # ============================================================================
    # v0.6.0 F4: CLAUDE.md Export
    # ============================================================================

    async def export_claude_md(self, max_constraints: int = 20) -> str:
        """Generate a CLAUDE.md from the knowledge graph."""
        from .claude_md_generator import generate_claude_md
        md = await generate_claude_md(self.kg, max_constraints=max_constraints)
        return md

    # ============================================================================
    # v0.7.4 F1: AGENTS.md / .agents/skills/ constraint reader
    # ============================================================================

    async def get_agents_md_constraints(
        self,
        project_dir: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> str:
        """Return declarative constraints parsed from AGENTS.md, CLAUDE.md,
        GEMINI.md, and .agents/skills/*.md files in the project.

        These are mixed into PreToolUse enforcement alongside SQLite
        constraints (warn/info tier; never hard-deny on their own).
        """
        from .agents_md_reader import to_json, virtual_constraints_for
        from pathlib import Path as _Path
        project_dir = project_dir or "."
        rows = virtual_constraints_for(_Path(project_dir).resolve(), file_path)
        return to_json(rows)

    # ============================================================================
    # v0.7.0 F3: Confidence-weighted contradiction resolution
    # ============================================================================

    async def resolve_contradiction(
        self,
        fact_a_id: str,
        fact_b_id: str,
        strategy: str = "auto",
        notes: Optional[str] = None,
        confirmer: Optional[str] = None,
    ) -> str:
        """Pick a winner between two contradicting facts and mark the loser superseded.

        v0.8.0: when ``confirmer`` is set, the winning fact gets stamped
        with that identity, marking it as settled per the working group
        spec sketch on anthropics/claude-code#47023.
        """
        from .contradictions import resolve
        result = await resolve(
            self.kg, fact_a_id, fact_b_id,
            strategy=strategy, notes=notes, confirmer=confirmer,
        )
        return result.model_dump_json()

    async def _recent_canonical_facts(
        self,
        limit: int = 10,
        search: Optional[str] = None,
        content_types: Optional[List[str]] = None,
        exclude_content_types: Optional[List[str]] = None,
    ) -> list:
        """Return recent canonical facts as dicts. Private helper for injection.

        content_types / exclude_content_types are the v0.12.3 routing hooks:
        - content_types=[...]  -> only rows where content_type IN (...)
                                  NULL rows are matched if 'NULL' is in the list.
        - exclude_content_types=[...] -> rows where content_type NOT IN (...)
                                         NULL rows are always kept unless
                                         'NULL' is in the exclusion list.

        Both filters compose. The default (both None) preserves pre-v0.12.3
        behavior — return every canonical fact regardless of content_type.
        """
        import aiosqlite

        clauses = ["status = 'canonical'"]
        params: List[Any] = []

        if search:
            clauses.append("fact_text LIKE ?")
            params.append(f"%{search}%")

        if content_types:
            null_included = "NULL" in content_types
            non_null = [c for c in content_types if c != "NULL"]
            if non_null and null_included:
                placeholders = ",".join("?" * len(non_null))
                clauses.append(f"(content_type IN ({placeholders}) OR content_type IS NULL)")
                params.extend(non_null)
            elif non_null:
                placeholders = ",".join("?" * len(non_null))
                clauses.append(f"content_type IN ({placeholders})")
                params.extend(non_null)
            elif null_included:
                clauses.append("content_type IS NULL")

        if exclude_content_types:
            null_excluded = "NULL" in exclude_content_types
            non_null = [c for c in exclude_content_types if c != "NULL"]
            if non_null and null_excluded:
                placeholders = ",".join("?" * len(non_null))
                clauses.append(
                    f"(content_type NOT IN ({placeholders}) AND content_type IS NOT NULL)"
                )
                params.extend(non_null)
            elif non_null:
                placeholders = ",".join("?" * len(non_null))
                clauses.append(
                    f"(content_type NOT IN ({placeholders}) OR content_type IS NULL)"
                )
                params.extend(non_null)
            elif null_excluded:
                clauses.append("content_type IS NOT NULL")

        where = " AND ".join(clauses)
        sql = (
            f"SELECT id, fact_text, valid_at, content_type FROM facts "
            f"WHERE {where} ORDER BY valid_at DESC LIMIT ?"
        )
        params.append(limit)

        async with aiosqlite.connect(self.kg.facts_db) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [
                {
                    "id": r["id"],
                    "fact_text": r["fact_text"],
                    "valid_at": r["valid_at"],
                    "content_type": r["content_type"],
                }
                for r in rows
            ]

    # ============================================================================
    # v0.7.0 F5: Compaction audit log
    # ============================================================================

    async def record_compaction_audit(
        self,
        session_id: Optional[str] = None,
        pre_compact_tokens: Optional[int] = None,
        post_compact_tokens: Optional[int] = None,
        facts_injected: int = 0,
        constraints_injected: int = 0,
        injection_event: Optional[str] = None,
        raw_summary: Optional[str] = None,
    ) -> str:
        """Record one compaction event in the audit log."""
        from .audit import record_compaction
        entry = await record_compaction(
            self.kg,
            session_id=session_id,
            pre_compact_tokens=pre_compact_tokens,
            post_compact_tokens=post_compact_tokens,
            facts_injected=facts_injected,
            constraints_injected=constraints_injected,
            injection_event=injection_event,
            raw_summary=raw_summary,
        )
        return entry.model_dump_json()

    async def get_compaction_audit(
        self,
        session_id: Optional[str] = None,
        limit: int = 50,
    ) -> str:
        """List recent compaction audit entries, most-recent first."""
        from .audit import list_compactions
        entries = await list_compactions(self.kg, session_id=session_id, limit=limit)
        return json.dumps(
            {
                "count": len(entries),
                "entries": [json.loads(e.model_dump_json()) for e in entries],
            },
            indent=2,
        )

    # ============================================================================
    # v0.7.0 F1: PostCompact / UserPromptSubmit auto-injection context
    # ============================================================================

    async def get_injection_context(
        self,
        event_type: str,
        project_hint: Optional[str] = None,
        max_constraints: int = 10,
        max_facts: int = 10,
    ) -> str:
        """Return a compact context bundle to inject after compaction or on user prompt.

        event_type: one of "PostCompact", "UserPromptSubmit", "SessionStart"
        project_hint: optional substring to bias fact selection toward (e.g. file path or topic)

        v0.12.3 content-type routing:
          - content_type='rule'      always-inject; gets its own section, drawn first
          - content_type='fact' or NULL   search-on-demand; fills remaining slots
          - content_type='procedure' never auto-inject; excluded from this bundle
                                     entirely (surface only via query_fact with
                                     an explicit content_type='procedure' filter)

        The max_facts budget covers both the rules section and the facts
        section combined. Rules get preference: up to `max_facts` rules,
        then the remainder is filled from the fact/NULL pool.
        """
        # Top constraints by violation_count (existing get_constraints orders by it desc)
        all_constraints = await self.kg.get_constraints()
        constraints = all_constraints[:max_constraints]

        # v0.12.3: two-pool routing. Rules first (always-inject), then
        # search-on-demand facts fill remaining slots. Procedures are
        # excluded from auto-injection by design.
        rules = await self._recent_canonical_facts(
            limit=max_facts,
            search=project_hint,
            content_types=["rule"],
        )
        remaining = max(0, max_facts - len(rules))
        recent_facts = await self._recent_canonical_facts(
            limit=remaining,
            search=project_hint,
            content_types=["fact", "NULL"],
        ) if remaining else []

        lines: list = []
        if constraints:
            lines.append("## Active constraints (top by violation count)")
            for c in constraints:
                lines.append(
                    f"- {c.rule_name}: {c.description} (violated {c.violation_count}x)"
                )

        if rules:
            if lines:
                lines.append("")
            lines.append("## Rules (always active)")
            for f in rules:
                lines.append(f"- {f['fact_text']}")

        if recent_facts:
            if lines:
                lines.append("")
            lines.append("## Recent canonical facts")
            for f in recent_facts:
                lines.append(f"- {f['fact_text']}")

        injection = "\n".join(lines).strip()
        return json.dumps(
            {
                "event_type": event_type,
                "injection": injection,
                "rules_count": len(rules),
                "facts_count": len(recent_facts),
                "constraints_count": len(constraints),
            },
            indent=2,
        )
