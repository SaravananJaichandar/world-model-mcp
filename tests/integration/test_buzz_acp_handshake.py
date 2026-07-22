"""
End-to-end ACP handshake integration test: real buzz-agent binary + real
world-model-mcp Python server + local fake LLM.

See tests/integration/README.md for prerequisites and rationale.

This is not a simulation. It spawns a real buzz-agent binary (built from
github.com/block/buzz), which in turn spawns world-model-mcp as a stdio
MCP subprocess via the standard ACP session/new mechanism.

Skipped only when BUZZ_AGENT_BIN is unset or missing — the ONE class of
skip explicitly documented as legitimate in the project engineering
mandate (environment-conditional skip for an external binary not
present in every test host).
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
# The test
# ---------------------------------------------------------------------------


@pytest.mark.timeout(120)
def test_buzz_agent_spawns_world_model_mcp_end_to_end() -> None:
    """buzz-agent spawns world-model-mcp via ACP session/new and routes
    a tool call to it that completes successfully."""

    buzz_agent = _buzz_agent_bin_path()
    assert buzz_agent is not None  # guarded by pytestmark

    python_bin = shutil.which("python3") or sys.executable

    # Repo root: this file lives at tests/integration/, so parent.parent
    # of __file__ is the repo root.
    from pathlib import Path
    repo_root = str(Path(__file__).resolve().parent.parent.parent)

    canned_tool_call = {
        "id": "chatcmpl-fake-1",
        "object": "chat.completion",
        "created": 1710000000,
        "model": "fake",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_fake_1",
                            "type": "function",
                            "function": {
                                "name": "world_model__query_fact",
                                "arguments": '{"query":"probe"}',
                            },
                        }
                    ],
                },
            }
        ],
    }
    canned_end = {
        "id": "chatcmpl-fake-2",
        "object": "chat.completion",
        "created": 1710000001,
        "model": "fake",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "done."},
            }
        ],
    }

    port = _find_free_port()
    fake_llm = _start_fake_llm(port, [canned_tool_call, canned_end])

    with tempfile.TemporaryDirectory() as wmm_db, \
         tempfile.TemporaryDirectory() as work_dir:
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

        # Drain stderr so the pipe doesn't back up.
        threading.Thread(
            target=lambda: [line for line in iter(proc.stderr.readline, b"")],
            daemon=True,
        ).start()

        try:
            # 1. initialize
            _send(proc, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": 1, "clientCapabilities": {}},
            })
            init_result = _read_until(
                proc, lambda f: f.get("id") == 1, timeout=15.0,
            )
            assert "result" in init_result, (
                f"initialize failed: {init_result}"
            )
            assert init_result["result"]["agentInfo"]["name"] == "buzz-agent"

            # 2. session/new — matches the McpServerStdio struct in
            #    buzz-agent/src/types.rs:195 exactly:
            #    name / command / args / env (list of {name, value}).
            _send(proc, {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/new",
                "params": {
                    "cwd": work_dir,
                    "mcpServers": [
                        {
                            "name": "world_model",  # underscore, not dash,
                            "command": python_bin,  # so the qualified tool
                            "args": [                # names buzz-agent
                                "-m",                # generates are clean
                                "world_model_server.server",
                            ],
                            "env": [
                                {"name": "WORLD_MODEL_DB_PATH",
                                 "value": wmm_db},
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
            assert "result" in session_result, (
                f"session/new failed: {session_result}"
            )
            session_id = session_result["result"]["sessionId"]
            assert session_id.startswith("ses_")

            # 3. session/prompt — triggers the LLM turn, which the fake
            #    LLM answers with a tool call to world_model__query_fact.
            _send(proc, {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": [
                        {"type": "text", "text": "call query_fact."}
                    ],
                },
            })

            saw_tool_call = False
            saw_tool_success = False
            saw_query_fact_result = False

            def _matcher(frame: dict) -> bool:
                nonlocal saw_tool_call, saw_tool_success, saw_query_fact_result
                update = frame.get("params", {}).get("update", {})
                stype = update.get("sessionUpdate")
                if stype in {"tool_call", "tool_call_update"}:
                    saw_tool_call = True
                    if update.get("status") == "completed":
                        saw_tool_success = True
                    content = update.get("content")
                    if isinstance(content, list):
                        for c in content:
                            inner = c.get("content", {})
                            text = (
                                inner.get("text", "")
                                if isinstance(inner, dict)
                                else ""
                            )
                            if all(k in text for k in ("exists", "facts", "confidence")):
                                saw_query_fact_result = True
                return frame.get("id") == 3

            _read_until(proc, _matcher, timeout=60.0)

            # Final assertions — these are the load-bearing verifications.
            assert saw_tool_call, (
                "buzz-agent never emitted a tool_call session/update; "
                "the LLM turn did not attempt an MCP tool call"
            )
            assert saw_tool_success, (
                "tool_call observed but never reached status=completed; "
                "world-model-mcp did not return a successful result to "
                "buzz-agent over the MCP call"
            )
            assert saw_query_fact_result, (
                "tool_call completed but the response payload did not "
                "look like a QueryFactResult (missing exists/facts/"
                "confidence keys); the MCP result may have been "
                "hijacked or malformed"
            )
            assert _FakeLLM._requests, (
                "fake LLM saw no requests from buzz-agent; the LLM "
                "turn never started"
            )

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
