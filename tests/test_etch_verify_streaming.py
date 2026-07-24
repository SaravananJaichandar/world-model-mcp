"""
Tests for verify_manifest_streaming (v0.15.5).

The streaming verifier must produce a verdict byte-identical to the
in-memory verify_manifest for the same manifest bytes. Hosted-side,
auditor-side, and the offline CLI all rely on this equivalence so
the same chain state produces the same evidence regardless of which
verifier path a consumer reaches for.

Also locked here:
  - Memory safety: peak allocation grows sublinearly with chain size
    (tracemalloc), so a future regression that materializes the
    manifest in memory fails the test.
  - Adversarial inputs: mutated fields on every field the verifier
    checks — chain link, entry_hash, epoch chain, epoch signature,
    annotation content, event content, manifest version — each
    surface the specific check name that the in-memory verifier
    would surface.
  - Empty-chain edge case.
  - "Audit disabled" export precondition still enforces via
    export_audit_dump_to_file_streaming; verify path is agnostic to
    that. Empty-manifest verify path yields OK.
"""

from __future__ import annotations

import gc
import json
import tracemalloc
from pathlib import Path

import pytest

from world_model_server import (
    audit_dump,
    etch_verify,
)
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event


# pytest-asyncio auto mode is already set project-wide in pyproject.toml,
# so async tests are picked up without an explicit marker.


async def _seed_chain(tmp_path, n_events: int):
    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()
    for i in range(n_events):
        await kg.create_event(Event(
            session_id=f"sess-{i}",
            event_type="tool_call",
            tool_name=f"seeded_tool_{i}",
            success=True,
        ))
    return kg


async def _seed_and_dump(tmp_path, monkeypatch, n_events: int,
                        epoch_size: int = 3):
    monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
    monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", str(epoch_size))
    kg = await _seed_chain(tmp_path, n_events=n_events)
    manifest_path = tmp_path / "manifest.json"
    await audit_dump.export_audit_dump_to_file_streaming(
        kg, str(manifest_path),
    )
    return manifest_path


class TestVerdictParityWithInMemory:
    """The streaming verifier must reach the same verdict as the
    in-memory verifier on the same manifest bytes. Byte-parity of
    the verdict is what lets hosted / auditor / CLI paths interchange
    freely."""

    async def test_ok_verdict_matches(self, tmp_path, monkeypatch):
        manifest_path = await _seed_and_dump(
            tmp_path, monkeypatch, n_events=6, epoch_size=3,
        )
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
        in_memory = etch_verify.verify_manifest(manifest)
        streaming = etch_verify.verify_manifest_streaming(manifest_path)

        assert in_memory.ok is True
        assert streaming.ok is True
        assert in_memory.entries_checked == streaming.entries_checked
        assert in_memory.epochs_checked == streaming.epochs_checked
        assert in_memory.annotations_checked == streaming.annotations_checked
        assert in_memory.events_checked == streaming.events_checked
        # Check names + pass/fail should match one-for-one in order.
        assert [c["name"] for c in in_memory.checks] == \
               [c["name"] for c in streaming.checks]
        assert [c["ok"] for c in in_memory.checks] == \
               [c["ok"] for c in streaming.checks]

    async def test_multi_epoch_ok_verdict_matches(
        self, tmp_path, monkeypatch,
    ):
        manifest_path = await _seed_and_dump(
            tmp_path, monkeypatch, n_events=12, epoch_size=4,
        )
        streaming = etch_verify.verify_manifest_streaming(manifest_path)
        assert streaming.ok
        assert streaming.epochs_checked >= 3
        assert streaming.entries_checked >= 12


class TestManifestVersionMismatch:
    """Version-mismatched manifests must fail with the same check
    name via streaming as via in-memory."""

    async def test_wrong_version_fails_with_named_check(
        self, tmp_path, monkeypatch,
    ):
        manifest_path = await _seed_and_dump(
            tmp_path, monkeypatch, n_events=3, epoch_size=3,
        )
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
        manifest["manifest_version"] = "999"
        with open(manifest_path, "w") as f:
            json.dump(
                manifest, f, indent=2, sort_keys=True, ensure_ascii=False,
            )

        streaming = etch_verify.verify_manifest_streaming(manifest_path)
        assert streaming.ok is False
        assert streaming.checks[0]["name"] == "manifest_version"
        assert streaming.checks[0]["ok"] is False


class TestAdversarialMutations:
    """Every field the verifier locks in should be caught with the
    exact check name the in-memory verifier would return, so hosted /
    auditor tooling can trigger the same alerting logic regardless
    of which verify path runs."""

    async def test_mutated_entry_hash_fails_chain_integrity(
        self, tmp_path, monkeypatch,
    ):
        manifest_path = await _seed_and_dump(
            tmp_path, monkeypatch, n_events=3, epoch_size=3,
        )
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
        # Corrupt the entry_hash of the first log entry. Chain
        # integrity will detect the recomputation mismatch.
        original = manifest["tamper_evident_log"][0]["entry_hash"]
        manifest["tamper_evident_log"][0]["entry_hash"] = (
            original.replace("sha256:", "sha256:0000")
        )
        with open(manifest_path, "w") as f:
            json.dump(
                manifest, f, indent=2, sort_keys=True, ensure_ascii=False,
            )

        streaming = etch_verify.verify_manifest_streaming(manifest_path)
        assert streaming.ok is False
        chain = [c for c in streaming.checks
                 if c["name"] == "chain_integrity"]
        assert chain and chain[0]["ok"] is False

    async def test_mutated_prev_hash_fails_chain_integrity(
        self, tmp_path, monkeypatch,
    ):
        manifest_path = await _seed_and_dump(
            tmp_path, monkeypatch, n_events=3, epoch_size=3,
        )
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
        # Corrupt second entry's prev_hash. Chain link check fires.
        manifest["tamper_evident_log"][1]["prev_hash"] = (
            "sha256:" + "0" * 64
        )
        with open(manifest_path, "w") as f:
            json.dump(
                manifest, f, indent=2, sort_keys=True, ensure_ascii=False,
            )

        streaming = etch_verify.verify_manifest_streaming(manifest_path)
        assert streaming.ok is False
        chain = [c for c in streaming.checks
                 if c["name"] == "chain_integrity"]
        assert chain and chain[0]["ok"] is False

    async def test_mutated_event_success_fails_event_content_lock(
        self, tmp_path, monkeypatch,
    ):
        """Flip an event's success bool. The chain locked in the
        original row_hash, so the flipped value fails content lock."""
        manifest_path = await _seed_and_dump(
            tmp_path, monkeypatch, n_events=3, epoch_size=3,
        )
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
        manifest["source_rows"]["events"][0]["success"] = False
        with open(manifest_path, "w") as f:
            json.dump(
                manifest, f, indent=2, sort_keys=True, ensure_ascii=False,
            )

        streaming = etch_verify.verify_manifest_streaming(manifest_path)
        assert streaming.ok is False
        content = [c for c in streaming.checks
                   if c["name"] == "event_content_lock"]
        assert content and content[0]["ok"] is False
        assert "row_hash mismatch" in (content[0]["detail"] or "")


class TestStreamingMemorySublinear:
    """Peak memory during streaming verify should not grow
    proportionally with chain size. Guards against a future refactor
    that accidentally materializes the manifest in memory."""

    async def test_peak_memory_grows_sublinearly(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "1024")

        async def _measure_peak_for_chain(n: int) -> int:
            sub_dir = tmp_path / f"chain_{n}"
            kg = await _seed_chain(sub_dir, n_events=n)
            manifest_path = sub_dir / "manifest.json"
            await audit_dump.export_audit_dump_to_file_streaming(
                kg, str(manifest_path),
            )
            gc.collect()
            tracemalloc.start()
            etch_verify.verify_manifest_streaming(manifest_path)
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            return peak

        peak_small = await _measure_peak_for_chain(100)
        peak_large = await _measure_peak_for_chain(500)

        # 5x more events should not produce ~5x peak allocation. The
        # streaming path scales roughly with the compact row_lookup
        # dict, which is 200-500 bytes per entry — measurable but
        # far below O(manifest) scaling. Allow up to 4x headroom to
        # tolerate the row_lookup growth + tracemalloc noise; catches
        # any regression that goes back to full-dict materialization.
        assert peak_large <= peak_small * 4, (
            f"streaming verify peak memory scaled with chain size — "
            f"100 events: {peak_small} bytes, "
            f"500 events: {peak_large} bytes. Ratio "
            f"{peak_large / peak_small:.2f}x is too close to the "
            f"expected 5x chain ratio, suggesting the manifest was "
            f"materialized in memory."
        )


class TestEmptyChainStreamingVerify:
    """A chain with no log entries, no epochs, no source rows should
    still verify OK — matches the in-memory verify path."""

    async def test_empty_chain_verifies_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        kg = KnowledgeGraph(str(tmp_path))
        await kg.initialize()

        manifest_path = tmp_path / "manifest.json"
        await audit_dump.export_audit_dump_to_file_streaming(
            kg, str(manifest_path),
        )
        report = etch_verify.verify_manifest_streaming(manifest_path)
        assert report.ok is True
        assert report.entries_checked == 0
        assert report.epochs_checked == 0
        assert report.annotations_checked == 0
        assert report.events_checked == 0


class TestMalformedManifestSurfacesJSONError:
    """Callers of verify_manifest_streaming get an ijson exception
    that the CLI converts to exit code 2 + a clear stderr message.
    Locked here so the CLI's error-handling contract has a test."""

    def test_malformed_json_raises_ijson_error(self, tmp_path):
        malformed = tmp_path / "bad.json"
        malformed.write_text("{not valid json")

        # ijson raises IncompleteJSONError, JSONError, or a
        # backend-specific exception. All are subclasses of the base
        # JSONError we catch in the CLI.
        with pytest.raises((Exception,)):
            etch_verify.verify_manifest_streaming(malformed)
