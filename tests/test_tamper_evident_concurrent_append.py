"""
Regression test for the concurrent-append race in
world_model_server.tamper_evident.append_entry.

The race was surfaced on prod 2026-07-23 when the offline verifier
detected a chain integrity break on the saha project — entry
seq=667 had a prev_hash pointing at seq=665's entry_hash (the SAME
prev_hash seq=666 had), instead of at seq=666's entry_hash. The
verifier caught it. This test locks the fix in place so that
future refactors of the append path can't reintroduce the window.

Why this test matters for the trust story:
  - We (Etch) ship an audit chain as compliance evidence. A silent
    race in the append path would mean chains that LOOK valid on
    a chain-integrity check when nobody's watching but fail loudly
    when an auditor runs the offline verifier. That's a worse
    surprise than a bug we already know about — it's a bug that
    makes our OWN evidence unusable.
  - Our own offline verifier caught this exact bug in production.
    That's the trust surface working correctly. But it's still a
    bug: any customer running world-model-mcp with concurrent MCP
    writers would accumulate the same chain break. Fixing it in
    the append path is the primary control; the verifier is the
    secondary catch.

The test:
  - Spawns N concurrent create_event tasks against a single
    KnowledgeGraph.
  - Runs to completion.
  - Reads the log back and verifies:
      * every entry's prev_hash equals the previous entry's
        entry_hash (chain integrity by construction)
      * seq numbers are contiguous starting from 1 (no gaps,
        no duplicates)
      * The offline verifier PASSes on the resulting chain.

If the race reappears, the prev_hash mismatch or a seq gap will
fail the invariant before the offline verifier ever runs.
"""

from __future__ import annotations

import asyncio
import os

import aiosqlite
import pytest

from world_model_server import audit_dump, audit_keys, etch_verify, tamper_evident
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Event


pytestmark = pytest.mark.asyncio


async def _spawn_writer(kg: KnowledgeGraph, idx: int) -> None:
    """One writer task: append a single event."""
    await kg.create_event(
        Event(
            session_id=f"sess-race",
            event_type="tool_call",
            tool_name=f"writer_{idx:03d}",
            reasoning=f"concurrent write attempt {idx}",
            success=True,
        )
    )


async def test_concurrent_appends_produce_valid_chain(
    tmp_path, monkeypatch,
):
    """N concurrent create_event tasks must land N contiguous
    entries with a valid chain. If any prev_hash mismatches its
    logical predecessor, this test fails — that IS the saha
    incident, reproduced deterministically."""
    monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
    monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "1000")

    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()

    # 40 concurrent appends — small enough to be quick, large enough
    # that the race window would be hit under the pre-fix code on any
    # box with even mild parallelism.
    N = 40
    await asyncio.gather(*(_spawn_writer(kg, i) for i in range(N)))

    # Read the whole chain
    async with aiosqlite.connect(kg.audit_db) as db:
        cur = await db.execute(
            "SELECT seq, entry_hash, prev_hash FROM tamper_evident_log "
            "ORDER BY seq ASC"
        )
        rows = await cur.fetchall()

    # 1. Exactly N entries, contiguous seq numbers 1..N. No gaps,
    #    no duplicates.
    assert len(rows) == N, (
        f"expected {N} entries after {N} concurrent appends, got "
        f"{len(rows)} — either some appends failed silently or the "
        f"race produced a duplicate seq that got renumbered"
    )
    seqs = [r[0] for r in rows]
    assert seqs == list(range(1, N + 1)), (
        f"seq numbers should be contiguous 1..{N}; got {seqs[:5]}..."
        f"{seqs[-5:]}"
    )

    # 2. Chain integrity: every entry's prev_hash equals the
    #    previous entry's entry_hash. Entry 1's prev_hash is the
    #    GENESIS_HASH.
    for i, (seq, entry_hash, prev_hash) in enumerate(rows):
        if i == 0:
            assert prev_hash == tamper_evident.GENESIS_HASH, (
                f"seq=1 prev_hash should be GENESIS_HASH, got {prev_hash!r}"
            )
        else:
            expected = rows[i - 1][1]  # previous entry's entry_hash
            assert prev_hash == expected, (
                f"chain integrity broken at seq={seq}: prev_hash "
                f"{prev_hash!r} does not match previous entry's "
                f"entry_hash {expected!r} — the concurrent-append "
                f"race is back"
            )

    # 3. Belt-and-braces: run the offline verifier over the exported
    #    dump. This is what a real auditor runs. It must PASS.
    manifest = await audit_dump.export_audit_dump(kg)
    report = etch_verify.verify_manifest(manifest)
    assert report.ok, (
        f"offline verifier must PASS on a chain produced by "
        f"concurrent writes; failures: "
        f"{[c for c in report.checks if not c['ok']]}"
    )
    assert report.entries_checked == N


async def test_concurrent_appends_across_epoch_boundaries(
    tmp_path, monkeypatch,
):
    """Same guarantee, but with the epoch-close code path active.
    Some writers land inside epoch 1, some trigger epoch close, some
    land in epoch 2. Chain integrity and epoch signatures both must
    remain valid."""
    monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
    monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "10")

    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()

    N = 25  # crosses two epoch closes (at seq=10, seq=20)
    await asyncio.gather(*(_spawn_writer(kg, i) for i in range(N)))

    manifest = await audit_dump.export_audit_dump(kg)
    report = etch_verify.verify_manifest(manifest)
    assert report.ok, (
        f"chain + epoch signatures must remain valid across "
        f"epoch boundaries under concurrent load; failures: "
        f"{[c for c in report.checks if not c['ok']]}"
    )
    assert report.entries_checked == N
    assert report.epochs_checked >= 2
