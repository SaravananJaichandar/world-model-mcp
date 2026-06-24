"""
Deterministic 50-task subset selector for the v0.9 repeat-mistake benchmark.

Selects 10 tasks from each of 5 repos using a difficulty-weighted approach
within each repo. The selected `instance_id`s are written to
`subset_50.json` so the benchmark run is reproducible.

The five repos were chosen by the criteria in DESIGN.md section "Corpus":
1. >=10 tasks available in SWE-bench Verified
2. Spread across web/scientific/visualization/ML/docs domains for
   varied failure-mode coverage
3. Not exclusively django (which is 46% of the dataset)

Within each repo, prefer the hardest tasks per SWE-bench Verified's
own `difficulty` column. This matches OpenAI's "prioritize harder tasks"
framing for the Verified set itself.

The output file SHA is recorded as the second SHA pin (the first being
the parquet file SHA from the dataset download).

Run: python benchmarks/repeat-mistake/select_subset.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PARQUET = Path(__file__).resolve().parent / "verified.parquet"
OUT_FILE = Path(__file__).resolve().parent / "subset_50.json"

# Locked selection: 5 repos, 10 tasks each. Repos in priority order:
# - django/django: largest, web framework
# - sympy/sympy: symbolic math, very different domain
# - matplotlib/matplotlib: visualization
# - scikit-learn/scikit-learn: ML
# - sphinx-doc/sphinx: docs/markup
#
# These five span web + math + viz + ML + docs domains. Together they
# cover 416/500 (83%) of the SWE-bench Verified dataset, so the
# selected 50 are highly representative of where failures occur.
SELECTED_REPOS = [
    "django/django",
    "sympy/sympy",
    "matplotlib/matplotlib",
    "scikit-learn/scikit-learn",
    "sphinx-doc/sphinx",
]
TASKS_PER_REPO = 10


def difficulty_rank(diff: str) -> int:
    """Rank difficulty for sort. Higher = harder.

    SWE-bench Verified `difficulty` values seen in practice:
    "<15 min fix", "15 min - 1 hour", "1-4 hours", ">4 hours".
    Treat unknown as 0 (skipped to end if there are ties).
    """
    if not isinstance(diff, str):
        return 0
    diff = diff.strip().lower()
    if ">4 hour" in diff or "more than" in diff:
        return 4
    if "1-4 hour" in diff:
        return 3
    if "15 min" in diff and "hour" in diff:
        return 2
    if "<15 min" in diff or "less than" in diff:
        return 1
    return 0


def main() -> int:
    if not PARQUET.exists():
        print(f"ERROR: {PARQUET} not found.", file=sys.stderr)
        print(
            "Download with:\n"
            "  curl -sL -o benchmarks/repeat-mistake/verified.parquet "
            "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/"
            "resolve/main/data/test-00000-of-00001.parquet",
            file=sys.stderr,
        )
        return 1

    parquet_sha = hashlib.sha256(PARQUET.read_bytes()).hexdigest()

    df = pd.read_parquet(PARQUET)
    print(f"Loaded {len(df)} tasks from {len(df['repo'].unique())} repos")
    print(f"Parquet SHA: {parquet_sha}\n")

    selected_ids: list[str] = []
    summary: dict = {
        "parquet_sha": parquet_sha,
        "total_tasks_in_dataset": len(df),
        "selection_method": "5 repos x 10 hardest tasks each",
        "repos": {},
    }

    for repo in SELECTED_REPOS:
        repo_df = df[df["repo"] == repo].copy()
        if len(repo_df) < TASKS_PER_REPO:
            print(
                f"WARNING: {repo} has only {len(repo_df)} tasks "
                f"(need {TASKS_PER_REPO})"
            )

        # Rank by difficulty (descending), then by instance_id (ascending)
        # for deterministic tie-break.
        repo_df["_diff_rank"] = repo_df["difficulty"].apply(difficulty_rank)
        repo_df = repo_df.sort_values(
            by=["_diff_rank", "instance_id"],
            ascending=[False, True],
        )
        picked = repo_df.head(TASKS_PER_REPO)

        picked_ids = picked["instance_id"].tolist()
        difficulties = Counter(picked["difficulty"].tolist())

        selected_ids.extend(picked_ids)
        summary["repos"][repo] = {
            "total_in_dataset": int((df["repo"] == repo).sum()),
            "selected": int(len(picked_ids)),
            "instance_ids": picked_ids,
            "difficulty_distribution": dict(difficulties),
        }

        print(f"{repo}: {len(picked_ids)} tasks selected")
        for diff, count in sorted(difficulties.items()):
            print(f"  {diff}: {count}")

    summary["total_selected"] = len(selected_ids)
    summary["selected_ids"] = selected_ids

    OUT_FILE.write_text(json.dumps(summary, indent=2))
    out_sha = hashlib.sha256(OUT_FILE.read_bytes()).hexdigest()

    print(f"\n{'=' * 60}")
    print(f"Selected {len(selected_ids)} tasks total")
    print(f"Output: {OUT_FILE.relative_to(REPO_ROOT)}")
    print(f"Output SHA: {out_sha}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
