"""
Agentic agent runner for the v0.9 repeat-mistake benchmark.

Drives `claude -p` agentically against a local repo checkout.
The agent has access to Read/Edit/Bash/Glob/Grep tools and runs
multi-turn until it decides to stop or the subprocess timeout fires.

This rewrites the v0.9 single-shot first draft because the user
chose Option Y (agentic) after seeing the single-shot smoke result
(116s but produced a parseable but functionally wrong patch).

Architecture:
- For each task: clone repo at base_commit (via clone_repo.py)
- Invoke `claude -p` with --allowedTools + --add-dir = repo
- Wait up to 30 min for completion
- Extract `git diff HEAD` as the agent's patch
- Persist patch + metadata to predictions/metadata files
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from task_setup import Task  # noqa: E402
from clone_repo import (  # noqa: E402
    Checkout,
    clone_at_commit,
    cleanup,
    extract_agent_diff,
)


AGENT_PROMPT = """You are an expert software engineer working on a real GitHub issue from the {repo} project.

Your job: read the problem statement, explore the codebase, identify the root cause, make the fix, and verify it.

Problem statement:
{problem_statement}

{hints_block}

Instructions:
1. Use the available tools to read files and understand the codebase. The repository is checked out in the current directory.
2. Identify the specific file(s) and function(s) that need to change.
3. Make the minimal edits needed to fix the issue.
4. Do not modify test files. Tests have already been set up.
5. When you are confident the issue is fixed, stop. Do not run the full test suite; you may run targeted tests if helpful for verification but they are not required.
6. Do not commit changes. Leave your edits as working-tree modifications.

Begin by exploring the repository structure to locate the relevant files for this issue.
"""

TREATMENT_HEADER = """Prior learned constraints (from earlier attempts at similar tasks in this codebase):
{constraints_block}

These constraints were extracted from failure analysis of earlier coding attempts on this repo. Consult them as you work.

"""


@dataclass
class AgentRun:
    """Result of one agentic invocation."""

    instance_id: str
    arm: str
    success: bool  # subprocess completed without crashing
    extracted_patch: str
    patch_is_empty: bool
    duration_sec: float
    timeout_hit: bool
    error: Optional[str]
    session_id: Optional[str] = None
    total_cost_usd: Optional[float] = None
    num_turns: Optional[int] = None
    stdout_tail: Optional[str] = None
    stderr_tail: Optional[str] = None


def _hints_block(hints: Optional[str]) -> str:
    if not hints or not hints.strip():
        return ""
    return f"GitHub issue discussion:\n{hints[:4000]}\n\n"


def _parse_json_result(stdout: str) -> dict:
    """Parse the --output-format json envelope claude -p prints.

    The envelope contains: session_id, result, total_cost_usd,
    num_turns, etc. We extract what we can; missing fields default
    to None.
    """
    if not stdout.strip():
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # Sometimes the JSON is wrapped or has trailing content
        try:
            # Find the last complete JSON object
            start = stdout.find("{")
            if start == -1:
                return {}
            return json.loads(stdout[start:])
        except Exception:
            return {}


def run_agent(
    task: Task,
    *,
    arm: str,
    constraints: Optional[list[str]] = None,
    timeout_sec: int = 1800,
    parent_dir: Optional[Path] = None,
    cleanup_after: bool = True,
) -> AgentRun:
    """Run one agentic attempt on one task.

    Args:
        task: the SWE-bench task
        arm: "baseline" or "treatment"
        constraints: list of constraint strings to include in the prompt
            (only meaningful for arm="treatment")
        timeout_sec: subprocess timeout (default 30 minutes)
        parent_dir: where to clone the repo (default: a temp dir)
        cleanup_after: if True, delete the temp dir after extracting
            the patch. Set to False for debugging.
    """
    if arm not in ("baseline", "treatment"):
        raise ValueError(f"arm must be baseline or treatment, got {arm!r}")

    t0 = time.monotonic()
    checkout: Optional[Checkout] = None

    try:
        checkout = clone_at_commit(task, parent_dir=parent_dir)
    except Exception as exc:
        return AgentRun(
            instance_id=task.instance_id,
            arm=arm,
            success=False,
            extracted_patch="",
            patch_is_empty=True,
            duration_sec=time.monotonic() - t0,
            timeout_hit=False,
            error=f"clone failed: {exc}",
        )

    try:
        prompt = AGENT_PROMPT.format(
            repo=task.repo,
            problem_statement=task.problem_statement,
            hints_block=_hints_block(task.hints_text),
        )
        if arm == "treatment" and constraints:
            constraints_block = "\n".join(f"- {c}" for c in constraints)
            prompt = TREATMENT_HEADER.format(constraints_block=constraints_block) + prompt

        cmd = [
            "claude", "-p", prompt,
            "--allowedTools", "Read,Edit,Bash,Glob,Grep,Write",
            "--permission-mode", "acceptEdits",
            "--output-format", "json",
            "--add-dir", str(checkout.repo_dir),
        ]

        timeout_hit = False
        error: Optional[str] = None
        stdout = ""
        stderr = ""

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(checkout.repo_dir),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            if proc.returncode != 0:
                error = f"claude exit {proc.returncode}"
        except subprocess.TimeoutExpired as te:
            timeout_hit = True
            error = f"timeout after {timeout_sec}s"
            stdout = te.stdout.decode("utf-8", errors="replace") if te.stdout else ""
            stderr = te.stderr.decode("utf-8", errors="replace") if te.stderr else ""

        # Extract the diff regardless of whether claude exited cleanly:
        # the agent may have made useful edits before the timeout.
        patch = extract_agent_diff(checkout)

        # Parse the JSON envelope for metadata
        env = _parse_json_result(stdout) if stdout else {}

        return AgentRun(
            instance_id=task.instance_id,
            arm=arm,
            success=(error is None),
            extracted_patch=patch,
            patch_is_empty=(not patch.strip()),
            duration_sec=time.monotonic() - t0,
            timeout_hit=timeout_hit,
            error=error,
            session_id=env.get("session_id"),
            total_cost_usd=env.get("total_cost_usd"),
            num_turns=env.get("num_turns"),
            stdout_tail=stdout[-2000:] if stdout else None,
            stderr_tail=stderr[-1000:] if stderr else None,
        )

    finally:
        if cleanup_after and checkout is not None:
            cleanup(checkout)


def run_baseline(task: Task, **kwargs) -> AgentRun:
    """Run the baseline arm."""
    return run_agent(task, arm="baseline", **kwargs)


def run_treatment(task: Task, constraints: list[str], **kwargs) -> AgentRun:
    """Run the treatment arm with the given pre-learned constraints."""
    return run_agent(task, arm="treatment", constraints=constraints, **kwargs)


if __name__ == "__main__":
    # Smoke test: one task, one agent run, end-to-end
    from task_setup import load_first_half

    tasks = load_first_half()
    t = tasks[0]
    print(f"=== Agentic smoke test on {t.instance_id} ({t.repo}) ===")
    print(f"Problem (first 200 chars): {t.problem_statement[:200]}")
    print(f"FAIL_TO_PASS tests: {t.fail_to_pass}")
    print()
    print("Running baseline arm with 20-minute timeout. This is one real")
    print("agentic run on your Claude subscription. Wall-clock ~5-15 min.")
    print()

    run = run_baseline(t, timeout_sec=1200)

    print(f"=== Result ===")
    print(f"success: {run.success}")
    print(f"duration: {run.duration_sec:.0f}s")
    print(f"timeout hit: {run.timeout_hit}")
    print(f"patch is empty: {run.patch_is_empty}")
    print(f"num_turns: {run.num_turns}")
    print(f"total_cost_usd: {run.total_cost_usd}")
    print(f"error: {run.error}")
    print()
    if run.extracted_patch:
        print(f"=== Extracted patch (first 800 chars) ===")
        print(run.extracted_patch[:800])
    else:
        print("(empty patch)")
