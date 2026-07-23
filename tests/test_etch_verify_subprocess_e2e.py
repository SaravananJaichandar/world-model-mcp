"""
etch-verify subprocess E2E (Day 3, v0.15.x follow-up).

Exercises the CLI the way a real auditor would: shell out to the
`etch-verify` process against a dump file on disk. The Day 2 tests
call verify_manifest / main() in-process; this test file adds the
subprocess boundary so we catch:

  - argparse regressions
  - stdout / stderr formatting drift
  - JSON output parseable when captured from a real pipe
  - non-zero exit codes propagate through a real process boundary

Invoked as `python -m world_model_server.etch_verify` so the tests
do not require `pip install .` to have placed the console script on
PATH. Behavior is identical — the `if __name__ == "__main__":` guard
in etch_verify.py routes through the same `main()` the entry point
declares.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from unittest import mock

import aiosqlite
import pytest

from world_model_server import audit_dump, audit_keys, tamper_evident
from world_model_server.config import Config
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event
from world_model_server.tools import WorldModelTools

pytestmark = pytest.mark.asyncio


async def _seed_and_export(tmp: str) -> str:
    """Seed one event + one annotation, close epoch, write dump to
    disk, return the file path."""
    kg = KnowledgeGraph(tmp)
    await kg.initialize()
    tools = WorldModelTools(kg, Config(db_path=tmp))
    await kg.create_event(
        Event(
            id="evt-1",
            session_id="sess-1",
            event_type="tool_call",
            tool_name="run_tests",
            success=True,
        ),
    )
    await tools.pin_annotation(
        session_id="sess-1",
        event_range_start="evt-1",
        event_range_end="evt-1",
        author="alice",
        rationale="reviewed and approved",
        annotation_type="human_note",
    )
    signer = audit_keys.load_or_create_signer(kg.db_path)
    async with aiosqlite.connect(kg.audit_db) as db:
        await tamper_evident.close_epoch(db, signer)
        await db.commit()
    out = os.path.join(tmp, "dump.json")
    await audit_dump.export_audit_dump_to_file(kg, out)
    return out


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run etch-verify as a real subprocess via `python -m …`.

    stdout + stderr captured as text; caller inspects returncode and
    stream contents.
    """
    return subprocess.run(
        [sys.executable, "-m", "world_model_server.etch_verify", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestSubprocessHappyPath:
    async def test_exit_zero_and_verdict_ok(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                path = await _seed_and_export(tmp)
                result = _run_cli(path)
        assert result.returncode == 0, (
            f"expected 0, got {result.returncode}. "
            f"stderr: {result.stderr!r}"
        )
        assert "VERDICT: OK" in result.stdout
        assert "[PASS] chain_integrity" in result.stdout
        assert "[PASS] epoch_signatures" in result.stdout
        assert "[PASS] annotation_content_lock" in result.stdout
        assert "[PASS] event_content_lock" in result.stdout

    async def test_json_output_parses_from_pipe(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                path = await _seed_and_export(tmp)
                result = _run_cli(path, "--json")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["counts"]["annotations"] == 1
        assert payload["counts"]["events"] == 1
        assert payload["manifest_path"].endswith("dump.json")
        assert len(payload["manifest_sha256"]) == 64


class TestSubprocessTamperDetection:
    async def test_edited_rationale_causes_exit_one(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                path = await _seed_and_export(tmp)
                with open(path) as f:
                    manifest = json.load(f)
                manifest["source_rows"]["annotations"][0][
                    "rationale"
                ] = "post-signing rationale rewrite"
                with open(path, "w") as f:
                    json.dump(manifest, f)
                result = _run_cli(path)
        assert result.returncode == 1
        assert "VERDICT: FAILED" in result.stdout
        assert "[FAIL] annotation_content_lock" in result.stdout

    async def test_edited_public_key_causes_exit_one(self) -> None:
        import base64

        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                path = await _seed_and_export(tmp)
                with open(path) as f:
                    manifest = json.load(f)
                manifest["public_keys"]["ed25519"] = base64.b64encode(
                    b"\x22" * 32
                ).decode()
                with open(path, "w") as f:
                    json.dump(manifest, f)
                result = _run_cli(path)
        assert result.returncode == 1
        assert "[FAIL] epoch_signatures" in result.stdout


class TestSubprocessInputErrors:
    def test_missing_file_exits_two_with_stderr(self, tmp_path) -> None:
        missing = tmp_path / "not-here.json"
        result = _run_cli(str(missing))
        assert result.returncode == 2
        assert "cannot read" in result.stderr

    def test_malformed_json_exits_two_with_stderr(self, tmp_path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json")
        result = _run_cli(str(bad))
        assert result.returncode == 2
        assert "not valid JSON" in result.stderr
