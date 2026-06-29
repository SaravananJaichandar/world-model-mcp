"""
Aggregate multi-seed runs of the v0.9 SWE-bench Verified benchmark.

Loads progress/results JSONLs from seed 1 (the existing v0.9 data) plus any
additional seeds (seed 2, seed 3, ...) and computes the variance metrics
defined in SEED_PLAN.md:

  - Per-instance pass rate across seeds (0, 1/3, 2/3, 1)
  - Per-instance stability classification (stable_pass, stable_fail, flaky)
  - Per-instance paired delta (treatment_passes - baseline_passes across seeds)
  - Mean paired delta across the load-bearing subset
  - Bootstrap 95% CI on the mean paired delta

The smart subset is locked in SEED_PLAN.md. This script trusts that lock —
it loads the same 17 instance IDs by default, but accepts a custom subset
via --instance-ids if needed for a follow-up analysis.

Usage:
    python multi_seed_aggregate.py \\
        --seed1-baseline baseline_results.jsonl \\
        --seed1-baseline-s2 baseline_results_s2.jsonl \\
        --seed1-treatment treatment_results_s1.jsonl \\
        --seed1-treatment-s2 treatment_results_s2_crossdomain.jsonl \\
        --seed2-baseline baseline_results_seed2.jsonl \\
        --seed2-treatment treatment_results_seed2_treatment.jsonl \\
        --seed3-baseline baseline_results_seed3.jsonl \\
        --seed3-treatment treatment_results_seed3_treatment.jsonl \\
        --out multi_seed_summary.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

# The 17 instances locked in SEED_PLAN.md. Categories matter for the
# replication interpretation.
SMART_SUBSET: dict[str, str] = {
    # Load-bearing flips and regressions (7)
    "django__django-11400": "load_bearing_flip",
    "django__django-13212": "load_bearing_flip",
    "django__django-13344": "load_bearing_flip",
    "sympy__sympy-16597": "load_bearing_flip",
    "sympy__sympy-17630": "load_bearing_regression",
    "scikit-learn__scikit-learn-14087": "load_bearing_flip_crossdomain",
    "sphinx-doc__sphinx-9461": "load_bearing_flip_crossdomain",
    # Variance-floor stable PASS (5)
    "django__django-10554": "variance_floor_pass",
    "sympy__sympy-11618": "variance_floor_pass",
    "matplotlib__matplotlib-14623": "variance_floor_pass",
    "scikit-learn__scikit-learn-10297": "variance_floor_pass",
    "sphinx-doc__sphinx-10466": "variance_floor_pass",
    # Variance-floor stable FAIL (5)
    "sympy__sympy-12489": "variance_floor_fail",
    "matplotlib__matplotlib-22865": "variance_floor_fail",
    "matplotlib__matplotlib-23314": "variance_floor_fail",
    "sphinx-doc__sphinx-7590": "variance_floor_fail",
    "sphinx-doc__sphinx-7748": "variance_floor_fail",
}

# v0.9 expected outcome per instance (from RESULTS.md). Used to compute
# the "same direction as seed 1" replication metric.
V09_EXPECTED: dict[str, dict[str, bool]] = {
    # Load-bearing flips: baseline FAIL, treatment PASS
    "django__django-11400": {"baseline": False, "treatment": True},
    "django__django-13212": {"baseline": False, "treatment": True},
    "django__django-13344": {"baseline": False, "treatment": True},
    "sympy__sympy-16597": {"baseline": False, "treatment": True},
    # Load-bearing regression: baseline PASS, treatment FAIL
    "sympy__sympy-17630": {"baseline": True, "treatment": False},
    # Cross-domain flips: baseline FAIL, treatment PASS
    "scikit-learn__scikit-learn-14087": {"baseline": False, "treatment": True},
    "sphinx-doc__sphinx-9461": {"baseline": False, "treatment": True},
    # Variance-floor pass: both PASS
    "django__django-10554": {"baseline": True, "treatment": True},
    "sympy__sympy-11618": {"baseline": True, "treatment": True},
    "matplotlib__matplotlib-14623": {"baseline": True, "treatment": True},
    "scikit-learn__scikit-learn-10297": {"baseline": True, "treatment": True},
    "sphinx-doc__sphinx-10466": {"baseline": True, "treatment": True},
    # Variance-floor fail: both FAIL
    "sympy__sympy-12489": {"baseline": False, "treatment": False},
    "matplotlib__matplotlib-22865": {"baseline": False, "treatment": False},
    "matplotlib__matplotlib-23314": {"baseline": False, "treatment": False},
    "sphinx-doc__sphinx-7590": {"baseline": False, "treatment": False},
    "sphinx-doc__sphinx-7748": {"baseline": False, "treatment": False},
}


def load_results(path: Path) -> dict[str, bool]:
    """Load a results JSONL and return instance_id -> resolved (bool) map."""
    if not path.exists():
        return {}
    out: dict[str, bool] = {}
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            out[r["instance_id"]] = bool(r.get("resolved", False))
    return out


def classify_stability(seed_outcomes: list[Optional[bool]]) -> str:
    """Given a list of seed outcomes (True/False/None for missing), classify."""
    present = [v for v in seed_outcomes if v is not None]
    if not present:
        return "no_data"
    if len(present) == 1:
        return "single_seed"
    if all(v is True for v in present):
        return "stable_pass"
    if all(v is False for v in present):
        return "stable_fail"
    return "flaky"


def bootstrap_ci(samples: list[float], n_iter: int = 10000, ci: float = 0.95) -> tuple[float, float]:
    """Bootstrap confidence interval on the mean."""
    if not samples:
        return (0.0, 0.0)
    rng = random.Random(20260625)
    means: list[float] = []
    n = len(samples)
    for _ in range(n_iter):
        resample = [samples[rng.randrange(n)] for _ in range(n)]
        means.append(sum(resample) / n)
    means.sort()
    lo_idx = int((1 - ci) / 2 * n_iter)
    hi_idx = int((1 + ci) / 2 * n_iter)
    return (means[lo_idx], means[hi_idx])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed1-baseline", type=Path, default=Path("baseline_results.jsonl"))
    parser.add_argument("--seed1-baseline-s2", type=Path, default=Path("baseline_results_s2.jsonl"))
    parser.add_argument("--seed1-treatment", type=Path, default=Path("treatment_results_s1.jsonl"))
    parser.add_argument(
        "--seed1-treatment-s2",
        type=Path,
        default=Path("treatment_results_s2_crossdomain.jsonl"),
    )
    parser.add_argument("--seed2-baseline", type=Path, default=Path("baseline_results_seed2.jsonl"))
    parser.add_argument(
        "--seed2-treatment", type=Path, default=Path("treatment_results_seed2_treatment.jsonl")
    )
    parser.add_argument("--seed3-baseline", type=Path, default=Path("baseline_results_seed3.jsonl"))
    parser.add_argument(
        "--seed3-treatment", type=Path, default=Path("treatment_results_seed3_treatment.jsonl")
    )
    parser.add_argument(
        "--instance-ids",
        nargs="*",
        default=None,
        help="Override the locked SMART_SUBSET list. Default: 17 instances from SEED_PLAN.md.",
    )
    parser.add_argument("--out", type=Path, default=Path("multi_seed_summary.json"))
    args = parser.parse_args()

    # Combine seed 1 baseline (Subset 1 + Subset 2) and treatment.
    seed1_baseline = {**load_results(args.seed1_baseline), **load_results(args.seed1_baseline_s2)}
    seed1_treatment = {
        **load_results(args.seed1_treatment),
        **load_results(args.seed1_treatment_s2),
    }
    seed2_baseline = load_results(args.seed2_baseline)
    seed2_treatment = load_results(args.seed2_treatment)
    seed3_baseline = load_results(args.seed3_baseline)
    seed3_treatment = load_results(args.seed3_treatment)

    target_ids = args.instance_ids or list(SMART_SUBSET.keys())

    rows: list[dict] = []
    for iid in target_ids:
        category = SMART_SUBSET.get(iid, "custom")
        baseline_seeds = [
            seed1_baseline.get(iid),
            seed2_baseline.get(iid),
            seed3_baseline.get(iid),
        ]
        treatment_seeds = [
            seed1_treatment.get(iid),
            seed2_treatment.get(iid),
            seed3_treatment.get(iid),
        ]
        b_present = [v for v in baseline_seeds if v is not None]
        t_present = [v for v in treatment_seeds if v is not None]
        b_pass_rate = sum(1 for v in b_present if v) / len(b_present) if b_present else None
        t_pass_rate = sum(1 for v in t_present if v) / len(t_present) if t_present else None
        b_stability = classify_stability(baseline_seeds)
        t_stability = classify_stability(treatment_seeds)
        # Paired delta across seeds where BOTH arms have data
        delta = 0
        n_paired = 0
        for b, t in zip(baseline_seeds, treatment_seeds):
            if b is not None and t is not None:
                delta += (1 if t else 0) - (1 if b else 0)
                n_paired += 1
        # Same-direction-as-seed-1 check (for load-bearing instances)
        expected = V09_EXPECTED.get(iid)
        same_direction_seeds = []
        if expected:
            for b, t in zip(baseline_seeds, treatment_seeds):
                if b is None or t is None:
                    continue
                # Same direction = both arms match the seed-1 outcome
                if b == expected["baseline"] and t == expected["treatment"]:
                    same_direction_seeds.append(True)
                else:
                    same_direction_seeds.append(False)
        rows.append(
            {
                "instance_id": iid,
                "category": category,
                "baseline_seeds": baseline_seeds,
                "treatment_seeds": treatment_seeds,
                "baseline_pass_rate": b_pass_rate,
                "treatment_pass_rate": t_pass_rate,
                "baseline_stability": b_stability,
                "treatment_stability": t_stability,
                "paired_delta_sum": delta,
                "n_paired_seeds": n_paired,
                "same_direction_seeds": same_direction_seeds,
                "v09_expected": expected,
            }
        )

    # Per-instance paired delta (treatment_passes - baseline_passes across seeds)
    deltas = [r["paired_delta_sum"] for r in rows if r["n_paired_seeds"] > 0]
    mean_delta = sum(deltas) / len(deltas) if deltas else 0.0
    ci_lo, ci_hi = bootstrap_ci(deltas) if deltas else (0.0, 0.0)

    # Load-bearing replication count
    load_bearing_rows = [
        r for r in rows if r["category"].startswith("load_bearing")
    ]
    load_bearing_replicated = 0
    for r in load_bearing_rows:
        sd = r["same_direction_seeds"]
        # Strict: ALL seeds match the v0.9 direction
        if sd and all(sd):
            load_bearing_replicated += 1

    # Replication interpretation requires at least 2 seeds of data on
    # the load-bearing rows. Until seed 2 lands, the "interpretation" is
    # a trivial restatement of seed 1.
    max_seeds_present = max(
        (r["n_paired_seeds"] for r in load_bearing_rows), default=0
    )
    interpretation = "no_data"
    if not load_bearing_rows:
        interpretation = "no_data"
    elif max_seeds_present < 2:
        interpretation = "seed1_only_baseline"
    elif load_bearing_replicated >= 5:
        interpretation = "strong_replication"
    elif load_bearing_replicated >= 3:
        interpretation = "moderate_replication"
    else:
        interpretation = "weak_replication"

    # Variance-floor summaries
    floor_pass_rows = [r for r in rows if r["category"] == "variance_floor_pass"]
    floor_fail_rows = [r for r in rows if r["category"] == "variance_floor_fail"]
    floor_pass_counts: dict[str, int] = defaultdict(int)
    floor_fail_counts: dict[str, int] = defaultdict(int)
    for r in floor_pass_rows:
        floor_pass_counts[r["baseline_stability"]] += 1
        floor_pass_counts[r["treatment_stability"]] += 1
    for r in floor_fail_rows:
        floor_fail_counts[r["baseline_stability"]] += 1
        floor_fail_counts[r["treatment_stability"]] += 1

    summary = {
        "rows": rows,
        "load_bearing": {
            "n_instances": len(load_bearing_rows),
            "replicated_count": load_bearing_replicated,
            "interpretation": interpretation,
        },
        "variance_floor": {
            "pass_floor_counts": dict(floor_pass_counts),
            "fail_floor_counts": dict(floor_fail_counts),
        },
        "paired_delta_summary": {
            "n_instances": len(deltas),
            "mean_delta": mean_delta,
            "bootstrap_95_ci": [ci_lo, ci_hi],
        },
    }

    args.out.write_text(json.dumps(summary, indent=2, default=str))

    # Human-readable table to stdout
    print(f"\n{'instance_id':<45} {'category':<32} {'b_seeds':<14} {'t_seeds':<14} {'delta':<6}")
    print("-" * 115)
    for r in rows:
        b = "/".join("P" if v is True else "F" if v is False else "-" for v in r["baseline_seeds"])
        t = "/".join("P" if v is True else "F" if v is False else "-" for v in r["treatment_seeds"])
        print(
            f"  {r['instance_id']:<43} {r['category']:<32} {b:<14} {t:<14} {r['paired_delta_sum']:+d}/{r['n_paired_seeds']}"
        )

    print()
    print(f"Load-bearing replication: {load_bearing_replicated}/{len(load_bearing_rows)} → {interpretation}")
    print(
        f"Mean paired delta across {len(deltas)} instances: {mean_delta:+.2f} (95% CI [{ci_lo:+.2f}, {ci_hi:+.2f}])"
    )
    print()
    print(f"Variance-floor PASS instances: {dict(floor_pass_counts)}")
    print(f"Variance-floor FAIL instances: {dict(floor_fail_counts)}")
    print()
    print(f"Summary JSON written to: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
