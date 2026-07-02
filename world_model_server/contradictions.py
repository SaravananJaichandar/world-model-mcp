"""
Confidence-weighted contradiction resolution.

Given two facts that contradict each other, pick a winner using one of:
  keep_higher_confidence          -- score = confidence
  keep_higher_confidence_decayed  -- score = confidence decayed by evidence-type half-life (v0.11)
  keep_most_recent                -- score = valid_at timestamp
  keep_most_sources               -- score = source_count
  supersede_a / supersede_b       -- explicit caller decision
  manual                          -- no automatic action; caller resolves out-of-band

The ``auto`` meta-strategy (v0.11 rewrite) folds in confirmer awareness and
per-evidence-type decay before falling through to the v0.7.0 heuristic:

  1. Settled beats pending: if one fact has a ``confirmer`` set and the other
     does not, the settled fact wins outright. Handles the categories
     ``confirmer_overrides_pending`` and ``settled_beats_higher_confidence``
     in the v0.8.1 contradiction benchmark.
  2. Decay-aware confidence: if either fact carries ``evidence_type``, both
     confidences are aged with ``compute_decayed_confidence`` before
     comparison. Handles ``decay_advantage_session_vs_source``,
     ``decay_advantage_stale_session``, ``evidence_type_user_correction``.
  3. Fall through: v0.7.0 heuristic (source_count → confidence → recency).

Resolution writes status='superseded' + invalid_at=now on the loser, leaving
the winner untouched. All strategies are deterministic given the inputs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from .decay import compute_decayed_confidence
from .models import ContradictionResolution

# Threshold below which decayed-confidence differences are treated as a tie.
# Matches the v0.8.1 benchmark runner's threshold so contradictions.py and
# the benchmark stay consistent.
_DECAY_TIE_EPSILON = 0.05


def _score(strategy: str, fact: Dict[str, Any]) -> float:
    """Return the score used to rank the fact under the given strategy.

    Higher score wins.
    """
    if strategy == "keep_higher_confidence":
        return float(fact.get("confidence") or 0.0)
    if strategy == "keep_higher_confidence_decayed":
        return float(compute_decayed_confidence(
            fact.get("confidence", 1.0),
            fact.get("evidence_type"),
            fact.get("valid_at"),
        ))
    if strategy == "keep_most_recent":
        valid_at = fact.get("valid_at") or fact.get("created_at")
        if not valid_at:
            return 0.0
        try:
            return datetime.fromisoformat(valid_at).timestamp()
        except (TypeError, ValueError):
            return 0.0
    if strategy == "keep_most_sources":
        # v0.11: prefer distinct-tools count when source_tools is present.
        # Three assertions from one tool are less independent evidence than
        # two assertions from two different tools. Falls back to raw
        # source_count when source_tools not present.
        tools = fact.get("source_tools")
        if tools:
            return float(len(set(tools)))
        return float(fact.get("source_count") or 1)
    return 0.0


def _valid_at_timestamp(fact: Dict[str, Any]) -> Optional[float]:
    """Return valid_at as a POSIX timestamp, or None when unavailable."""
    valid_at = fact.get("valid_at") or fact.get("created_at")
    if not valid_at:
        return None
    try:
        return datetime.fromisoformat(valid_at).timestamp()
    except (TypeError, ValueError):
        return None


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
    if strategy == "keep_higher_confidence_decayed":
        if abs(score_a - score_b) < _DECAY_TIE_EPSILON:
            return None
    if score_a > score_b:
        return "a"
    if score_b > score_a:
        return "b"
    return None


# Auto-strategy tuning: gaps below these thresholds count as a tie and let
# control fall through to the next rung. Chosen to match the v0.8.1
# contradiction benchmark's category boundaries.
_AUTO_SOURCE_RATIO = 2.0            # keep_most_sources requires 2x AND max>=2
_AUTO_SOURCE_MIN_MAX = 2
_AUTO_CONFIDENCE_GAP = 0.1          # confidence gap needed to decide
_AUTO_RECENCY_GAP_SECONDS = 2 * 86400   # need >=2 days apart to prefer recency


def _pick_winner_auto(
    fact_a: Dict[str, Any],
    fact_b: Dict[str, Any],
) -> tuple[Optional[str], str]:
    """v0.11 auto strategy: confirmer-aware + decay-aware, then v0.7 fall-through.

    Order:
      1. Settled (confirmer != None) beats pending. Handles
         ``confirmer_overrides_pending`` and ``settled_beats_higher_confidence``.
      2. If either side has ``evidence_type``, use decayed confidence.
         Handles the three decay categories in the v0.8.1 benchmark.
      3. Fall through to the v0.7 heuristic (source_count → confidence →
         recency), but each rung requires a meaningful gap. If all three
         signals are within tie thresholds, return None (surface for manual
         review) rather than break the tie arbitrarily.

    Returns ``(winner, tier)`` where ``winner`` is 'a', 'b', or None, and
    ``tier`` is the concrete strategy name that fired (used to stamp the
    ContradictionResolution audit record so callers can see which rule
    resolved the pair). None winner means "genuinely ambiguous — surface
    for manual review."
    """
    # Priority 1: settled beats pending. Only fires when the two facts differ
    # in confirmer-presence; if both are settled or both pending, this rung is
    # neutral and control falls through.
    a_settled = fact_a.get("confirmer") is not None
    b_settled = fact_b.get("confirmer") is not None
    if a_settled and not b_settled:
        return "a", "keep_higher_confidence"
    if b_settled and not a_settled:
        return "b", "keep_higher_confidence"

    # Priority 2: decay-aware confidence when at least one side carries the
    # evidence_type field that the decay function needs.
    if fact_a.get("evidence_type") is not None or fact_b.get("evidence_type") is not None:
        decayed = pick_winner("keep_higher_confidence_decayed", fact_a, fact_b)
        if decayed is not None:
            return decayed, "keep_higher_confidence_decayed"

    # Priority 3a: source-count gap must be at least 2x AND max >= 2.
    src_a = _score("keep_most_sources", fact_a)
    src_b = _score("keep_most_sources", fact_b)
    max_src = max(src_a, src_b)
    min_src = min(src_a, src_b)
    if max_src >= _AUTO_SOURCE_MIN_MAX and max_src >= _AUTO_SOURCE_RATIO * min_src:
        if src_a != src_b:
            return ("a" if src_a > src_b else "b"), "keep_most_sources"

    # Priority 3a-bis: user assertion beats model assertion at tied source
    # count. A human's direct statement is stronger evidence than a model's,
    # even when raw source_count is identical. Only fires when one side
    # names "user" as a source_tool and the other side does not.
    tools_a = set(fact_a.get("source_tools") or [])
    tools_b = set(fact_b.get("source_tools") or [])
    a_has_user = "user" in tools_a
    b_has_user = "user" in tools_b
    if a_has_user and not b_has_user:
        return "a", "keep_most_sources"
    if b_has_user and not a_has_user:
        return "b", "keep_most_sources"

    # Priority 3b: confidence gap must be at least _AUTO_CONFIDENCE_GAP.
    # Guard against 0.0 confidence being falsy-coerced to the default 1.0.
    conf_a_raw = fact_a.get("confidence")
    conf_b_raw = fact_b.get("confidence")
    conf_a = float(1.0 if conf_a_raw is None else conf_a_raw)
    conf_b = float(1.0 if conf_b_raw is None else conf_b_raw)
    if abs(conf_a - conf_b) >= _AUTO_CONFIDENCE_GAP:
        return ("a" if conf_a > conf_b else "b"), "keep_higher_confidence"

    # Priority 3c: recency gap must be at least _AUTO_RECENCY_GAP_SECONDS.
    ts_a = _valid_at_timestamp(fact_a)
    ts_b = _valid_at_timestamp(fact_b)
    if ts_a is not None and ts_b is not None:
        if abs(ts_a - ts_b) >= _AUTO_RECENCY_GAP_SECONDS:
            return ("a" if ts_a > ts_b else "b"), "keep_most_recent"

    # All three signals are within tie thresholds. Surface for manual review.
    return None, "manual"


def suggest_strategy(fact_a: Dict[str, Any], fact_b: Dict[str, Any]) -> str:
    """Recommend a non-auto resolution strategy for the two facts.

    This is the v0.7 heuristic and is preserved for backwards compatibility.
    The v0.11 ``auto`` handling in ``_pick_winner_auto`` uses this only as
    the final fall-through rung; callers that pass ``strategy="auto"`` to
    ``resolve()`` get the full confirmer-aware + decay-aware ranking, not
    this heuristic alone.

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
    confirmer: Optional[str] = None,
) -> ContradictionResolution:
    """Resolve a contradiction by superseding the loser.

    strategy="auto" picks one via suggest_strategy based on the actual fact rows.
    Returns a ContradictionResolution record with winner/loser ids.

    v0.8.0: when ``confirmer`` is set, the winning fact gets its
    ``confirmer`` column stamped with that value, moving the fact from
    pending to settled per the spec sketch on
    anthropics/claude-code#47023. The confirmer is the identity of who
    closed the loop (typically ``"user"`` or another tool that
    corroborated the winning fact externally).
    """
    fact_a = await kg.get_fact_by_id(fact_a_id)
    fact_b = await kg.get_fact_by_id(fact_b_id)
    if not fact_a or not fact_b:
        raise ValueError("One or both fact ids do not exist")

    # v0.11 auto rewrite: confirmer-aware + decay-aware, then fall through
    # to the v0.7 heuristic. The ContradictionResolution's strategy field
    # is stamped with the concrete tier that fired (not "auto") so audit
    # readers can see which rule resolved the pair, preserving the
    # v0.7 contract.
    if strategy == "auto":
        winner, tier = _pick_winner_auto(fact_a, fact_b)
        if winner is None:
            return ContradictionResolution(
                fact_a_id=fact_a_id,
                fact_b_id=fact_b_id,
                strategy="manual",
                winner_id=None,
                loser_id=None,
                notes=notes or "Auto strategy could not break the tie; manual resolution required",
            )
        winner_id = fact_a_id if winner == "a" else fact_b_id
        loser_id = fact_b_id if winner == "a" else fact_a_id
        await kg.supersede_fact(loser_id, reason=f"superseded by {winner_id} via auto/{tier}")
        if confirmer:
            import aiosqlite
            async with aiosqlite.connect(kg.facts_db) as db:
                await db.execute(
                    "UPDATE facts SET confirmer = ? WHERE id = ?",
                    (confirmer, winner_id),
                )
                await db.commit()
        return ContradictionResolution(
            fact_a_id=fact_a_id,
            fact_b_id=fact_b_id,
            strategy=tier,
            winner_id=winner_id,
            loser_id=loser_id,
            notes=notes,
        )

    valid = {
        "keep_higher_confidence",
        "keep_higher_confidence_decayed",
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

    # v0.8.0: stamp confirmer on the winning fact if provided. This
    # marks the fact as settled per the working group spec sketch.
    if confirmer:
        import aiosqlite
        async with aiosqlite.connect(kg.facts_db) as db:
            await db.execute(
                "UPDATE facts SET confirmer = ? WHERE id = ?",
                (confirmer, winner_id),
            )
            await db.commit()

    return ContradictionResolution(
        fact_a_id=fact_a_id,
        fact_b_id=fact_b_id,
        strategy=strategy,
        winner_id=winner_id,
        loser_id=loser_id,
        notes=notes,
    )
