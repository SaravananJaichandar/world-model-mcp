"""
Tests for the streaming manifest exporter.

The load-bearing property: byte-identity between
`export_audit_dump_to_file` (in-memory) and
`export_audit_dump_to_file_streaming` for the same chain state.
Auditors hash the manifest itself as an artifact of record, so if
the two exporters produced different bytes for the same chain, the
same chain would have two different sha256 hashes depending on
which exporter the operator used. That defeats the artifact-hashing
guarantee.

We also lock:
  - Correctness: etch_verify PASSES on streaming output.
  - Memory safety: the streaming path uses O(row) instead of
    O(chain) memory, verified via tracemalloc on a synthetic chain
    large enough that the in-memory dict would dominate.
  - Empty-chain edge case: a project with no epochs and no events
    still produces valid parseable JSON.
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import os
import tracemalloc
from pathlib import Path

import aiosqlite
import pytest

from world_model_server import (
    audit_dump,
    audit_keys,
    etch_verify,
    tamper_evident,
)
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event


pytestmark = pytest.mark.asyncio


async def _seed_chain(tmp_path, n_events: int = 6):
    """Seed a KG with n_events events. Relies on auto-close at the
    threshold set by the caller via WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE
    to close epochs, so callers pick n_events >= threshold to
    guarantee at least one closed epoch."""
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


class TestByteParityWithInMemoryExporter:
    """The streaming exporter must produce bytes identical to the
    in-memory exporter for the same chain state (modulo the
    generated_at timestamp field which is captured at export time).

    Load-bearing because auditors hash the manifest as an artifact.
    Two exporters producing different bytes for the same chain would
    break the artifact-hashing guarantee.
    """

    async def test_bytes_match_after_normalizing_generated_at(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "3")
        kg = await _seed_chain(tmp_path, n_events=3)

        in_memory_path = tmp_path / "in_memory.json"
        streaming_path = tmp_path / "streaming.json"
        await audit_dump.export_audit_dump_to_file(kg, str(in_memory_path))
        await audit_dump.export_audit_dump_to_file_streaming(
            kg, str(streaming_path),
        )

        # Load both back and normalize the generated_at field before
        # comparing raw bytes. generated_at is set to datetime.now(UTC)
        # inside each export call, so the two will differ by whichever
        # microsecond they landed at.
        with open(in_memory_path, "r") as f:
            in_memory_json = json.load(f)
        with open(streaming_path, "r") as f:
            streaming_json = json.load(f)
        # Normalize the timestamp before re-serializing for byte
        # comparison.
        in_memory_json["generated_at"] = "NORMALIZED"
        streaming_json["generated_at"] = "NORMALIZED"

        # Re-serialize each with the SAME parameters both exporters
        # commit to, then compare bytes. This proves the two exporters
        # would produce identical files if given the exact same
        # generated_at value.
        in_memory_bytes = json.dumps(
            in_memory_json, indent=2, sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")
        streaming_bytes = json.dumps(
            streaming_json, indent=2, sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")
        assert in_memory_bytes == streaming_bytes, (
            "streaming exporter output does not round-trip byte-identical "
            "to the in-memory exporter output. Diff exposed via hash: "
            f"in-memory sha256={hashlib.sha256(in_memory_bytes).hexdigest()[:12]} "
            f"streaming sha256={hashlib.sha256(streaming_bytes).hexdigest()[:12]}"
        )

    async def test_streaming_raw_bytes_match_in_memory_raw_bytes(
        self, tmp_path, monkeypatch,
    ):
        """Even stronger: the two exporters' RAW output files should
        be byte-identical apart from the generated_at line. This
        proves indent, sort order, escape rules, and whitespace all
        match — not just the parsed JSON structure."""
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "3")
        kg = await _seed_chain(tmp_path, n_events=3)

        in_memory_path = tmp_path / "in_memory.json"
        streaming_path = tmp_path / "streaming.json"
        await audit_dump.export_audit_dump_to_file(kg, str(in_memory_path))
        await audit_dump.export_audit_dump_to_file_streaming(
            kg, str(streaming_path),
        )

        in_memory_raw = in_memory_path.read_text()
        streaming_raw = streaming_path.read_text()

        # Strip out the generated_at line before comparing. It's the
        # single line that legitimately differs between two exports
        # of the same chain.
        def _strip_generated_at(s: str) -> str:
            lines = s.split("\n")
            return "\n".join(
                l for l in lines if '"generated_at"' not in l
            )

        assert _strip_generated_at(in_memory_raw) == _strip_generated_at(
            streaming_raw
        ), (
            "streaming exporter raw bytes do not match in-memory raw bytes "
            "(diff not in generated_at line). Byte-parity broken."
        )


class TestStreamingOutputVerifies:
    """etch_verify.verify_manifest must PASS on streaming output.
    This is the "does the file work as evidence" property."""

    async def test_etch_verify_passes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "3")
        kg = await _seed_chain(tmp_path, n_events=3)

        out_path = tmp_path / "streaming.json"
        await audit_dump.export_audit_dump_to_file_streaming(
            kg, str(out_path),
        )
        with open(out_path, "r") as f:
            manifest = json.load(f)
        report = etch_verify.verify_manifest(manifest)
        assert report.ok, (
            f"etch_verify should PASS on streaming output; "
            f"failures: {[c for c in report.checks if not c['ok']]}"
        )

    async def test_etch_verify_passes_on_multi_epoch_chain(
        self, tmp_path, monkeypatch,
    ):
        """Chains with multiple closed epochs stream cleanly too. Also
        exercises the tamper_evident_log iterator across a longer
        seq range."""
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "4")
        kg = await _seed_chain(tmp_path, n_events=12)  # 3 epochs

        out_path = tmp_path / "streaming.json"
        await audit_dump.export_audit_dump_to_file_streaming(
            kg, str(out_path),
        )
        with open(out_path, "r") as f:
            manifest = json.load(f)
        assert len(manifest["epochs"]) >= 3
        assert len(manifest["tamper_evident_log"]) >= 12
        report = etch_verify.verify_manifest(manifest)
        assert report.ok


class TestStreamingUsesConstantMemory:
    """The whole point of streaming: memory usage grows with row
    count on the in-memory path and stays O(single row) on the
    streaming path. Verified via tracemalloc so a future refactor
    that accidentally materializes the manifest in memory fails
    the test loud."""

    async def test_streaming_memory_scales_sublinearly_with_chain(
        self, tmp_path, monkeypatch,
    ):
        """Seed two chains of very different sizes, measure peak
        allocation during streaming export of each. If streaming
        is O(row) as designed, peak memory is roughly equal for
        both. If it accidentally became O(chain), peak memory
        scales roughly with n_events."""
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "1024")

        async def _measure_peak_for_chain(n: int) -> int:
            sub_dir = tmp_path / f"chain_{n}"
            kg = await _seed_chain(sub_dir, n_events=n)
            out_path = sub_dir / "streaming.json"
            gc.collect()
            tracemalloc.start()
            await audit_dump.export_audit_dump_to_file_streaming(
                kg, str(out_path),
            )
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            return peak

        # 100 vs 500 events — a 5x scale.
        peak_small = await _measure_peak_for_chain(100)
        peak_large = await _measure_peak_for_chain(500)

        # Streaming should NOT have peak memory that scales with N.
        # Allow a generous 2x headroom for GC noise, file buffering,
        # etc, but if peak_large is 5x peak_small we know the manifest
        # is materialized.
        assert peak_large <= peak_small * 2, (
            f"streaming export peak memory scaled with chain size — "
            f"100 events: {peak_small} bytes, "
            f"500 events: {peak_large} bytes. If the ratio is close "
            f"to 5x, the streaming path is accidentally materializing "
            f"the manifest in memory."
        )


class TestEmptyChainEdgeCase:
    """A project that has enabled audit log but has zero events and
    zero closed epochs should still export a valid parseable
    manifest. The in-memory path handles this; the streaming path
    must too."""

    async def test_empty_chain_exports_parseable_json(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        # Just initialize a KG, do nothing else.
        kg = KnowledgeGraph(str(tmp_path))
        await kg.initialize()

        out_path = tmp_path / "streaming.json"
        await audit_dump.export_audit_dump_to_file_streaming(
            kg, str(out_path),
        )
        with open(out_path, "r") as f:
            manifest = json.load(f)
        assert manifest["manifest_version"] == "1"
        assert manifest["epochs"] == []
        assert manifest["tamper_evident_log"] == []
        assert manifest["source_rows"]["annotations"] == []
        assert manifest["source_rows"]["events"] == []


class TestStreamingRaisesWhenAuditDisabled:
    """Same guard as export_audit_dump — refuse to export from a KG
    that never enabled the audit log, with the same actionable
    message. Callers should not silently produce empty manifests."""

    async def test_raises_valueerror(self, tmp_path):
        # Do NOT set WORLD_MODEL_AUDIT_LOG.
        kg = KnowledgeGraph(str(tmp_path))
        await kg.initialize()
        with pytest.raises(ValueError, match="audit chain is disabled"):
            await audit_dump.export_audit_dump_to_file_streaming(
                kg, str(tmp_path / "streaming.json"),
            )
