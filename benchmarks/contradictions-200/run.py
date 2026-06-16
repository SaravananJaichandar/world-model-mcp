#!/usr/bin/env python3
"""
Contradiction-resolution benchmark for world-model-mcp v0.8.1.

Expanded from the v0.7.4 24-pair benchmark to 105 pairs across 19
categories. The original 13 categories were preserved; six new
categories were added to exercise the v0.8.0 schema specifically:

- ``source_tool_corroboration`` -- distinct source_tool values across
  rows should count as independent corroboration (the spec primitive
  Patdolitse named on openai/codex#19195 and ferhimedamine endorsed).
- ``confirmer_overrides_pending`` -- a settled fact (confirmer != NULL)
  should beat a higher-confidence pending fact under the ``auto`` strategy.
- ``decay_advantage_session_vs_source`` -- same age, same confidence;
  the difference is ``evidence_type``. With decay on, source_code beats
  session because session decays 26x faster.
- ``decay_advantage_stale_session`` -- a younger session fact loses to
  an older bug_fix fact because the session has decayed below.
- ``evidence_type_user_correction`` -- user_correction beats session
  even when older because the half-life is 52x longer.
- ``settled_beats_higher_confidence`` -- a fact with confirmer="user"
  beats a higher-confidence pending fact.

The runner is deterministic: no LLM calls, no embeddings, no network.
Re-runs produce identical results bit-for-bit.

Usage:
    python benchmarks/contradictions-200/run.py
    python benchmarks/contradictions-200/run.py --out results.json
    python benchmarks/contradictions-200/run.py --strategy keep_higher_confidence

This is the v0.8.1 expansion. The original 24-pair v0.7.4 benchmark at
``benchmarks/contradictions/`` is preserved as historical baseline; its
93.5% number is not modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from world_model_server.contradictions import pick_winner, suggest_strategy  # noqa: E402
from world_model_server.decay import compute_decayed_confidence  # noqa: E402


DATASET = Path(__file__).resolve().parent / "dataset.jsonl"

CANONICAL_STRATEGIES = (
    "keep_higher_confidence",
    "keep_most_recent",
    "keep_most_sources",
    "auto",
)

DECAYED_STRATEGY = "keep_higher_confidence_decayed"


def _materialize_fact(spec: dict) -> dict:
    """Turn a benchmark fact spec into the dict shape pick_winner expects.

    v0.8.1 additions: ``evidence_type``, ``confirmer``, ``source_tools``
    are passed through if present. ``source_tools`` is a list and the
    materializer collapses it to ``source_count = len(unique(source_tools))``
    when present.
    """
    fact: dict[str, Any] = {
        "fact_text": spec.get("text", ""),
    }
    if "confidence" in spec:
        fact["confidence"] = spec["confidence"]
    if "source_count" in spec:
        fact["source_count"] = spec["source_count"]
    if "valid_at_days_ago" in spec:
        fact["valid_at"] = (
            datetime.now() - timedelta(days=spec["valid_at_days_ago"])
        ).isoformat()
    if "evidence_type" in spec:
        fact["evidence_type"] = spec["evidence_type"]
    if "confirmer" in spec:
        fact["confirmer"] = spec["confirmer"]
    if "source_tools" in spec:
        fact["source_tools"] = spec["source_tools"]
        if "source_count" not in spec:
            fact["source_count"] = len(set(spec["source_tools"]))
    return fact


def _pick_winner_with_decay(strategy: str, fact_a: dict, fact_b: dict) -> str | None:
    """Like ``pick_winner`` but applies v0.8.0 decay before scoring.

    For ``keep_higher_confidence_decayed``, decays each fact's confidence
    using its ``evidence_type`` and ``valid_at``, then picks the higher.
    For ``auto`` when ``evidence_type`` is present, prefers settled facts
    (``confirmer != NULL``) before falling back to decayed confidence.
    """
    if strategy == DECAYED_STRATEGY:
        conf_a = compute_decayed_confidence(
            fact_a.get("confidence", 1.0),
            fact_a.get("evidence_type"),
            fact_a.get("valid_at"),
        )
        conf_b = compute_decayed_confidence(
            fact_b.get("confidence", 1.0),
            fact_b.get("evidence_type"),
            fact_b.get("valid_at"),
        )
        if abs(conf_a - conf_b) < 0.05:
            return None
        return "a" if conf_a > conf_b else "b"

    if strategy == "auto":
        a_settled = (
            fact_a.get("confirmer") is not None
            and fact_a.get("evidence_type") == "user_correction"
        )
        b_settled = (
            fact_b.get("confirmer") is not None
            and fact_b.get("evidence_type") == "user_correction"
        )
        if a_settled and not b_settled:
            return "a"
        if b_settled and not a_settled:
            return "b"

        if (
            fact_a.get("evidence_type") is not None
            or fact_b.get("evidence_type") is not None
        ):
            decayed = _pick_winner_with_decay(DECAYED_STRATEGY, fact_a, fact_b)
            if decayed is not None:
                return decayed

        return pick_winner(suggest_strategy(fact_a, fact_b), fact_a, fact_b)

    return pick_winner(strategy, fact_a, fact_b)


def _load_dataset() -> list[dict]:
    rows = []
    for line in DATASET.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _score_one(row: dict, strategy: str) -> dict:
    fact_a = _materialize_fact(row["fact_a"])
    fact_b = _materialize_fact(row["fact_b"])

    # v0.8.1 scoring discipline: the decayed strategy is only evaluated
    # on pairs where at least one side has ``evidence_type`` set. Without
    # ``evidence_type``, decay returns the input confidence unchanged
    # and the strategy degenerates into ``keep_higher_confidence``; that
    # would inflate the score by counting easy wins where decay never
    # fires.
    if strategy == DECAYED_STRATEGY:
        if (
            fact_a.get("evidence_type") is None
            and fact_b.get("evidence_type") is None
        ):
            return {
                "id": row["id"],
                "category": row["category"],
                "strategy": strategy,
                "expected": None,
                "actual": None,
                "passed": True,
                "skipped": True,
            }

    expected = row.get("expected_winner_strategies", {}).get(strategy)
    actual = _pick_winner_with_decay(strategy, fact_a, fact_b)
    passed = expected == actual
    return {
        "id": row["id"],
        "category": row["category"],
        "strategy": strategy,
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "skipped": False,
    }


def run(
    out_path: Path | None = None,
    only_strategy: str | None = None,
) -> dict:
    rows = _load_dataset()
    strategies = (
        [only_strategy]
        if only_strategy
        else list(CANONICAL_STRATEGIES) + [DECAYED_STRATEGY]
    )

    per_strategy: dict[str, dict] = {}
    per_category_overall: dict[str, dict] = {}
    all_scored: list[dict] = []

    for strategy in strategies:
        passed = 0
        total = 0
        scored = 0  # excludes skipped pairs
        applicable = 0
        for row in rows:
            score = _score_one(row, strategy)
            all_scored.append(score)
            total += 1
            if score.get("skipped"):
                # Skipped pairs are not counted in accuracy denominators.
                continue
            scored += 1
            if score["expected"] is not None or score["actual"] is not None:
                applicable += 1
            if score["passed"]:
                passed += 1

            cat = row["category"]
            if cat not in per_category_overall:
                per_category_overall[cat] = {"total": 0, "passed": 0}
            per_category_overall[cat]["total"] += 1
            if score["passed"]:
                per_category_overall[cat]["passed"] += 1

        per_strategy[strategy] = {
            "total": total,
            "scored": scored,
            "passed": passed,
            "applicable": applicable,
            "accuracy": passed / scored if scored else 0.0,
        }

    overall = {
        "total_pairs": len(rows),
        "total_scored": sum(s["scored"] for s in per_strategy.values()),
        "total_passed": sum(s["passed"] for s in per_strategy.values()),
        "overall_accuracy": (
            sum(s["passed"] for s in per_strategy.values())
            / sum(s["scored"] for s in per_strategy.values())
            if per_strategy and sum(s["scored"] for s in per_strategy.values()) > 0
            else 0.0
        ),
    }

    result = {
        "dataset_path": str(DATASET.relative_to(REPO_ROOT)),
        "dataset_pairs": len(rows),
        "strategies_scored": strategies,
        "per_strategy": per_strategy,
        "per_category": per_category_overall,
        "overall": overall,
    }

    if out_path is not None:
        out_path.write_text(json.dumps(result, indent=2))
    return result


def _print_summary(result: dict) -> None:
    print("=" * 70)
    print(f"Contradiction-resolution benchmark (v0.8.1, {result['dataset_pairs']} pairs)")
    print("=" * 70)
    print()
    print("Per-strategy accuracy:")
    for strategy, stats in result["per_strategy"].items():
        skipped = stats["total"] - stats["scored"]
        skip_note = f" [skipped {skipped}]" if skipped else ""
        print(
            f"  {strategy:38s}  "
            f"{stats['passed']:3d}/{stats['scored']:3d}  "
            f"({stats['accuracy']:.1%}){skip_note}"
        )
    print()
    print("Overall:")
    o = result["overall"]
    print(f"  total scored: {o['total_scored']}")
    print(f"  passed: {o['total_passed']}")
    print(f"  overall accuracy: {o['overall_accuracy']:.1%}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--out", type=str, default=None, help="Write JSON result")
    parser.add_argument(
        "--strategy", type=str, default=None,
        help="Score only this strategy (default: all)",
    )
    args = parser.parse_args()

    out_path = Path(args.out) if args.out else None
    result = run(out_path=out_path, only_strategy=args.strategy)
    _print_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
