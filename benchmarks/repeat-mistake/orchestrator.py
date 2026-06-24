"""
Orchestrator for the v0.9 repeat-mistake benchmark.

Drives the agent over the full 50-task subset (or any subset), one task
at a time, with per-task checkpointing.

Design:
- Append-only progress file. Each completed task writes one JSON line.
- A re-run reads the progress file and skips tasks that already have a
  recorded result. This makes the runner resumable after a crash, sleep,
  power loss, or rate-limit pause.
- The agent run itself happens in a clean temp dir per task. The temp
  dir is removed after the patch is extracted, so peak disk during the
  run is one repo at a time.
- For the operational two-halves disk strategy (django+sympy first, then
  matplotlib+scikit-learn+sphinx), use --first-half / --second-half.

Usage:
    # Run baseline arm on the first half (django + sympy, 20 tasks)
    python orchestrator.py --arm baseline --first-half

    # Resume an interrupted run (same args)
    python orchestrator.py --arm baseline --first-half --resume

    # Run treatment arm; constraints are loaded from a JSON file
    python orchestrator.py --arm treatment --constraints constraints.json \
        --first-half
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agent_runner import AgentRun, run_baseline, run_treatment  # noqa: E402
from task_setup import (  # noqa: E402
    Task,
    load_first_half,
    load_second_half,
    load_subset,
)


def _progress_path(arm: str, suffix: str = "") -> Path:
    return HERE / f"{arm}_progress{suffix}.jsonl"


def _predictions_path(arm: str, suffix: str = "") -> Path:
    return HERE / f"{arm}_predictions{suffix}.json"


def _completed_ids(progress_path: Path) -> set[str]:
    """Return the set of instance_ids already in the progress file."""
    done: set[str] = set()
    if not progress_path.exists():
        return done
    for line in progress_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            done.add(rec["instance_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return done


def _append_progress(progress_path: Path, run: AgentRun) -> None:
    """Append a JSON line for one completed task."""
    rec = asdict(run)
    # Trim noisy fields for the progress file; full info is in stdout_tail
    rec.pop("stderr_tail", None)
    with progress_path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def _load_constraints(path: Path) -> list[str]:
    """Read constraints.json produced by Phase 5 learning hook.

    Expected shape: ``{"constraints": ["...", "...", ...]}`` or a flat
    list of strings.
    """
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if isinstance(data, dict) and "constraints" in data:
        return list(data["constraints"])
    if isinstance(data, list):
        return list(data)
    return []


def _select_tasks(
    *,
    first_half: bool,
    second_half: bool,
    instance_ids: Optional[list[str]],
) -> list[Task]:
    if instance_ids:
        return load_subset(only_ids=instance_ids)
    if first_half:
        return load_first_half()
    if second_half:
        return load_second_half()
    return load_subset()


def run(
    *,
    arm: str,
    tasks: list[Task],
    progress_path: Path,
    constraints: Optional[list[str]] = None,
    per_task_timeout_sec: int = 1800,
    on_progress=None,
) -> dict:
    """Run the arm over ``tasks``, checkpointing each completed task.

    Returns a summary dict with totals so the caller can log a finish
    line.
    """
    done = _completed_ids(progress_path)
    total = len(tasks)
    remaining = [t for t in tasks if t.instance_id not in done]

    if on_progress:
        on_progress(
            f"arm={arm}  total={total}  done={len(done)}  remaining={len(remaining)}"
        )

    if not remaining:
        return {
            "arm": arm,
            "total_tasks": total,
            "already_done": len(done),
            "ran_this_session": 0,
            "elapsed_sec": 0.0,
        }

    started = time.monotonic()
    ran = 0
    cost_total = 0.0
    fail_count = 0

    for i, task in enumerate(remaining, start=1):
        if on_progress:
            on_progress(
                f"[{i}/{len(remaining)}] {task.instance_id} ({task.repo}) ..."
            )

        if arm == "baseline":
            agent_run = run_baseline(
                task,
                timeout_sec=per_task_timeout_sec,
            )
        elif arm == "treatment":
            agent_run = run_treatment(
                task,
                constraints=constraints or [],
                timeout_sec=per_task_timeout_sec,
            )
        else:
            raise ValueError(f"unknown arm: {arm}")

        _append_progress(progress_path, agent_run)
        ran += 1

        if agent_run.total_cost_usd:
            cost_total += float(agent_run.total_cost_usd)
        if not agent_run.success or agent_run.patch_is_empty:
            fail_count += 1

        if on_progress:
            tag = "OK" if agent_run.success and not agent_run.patch_is_empty else "FAIL"
            on_progress(
                f"    {tag} dur={agent_run.duration_sec:.0f}s "
                f"turns={agent_run.num_turns} "
                f"cost=${agent_run.total_cost_usd or 0:.2f} "
                f"patch_empty={agent_run.patch_is_empty}"
            )

    elapsed = time.monotonic() - started
    return {
        "arm": arm,
        "total_tasks": total,
        "already_done": len(done),
        "ran_this_session": ran,
        "fail_count": fail_count,
        "cost_total_usd_session": cost_total,
        "elapsed_sec": elapsed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--arm", required=True, choices=["baseline", "treatment"],
        help="Which arm to run",
    )
    parser.add_argument(
        "--first-half", action="store_true",
        help="Run only django + sympy (20 tasks)",
    )
    parser.add_argument(
        "--second-half", action="store_true",
        help="Run only matplotlib + scikit-learn + sphinx (30 tasks)",
    )
    parser.add_argument(
        "--instance-ids", nargs="*", default=None,
        help="Explicit instance_ids to run (overrides half flags)",
    )
    parser.add_argument(
        "--constraints", type=str, default=None,
        help="JSON file with learned constraints for the treatment arm",
    )
    parser.add_argument(
        "--per-task-timeout", type=int, default=1800,
        help="Per-task timeout in seconds (default 1800 = 30 min)",
    )
    parser.add_argument(
        "--progress-suffix", type=str, default="",
        help="Suffix appended to progress filename (for parallel runs)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from existing progress (default behavior is also resume; flag is for clarity)",
    )
    args = parser.parse_args()

    tasks = _select_tasks(
        first_half=args.first_half,
        second_half=args.second_half,
        instance_ids=args.instance_ids,
    )
    if not tasks:
        print("No tasks selected.", file=sys.stderr)
        return 1

    constraints: Optional[list[str]] = None
    if args.arm == "treatment":
        if not args.constraints:
            print(
                "--constraints is required for the treatment arm.",
                file=sys.stderr,
            )
            return 1
        constraints = _load_constraints(Path(args.constraints))
        print(f"Loaded {len(constraints)} constraints for treatment arm.")

    progress_path = _progress_path(args.arm, args.progress_suffix)

    def log(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    log(f"Starting arm={args.arm} on {len(tasks)} tasks")
    log(f"Progress file: {progress_path}")
    log(f"Per-task timeout: {args.per_task_timeout}s")

    summary = run(
        arm=args.arm,
        tasks=tasks,
        progress_path=progress_path,
        constraints=constraints,
        per_task_timeout_sec=args.per_task_timeout,
        on_progress=log,
    )

    print()
    print("=" * 60)
    print(f"Session done. {json.dumps(summary, indent=2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
