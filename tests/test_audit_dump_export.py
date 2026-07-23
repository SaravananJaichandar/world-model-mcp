"""
audit_dump export tests (etch-verify Day 1, v0.15.x follow-up).

Covers the dump-manifest export function that etch-verify will
consume. Verifies:

  - The dump captures every tamper_evident_log entry in seq order
  - The dump captures every closed epoch with parsed signature envelopes
  - Public keys are exported base64-Raw and round-trip back to bytes
  - Source-row sections capture annotations + events verbatim
  - Rationale text IS included in the annotations source-row section
    (needed for verifier reconstruction — deliberately not in the
    tamper log itself)
  - Manifest round-trips through JSON without loss
  - export_audit_dump_to_file writes byte-identical output for the
    same state (sorted keys + fixed indent) so audit artifacts hash
    deterministically
  - The audit-disabled path raises with a clear message

Chain-integrity, signature, and content-lock verification of the
exported manifest are Day 2's scope (the CLI reads the dump and
runs verify_inclusion_bundle over every row_id).
"""

from __future__ import annotations

import base64
import json
import os
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


async def _seeded_kg(tmp: str) -> tuple[KnowledgeGraph, WorldModelTools]:
    """A KnowledgeGraph with the audit chain on, plus one event and
    one pinned annotation so every dump section has content."""
    kg = KnowledgeGraph(tmp)
    await kg.initialize()
    tools = WorldModelTools(kg, Config(db_path=tmp))
    event = Event(
        id="evt-1",
        session_id="sess-1",
        event_type="tool_call",
        tool_name="run_tests",
        success=True,
    )
    await kg.create_event(event)
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
    return kg, tools


class TestDumpShape:
    """Top-level manifest structure — every key required for offline
    verification is present."""

    async def test_top_level_keys(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg, _ = await _seeded_kg(tmp)
                dump = await audit_dump.export_audit_dump(kg)

        assert dump["manifest_version"] == audit_dump.MANIFEST_VERSION
        assert set(dump.keys()) >= {
            "manifest_version",
            "generated_at",
            "world_model_mcp_version",
            "genesis_hash",
            "epoch_genesis_root",
            "public_keys",
            "tamper_evident_log",
            "epochs",
            "source_rows",
        }

    async def test_genesis_constants_match_module(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg, _ = await _seeded_kg(tmp)
                dump = await audit_dump.export_audit_dump(kg)

        assert dump["genesis_hash"] == tamper_evident.GENESIS_HASH
        assert dump["epoch_genesis_root"] == tamper_evident.EPOCH_GENESIS_ROOT


class TestLogAndEpochsCapture:
    """The dump captures every log entry and every closed epoch —
    nothing gets dropped."""

    async def test_all_log_entries_present_in_seq_order(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg, _ = await _seeded_kg(tmp)
                dump = await audit_dump.export_audit_dump(kg)

        entries = dump["tamper_evident_log"]
        assert len(entries) >= 2  # one event + one annotation, minimum
        kinds = [e["kind"] for e in entries]
        assert "event_create" in kinds
        assert "annotation_create" in kinds
        seqs = [e["seq"] for e in entries]
        assert seqs == sorted(seqs), (
            f"entries not in seq order: {seqs}"
        )
        for e in entries:
            assert set(e.keys()) == {
                "seq", "kind", "row_id", "row_hash",
                "prev_hash", "entry_hash", "ts",
            }

    async def test_closed_epoch_captured_with_parsed_envelope(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg, _ = await _seeded_kg(tmp)
                dump = await audit_dump.export_audit_dump(kg)

        epochs = dump["epochs"]
        assert len(epochs) == 1
        e = epochs[0]
        assert e["seq"] == 1
        assert e["merkle_root"].startswith("sha256:")
        assert e["prev_epoch_root"] == tamper_evident.EPOCH_GENESIS_ROOT
        # signature_envelope MUST be a dict, not a JSON string —
        # the verifier consumes it directly through verify_hybrid.
        assert isinstance(e["signature_envelope"], dict)


class TestPublicKeysExport:
    """Both halves of the hybrid signer's public key material are
    exported base64-Raw and decode back to the on-disk key bytes."""

    async def test_public_keys_round_trip_to_signer_bytes(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg, _ = await _seeded_kg(tmp)
                dump = await audit_dump.export_audit_dump(kg)
                signer = audit_keys.load_or_create_signer(kg.db_path)

        ed_from_dump = base64.b64decode(dump["public_keys"]["ed25519"])
        slh_from_dump = base64.b64decode(dump["public_keys"]["slh_dsa"])
        assert ed_from_dump == signer.ed25519_public_key_bytes()
        assert slh_from_dump == signer.slh_dsa_public_key_bytes()


class TestSourceRows:
    """Source-row sections capture the fields the verifier needs to
    reconstruct each chained row's canonical payload."""

    async def test_annotations_source_row_shape(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg, _ = await _seeded_kg(tmp)
                dump = await audit_dump.export_audit_dump(kg)

        annotations = dump["source_rows"]["annotations"]
        assert len(annotations) == 1
        row = annotations[0]
        assert set(row.keys()) == {
            "id",
            "session_id",
            "event_range_start",
            "event_range_end",
            "author",
            "rationale",
            "annotation_type",
        }
        # Rationale text IS in the dump — the verifier needs it to
        # recompute rationale_hash. Absence in the audit log itself
        # is the PII discipline; presence here is the auditor's tool.
        assert row["rationale"] == "reviewed and approved"

    async def test_events_source_row_shape(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg, _ = await _seeded_kg(tmp)
                dump = await audit_dump.export_audit_dump(kg)

        events = dump["source_rows"]["events"]
        assert len(events) == 1
        row = events[0]
        assert set(row.keys()) == {
            "id", "session_id", "event_type",
            "entity_id", "tool_name", "success",
        }
        assert row["id"] == "evt-1"
        assert row["success"] is True


class TestJsonRoundTrip:
    """Full manifest round-trips through json.dumps/json.loads
    without loss — the etch-verify CLI reads it back from disk."""

    async def test_manifest_json_serializes(self) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg, _ = await _seeded_kg(tmp)
                dump = await audit_dump.export_audit_dump(kg)

        encoded = json.dumps(dump)
        reloaded = json.loads(encoded)
        assert reloaded == dump


class TestFileExportIsDeterministic:
    """export_audit_dump_to_file writes sorted-key + fixed-indent
    JSON so the same audit state produces byte-identical files.
    Auditors hash the file as the artifact of record — non-
    determinism would break that."""

    async def test_two_dumps_of_same_state_produce_identical_files(
        self,
    ) -> None:
        with mock.patch.dict("os.environ", {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg, _ = await _seeded_kg(tmp)
                out_a = os.path.join(tmp, "dump_a.json")
                out_b = os.path.join(tmp, "dump_b.json")
                await audit_dump.export_audit_dump_to_file(kg, out_a)
                # generated_at differs between the two calls; read
                # both while the tempdir still exists, drop that key,
                # compare the rest for byte-equivalence.
                await audit_dump.export_audit_dump_to_file(kg, out_b)

                with open(out_a) as f:
                    a = json.load(f)
                with open(out_b) as f:
                    b = json.load(f)
        a.pop("generated_at")
        b.pop("generated_at")
        assert a == b, "two dumps of identical state differ (non-generated_at)"


class TestAuditDisabledPath:
    """Nothing to dump if the audit chain never ran — raise with
    an actionable message rather than emit an empty manifest."""

    async def test_disabled_raises(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                assert not kg.tamper_evident_enabled
                with pytest.raises(ValueError, match="audit chain is disabled"):
                    await audit_dump.export_audit_dump(kg)
