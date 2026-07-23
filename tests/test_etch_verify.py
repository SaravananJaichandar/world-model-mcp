"""
etch-verify CLI + verifier tests (Day 2, v0.15.x follow-up).

Exercises world_model_server.etch_verify — the offline reference
verifier. Coverage:

  - Happy path: a manifest exported by audit_dump verifies clean
    across all four checks (chain integrity + epoch chain +
    epoch signatures + annotation content lock + event content lock).
    Reported counts match the seeded state.
  - Chain integrity fails when a log entry's entry_hash is edited.
  - Epoch signature fails when a public key is swapped for a bad one.
  - Epoch chain fails when a prev_epoch_root is edited.
  - Annotation content lock fails when the dump's rationale text is
    edited (mimics annotations.db being tampered post-signing).
  - Annotation content lock fails when annotation_type is swapped.
  - Event content lock fails when tool_name is edited in the dump.
  - Missing log entry for a source_rows row is rejected.
  - Bad manifest version is rejected.
  - CLI happy path exits 0 with human-readable "VERDICT: OK".
  - CLI --json flag emits parseable JSON with expected keys.
  - CLI exits 1 on a tampered manifest.
  - CLI exits 2 on unreadable / malformed manifest.

Manifest is regenerated fresh in each test via audit_dump so the
verifier is exercised against real chain state, not fixture data.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from unittest import mock

import aiosqlite
import pytest

from world_model_server import (
    audit_dump,
    audit_keys,
    etch_verify,
    tamper_evident,
)
from world_model_server.config import Config
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event
from world_model_server.tools import WorldModelTools

pytestmark = pytest.mark.asyncio


async def _seeded_manifest(tmp: str) -> tuple[dict, KnowledgeGraph]:
    """Seed one event + one annotation, close epoch, return the
    manifest a Day 1 export produces."""
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
    manifest = await audit_dump.export_audit_dump(kg)
    return manifest, kg


class TestVerifierHappyPath:
    async def test_seeded_manifest_verifies_clean(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
        report = etch_verify.verify_manifest(manifest)
        assert report.ok, [c for c in report.checks if not c["ok"]]
        assert report.entries_checked >= 2
        assert report.epochs_checked == 1
        assert report.annotations_checked == 1
        assert report.events_checked == 1
        names = {c["name"] for c in report.checks}
        assert names >= {
            "manifest_version",
            "chain_integrity",
            "epoch_chain",
            "epoch_signatures",
            "annotation_content_lock",
            "event_content_lock",
        }


class TestChainIntegrityTamper:
    async def test_edited_entry_hash_detected(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
        manifest["tamper_evident_log"][1]["entry_hash"] = (
            "sha256:" + "00" * 32
        )
        report = etch_verify.verify_manifest(manifest)
        assert not report.ok
        failure = next(c for c in report.checks if not c["ok"])
        assert failure["name"] == "chain_integrity"
        assert "recomputation mismatch" in (failure.get("detail") or "")

    async def test_edited_prev_hash_detected(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
        manifest["tamper_evident_log"][1]["prev_hash"] = (
            "sha256:" + "aa" * 32
        )
        report = etch_verify.verify_manifest(manifest)
        assert not report.ok
        assert any(
            not c["ok"] and c["name"] == "chain_integrity" for c in report.checks
        )


class TestEpochChecks:
    async def test_bad_ed25519_public_key_fails_signature(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
        # Swap ed25519 public key for an unrelated 32-byte value.
        manifest["public_keys"]["ed25519"] = base64.b64encode(b"\x11" * 32).decode()
        report = etch_verify.verify_manifest(manifest)
        assert not report.ok
        assert any(
            not c["ok"] and c["name"] == "epoch_signatures"
            for c in report.checks
        )

    async def test_edited_prev_epoch_root_fails_chain(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
        manifest["epochs"][0]["prev_epoch_root"] = (
            "sha256:" + "cc" * 32
        )
        report = etch_verify.verify_manifest(manifest)
        assert not report.ok
        assert any(
            not c["ok"] and c["name"] == "epoch_chain"
            for c in report.checks
        )


class TestAnnotationContentLock:
    @pytest.mark.parametrize(
        "field,new_value",
        [
            ("rationale", "tampered rationale not signed"),
            ("author", "eve@example.com"),
            ("event_range_start", "evt-forged"),
            ("event_range_end", "evt-forged"),
            ("annotation_type", "override_justification"),
        ],
    )
    async def test_field_mutation_detected(
        self, field: str, new_value: str,
    ) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
        manifest["source_rows"]["annotations"][0][field] = new_value
        report = etch_verify.verify_manifest(manifest)
        assert not report.ok
        assert any(
            not c["ok"] and c["name"] == "annotation_content_lock"
            for c in report.checks
        )

    async def test_missing_log_entry_for_annotation_rejected(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
        # Change the annotation id in source_rows so there is no
        # matching row_id in the log.
        manifest["source_rows"]["annotations"][0]["id"] = "does-not-exist"
        report = etch_verify.verify_manifest(manifest)
        assert not report.ok
        failure = next(
            c for c in report.checks
            if not c["ok"] and c["name"] == "annotation_content_lock"
        )
        assert "no matching log entry" in failure["detail"]


class TestEventContentLock:
    async def test_edited_tool_name_detected(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
        manifest["source_rows"]["events"][0]["tool_name"] = "rm_rf_slash"
        report = etch_verify.verify_manifest(manifest)
        assert not report.ok
        assert any(
            not c["ok"] and c["name"] == "event_content_lock"
            for c in report.checks
        )


class TestManifestVersionCheck:
    async def test_unknown_version_rejected(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
        manifest["manifest_version"] = "99"
        report = etch_verify.verify_manifest(manifest)
        assert not report.ok
        failure = report.checks[0]
        assert failure["name"] == "manifest_version"
        assert not failure["ok"]


class TestCLIHappyPath:
    async def test_cli_exit_zero_on_clean_manifest(
        self, capsys: pytest.CaptureFixture,
    ) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
                path = os.path.join(tmp, "dump.json")
                with open(path, "w") as f:
                    json.dump(manifest, f)
                rc = etch_verify.main([path])
        captured = capsys.readouterr()
        assert rc == 0
        assert "VERDICT: OK" in captured.out
        assert "[PASS] chain_integrity" in captured.out

    async def test_cli_json_flag_emits_parseable_json(
        self, capsys: pytest.CaptureFixture,
    ) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
                path = os.path.join(tmp, "dump.json")
                with open(path, "w") as f:
                    json.dump(manifest, f)
                rc = etch_verify.main([path, "--json"])
        captured = capsys.readouterr()
        assert rc == 0
        parsed = json.loads(captured.out)
        assert parsed["ok"] is True
        assert parsed["counts"]["annotations"] == 1
        assert parsed["counts"]["events"] == 1
        # The CLI stamps a SHA-256 of the exact bytes read.
        assert "manifest_sha256" in parsed
        assert len(parsed["manifest_sha256"]) == 64
        assert "manifest_path" in parsed


class TestCLIFailureExitCodes:
    async def test_cli_exit_one_on_tampered_manifest(
        self, capsys: pytest.CaptureFixture,
    ) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                manifest, _ = await _seeded_manifest(tmp)
                manifest["source_rows"]["annotations"][0][
                    "rationale"
                ] = "TAMPER"
                path = os.path.join(tmp, "dump.json")
                with open(path, "w") as f:
                    json.dump(manifest, f)
                rc = etch_verify.main([path])
        captured = capsys.readouterr()
        assert rc == 1
        assert "VERDICT: FAILED" in captured.out
        assert "annotation_content_lock" in captured.out

    def test_cli_exit_two_on_missing_manifest(
        self, capsys: pytest.CaptureFixture, tmp_path,
    ) -> None:
        missing = tmp_path / "does-not-exist.json"
        rc = etch_verify.main([str(missing)])
        captured = capsys.readouterr()
        assert rc == 2
        assert "cannot read" in captured.err

    def test_cli_exit_two_on_malformed_json(
        self, capsys: pytest.CaptureFixture, tmp_path,
    ) -> None:
        bad = tmp_path / "malformed.json"
        bad.write_text("{ not valid json")
        rc = etch_verify.main([str(bad)])
        captured = capsys.readouterr()
        assert rc == 2
        assert "not valid JSON" in captured.err
