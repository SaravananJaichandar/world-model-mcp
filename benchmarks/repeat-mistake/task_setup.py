"""
Task loader for the v0.9 repeat-mistake benchmark.

Reads SWE-bench Verified tasks from the SHA-pinned parquet, selects
the subset declared in subset_50.json, and yields task dicts in the
shape the agent_runner expects.

The task dict carries the fields the agent needs to see (problem
statement, repo, base_commit) and the fields the scorer needs to
verify (FAIL_TO_PASS, PASS_TO_PASS, expected gold patch).

This module does NOT touch Docker or git. Repo cloning and
environment setup are delegated to the SWE-bench harness at
score-time. The agent receives the problem statement as text and
produces a patch as text; that patch is fed back to the harness
which handles all the Docker complexity.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
PARQUET = HERE / "verified.parquet"
SUBSET = HERE / "subset_50.json"


@dataclass
class Task:
    """One SWE-bench Verified task formatted for our benchmark."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    test_patch: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    environment_setup_commit: str
    version: str
    difficulty: str
    gold_patch: str
    hints_text: Optional[str] = field(default=None)

    def to_dict(self) -> dict:
        return asdict(self)

    def agent_visible(self) -> dict:
        """Return ONLY the fields the agent is allowed to see.

        The agent is NOT allowed to see gold_patch (cheating),
        test_patch contents (reverse-engineerable), or fail_to_pass /
        pass_to_pass test names (constrains patch shape unrealistically).

        The agent gets: problem_statement, repo identity, base_commit,
        and optionally hints_text (the original GitHub issue thread).
        """
        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "problem_statement": self.problem_statement,
            "hints_text": self.hints_text or "",
        }


def _decode_json_list(value) -> list[str]:
    """Decode a column that may already be a list or a JSON string."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
            if isinstance(decoded, list):
                return decoded
        except json.JSONDecodeError:
            pass
    return []


def load_subset(only_ids: Optional[list[str]] = None) -> list[Task]:
    """Load the 50-task subset.

    Args:
        only_ids: if provided, return only tasks whose instance_id is in
            this list. Used by the orchestrator to run a half-batch.

    Returns:
        List of Task dataclasses, ordered as they appear in subset_50.json.
    """
    if not PARQUET.exists():
        raise FileNotFoundError(
            f"{PARQUET} not found. Re-download per DESIGN.md."
        )
    if not SUBSET.exists():
        raise FileNotFoundError(
            f"{SUBSET} not found. Run select_subset.py first."
        )

    df = pd.read_parquet(PARQUET)
    df = df.set_index("instance_id")

    subset_meta = json.loads(SUBSET.read_text())
    all_selected_ids = subset_meta["selected_ids"]

    if only_ids is not None:
        target_ids = [i for i in all_selected_ids if i in only_ids]
    else:
        target_ids = all_selected_ids

    tasks: list[Task] = []
    for instance_id in target_ids:
        if instance_id not in df.index:
            raise ValueError(
                f"instance_id {instance_id!r} from subset_50.json not "
                "found in the parquet. SHA mismatch?"
            )
        row = df.loc[instance_id]
        tasks.append(
            Task(
                instance_id=instance_id,
                repo=str(row["repo"]),
                base_commit=str(row["base_commit"]),
                problem_statement=str(row["problem_statement"]),
                test_patch=str(row["test_patch"]),
                fail_to_pass=_decode_json_list(row["FAIL_TO_PASS"]),
                pass_to_pass=_decode_json_list(row["PASS_TO_PASS"]),
                environment_setup_commit=str(row["environment_setup_commit"]),
                version=str(row["version"]),
                difficulty=str(row["difficulty"]),
                gold_patch=str(row["patch"]),
                hints_text=(
                    str(row["hints_text"])
                    if pd.notna(row["hints_text"])
                    else None
                ),
            )
        )

    return tasks


# Repo groups for the two-half operational split.
FIRST_HALF_REPOS = ("django/django", "sympy/sympy")
SECOND_HALF_REPOS = (
    "matplotlib/matplotlib",
    "scikit-learn/scikit-learn",
    "sphinx-doc/sphinx",
)


def load_first_half() -> list[Task]:
    """Load tasks from django + sympy (20 tasks)."""
    all_tasks = load_subset()
    return [t for t in all_tasks if t.repo in FIRST_HALF_REPOS]


def load_second_half() -> list[Task]:
    """Load tasks from matplotlib + scikit-learn + sphinx (30 tasks)."""
    all_tasks = load_subset()
    return [t for t in all_tasks if t.repo in SECOND_HALF_REPOS]


if __name__ == "__main__":
    first = load_first_half()
    second = load_second_half()
    print(f"First half: {len(first)} tasks")
    for t in first[:3]:
        print(f"  {t.instance_id} ({t.difficulty}, {len(t.fail_to_pass)} F2P)")
    print(f"Second half: {len(second)} tasks")
    print(f"Combined: {len(first) + len(second)} tasks (expected 50)")
