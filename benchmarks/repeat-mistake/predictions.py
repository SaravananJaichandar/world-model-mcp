"""
Convert orchestrator progress files into SWE-bench predictions JSON.

The official harness expects predictions in this shape:

[
  {
    "instance_id": "django__django-10554",
    "model_patch": "diff --git ...",
    "model_name_or_path": "world-model-mcp-v0.9-baseline"
  },
  ...
]

This script reads ``baseline_progress.jsonl`` (or treatment) and emits
the matching predictions file.

Tasks where the agent produced no patch (empty diff, timeout with no
edits) are included with an empty patch. The harness will mark them
as unresolved, which is the correct outcome — the agent failed to
produce a fix.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def progress_to_predictions(
    progress_path: Path,
    model_name: str,
) -> list[dict]:
    """Read the orchestrator progress file and return predictions records."""
    if not progress_path.exists():
        raise FileNotFoundError(progress_path)

    predictions: list[dict] = []
    seen: set[str] = set()

    for line in progress_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        iid = rec.get("instance_id")
        if not iid or iid in seen:
            # Skip duplicates if the file was appended-to across resume runs
            continue
        seen.add(iid)

        predictions.append({
            "instance_id": iid,
            "model_patch": rec.get("extracted_patch", "") or "",
            "model_name_or_path": model_name,
        })

    return predictions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--progress", required=True, type=str,
        help="Path to progress JSONL (e.g. baseline_progress.jsonl)",
    )
    parser.add_argument(
        "--out", required=True, type=str,
        help="Path to write predictions.json",
    )
    parser.add_argument(
        "--model-name", required=True, type=str,
        help="model_name_or_path field value (e.g. world-model-mcp-v0.9-baseline)",
    )
    args = parser.parse_args()

    progress_path = Path(args.progress).resolve()
    out_path = Path(args.out).resolve()

    preds = progress_to_predictions(progress_path, args.model_name)
    out_path.write_text(json.dumps(preds, indent=2))

    non_empty = sum(1 for p in preds if p["model_patch"].strip())
    empty = len(preds) - non_empty
    print(f"Wrote {len(preds)} predictions to {out_path}")
    print(f"  non-empty patches: {non_empty}")
    print(f"  empty patches:     {empty}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
