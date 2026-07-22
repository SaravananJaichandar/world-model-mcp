"""
End-to-end ACP integration tests: real buzz-agent binary + real
world-model-mcp Python server + local fake LLM.

See tests/integration/README.md for prerequisites and rationale.

Two tests:

  test_buzz_agent_spawns_world_model_mcp_end_to_end
      Single-agent verification: buzz-agent spawns world-model-mcp via
      ACP session/new, a tool call to query_fact round-trips, and the
      session ends cleanly.

  test_multi_agent_handoff_5_agents_via_shared_world_model_mcp
      Five sequential buzz-agent processes point at the same
      WORLD_MODEL_DB_PATH. Agents 1..4 each write a decision with a
      unique marker via record_decision. Agent 5 reads the decision
      log via get_decision_log and must see all four prior markers.
      Proves the multi-agent handoff claim at N=5: multiple agent
      processes hand off context to each other through the shared
      memory graph, without any agent knowing about the others.

Neither test is a simulation. Both spawn a real buzz-agent binary
(built from github.com/block/buzz) which in turn spawns
world-model-mcp as a stdio MCP subprocess via the standard ACP
mechanism. The LLM turn is answered by a local HTTP server that
returns canned OpenAI-shape responses — same pattern buzz-agent's own
tests use to avoid a real API dependency.

Skipped only when BUZZ_AGENT_BIN is unset or missing — the ONE class
of skip explicitly documented as legitimate in the project
engineering mandate (environment-conditional skip for an external
binary not present in every test host).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


BUZZ_AGENT_BIN_ENV = "BUZZ_AGENT_BIN"


def _buzz_agent_bin_path() -> str | None:
    """Resolve the buzz-agent binary path from env; None if unusable."""
    p = os.environ.get(BUZZ_AGENT_BIN_ENV)
    if not p:
        return None
    if not os.path.isfile(p):
        return None
    if not os.access(p, os.X_OK):
        return None
    return p


pytestmark = pytest.mark.skipif(
    _buzz_agent_bin_path() is None,
    reason=(
        "buzz-agent binary not available. Set BUZZ_AGENT_BIN to a "
        "release build (see tests/integration/README.md)."
    ),
)


# ---------------------------------------------------------------------------
# Fake OpenAI-compatible LLM server
# ---------------------------------------------------------------------------


class _FakeLLM(BaseHTTPRequestHandler):
    _canned: list[dict] = []
    _requests: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body_raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(body_raw) if body_raw else {}
        except json.JSONDecodeError:
            body = {}
        _FakeLLM._requests.append({"path": self.path, "body": body})
        response = (
            _FakeLLM._canned.pop(0)
            if _FakeLLM._canned
            else {"error": "no canned response left"}
        )
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200 if _FakeLLM._canned or response else 500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_fake_llm(port: int, canned: list[dict]) -> HTTPServer:
    _FakeLLM._canned = list(canned)
    _FakeLLM._requests = []
    server = HTTPServer(("127.0.0.1", port), _FakeLLM)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------


def _send(proc: subprocess.Popen, msg: dict) -> None:
    line = json.dumps(msg, separators=(",", ":")) + "\n"
    assert proc.stdin is not None
    proc.stdin.write(line.encode("utf-8"))
    proc.stdin.flush()


def _read_until(proc: subprocess.Popen, matcher, timeout: float) -> dict:
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.02)
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            continue
        if matcher(frame):
            return frame
    raise TimeoutError(f"timed out after {timeout}s waiting for frame")


# ---------------------------------------------------------------------------
# Session helper: run a single buzz-agent + world-model-mcp session
# ---------------------------------------------------------------------------


def _repo_root() -> str:
    return str(Path(__file__).resolve().parent.parent.parent)


def _run_session(
    canned_llm_responses: list[dict],
    wmm_db_path: str,
    work_dir: str,
    prompt_text: str,
) -> dict:
    """Run one buzz-agent session end-to-end and return a summary dict
    with the observed session_id, whether tool_call reached completed,
    and the collected tool-result content strings from
    session/update tool_call_update frames.

    canned_llm_responses is consumed FIFO by the fake LLM. The typical
    pattern is [tool_call_response, end_of_turn_response] — two entries.
    """
    buzz_agent = _buzz_agent_bin_path()
    assert buzz_agent is not None  # pytestmark guard

    python_bin = shutil.which("python3") or sys.executable
    repo_root = _repo_root()

    port = _find_free_port()
    fake_llm = _start_fake_llm(port, canned_llm_responses)

    env = os.environ.copy()
    env.update({
        "BUZZ_AGENT_PROVIDER": "openai",
        "OPENAI_COMPAT_BASE_URL": f"http://127.0.0.1:{port}",
        "OPENAI_COMPAT_API_KEY": "not-real",
        "OPENAI_COMPAT_MODEL": "fake",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": repo_root,
    })

    proc = subprocess.Popen(
        [buzz_agent],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=work_dir,
    )

    threading.Thread(
        target=lambda: [line for line in iter(proc.stderr.readline, b"")],
        daemon=True,
    ).start()

    try:
        _send(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1, "clientCapabilities": {}},
        })
        init_result = _read_until(
            proc, lambda f: f.get("id") == 1, timeout=15.0,
        )
        assert "result" in init_result
        assert init_result["result"]["agentInfo"]["name"] == "buzz-agent"

        _send(proc, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": work_dir,
                "mcpServers": [
                    {
                        "name": "world_model",
                        "command": python_bin,
                        "args": ["-m", "world_model_server.server"],
                        "env": [
                            {"name": "WORLD_MODEL_DB_PATH",
                             "value": wmm_db_path},
                            {"name": "PYTHONPATH",
                             "value": repo_root},
                        ],
                    }
                ],
            },
        })
        session_result = _read_until(
            proc, lambda f: f.get("id") == 2, timeout=60.0,
        )
        assert "result" in session_result
        session_id = session_result["result"]["sessionId"]
        assert session_id.startswith("ses_")

        _send(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": prompt_text}],
            },
        })

        summary = {
            "session_id": session_id,
            "saw_tool_call": False,
            "saw_tool_success": False,
            "tool_response_texts": [],
        }

        def _matcher(frame: dict) -> bool:
            update = frame.get("params", {}).get("update", {})
            stype = update.get("sessionUpdate")
            if stype in {"tool_call", "tool_call_update"}:
                summary["saw_tool_call"] = True
                if update.get("status") == "completed":
                    summary["saw_tool_success"] = True
                content = update.get("content")
                if isinstance(content, list):
                    for c in content:
                        inner = c.get("content", {})
                        if isinstance(inner, dict):
                            text = inner.get("text", "")
                            if text:
                                summary["tool_response_texts"].append(text)
            return frame.get("id") == 3

        _read_until(proc, _matcher, timeout=60.0)
        return summary

    finally:
        fake_llm.shutdown()
        try:
            proc.stdin.close()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _canned_tool_call(
    tool_name: str,
    arguments: dict,
    call_id: str = "call_fake_1",
) -> dict:
    """Build a canned OpenAI-shape response that emits one tool call."""
    return {
        "id": "chatcmpl-fake-tool",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "fake",
        "choices": [{
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments),
                    },
                }],
            },
        }],
    }


def _canned_end() -> dict:
    """Build a canned response that ends the turn cleanly."""
    return {
        "id": "chatcmpl-fake-end",
        "object": "chat.completion",
        "created": 1710000001,
        "model": "fake",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "done."},
        }],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.timeout(120)
def test_buzz_agent_spawns_world_model_mcp_end_to_end() -> None:
    """buzz-agent spawns world-model-mcp via ACP session/new and routes
    a tool call to it that completes successfully."""
    with tempfile.TemporaryDirectory() as wmm_db, \
         tempfile.TemporaryDirectory() as work_dir:
        summary = _run_session(
            canned_llm_responses=[
                _canned_tool_call(
                    "world_model__query_fact",
                    {"query": "probe"},
                ),
                _canned_end(),
            ],
            wmm_db_path=wmm_db,
            work_dir=work_dir,
            prompt_text="call query_fact.",
        )

    assert summary["saw_tool_call"], (
        "buzz-agent never emitted a tool_call session/update; the LLM "
        "turn did not attempt an MCP tool call"
    )
    assert summary["saw_tool_success"], (
        "tool_call observed but never reached status=completed; "
        "world-model-mcp did not return a successful result over MCP"
    )
    # QueryFactResult keys must appear in the returned payload
    joined = " ".join(summary["tool_response_texts"])
    assert all(k in joined for k in ("exists", "facts", "confidence")), (
        f"tool response did not contain QueryFactResult keys: "
        f"{summary['tool_response_texts']}"
    )
    assert _FakeLLM._requests, "fake LLM saw no requests from buzz-agent"


@pytest.mark.timeout(300)
def test_multi_agent_handoff_5_agents_via_shared_world_model_mcp() -> None:
    """Five sequential buzz-agent processes point at the same
    WORLD_MODEL_DB_PATH. Agents 1..4 each write a decision with a
    unique marker via record_decision. Agent 5 reads the decision log
    via get_decision_log and must observe all four prior markers.

    Proves the multi-agent handoff claim at N=5: multiple independent
    agent processes hand off context to each other through the shared
    memory graph, without any agent knowing about the others.

    Each agent runs in its own buzz-agent process with its own cwd and
    its own ACP session id. The ONLY shared state between them is the
    on-disk WORLD_MODEL_DB_PATH. Cross-contamination is impossible
    unless world-model-mcp truly persists writes visibly across
    processes."""

    NUM_WRITERS = 4
    shared_session_id = "buzz-multi-agent-5-handoff-test"
    markers = [
        f"MARKER-writer-{i}-{'abcdef123456789'[:9]}-{i}"
        for i in range(NUM_WRITERS)
    ]

    session_ids: list[str] = []

    with tempfile.TemporaryDirectory() as wmm_db:
        # === Agents 1..NUM_WRITERS: each writes one decision ===
        for i, marker in enumerate(markers):
            with tempfile.TemporaryDirectory() as work_dir:
                writer = _run_session(
                    canned_llm_responses=[
                        _canned_tool_call(
                            "world_model__record_decision",
                            {
                                "session_id": shared_session_id,
                                "decision_type": "approval",
                                "reasoning": marker,
                                "tool_name": f"handoff_write_{i}",
                            },
                            call_id=f"call_writer_{i}",
                        ),
                        _canned_end(),
                    ],
                    wmm_db_path=wmm_db,
                    work_dir=work_dir,
                    prompt_text=f"record decision {i}.",
                )
                assert writer["saw_tool_success"], (
                    f"Writer agent {i} failed to record decision "
                    f"(marker {marker!r}); cannot proceed with handoff "
                    f"test. Summary: {writer}"
                )
                session_ids.append(writer["session_id"])

        # === Agent 5: fresh process, same DB, reads all decisions ===
        with tempfile.TemporaryDirectory() as work_dir:
            reader = _run_session(
                canned_llm_responses=[
                    _canned_tool_call(
                        "world_model__get_decision_log",
                        {"session_id": shared_session_id, "limit": 100},
                        call_id="call_reader",
                    ),
                    _canned_end(),
                ],
                wmm_db_path=wmm_db,
                work_dir=work_dir,
                prompt_text="get decision log.",
            )
            session_ids.append(reader["session_id"])

    assert reader["saw_tool_success"], (
        f"Reader agent failed to read the decision log. Summary: "
        f"{reader}"
    )

    # Load-bearing assertion: reader must see EVERY writer's marker.
    joined = " ".join(reader["tool_response_texts"])
    missing = [m for m in markers if m not in joined]
    assert not missing, (
        f"Multi-agent handoff broken. {NUM_WRITERS} writer agents wrote "
        f"decisions to shared world-model-mcp DB, but the reader agent "
        f"({reader['session_id']}) did NOT observe {len(missing)} of "
        f"them. Missing markers: {missing}. Reader tool response: "
        f"{reader['tool_response_texts']!r}"
    )

    # Sanity: every one of the 5 agents must have gotten a distinct ACP
    # session id (else we accidentally ran fewer buzz-agent processes
    # than we thought, and the handoff test could be vacuously true).
    assert len(set(session_ids)) == NUM_WRITERS + 1, (
        f"Expected {NUM_WRITERS + 1} distinct ACP session ids across "
        f"5 buzz-agent processes, got: {session_ids}"
    )
