"""
Phase 4: Failure classifier using the SWE-bench Pro 7-category taxonomy.

Reads a baseline progress file, identifies failed tasks, and classifies
each failure into one of 7 categories from the SWE-bench Pro paper
(arxiv 2509.16941):

  1. Wrong Solution           - functionally incorrect patch
  2. Tool-Use                 - improper tool calls
  3. Syntax Error             - compilation/runtime errors
  4. Incorrect File           - modified wrong file
  5. Endless File Reading     - non-productive exploration loops
  6. Misunderstood Problem    - fundamental task misread
  7. Other                    - computational limits or compounding issues

Classification is done by Claude headless (consistent with the rest of
the benchmark using your subscription). Each failure's category is
written to `<arm>_classified.jsonl` for downstream constraint extraction.

A failed task is one where:
  - patch_is_empty=True (agent produced nothing), OR
  - the SWE-bench harness reports `resolved=False` for that instance

This module does NOT call the harness itself - it reads a results file
produced by score.py. Run order:

  1. orchestrator.py --arm baseline    (produces baseline_progress.jsonl)
  2. predictions.py + score.py         (produces baseline_results.jsonl)
  3. failure_classifier.py             (produces baseline_classified.jsonl)
  4. learning_hook.py                  (produces constraints.json)

The judge prompt is locked verbatim and SHA-pinned in DESIGN.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from claude_client import ClaudeClient, CompletionResult  # noqa: E402


# The 7-category taxonomy verbatim from arxiv 2509.16941 (SWE-bench Pro).
# Definitions paraphrased for clarity but every category name matches
# the published taxonomy.
CATEGORIES = (
    "Wrong Solution",
    "Tool-Use",
    "Syntax Error",
    "Incorrect File",
    "Endless File Reading",
    "Misunderstood Problem Statement",
    "Other",
)

# Judge prompt. Locked. Any change here invalidates the methodology.
JUDGE_PROMPT = """You are a careful failure-mode classifier for an autonomous coding agent. You will read a failed attempt and classify the dominant failure mode using the SWE-bench Pro 7-category taxonomy.

The 7 categories (definitions from arxiv 2509.16941):

1. Wrong Solution - The agent produced a syntactically valid patch that is functionally incorrect, incomplete, or fails to address the core problem. The patch applies cleanly but the tests fail because the logic is wrong.

2. Tool-Use - The agent's incorrect use of available tools prevented it from gathering necessary information or applying changes correctly. For example: not reading the right files, using grep with wrong patterns, calling bash with broken commands.

3. Syntax Error - The agent successfully modified target files but introduced syntactic errors that render the codebase uncompilable or unrunnable. The patch fails because the code itself cannot be parsed.

4. Incorrect File - The agent correctly understood the high-level goal but failed to locate the correct source file or function for modification. The patch edits a different file than the one that needed changing.

5. Endless File Reading - The agent got stuck in non-productive loops of exploration without implementation. Many reads, few or no edits, ran out of turns without committing changes.

6. Misunderstood Problem Statement - The agent fundamentally misread the task. The patch addresses a different problem than the one described in the issue.

7. Other - Failures from computational limits, compounding minor issues, or anything that doesn't cleanly fit the above six categories.

Now classify this failed attempt. Read the problem statement, the agent's run metadata, and the patch (if any). Respond with EXACTLY one of:

CATEGORY: <category name from the seven above>
REASONING: <one short sentence explaining the choice>

Do not add other commentary. Do not propose new categories. Choose the single dominant failure mode.

---

PROBLEM STATEMENT:
{problem_statement}

---

AGENT RUN METADATA:
- Turns used: {num_turns}
- Wall-clock seconds: {duration_sec}
- Timeout hit: {timeout_hit}
- Patch is empty: {patch_is_empty}
- Total cost USD: {total_cost_usd}

---

AGENT PATCH (truncated to 2000 chars):
{patch}

---

CLASSIFICATION:"""


@dataclass
class Classification:
    instance_id: str
    category: str
    reasoning: str
    judge_raw: str  # raw judge output for audit
    judge_duration_sec: float


def _parse_judge_output(raw: str) -> tuple[str, str]:
    """Parse 'CATEGORY: X\\nREASONING: Y' from judge response.

    Returns (category, reasoning). If parsing fails, returns
    ("Other", raw_first_line).
    """
    cat: Optional[str] = None
    reason: Optional[str] = None

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("CATEGORY:"):
            cat = line.split(":", 1)[1].strip()
        elif upper.startswith("REASONING:"):
            reason = line.split(":", 1)[1].strip()

    # Validate category against the known 7
    if cat is None:
        return ("Other", raw.splitlines()[0][:200] if raw else "(empty judge output)")

    # Tolerate slight variations (case, trailing punctuation)
    cat_normalized = cat.rstrip(". ").lower()
    for known in CATEGORIES:
        if known.lower() == cat_normalized:
            return (known, reason or "(no reasoning)")

    # Fallback: return Other if we cannot match
    return ("Other", f"unmatched_judge_category={cat!r}; reason={reason}")


def classify_one(
    *,
    problem_statement: str,
    patch: str,
    num_turns: Optional[int],
    duration_sec: float,
    timeout_hit: bool,
    patch_is_empty: bool,
    total_cost_usd: Optional[float],
    client: ClaudeClient,
) -> tuple[str, str, str, float]:
    """Run the judge on one failed attempt.

    Returns (category, reasoning, judge_raw, judge_duration_sec).
    """
    prompt = JUDGE_PROMPT.format(
        problem_statement=problem_statement[:3000],
        num_turns=num_turns if num_turns is not None else "unknown",
        duration_sec=int(duration_sec),
        timeout_hit=timeout_hit,
        patch_is_empty=patch_is_empty,
        total_cost_usd=f"{total_cost_usd:.2f}" if total_cost_usd is not None else "unknown",
        patch=patch[:2000] if patch else "(empty patch)",
    )

    t0 = time.monotonic()
    result: CompletionResult = client.complete(prompt, timeout_sec=120)
    dt = time.monotonic() - t0

    if not result.ok:
        return ("Other", f"judge_failed: {result.error}", result.text or "", dt)

    category, reasoning = _parse_judge_output(result.text)
    return category, reasoning, result.text, dt


def is_task_failed(
    *,
    progress_rec: dict,
    score_rec: Optional[dict],
) -> bool:
    """Decide whether a task counts as failed for the purpose of
    classification.

    A task is failed if either:
      - The progress file marks success=False, OR
      - The progress file says patch_is_empty=True, OR
      - The score file (if available) marks resolved=False

    Resolved tasks are not classified.
    """
    if progress_rec.get("success") is False:
        return True
    if progress_rec.get("patch_is_empty") is True:
        return True
    if score_rec is not None and score_rec.get("resolved") is False:
        return True
    return False


def _load_progress(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            iid = rec.get("instance_id")
            if iid:
                out[iid] = rec
        except json.JSONDecodeError:
            continue
    return out


def _load_scores(path: Optional[Path]) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            iid = rec.get("instance_id")
            if iid:
                out[iid] = rec
        except json.JSONDecodeError:
            continue
    return out


def _load_tasks_for_lookup() -> dict:
    """Return ``{instance_id: Task}`` from the subset."""
    from task_setup import load_subset
    return {t.instance_id: t for t in load_subset()}


def classify_arm(
    *,
    progress_path: Path,
    scores_path: Optional[Path],
    out_path: Path,
    client: Optional[ClaudeClient] = None,
    on_progress=None,
) -> dict:
    """Classify all failed tasks in an arm and write classifications
    to ``out_path``. Already-classified tasks (instance_ids present in
    out_path from a prior run) are skipped, so this resumes cleanly.

    Returns a summary dict.
    """
    if client is None:
        client = ClaudeClient(timeout_sec=120, max_retries=3)

    progress = _load_progress(progress_path)
    scores = _load_scores(scores_path)
    tasks = _load_tasks_for_lookup()

    # Resume support
    already_classified: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                already_classified.add(rec["instance_id"])
            except (json.JSONDecodeError, KeyError):
                continue

    failed_ids: list[str] = []
    for iid, prec in progress.items():
        score_rec = scores.get(iid)
        if is_task_failed(progress_rec=prec, score_rec=score_rec):
            if iid not in already_classified:
                failed_ids.append(iid)

    if on_progress:
        on_progress(
            f"progress_records={len(progress)} score_records={len(scores)} "
            f"failed_to_classify={len(failed_ids)} already_done={len(already_classified)}"
        )

    cat_counts: dict[str, int] = {c: 0 for c in CATEGORIES}

    with out_path.open("a") as out_f:
        for i, iid in enumerate(failed_ids, start=1):
            prec = progress[iid]
            task = tasks.get(iid)
            problem_statement = task.problem_statement if task else "(task not found)"

            if on_progress:
                on_progress(f"[{i}/{len(failed_ids)}] classifying {iid}")

            category, reasoning, judge_raw, judge_dt = classify_one(
                problem_statement=problem_statement,
                patch=prec.get("extracted_patch", "") or "",
                num_turns=prec.get("num_turns"),
                duration_sec=float(prec.get("duration_sec", 0)),
                timeout_hit=bool(prec.get("timeout_hit", False)),
                patch_is_empty=bool(prec.get("patch_is_empty", True)),
                total_cost_usd=prec.get("total_cost_usd"),
                client=client,
            )

            cls = Classification(
                instance_id=iid,
                category=category,
                reasoning=reasoning,
                judge_raw=judge_raw,
                judge_duration_sec=judge_dt,
            )
            out_f.write(json.dumps(asdict(cls)) + "\n")
            out_f.flush()
            cat_counts[category] = cat_counts.get(category, 0) + 1

            if on_progress:
                on_progress(f"    -> {category} ({judge_dt:.1f}s)")

    summary = {
        "progress_path": str(progress_path),
        "out_path": str(out_path),
        "failed_classified_this_session": len(failed_ids),
        "category_counts_this_session": cat_counts,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--progress", required=True, type=str,
        help="Path to <arm>_progress.jsonl from orchestrator",
    )
    parser.add_argument(
        "--scores", default=None, type=str,
        help="Optional path to <arm>_results.jsonl from score.py",
    )
    parser.add_argument(
        "--out", required=True, type=str,
        help="Path to write <arm>_classified.jsonl",
    )
    args = parser.parse_args()

    progress_path = Path(args.progress).resolve()
    scores_path = Path(args.scores).resolve() if args.scores else None
    out_path = Path(args.out).resolve()

    def log(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    log(f"Classifying failures from {progress_path}")
    summary = classify_arm(
        progress_path=progress_path,
        scores_path=scores_path,
        out_path=out_path,
        on_progress=log,
    )

    print()
    print("=" * 60)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
