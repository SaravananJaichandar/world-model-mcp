"""
Prediction layer for world-model-mcp.

Computes regression risk, change simulation, and test failure prediction
from data already in the knowledge graph. No ML training required - pure
statistical scoring over historical signals.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from .knowledge_graph import KnowledgeGraph
from .models import RegressionPrediction, SimulationResult, TestFailurePrediction

logger = logging.getLogger(__name__)


class RegressionPredictor:
    """Predicts regression risk and change impact from KG history."""

    def __init__(self, kg: KnowledgeGraph):
        self.kg = kg

    async def predict_regression(
        self, file_path: str, change_description: Optional[str] = None
    ) -> RegressionPrediction:
        """
        Score regression risk for a proposed change to file_path.

        Risk = 0.30 * past_bugs
             + 0.20 * recent_test_failures (last 30d)
             + 0.10 * constraint_violations (sum of violation_counts)
             + 0.05 * co_edit_blast_radius
        Capped at 1.0.
        """
        bugs = await self.kg.get_bugs_for_file(file_path)
        past_bugs = len(bugs)

        outcomes = await self.kg.get_outcomes_for_file(file_path, limit=50)
        cutoff = datetime.now() - timedelta(days=30)
        recent_test_failures = sum(
            1 for o in outcomes if not o.passed and o.timestamp > cutoff
        )

        constraints = await self.kg.get_constraints(file_path)
        constraint_violations = sum(c.violation_count for c in constraints)

        co_edits = await self.kg.get_co_edited_files(file_path, limit=20)
        co_edit_blast_radius = len(co_edits)

        score = min(
            1.0,
            0.30 * past_bugs
            + 0.20 * recent_test_failures
            + 0.10 * constraint_violations
            + 0.05 * co_edit_blast_radius,
        )

        if score < 0.3:
            level = "low"
        elif score <= 0.6:
            level = "medium"
        else:
            level = "high"

        return RegressionPrediction(
            file_path=file_path,
            change_description=change_description,
            risk_score=round(score, 3),
            risk_level=level,
            factors={
                "past_bugs": past_bugs,
                "recent_test_failures": recent_test_failures,
                "constraint_violations": constraint_violations,
                "co_edit_blast_radius": co_edit_blast_radius,
            },
        )

    async def simulate_change(
        self, file_path: str, change_description: str
    ) -> SimulationResult:
        """
        Project the blast radius and historical outcomes for a proposed change.

        Blast radius = co-edited files (from trajectories) + imports (1 hop).
        Historical outcomes = recent test results for files in blast radius.
        """
        co_edits = await self.kg.get_co_edited_files(file_path, limit=10)
        blast_radius = [
            {"file_path": c["file_path"], "kind": "co_edit", "weight": str(c["co_edit_count"])}
            for c in co_edits
        ]

        # Historical outcomes for affected files
        historical_outcomes = []
        for entry in blast_radius[:5]:
            outs = await self.kg.get_outcomes_for_file(entry["file_path"], limit=3)
            for o in outs:
                historical_outcomes.append({
                    "file": entry["file_path"],
                    "test_name": o.test_name,
                    "passed": o.passed,
                    "timestamp": o.timestamp.isoformat(),
                })

        # Confidence based on signal strength
        signal_count = (1 if co_edits else 0) + (1 if historical_outcomes else 0)
        confidence = 0.4 + 0.2 * signal_count

        return SimulationResult(
            file_path=file_path,
            change_description=change_description,
            blast_radius=blast_radius,
            historical_outcomes=historical_outcomes,
            confidence=round(confidence, 3),
        )

    async def predict_test_failures(self, file_paths: List[str]) -> TestFailurePrediction:
        """Surface tests likely to fail based on historical failure rates."""
        rates = await self.kg.get_test_failure_rates(file_paths, min_runs=1)
        # Filter to tests with > 30% failure rate
        likely = [r for r in rates if r["failure_rate"] > 0.3]
        return TestFailurePrediction(
            file_paths=file_paths,
            likely_failing_tests=likely,
        )
