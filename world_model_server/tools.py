"""
MCP tool implementations for the World Model server.

Implements the 6 core tools: query_fact, record_event, validate_change,
get_constraints, record_correction, get_related_bugs.
"""

import json
import logging
from datetime import datetime
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
        self, query: str, entity_type: Optional[str] = None, context: Dict[str, Any] = None
    ) -> QueryFactResult:
        """
        Query the knowledge graph for facts about entities.

        Args:
            query: Search query (e.g., "User.findByEmail", "JWT authentication")
            entity_type: Optional filter by entity type
            context: Additional context for the query

        Returns:
            QueryFactResult with exists flag, matching facts, and confidence score
        """
        logger.info(f"Querying fact: {query}, entity_type: {entity_type}")

        # Search facts using full-text search
        result = await self.kg.query_facts(query=query, entity_type=entity_type, current_only=True)

        # If no facts found, try searching entities directly
        if not result.facts and entity_type:
            entities = await self.kg.find_entities(entity_type=entity_type, name=query)
            if entities:
                # Found matching entities, treat as exists
                result.exists = True
                result.confidence = 0.8
                result.alternatives = [e.name for e in entities[:5]]

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

        # 1. Check against learned constraints from the world model
        constraints = await self.kg.get_constraints(file_path)

        for constraint in constraints:
            if self._violates_constraint(proposed_content, constraint):
                violations.append(
                    {
                        "rule": constraint.rule_name,
                        "type": constraint.constraint_type,
                        "severity": constraint.severity,
                        "description": constraint.description,
                        "violation_count": constraint.violation_count,
                        "source": "world_model",
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

        safe = len(violations) == 0
        confidence = 1.0 if safe else 0.95 if any(v.get("source") == "linter" for v in violations) else 0.9

        result = ValidationResult(
            safe=safe, violations=violations, suggestions=suggestions, confidence=confidence
        )

        logger.info(f"Validation result: safe={safe}, violations={len(violations)}")
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
