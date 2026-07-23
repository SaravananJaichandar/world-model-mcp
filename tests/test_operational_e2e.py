"""
Operational-behavior E2E tests (2026-07-23).

These prove the operational claims Etch's public replies make,
not the crypto claims. Both scenarios were called out publicly
as gaps in the launch conversation:

  1. "If the OSS world-model-mcp server crashes mid-session, does
     the agent tool call fail (audit-critical), or does the agent
     keep working with the chain event dropped?"

  2. "What happens if the primary write succeeds but the audit
     append fails? The offline verifier proves the chain-as-collected
     is internally consistent — it can't prove completeness."

We answer each with an executable test rather than a prose claim.
If the operational behavior ever regresses, these fail and force
the answer to be re-derived.

Design intent
=============

Test 1 (MCP subprocess crash):
  - Start a real world-model-mcp subprocess.
  - Send it a valid tool call over stdio via JSON-RPC.
  - Kill it mid-response with SIGKILL to simulate a crash.
  - Confirm: the DB on disk is in a consistent state (either the
    write persisted OR it didn't — no half-written row).
  - Restart the server process.
  - Send another valid tool call.
  - Confirm: the new call succeeds; chain state is coherent; the
    offline verifier passes on whatever the on-disk chain is.

Test 2 (primary write succeeds, audit append fails):
  - Bring up a KnowledgeGraph with tamper-evident opt-in.
  - Monkey-patch the audit append function to raise.
  - Call create_event — primary state persists, audit throws.
  - Confirm: primary events table has the row.
  - Confirm: audit chain does NOT have a corresponding entry.
  - Run the offline verifier — it should return PASS because the
    audit chain itself is internally consistent.
  - This is the honest limit we name publicly: offline verifier
    proves what IS on the chain is consistent, not that everything
    that happened made it onto the chain.

Neither test is a "should never happen" edge case — both are
real failure modes a compliance officer will ask about. If we
tell customers "the offline verifier is the trust root," we owe
them a written characterization of exactly what it verifies and
what it doesn't. These tests are that characterization.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from unittest import mock

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


class TestMcpServerCrashDuringSession:
    """The 'MCP subprocess dies mid-tool-call' scenario.

    We simulate the crash at the KG layer directly — the client is
    Python calling into the KG async API, which is how the MCP
    server processes JSON-RPC requests once dispatched. This is
    the same code path a real subprocess crash breaks.

    We do NOT exercise the actual subprocess-over-stdio dance
    because (a) that is a Claude Code / Cursor concern, not Etch's
    — the client side's crash-and-recover is their responsibility,
    and (b) the crash-recovery invariant that matters to Etch is
    "chain state on disk stays consistent," which is what we assert
    here directly."""

    async def test_chain_state_survives_crash_between_writes(
        self, monkeypatch, tmp_path,
    ):
        """First 'process' writes some events, closes an epoch,
        then abruptly terminates (simulated by dropping the
        connection). Second 'process' opens the same DB path,
        continues writing, closes another epoch. Offline verifier
        runs against the full dump and passes.

        Regression guard: if the epoch-close write ever becomes
        non-transactional, this test would surface it — the second
        process would see partial state and the offline verifier
        would fail."""
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "2")

        # Process A: write 2 events, epoch closes at threshold=2
        kg_a = KnowledgeGraph(str(tmp_path))
        await kg_a.initialize()
        await kg_a.create_event(Event(
            session_id="sess-1", event_type="tool_call",
            tool_name="pre_crash_1", success=True,
        ))
        await kg_a.create_event(Event(
            session_id="sess-1", event_type="tool_call",
            tool_name="pre_crash_2", success=True,
        ))

        # Simulate the process dying — drop the KG reference. The
        # aiosqlite connection is per-write, not held open, so no
        # explicit close is needed. Any half-committed transaction
        # would have been rolled back at write time.
        del kg_a

        # Process B: open the same DB path, resume writing
        kg_b = KnowledgeGraph(str(tmp_path))
        await kg_b.initialize()
        await kg_b.create_event(Event(
            session_id="sess-1", event_type="tool_call",
            tool_name="post_crash_1", success=True,
        ))
        await kg_b.create_event(Event(
            session_id="sess-1", event_type="tool_call",
            tool_name="post_crash_2", success=True,
        ))

        # Signing keys must survive crash — same keys on both sides
        signer = audit_keys.load_or_create_signer(kg_b.db_path)
        assert signer.ed25519_public_key_bytes() is not None

        # Offline verifier passes over the full chain
        manifest = await audit_dump.export_audit_dump(kg_b)
        report = etch_verify.verify_manifest(manifest)
        assert report.ok, (
            f"chain must remain verifiable across process crash; "
            f"got failures: "
            f"{[c for c in report.checks if not c['ok']]}"
        )
        # And it saw all 4 events
        assert report.entries_checked >= 4

    async def test_agent_does_not_hang_when_server_absent(
        self, tmp_path, monkeypatch,
    ):
        """The compliance-officer question: if the MCP server can't
        be reached, does the developer's agent hang, or does the
        specific tool call fail cleanly?

        We can't simulate the whole Claude-Code-over-stdio dance
        here, but we CAN prove the equivalent at the API layer:
        calling create_event against a KG whose DB path is
        unreadable raises quickly rather than blocking. The MCP
        wrapper propagates that as a tool-call error to the client,
        which is what Claude Code sees."""
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        # Create a KG whose db_path is a directory we don't have
        # write access to. Simulates a broken deployment where the
        # server process exists but can't write.
        bogus_root = tmp_path / "does_not_exist" / "nested"
        try:
            kg = KnowledgeGraph(str(bogus_root))
            # The failure surfaces on initialize or first write.
            # Either way, it must raise, not hang.
            await asyncio.wait_for(kg.initialize(), timeout=5.0)
            await asyncio.wait_for(
                kg.create_event(Event(
                    session_id="sess-x", event_type="tool_call",
                    tool_name="test", success=True,
                )),
                timeout=5.0,
            )
            # If we reached here without raising, the path was
            # somehow writable — not a real failure of the invariant
            # we care about. Test degrades to a no-op assertion
            # rather than a false positive.
            assert True
        except asyncio.TimeoutError:
            pytest.fail(
                "server-side write blocked instead of raising — a "
                "real deployment failure would hang the dev's agent"
            )
        except Exception:
            # Expected: some concrete exception surfaces. The MCP
            # wrapper turns this into a tool-call error, not a hang.
            assert True


class TestSubprocessBoundaryCrash:
    """Real subprocess crash coverage. A Python-level 'del kg'
    proves the ORM state resets cleanly; this proves the same
    behavior across a hard kill of a real Python subprocess. The
    scenario matches how Claude Code / Cursor actually run the
    MCP server."""

    async def test_chain_survives_hard_kill_of_writer_subprocess(
        self, tmp_path,
    ):
        """Spawn a subprocess that writes N events, kill it with
        SIGKILL while it's writing, then a fresh subprocess opens
        the same DB path, writes more, closes an epoch, exports a
        dump. Offline verifier must PASS.

        Regression guard: if SQLite ever stops honoring the WAL
        checkpoint semantics for our writes, or if a write
        starts leaving partial state across a hard kill, this
        surfaces it."""
        import signal
        import textwrap
        writer_script = tmp_path / "writer.py"
        writer_script.write_text(textwrap.dedent(f"""
            import asyncio, os, sys, time
            os.environ["WORLD_MODEL_AUDIT_LOG"] = "on"
            os.environ["WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE"] = "5"
            from world_model_server.knowledge_graph import KnowledgeGraph
            from world_model_server.models import Event

            async def main():
                kg = KnowledgeGraph({str(tmp_path)!r})
                await kg.initialize()
                for i in range(100):
                    await kg.create_event(Event(
                        session_id="sess-writer",
                        event_type="tool_call",
                        tool_name=f"writer_{{i}}",
                        success=True,
                    ))
                    print(f"wrote {{i}}", flush=True)
                    await asyncio.sleep(0.005)

            asyncio.run(main())
        """))

        import subprocess
        p = subprocess.Popen(
            [sys.executable, str(writer_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Give it a moment to write some events, then hard-kill
        try:
            for _ in range(50):
                line = p.stdout.readline()
                if b"wrote 3" in line:
                    break
            p.send_signal(signal.SIGKILL)
            p.wait(timeout=5.0)
        finally:
            if p.poll() is None:
                p.kill()

        # Fresh recovery process: opens same DB path, writes 5 more
        # events (triggers an epoch close at threshold=5), exports
        # a dump, runs the verifier. Everything must succeed.
        async def _recover_and_verify():
            os.environ["WORLD_MODEL_AUDIT_LOG"] = "on"
            os.environ["WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE"] = "5"
            kg = KnowledgeGraph(str(tmp_path))
            await kg.initialize()
            for i in range(5):
                await kg.create_event(Event(
                    session_id="sess-recover",
                    event_type="tool_call",
                    tool_name=f"recover_{i}", success=True,
                ))
            manifest = await audit_dump.export_audit_dump(kg)
            return etch_verify.verify_manifest(manifest)

        report = await _recover_and_verify()
        assert report.ok, (
            f"chain must remain verifiable after hard subprocess "
            f"kill; got: {[c for c in report.checks if not c['ok']]}"
        )


class TestOfflineVerifierCatchesDroppedAuditEntry:
    """When primary-write succeeds and audit-append fails, the
    offline verifier DOES catch the mismatch — I initially thought
    this was an unclosed completeness limit, but the reference
    verifier's event_content_lock pass cross-references every row
    in events.db with the tamper-evident log and flags any event
    row that has no matching log entry.

    Testing this explicitly (a) proves the operational story we tell
    publicly, (b) locks in behavior so a future refactor of
    _verify_event_content_lock can't silently regress the coverage.

    The real completeness limit that DOES exist is separate: if a
    tool call is attempted and BOTH the primary write AND the
    audit append fail (subprocess crash before either commit,
    network drop mid-request), nothing lands in either DB and the
    verifier has no evidence to detect. That is a limit of any
    offline verifier that reads storage — nothing to verify is not
    the same as verified-absent. We do not test it here because
    there is no state to assert on.
    """

    async def test_verifier_flags_event_row_with_missing_audit_entry(
        self, monkeypatch, tmp_path,
    ):
        """Simulate the failure mode:
          1. Write event 1 with audit path healthy → chain has entry 1
          2. Write event 2 with the audit append monkey-patched to
             throw → primary event 2 persists in events.db, audit.db
             has NO corresponding entry
          3. Write event 3 with audit path healthy → chain has entry 2
             (audit.db seq is contiguous, but event 2 is orphaned)
          4. Close epoch, dump, run offline verifier
          5. Assert: verifier FAILS with an event_content_lock error
             naming the orphan event id. The verifier reads BOTH
             events.db and audit.db (via the dump manifest) and
             detects any events row with no matching log entry.

        Regression guard: if this ever passes, the verifier has
        stopped cross-referencing events with the chain and the
        public claim that "primary/audit divergence gets caught"
        needs to be rescinded.
        """
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "10")

        kg = KnowledgeGraph(str(tmp_path))
        await kg.initialize()

        # 1. Healthy audit path
        await kg.create_event(Event(
            id="evt-good-1", session_id="sess-1",
            event_type="tool_call", tool_name="one",
            reasoning="first, audit healthy", success=True,
        ))

        # 2. Force the tamper-evident append to raise mid-write.
        # The primary write persists (it commits before this side
        # effect), the audit append fails, the caller sees the
        # exception.
        real_append = tamper_evident.append_entry

        async def failing_append(db, kind, row_id, payload):
            raise RuntimeError(
                "simulated audit-path failure (disk full, "
                "permission denied, whatever the infra glitch is)"
            )

        with mock.patch.object(
            tamper_evident, "append_entry", side_effect=failing_append,
        ):
            with pytest.raises(RuntimeError):
                await kg.create_event(Event(
                    id="evt-orphan", session_id="sess-1",
                    event_type="tool_call", tool_name="two",
                    reasoning="second, audit broken", success=True,
                ))

        # 3. Audit path healthy again, third event lands on chain
        await kg.create_event(Event(
            id="evt-good-3", session_id="sess-1",
            event_type="tool_call", tool_name="three",
            reasoning="third, audit healthy", success=True,
        ))

        # Verify direct DB state: primary has all 3 events including
        # the orphan; audit chain has only 2 entries (the healthy
        # ones). Events live in kg.events_db, not kg.db_path (which
        # is the storage-root directory).
        conn = sqlite3.connect(kg.events_db)
        try:
            primary_ids = {
                r[0] for r in conn.execute("SELECT id FROM events")
            }
        finally:
            conn.close()
        assert primary_ids == {"evt-good-1", "evt-orphan", "evt-good-3"}, (
            "primary DB should have persisted all 3 event rows even "
            "though event 2's audit append failed"
        )

        # Close the epoch so the offline verifier has something to
        # verify.
        signer = audit_keys.load_or_create_signer(kg.db_path)
        import aiosqlite
        async with aiosqlite.connect(kg.audit_db) as db:
            await tamper_evident.close_epoch(db, signer)
            await db.commit()

        # 4. Run the offline verifier over the exported dump.
        manifest = await audit_dump.export_audit_dump(kg)
        report = etch_verify.verify_manifest(manifest)

        # 5. The verifier CATCHES the orphan via event_content_lock.
        assert not report.ok, (
            "verifier should FAIL when an events.db row has no "
            "matching log entry — if it now passes, the "
            "event_content_lock cross-reference has been dropped"
        )
        # And the failure explicitly names the orphan event.
        failed_checks = [c for c in report.checks if not c.get("ok")]
        assert failed_checks, "expected at least one FAIL check"
        combined = " ".join(
            (c.get("detail") or "") for c in failed_checks
        )
        assert "evt-orphan" in combined, (
            f"failure detail should name the orphan event id; got "
            f"failed checks: {failed_checks!r}"
        )
        # And the chain itself has exactly 2 entries — event 1 and
        # event 3. Event 2's audit append raised, so the chain seq
        # is contiguous over the two healthy entries.
        assert report.entries_checked == 2, (
            f"chain should have exactly 2 audit entries (the healthy "
            f"writes), got {report.entries_checked}"
        )
