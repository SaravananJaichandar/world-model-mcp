"""
Confidence-weighted contradiction resolution (v0.7.0 F3).

Given two facts that contradict each other, pick a winner using one of:
  keep_higher_confidence  -- score = confidence
  keep_most_recent        -- score = valid_at timestamp
  keep_most_sources       -- score = source_count
  supersede_a / supersede_b -- explicit caller decision
  manual                  -- no automatic action; caller resolves out-of-band

Resolution writes status='superseded' + invalid_at=now on the loser, leaving the
winner untouched. All strategies are deterministic given the inputs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from .models import ContradictionResolution


def _score(strategy: str, fact: Dict[str, Any]) -> float:
    """Return the score used to rank the fact under the given strategy.

    Higher score wins.
    """
    if strategy == "keep_higher_confidence":
        return float(fact.get("confidence") or 0.0)
    if strategy == "keep_most_recent":
        valid_at = fact.get("valid_at") or fact.get("created_at")
        if not valid_at:
            return 0.0
        try:
            return datetime.fromisoformat(valid_at).timestamp()
        except (TypeError, ValueError):
            return 0.0
    if strategy == "keep_most_sources":
        return float(fact.get("source_count") or 1)
    return 0.0


def pick_winner(strategy: str, fact_a: Dict[str, Any], fact_b: Dict[str, Any]) -> Optional[str]:
    """Return 'a', 'b', or None (tie) under the given strategy."""
    if strategy == "supersede_a":
        return "b"
    if strategy == "supersede_b":
        return "a"
    if strategy == "manual":
        return None
    score_a = _score(strategy, fact_a)
    score_b = _score(strategy, fact_b)
    if score_a > score_b:
        return "a"
    if score_b > score_a:
        return "b"
    return None


def suggest_strategy(fact_a: Dict[str, Any], fact_b: Dict[str, Any]) -> str:
    """Recommend a resolution strategy based on which signal is most informative.

    Priority:
      1. If source_count differs meaningfully (>=2x), prefer keep_most_sources.
      2. Else if confidence differs by >=0.1, prefer keep_higher_confidence.
      3. Else fall back to keep_most_recent.
    """
    src_a = float(fact_a.get("source_count") or 1)
    src_b = float(fact_b.get("source_count") or 1)
    if max(src_a, src_b) >= 2 * min(src_a, src_b) and max(src_a, src_b) >= 2:
        return "keep_most_sources"

    conf_a = float(fact_a.get("confidence") or 1.0)
    conf_b = float(fact_b.get("confidence") or 1.0)
    if abs(conf_a - conf_b) >= 0.1:
        return "keep_higher_confidence"

    return "keep_most_recent"


async def resolve(
    kg,
    fact_a_id: str,
    fact_b_id: str,
    strategy: str = "auto",
    notes: Optional[str] = None,
) -> ContradictionResolution:
    """Resolve a contradiction by superseding the loser.

    strategy="auto" picks one via suggest_strategy based on the actual fact rows.
    Returns a ContradictionResolution record with winner/loser ids.
    """
    fact_a = await kg.get_fact_by_id(fact_a_id)
    fact_b = await kg.get_fact_by_id(fact_b_id)
    if not fact_a or not fact_b:
        raise ValueError("One or both fact ids do not exist")

    if strategy == "auto":
        strategy = suggest_strategy(fact_a, fact_b)

    valid = {
        "keep_higher_confidence",
        "keep_most_recent",
        "keep_most_sources",
        "supersede_a",
        "supersede_b",
        "manual",
    }
    if strategy not in valid:
        raise ValueError(f"Unknown strategy: {strategy}")

    winner = pick_winner(strategy, fact_a, fact_b)

    if strategy == "manual" or winner is None:
        return ContradictionResolution(
            fact_a_id=fact_a_id,
            fact_b_id=fact_b_id,
            strategy="manual" if strategy == "manual" else strategy,
            winner_id=None,
            loser_id=None,
            notes=notes or "Tie; manual resolution required",
        )

    winner_id = fact_a_id if winner == "a" else fact_b_id
    loser_id = fact_b_id if winner == "a" else fact_a_id

    await kg.supersede_fact(loser_id, reason=f"superseded by {winner_id} via {strategy}")

    return ContradictionResolution(
        fact_a_id=fact_a_id,
        fact_b_id=fact_b_id,
        strategy=strategy,
        winner_id=winner_id,
        loser_id=loser_id,
        notes=notes,
    )
