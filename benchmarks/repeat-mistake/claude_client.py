"""
Claude Code headless client wrapper for the LoCoMo benchmark.

Shells out to ``claude -p`` for one-shot prompt -> response calls. The
wrapper hardens the failure modes a multi-hour benchmark run can hit:

- per-call timeout (default 60s, configurable per call)
- exponential backoff on rate limits and transient errors (5 retries)
- output stripping so the answer is exactly what claude printed (no
  trailing newlines, no shell artifacts)
- structured error result instead of raising, so the calling harness
  can record the failure mode and move on

Usage:
    from benchmarks.locomo.claude_client import ClaudeClient
    c = ClaudeClient()
    out = c.complete("What is 2+2?")
    if out.ok:
        print(out.text)
    else:
        print(out.error)

The wrapper does not retain conversation state across calls. Every
``complete()`` is a fresh one-shot prompt; the LoCoMo benchmark fits
this shape because the answerer prompt is a single document containing
the retrieved memories plus the question.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class CompletionResult:
    """Outcome of one ``claude -p`` call.

    ``ok=True`` means stdout was returned. ``ok=False`` means we failed
    after all retries and ``error`` describes why.
    """
    ok: bool
    text: str = ""
    error: Optional[str] = None
    attempt: int = 0
    duration_sec: float = 0.0


class ClaudeClient:
    """Thin wrapper around ``claude -p`` for benchmark calls."""

    def __init__(
        self,
        binary: str = "claude",
        timeout_sec: int = 60,
        max_retries: int = 5,
        backoff_base_sec: float = 2.0,
    ) -> None:
        self.binary = binary
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.backoff_base_sec = backoff_base_sec

    def complete(
        self,
        prompt: str,
        *,
        timeout_sec: Optional[int] = None,
    ) -> CompletionResult:
        """Send ``prompt`` to ``claude -p`` and return the result.

        Uses ``--output-format text`` to keep stdout clean. The prompt is
        passed on stdin so prompts longer than shell argv limits work.
        On timeout, kills the process and retries with backoff. On any
        non-zero exit, retries up to ``max_retries`` with exponential
        backoff.
        """
        per_call_timeout = timeout_sec or self.timeout_sec
        last_error: Optional[str] = None
        start = time.monotonic()

        for attempt in range(1, self.max_retries + 1):
            try:
                proc = subprocess.run(
                    [self.binary, "-p", "--output-format", "text"],
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=per_call_timeout,
                )
                if proc.returncode == 0:
                    text = (proc.stdout or "").strip()
                    if text:
                        return CompletionResult(
                            ok=True,
                            text=text,
                            attempt=attempt,
                            duration_sec=time.monotonic() - start,
                        )
                    last_error = "empty stdout"
                else:
                    stderr = (proc.stderr or "").strip()[:500]
                    last_error = (
                        f"exit {proc.returncode}: {stderr or 'no stderr'}"
                    )
            except subprocess.TimeoutExpired:
                last_error = f"timeout after {per_call_timeout}s"
            except FileNotFoundError:
                # Binary missing is unrecoverable; do not retry.
                return CompletionResult(
                    ok=False,
                    error=f"binary {self.binary!r} not on PATH",
                    attempt=attempt,
                    duration_sec=time.monotonic() - start,
                )
            except Exception as exc:
                last_error = f"unexpected: {type(exc).__name__}: {exc}"

            if attempt < self.max_retries:
                wait = self.backoff_base_sec * (2 ** (attempt - 1))
                time.sleep(wait)

        return CompletionResult(
            ok=False,
            error=last_error or "unknown failure",
            attempt=self.max_retries,
            duration_sec=time.monotonic() - start,
        )

    def is_available(self) -> bool:
        """Return True if ``claude --version`` exits cleanly."""
        try:
            proc = subprocess.run(
                [self.binary, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return proc.returncode == 0
        except Exception:
            return False
