"""
Score predictions with the official SWE-bench harness.

This is a thin wrapper around ``python -m swebench.harness.run_evaluation``
that:

1. Invokes the harness with the right flags for SWE-bench Verified +
   ARM64 macOS (the ``-n ''`` namespace flag builds Docker images
   locally instead of pulling from a registry that does not have
   arm64 images).
2. Parses the harness output (a per-task JSON file under
   ``logs/run_evaluation/<run_id>/<model_name>/<instance_id>/``) into a
   single combined ``results.jsonl``.
3. Reports overall and per-repo pass rates.

The harness handles all Docker container lifecycle, environment setup
per task, and FAIL_TO_PASS / PASS_TO_PASS test execution. We do not
reimplement any of that.

Usage:
    python score.py --predictions baseline_predictions.json \\
        --run-id v0.9-baseline --out baseline_results.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run_harness(
    *,
    predictions_path: Path,
    run_id: str,
    max_workers: int = 1,
    timeout_per_task: int = 1800,
    dataset: str = "princeton-nlp/SWE-bench_Verified",
) -> int:
    """Invoke the SWE-bench harness. Returns the harness exit code."""
    cmd = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "-d", dataset,
        "-p", str(predictions_path),
        "-id", run_id,
        "-n", "",  # arm64 mac: build images locally
        "--max_workers", str(max_workers),
        "-t", str(timeout_per_task),
    ]
    print(f"Invoking: {' '.join(cmd)}")
    proc = subprocess.run(cmd, env=os.environ.copy())
    return proc.returncode


def collect_results(
    *,
    run_id: str,
    model_name: str,
    out_path: Path,
) -> tuple[int, int]:
    """Walk the harness output dir and combine per-task results.

    Returns (total_tasks, resolved_tasks).
    """
    # The harness writes results under logs/run_evaluation/<run_id>/<model>/
    log_root = Path("logs/run_evaluation") / run_id / model_name
    if not log_root.exists():
        # Try the cwd location
        log_root = Path.cwd() / "logs" / "run_evaluation" / run_id / model_name

    total = 0
    resolved = 0
    records: list[dict] = []

    if log_root.exists():
        for task_dir in sorted(log_root.iterdir()):
            if not task_dir.is_dir():
                continue
            report_file = task_dir / "report.json"
            if not report_file.exists():
                continue
            try:
                rep = json.loads(report_file.read_text())
            except json.JSONDecodeError:
                continue
            for instance_id, status in rep.items():
                total += 1
                is_resolved = bool(status.get("resolved", False))
                if is_resolved:
                    resolved += 1
                records.append({
                    "instance_id": instance_id,
                    "resolved": is_resolved,
                    "tests_status": status.get("tests_status", {}),
                    "patch_applied": status.get("patch_is_None", False) is False,
                })

    with out_path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    return total, resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--predictions", required=True, type=str,
        help="Path to predictions JSON",
    )
    parser.add_argument(
        "--run-id", required=True, type=str,
        help="Harness run identifier (e.g. v0.9-baseline)",
    )
    parser.add_argument(
        "--model-name", required=True, type=str,
        help="model_name_or_path used in the predictions (matches Phase 3f)",
    )
    parser.add_argument(
        "--out", required=True, type=str,
        help="Path to write combined results.jsonl",
    )
    parser.add_argument(
        "--max-workers", type=int, default=1,
        help="Parallel harness workers (default 1 for low-memory Mac)",
    )
    parser.add_argument(
        "--timeout-per-task", type=int, default=1800,
        help="Per-task timeout in the harness",
    )
    parser.add_argument(
        "--skip-harness", action="store_true",
        help="Skip the harness call; only collect existing results",
    )
    args = parser.parse_args()

    predictions_path = Path(args.predictions).resolve()
    out_path = Path(args.out).resolve()

    if not args.skip_harness:
        rc = run_harness(
            predictions_path=predictions_path,
            run_id=args.run_id,
            max_workers=args.max_workers,
            timeout_per_task=args.timeout_per_task,
        )
        if rc != 0:
            print(
                f"WARNING: harness exited {rc}; collecting partial results.",
                file=sys.stderr,
            )

    total, resolved = collect_results(
        run_id=args.run_id,
        model_name=args.model_name,
        out_path=out_path,
    )

    print()
    print("=" * 60)
    print(f"Combined results -> {out_path}")
    print(f"Total tasks scored: {total}")
    print(f"Resolved: {resolved}")
    if total > 0:
        print(f"Pass rate: {resolved / total:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
