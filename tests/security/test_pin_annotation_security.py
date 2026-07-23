"""
v0.15.0 pin_annotation (ADR-0001 §5) — security tests.

Security-lens attestations for pin_annotation. Some overlap with the
Day 3 chain tests and Day 4 verifier tests exists deliberately — the
security file names the security PROPERTY being attested, so audit
reviewers can point to a single suite rather than reconstructing
coverage from feature test files.

Properties attested here:

  - Signature validity. A pinned annotation's containing epoch has
    a hybrid signature envelope that verifies under the operator's
    on-disk hybrid public keys via the reference verifier.
  - Tamper detection (full-field sweep). Any post-hoc mutation of
    the annotations.db row breaks prove_annotation_inclusion.
    Covers rationale, author, event_range_start, event_range_end,
    annotation_type — the five fields the audit chain locks down.
  - Domain separation (leaf-level). A payload identical to a real
    annotation but with the domain field replaced by the event or
    fact leaf's shape hashes to a different value — cross-type
    replay defense holds.
  - Rationale hash collision resistance (spot check). Two distinct
    rationale strings produce distinct rationale_hash values in the
    canonical payload; the log entry for each is likewise distinct.
  - No auth bypass (OSS boundary). The MCP tool respects the
    filesystem permission model that is the OSS auth boundary per
    ADR-0001 §5: a caller with no write access to the DB path
    cannot pin an annotation. (Hosted Etch adds KMS identity on top;
    that is the hosted layer and is not exercised here.)

Signature-validity path uses hybrid_signer.verify_hybrid via
tamper_evident.verify_inclusion_bundle — the same code path a
standalone TypeScript reference verifier would need to reproduce.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import aiosqlite
import pytest

from world_model_server import audit_keys, tamper_evident
from world_model_server.config import Config
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.tools import WorldModelTools

pytestmark = pytest.mark.asyncio


async def _build(tmp_path: str) -> tuple[KnowledgeGraph, WorldModelTools]:
    kg = KnowledgeGraph(tmp_path)
    await kg.initialize()
    return kg, WorldModelTools(kg, Config(db_path=tmp_path))


async def _pin_and_close(
    kg: KnowledgeGraph,
    tools: WorldModelTools,
    **overrides: Any,
) -> str:
    args = {
        "session_id": "sess-1",
        "event_range_start": "evt-1",
        "event_range_end": "evt-2",
        "author": "alice",
        "rationale": "security lens rationale",
        "annotation_type": "human_note",
    }
    args.update(overrides)
    raw = await tools.pin_annotation(**args)
    annotation_id = json.loads(raw)["annotation_id"]
    signer = audit_keys.load_or_create_signer(kg.db_path)
    async with aiosqlite.connect(kg.audit_db) as db:
        await tamper_evident.close_epoch(db, signer)
        await db.commit()
    return annotation_id


class TestSignatureValidity:
    """The annotation's containing epoch signature verifies under the
    operator's hybrid public keys via verify_inclusion_bundle — the
    same code path a standalone reference verifier must reproduce."""

    async def test_pinned_annotation_signature_verifies(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"WORLD_MODEL_AUDIT_LOG": "on"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build(tmp)
                annotation_id = await _pin_and_close(kg, tools)

                bundle = await kg.prove_annotation_inclusion(annotation_id)
                signer = audit_keys.load_or_create_signer(kg.db_path)
                ok, reason = tamper_evident.verify_inclusion_bundle(
                    bundle,
                    ed25519_public_key=signer.ed25519_public_key_bytes(),
                    slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
                )
                assert ok, f"annotation epoch signature failed: {reason}"

    async def test_wrong_ed25519_key_fails_verification(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"WORLD_MODEL_AUDIT_LOG": "on"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build(tmp)
                annotation_id = await _pin_and_close(kg, tools)

                bundle = await kg.prove_annotation_inclusion(annotation_id)
                signer = audit_keys.load_or_create_signer(kg.db_path)
                # Swap the Ed25519 public key for a fresh unrelated one.
                # SLH-DSA half of the hybrid stays valid; verify_hybrid
                # must still refuse since it requires BOTH halves.
                from cryptography.hazmat.primitives.asymmetric import (
                    ed25519,
                )

                bad_ed = ed25519.Ed25519PrivateKey.generate().public_key()
                from cryptography.hazmat.primitives import serialization

                bad_pub_bytes = bad_ed.public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
                ok, reason = tamper_evident.verify_inclusion_bundle(
                    bundle,
                    ed25519_public_key=bad_pub_bytes,
                    slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
                )
                assert not ok
                assert reason is not None
                assert "signature" in reason.lower()


class TestTamperDetectionFullSweep:
    """Every mutable field in the annotations.db row is part of the
    canonical payload the log locks in. Any change breaks the
    row_hash recomputation → prove_annotation_inclusion refuses."""

    @pytest.mark.parametrize(
        "field,new_value",
        [
            ("rationale", "rationale rewritten after signing"),
            ("author", "attacker@example.com"),
            ("event_range_start", "evt-forged-start"),
            ("event_range_end", "evt-forged-end"),
            ("annotation_type", "override_justification"),
        ],
    )
    async def test_field_mutation_breaks_proof(
        self, field: str, new_value: str,
    ) -> None:
        with mock.patch.dict(
            "os.environ",
            {"WORLD_MODEL_AUDIT_LOG": "on"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build(tmp)
                annotation_id = await _pin_and_close(kg, tools)

                async with aiosqlite.connect(kg.annotations_db) as db:
                    await db.execute(
                        f"UPDATE annotations SET {field} = ? WHERE id = ?",
                        (new_value, annotation_id),
                    )
                    await db.commit()

                with pytest.raises(ValueError, match="does not match"):
                    await kg.prove_annotation_inclusion(annotation_id)


# The domain-separation tests below are pure functions of row_hash and
# don't need an event loop. Kept at module scope so the file-wide
# pytest.mark.asyncio doesn't try to await sync functions.
def test_annotation_domain_vs_no_domain_differ() -> None:
    """A payload missing the domain field hashes differently from one
    with it — attacker cannot replay a raw content payload as an
    annotation leaf."""
    base = {
        "id": "row-1",
        "session_id": "sess-1",
        "event_range_start": "evt-1",
        "event_range_end": "evt-2",
        "author": "alice",
        "annotation_type": "human_note",
        "rationale_hash": "sha256:abcd",
    }
    with_domain = dict(base)
    with_domain["domain"] = tamper_evident.DOMAIN_ANNOTATION
    assert tamper_evident.row_hash(base) != tamper_evident.row_hash(
        with_domain
    )


def test_annotation_domain_vs_fake_domain_differ() -> None:
    """A payload that claims a different domain string (e.g. a
    fabricated event/v1) must not hash to the same leaf as a real
    annotation. Cross-type replay defense."""
    base = {
        "id": "row-1",
        "session_id": "sess-1",
        "event_range_start": "evt-1",
        "event_range_end": "evt-2",
        "author": "alice",
        "annotation_type": "human_note",
        "rationale_hash": "sha256:abcd",
    }
    legit = dict(base)
    legit["domain"] = tamper_evident.DOMAIN_ANNOTATION
    forged = dict(base)
    forged["domain"] = "world-model-mcp/transparency-log/event/v1"
    assert tamper_evident.row_hash(legit) != tamper_evident.row_hash(
        forged
    )


class TestRationaleHashDistinctness:
    """Two distinct rationale strings produce distinct rationale_hash
    values in the canonical payload, and therefore distinct row_hash
    entries in the log. Spot check on the SHA-256 preimage-distinct
    property — not a full crypto proof."""

    async def test_two_annotations_with_different_rationales_differ(
        self,
    ) -> None:
        with mock.patch.dict(
            "os.environ",
            {"WORLD_MODEL_AUDIT_LOG": "on"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build(tmp)
                raw_a = await tools.pin_annotation(
                    session_id="sess-1",
                    event_range_start="evt-1",
                    event_range_end="evt-2",
                    author="alice",
                    rationale="rationale A",
                    annotation_type="human_note",
                )
                raw_b = await tools.pin_annotation(
                    session_id="sess-1",
                    event_range_start="evt-1",
                    event_range_end="evt-2",
                    author="alice",
                    rationale="rationale B",
                    annotation_type="human_note",
                )
                id_a = json.loads(raw_a)["annotation_id"]
                id_b = json.loads(raw_b)["annotation_id"]

                async with aiosqlite.connect(kg.audit_db) as db:
                    cursor = await db.execute(
                        "SELECT row_id, row_hash FROM tamper_evident_log "
                        "WHERE row_id IN (?, ?)",
                        (id_a, id_b),
                    )
                    rows = {r[0]: r[1] for r in await cursor.fetchall()}
                assert rows[id_a] != rows[id_b], (
                    "different rationales produced the same row_hash"
                )


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX permission model not applicable on Windows",
)
class TestNoAuthBypassFilesystemBoundary:
    """OSS auth boundary is the filesystem permission on the DB path
    (ADR-0001 §5). A caller that cannot write to annotations.db
    cannot pin an annotation. This attests that the tool does NOT
    silently swallow the permission error and confirm success."""

    async def test_unwritable_db_path_raises(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"WORLD_MODEL_AUDIT_LOG": "on"},
        ):
            with tempfile.TemporaryDirectory() as tmp:
                kg, tools = await _build(tmp)
                # Take away write access on annotations.db so aiosqlite
                # cannot open it for INSERT. The tool must raise, not
                # return a fabricated success payload.
                annotations_path = Path(kg.annotations_db)
                orig_mode = annotations_path.stat().st_mode
                annotations_path.chmod(stat.S_IRUSR)  # r--------
                try:
                    with pytest.raises(Exception):
                        await tools.pin_annotation(
                            session_id="sess-1",
                            event_range_start="evt-1",
                            event_range_end="evt-2",
                            author="alice",
                            rationale="attempt against read-only db",
                            annotation_type="human_note",
                        )
                finally:
                    annotations_path.chmod(orig_mode)
