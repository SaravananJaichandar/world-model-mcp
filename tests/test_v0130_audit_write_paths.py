"""
v0.13 — audit write-path wiring.

Follow-up to test_v0130_tamper_evident_log.py (schema-only PR). Covers the
integration between KnowledgeGraph's four durable write paths and the
tamper-evident log:

- create_fact                  → fact_create
- create_or_update_constraint  → constraint_create (new) / constraint_update
- create_event                 → event_create
- record_decision              → decision_create

Also verifies:
- Chain stays valid across a mix of write kinds
- Opt-in gating: no audit entries when WORLD_MODEL_AUDIT_LOG is off
- Payload contains identity + purpose-shaped fields, no volatile
  timestamps or PII-heavy free text
"""

import os
import tempfile
from datetime import datetime
from unittest import mock
from uuid import uuid4

import aiosqlite
import pytest

from world_model_server import tamper_evident
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import (
    Constraint,
    Decision,
    Event,
    Fact,
)


async def _fetch_entries(audit_db_path):
    async with aiosqlite.connect(audit_db_path) as db:
        cursor = await db.execute(
            "SELECT seq, kind, row_id, row_hash, prev_hash, entry_hash, ts "
            "FROM tamper_evident_log ORDER BY seq"
        )
        rows = await cursor.fetchall()
    return [
        dict(
            seq=r[0], kind=r[1], row_id=r[2], row_hash=r[3],
            prev_hash=r[4], entry_hash=r[5], ts=r[6],
        )
        for r in rows
    ]


@pytest.mark.asyncio
class TestWritePathWiring:
    async def _kg_with_audit_on(self, tmp):
        kg = KnowledgeGraph(tmp)
        await kg.initialize()
        return kg

    async def test_create_fact_appends_fact_create(self):
        with mock.patch.dict(os.environ, {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg = await self._kg_with_audit_on(tmp)
                fact = Fact(
                    fact_text="pytest uses fixtures",
                    evidence_type="source_code",
                    evidence_path="tests/conftest.py",
                    confidence=0.9,
                    status="canonical",
                )
                fid = await kg.create_fact(fact)
                entries = await _fetch_entries(kg.audit_db)
                assert len(entries) == 1
                assert entries[0]["kind"] == "fact_create"
                assert entries[0]["row_id"] == fid

    async def test_constraint_create_then_update(self):
        with mock.patch.dict(os.environ, {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg = await self._kg_with_audit_on(tmp)
                c = Constraint(
                    constraint_type="linting",
                    rule_name="no-console",
                    description="Do not use console.log in production code",
                    severity="warning",
                    examples=[{"correct": "logger.info", "incorrect": "console.log"}],
                )
                cid1 = await kg.create_or_update_constraint(c)
                # Same rule_name triggers UPDATE branch.
                c2 = Constraint(
                    constraint_type="linting",
                    rule_name="no-console",
                    description="Do not use console.log; use logger.info",
                    severity="warning",
                    examples=[{"correct": "logger.info", "incorrect": "console.log"}],
                )
                cid2 = await kg.create_or_update_constraint(c2)
                assert cid1 == cid2  # UPDATE reuses id

                entries = await _fetch_entries(kg.audit_db)
                assert len(entries) == 2
                assert entries[0]["kind"] == "constraint_create"
                assert entries[1]["kind"] == "constraint_update"

    async def test_create_event_appends_event_create(self):
        with mock.patch.dict(os.environ, {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg = await self._kg_with_audit_on(tmp)
                e = Event(
                    session_id="s-1",
                    event_type="file_edit",
                    tool_name="Edit",
                    success=True,
                )
                eid = await kg.create_event(e)
                entries = await _fetch_entries(kg.audit_db)
                assert len(entries) == 1
                assert entries[0]["kind"] == "event_create"
                assert entries[0]["row_id"] == eid

    async def test_record_decision_appends_decision_create(self):
        with mock.patch.dict(os.environ, {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg = await self._kg_with_audit_on(tmp)
                d = Decision(
                    session_id="s-1",
                    tool_name="Edit",
                    agent_proposal={"file": "auth.ts"},
                    human_correction={"file": "auth.ts"},
                    decision_type="correction",
                )
                did = await kg.record_decision(d)
                entries = await _fetch_entries(kg.audit_db)
                assert len(entries) == 1
                assert entries[0]["kind"] == "decision_create"
                assert entries[0]["row_id"] == did


@pytest.mark.asyncio
class TestChainAcrossWriteKinds:
    async def test_chain_stays_valid_across_mixed_writes(self):
        """Mix fact + constraint + event + decision writes; verify chain."""
        with mock.patch.dict(os.environ, {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()

                await kg.create_fact(Fact(
                    fact_text="a", evidence_type="source_code",
                    evidence_path="x.py", confidence=0.8, status="canonical",
                ))
                await kg.create_or_update_constraint(Constraint(
                    constraint_type="linting", rule_name="r1",
                    description="d1", severity="warning", examples=[],
                ))
                await kg.create_event(Event(
                    session_id="s", event_type="file_edit",
                    tool_name="Edit", success=True,
                ))
                await kg.record_decision(Decision(
                    session_id="s", tool_name="Edit",
                    agent_proposal={}, human_correction={},
                    decision_type="correction",
                ))

                entries = await _fetch_entries(kg.audit_db)
                assert len(entries) == 4
                kinds = [e["kind"] for e in entries]
                assert kinds == [
                    "fact_create", "constraint_create",
                    "event_create", "decision_create",
                ]
                ok, reason = tamper_evident.verify_chain(entries)
                assert ok, reason


@pytest.mark.asyncio
class TestOptInGating:
    async def test_no_audit_writes_when_env_var_off(self):
        # Explicit "off" — env var unset entirely.
        env = {k: v for k, v in os.environ.items() if k != "WORLD_MODEL_AUDIT_LOG"}
        with mock.patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                # Do enough writes to be sure the audit path would have
                # fired if enabled.
                await kg.create_fact(Fact(
                    fact_text="test", evidence_type="source_code",
                    evidence_path="x.py", confidence=0.5, status="canonical",
                ))
                await kg.create_or_update_constraint(Constraint(
                    constraint_type="linting", rule_name="r-off",
                    description="off", severity="info", examples=[],
                ))
                # The table itself should not exist when opt-in is off.
                async with aiosqlite.connect(kg.audit_db) as db:
                    cursor = await db.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='tamper_evident_log'"
                    )
                    assert await cursor.fetchone() is None


@pytest.mark.asyncio
class TestPayloadShape:
    async def test_fact_payload_omits_volatile_fields(self):
        with mock.patch.dict(os.environ, {"WORLD_MODEL_AUDIT_LOG": "on"}):
            with tempfile.TemporaryDirectory() as tmp:
                kg = KnowledgeGraph(tmp)
                await kg.initialize()
                fact = Fact(
                    fact_text="stable text",
                    evidence_type="source_code",
                    evidence_path="tests/x.py",
                    confidence=0.7,
                    status="canonical",
                    session_id="s-fixed",
                )
                await kg.create_fact(fact)
                entries = await _fetch_entries(kg.audit_db)
                # Recompute row_hash from the same shape the wiring emits.
                # This catches drift between the write-path payload and
                # what verifiers expect.
                expected_hash = tamper_evident.row_hash({
                    "id": fact.id,
                    "fact_text": fact.fact_text,
                    "evidence_type": fact.evidence_type,
                    "evidence_path": fact.evidence_path,
                    "confidence": fact.confidence,
                    "status": fact.status,
                    "session_id": fact.session_id,
                    "content_type": fact.content_type,
                })
                assert entries[0]["row_hash"] == expected_hash
