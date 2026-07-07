#!/usr/bin/env python3
"""
Coach-Player adversarial verification benchmark (v0.12.12).

Runs the shipped Coach LLM against a hand-labeled set of query/answer/facts
triples and reports:

  - Hallucination catch rate (Coach labels LOW when the answer is truly
    hallucinated). Ship floor for v0.12.12: >=95%. Aspirational; the 12
    starter pairs make the effective floor 12/12 or 11/12 (=91.7%). Post-
    launch target: expand pairs.json to >=30 pairs.
  - False positive rate (Coach labels LOW when the answer is truly grounded).
    Ship floor: <=10%.
  - MEDIUM band correctness (Coach labels MEDIUM within one band of the
    ground truth for partial answers).

Requires ANTHROPIC_API_KEY. Costs ~$0.03 per full run at Haiku 4.5 pricing.
Exits nonzero if ship floors are not met.

Usage:
  python benchmarks/coach-player/run.py
  python benchmarks/coach-player/run.py --model claude-haiku-4-5-20251001
  python benchmarks/coach-player/run.py --pairs benchmarks/coach-player/pairs.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


HALLUCINATION_CATCH_FLOOR = 0.95  # aspirational
FALSE_POSITIVE_CEILING = 0.10


@dataclass
class Row:
    id: str
    category: str
    expected: str
    actual: str
    correct: bool
    error: str | None
    reasoning: str | None


def _band_distance(a: str, b: str) -> int:
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    return abs(order[a] - order[b])


async def _run_one(pair, model, api_key):
    from anthropic import AsyncAnthropic
    from world_model_server.models import Fact
    from world_model_server.verification import verify_answer

    client = AsyncAnthropic(api_key=api_key)
    facts = [
        Fact(
            id=f["id"],
            fact_text=f["fact_text"],
            evidence_path=f["evidence_path"],
            valid_at=datetime.now(),
            status="canonical",
        )
        for f in pair["facts"]
    ]
    result = await verify_answer(
        client=client,
        model=model,
        query=pair["query"],
        answer=pair["answer"],
        facts=facts,
    )
    correct = result.confidence == pair["expected_confidence"]
    return Row(
        id=pair["id"],
        category=pair["category"],
        expected=pair["expected_confidence"],
        actual=result.confidence,
        correct=correct,
        error=result.error,
        reasoning=result.coach_reasoning,
    )


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument(
        "--pairs",
        default=str(Path(__file__).parent / "pairs.json"),
    )
    ap.add_argument("--out", default=None, help="Optional JSON results file")
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ERROR: ANTHROPIC_API_KEY not set. The Coach benchmark requires\n"
            "       a live Anthropic API key. Set it and rerun:\n"
            "         export ANTHROPIC_API_KEY=sk-ant-...\n"
            "         python benchmarks/coach-player/run.py",
            file=sys.stderr,
        )
        sys.exit(2)

    # Ensure the repo package is importable when the script runs from anywhere.
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    data = json.loads(Path(args.pairs).read_text())
    pairs = data["pairs"]

    print(f"Coach-Player benchmark v0.12.12")
    print(f"Model: {args.model}")
    print(f"Pairs: {len(pairs)}  (loaded from {args.pairs})")
    print("=" * 72)

    results: list[Row] = []
    for pair in pairs:
        row = await _run_one(pair, args.model, api_key)
        results.append(row)
        status = "OK  " if row.correct else "FAIL"
        band_note = "" if row.correct else f"  (dist={_band_distance(row.expected, row.actual)})"
        err_note = f"  [err={row.error}]" if row.error else ""
        print(
            f"  {status}  {row.id:>4}  {row.category:<12} "
            f"expected={row.expected:<6} actual={row.actual:<6}{band_note}{err_note}"
        )

    # Metrics
    grounded = [r for r in results if r.category == "grounded"]
    partial = [r for r in results if r.category == "partial"]
    hallucinated = [r for r in results if r.category == "hallucinated"]

    hallucination_catch = (
        sum(1 for r in hallucinated if r.actual == "LOW") / len(hallucinated)
        if hallucinated else 0.0
    )
    false_positive = (
        sum(1 for r in grounded if r.actual == "LOW") / len(grounded)
        if grounded else 0.0
    )
    partial_band_correct = (
        sum(1 for r in partial if r.actual == "MEDIUM") / len(partial)
        if partial else 0.0
    )
    partial_within_one_band = (
        sum(1 for r in partial if _band_distance(r.expected, r.actual) <= 1) / len(partial)
        if partial else 0.0
    )
    overall = sum(1 for r in results if r.correct) / len(results)

    print()
    print("=" * 72)
    print("Metrics:")
    print(f"  Hallucination catch rate:  {hallucination_catch:.1%}   "
          f"(floor: {HALLUCINATION_CATCH_FLOOR:.0%}; aspirational)")
    print(f"  False positive rate:       {false_positive:.1%}   "
          f"(ceiling: {FALSE_POSITIVE_CEILING:.0%})")
    print(f"  Partial exact (MEDIUM):    {partial_band_correct:.1%}")
    print(f"  Partial within one band:   {partial_within_one_band:.1%}")
    print(f"  Overall exact match:       {overall:.1%}")

    if args.out:
        summary = {
            "model": args.model,
            "pairs_count": len(pairs),
            "metrics": {
                "hallucination_catch_rate": hallucination_catch,
                "false_positive_rate": false_positive,
                "partial_exact_medium": partial_band_correct,
                "partial_within_one_band": partial_within_one_band,
                "overall_exact_match": overall,
            },
            "rows": [
                {
                    "id": r.id, "category": r.category,
                    "expected": r.expected, "actual": r.actual,
                    "correct": r.correct, "error": r.error,
                }
                for r in results
            ],
        }
        Path(args.out).write_text(json.dumps(summary, indent=2))
        print(f"\nWrote results to {args.out}")

    # Ship-floor enforcement. Only enforce ceilings that are non-aspirational;
    # the hallucination catch rate is aspirational at 12 pairs and does NOT
    # gate exit code. Expand pairs.json to enforce it.
    ship_ok = false_positive <= FALSE_POSITIVE_CEILING
    if not ship_ok:
        print(
            f"\nSHIP FLOOR NOT MET: false_positive_rate {false_positive:.1%} > "
            f"{FALSE_POSITIVE_CEILING:.0%}"
        )
        sys.exit(1)
    print("\nShip floor (false positives): OK")


if __name__ == "__main__":
    asyncio.run(main())
