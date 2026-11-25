"""The HTTP transport on the mcp release the security floor requires."""

from __future__ import annotations

import http.client
import importlib.metadata
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

pytest.importorskip("uvicorn")


@pytest.fixture(scope="module")
def url(tmp_path_factory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "world_model_server.server"],
        env={**os.environ, "WORLD_MODEL_TRANSPORT": "http", "WORLD_MODEL_HTTP_HOST": "127.0.0.1",
             "WORLD_MODEL_HTTP_PORT": str(port),
             "WORLD_MODEL_DB_PATH": str(tmp_path_factory.mktemp("db"))},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(200):
        try:
            urllib.request.urlopen(base + "/healthz", timeout=1)
            break
        except OSError:
            assert proc.poll() is None, "server exited"
            time.sleep(0.1)
    yield base + "/mcp/"
    proc.terminate()
    proc.wait(timeout=10)


def _post(url, body, session=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session:
        headers["mcp-session-id"] = session
    data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=30) as r:
            text = r.read().decode()
            lines = [x[5:] for x in text.splitlines() if x.startswith("data:")] or ([text] if text.strip() else [])
            return r.status, r.headers, json.loads(lines[0]) if lines else None
    except urllib.error.HTTPError as e:
        return e.code, e.headers, None


def _open(url):
    status, headers, _ = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    assert status == 200
    session = headers["mcp-session-id"]
    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    return session


def test_installed_mcp_is_at_the_floor():
    major, minor = map(int, importlib.metadata.version("mcp").split(".")[:2])
    assert (major, minor) >= (1, 30) and major < 2


def test_tools_list_and_call(url):
    session = _open(url)
    _, _, reply = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session)
    assert "record_event" in {t["name"] for t in reply["result"]["tools"]}
    _, _, reply = _post(url, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "record_event", "arguments": {"event_type": "tool_call", "session_id": "s",
                                              "entities": ["e"], "description": "d", "success": True}}}, session)
    assert not reply["result"].get("isError"), reply
    assert json.loads(reply["result"]["content"][0]["text"])["event_id"]


def test_unknown_session_is_404_and_oversize_is_413(url):
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    assert _post(url, call, "0" * 32)[0] == 404
    # Only the headers are sent: the size limit answers on Content-Length.
    conn = http.client.HTTPConnection(url.split("/")[2], timeout=30)
    conn.putrequest("POST", "/mcp/")
    for k, v in (("Content-Type", "application/json"), ("Accept", "application/json, text/event-stream"),
                 ("mcp-session-id", _open(url)), ("Content-Length", str(5 * 1024 * 1024))):
        conn.putheader(k, v)
    conn.endheaders()
    assert conn.getresponse().status == 413
    conn.close()


def test_idle_sessions_end_after_a_day(monkeypatch):
    import asyncio

    import mcp.server.streamable_http_manager as manager

    from world_model_server import server as srv

    seen = {}

    class StopError(Exception):
        pass

    def capture(*args, **kwargs):
        seen.update(kwargs)
        raise StopError

    monkeypatch.setattr(manager, "StreamableHTTPSessionManager", capture)
    with pytest.raises(StopError):
        asyncio.run(srv._run_http(object()))
    assert seen["session_idle_timeout"] == 24 * 60 * 60
