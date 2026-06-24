"""
Repo checkout for the v0.9 repeat-mistake benchmark.

Clones a SWE-bench task's repo at the exact `base_commit` and applies
the `test_patch`. Returns a temp directory with the repo at the
correct state for the agent to work in.

The agent will modify files in this directory. We extract the agent's
patch via `git diff HEAD` after the agent finishes.

This module does NOT touch Docker. Docker is the SWE-bench harness's
job in Phase B. Here we only need a local checkout for the agent to
explore and edit.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from task_setup import Task


@dataclass
class Checkout:
    """A prepared repo checkout for one task."""
    instance_id: str
    repo_dir: Path
    base_commit: str
    test_patch_applied: bool
    cleanup_token: Optional[Path] = None


def _run(cmd: list[str], cwd: Optional[Path] = None, timeout: int = 600) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def clone_at_commit(task: Task, parent_dir: Optional[Path] = None) -> Checkout:
    """Clone ``task.repo`` at ``task.base_commit`` into a temp dir.

    A full clone is used (not --depth 1) because SWE-bench tasks
    reference historical commits that may be ancestors of the current
    HEAD but not present in a shallow clone. The clone is slow (~30-60s
    for django) but necessary.

    The cloned repo is left in a state where ``test_patch`` has been
    applied via ``git apply`` (and committed as a benchmark-internal
    marker commit so ``git diff HEAD`` later only captures agent
    changes).
    """
    if parent_dir is None:
        parent_dir = Path(tempfile.mkdtemp(prefix="swe-bench-"))
    repo_dir = parent_dir / task.repo.split("/")[-1]

    repo_url = f"https://github.com/{task.repo}.git"
    rc, out, err = _run(["git", "clone", repo_url, str(repo_dir)], timeout=600)
    if rc != 0:
        raise RuntimeError(f"git clone failed: {err}")

    rc, _, err = _run(
        ["git", "checkout", task.base_commit],
        cwd=repo_dir,
        timeout=120,
    )
    if rc != 0:
        raise RuntimeError(f"git checkout {task.base_commit} failed: {err}")

    # Apply test_patch and commit so the agent's later diff is clean
    if task.test_patch and task.test_patch.strip():
        patch_file = repo_dir / "_swe_test_patch.diff"
        patch_file.write_text(task.test_patch)
        rc, _, err = _run(
            ["git", "apply", "--allow-empty", str(patch_file)],
            cwd=repo_dir,
            timeout=60,
        )
        if rc != 0:
            patch_file.unlink(missing_ok=True)
            raise RuntimeError(f"git apply test_patch failed: {err}")
        patch_file.unlink()

        # Stage and commit so HEAD reflects the test_patch state
        _run(["git", "add", "-A"], cwd=repo_dir, timeout=60)
        _run(
            ["git", "-c", "user.email=swe@bench", "-c", "user.name=swe",
             "commit", "-m", "test_patch baseline (benchmark internal)"],
            cwd=repo_dir, timeout=60,
        )
        test_patch_applied = True
    else:
        test_patch_applied = False

    return Checkout(
        instance_id=task.instance_id,
        repo_dir=repo_dir,
        base_commit=task.base_commit,
        test_patch_applied=test_patch_applied,
        cleanup_token=parent_dir,
    )


def extract_agent_diff(checkout: Checkout) -> str:
    """Return the diff of agent changes relative to the test_patch baseline.

    Uses ``git diff HEAD`` so the test_patch commit is excluded and
    only the agent's modifications are captured. If the agent made no
    changes, returns empty string.
    """
    rc, stdout, _ = _run(
        ["git", "diff", "HEAD"],
        cwd=checkout.repo_dir,
        timeout=60,
    )
    if rc != 0:
        return ""

    # Also include staged-but-uncommitted changes the agent may have left
    rc2, staged, _ = _run(
        ["git", "diff", "--staged", "HEAD"],
        cwd=checkout.repo_dir,
        timeout=60,
    )
    combined = stdout
    if rc2 == 0 and staged.strip() and staged not in combined:
        combined = combined + "\n" + staged

    return combined.strip()


def cleanup(checkout: Checkout) -> None:
    """Remove the temp directory created by clone_at_commit."""
    import shutil
    if checkout.cleanup_token and checkout.cleanup_token.exists():
        shutil.rmtree(checkout.cleanup_token, ignore_errors=True)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from task_setup import load_first_half

    tasks = load_first_half()
    t = tasks[0]
    print(f"Cloning {t.repo} at {t.base_commit[:12]}... (may take 30-60s)")
    co = clone_at_commit(t)
    try:
        print(f"OK. repo_dir={co.repo_dir}")
        print(f"test_patch applied: {co.test_patch_applied}")
        # Verify HEAD is at the expected state
        rc, out, _ = _run(["git", "log", "--oneline", "-3"], cwd=co.repo_dir)
        print("recent commits:")
        print(out)
    finally:
        cleanup(co)
        print("Cleaned up.")
