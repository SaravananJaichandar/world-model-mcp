"""
Audit-chain dump manifest export (v0.15.x follow-up, ADR-0001 §5 e2e).

Produces a self-contained JSON manifest that an offline verifier
(`etch-verify` CLI) can consume without live DB access to independently
prove:

  1. Chain integrity — every tamper_evident_log entry's `entry_hash`
     chains to its predecessor via prev_hash, back to GENESIS_HASH.
  2. Epoch signatures — every closed epoch's hybrid signature verifies
     under the operator's on-disk Ed25519 + SLH-DSA public keys.
  3. Epoch chain — each epoch's `prev_epoch_root` matches the previous
     epoch's `merkle_root` back to EPOCH_GENESIS_ROOT.
  4. Content lock — every source row (annotation, event, ...) captured
     in the dump reconstructs to the exact `row_hash` the audit chain
     locked in. Any post-hoc mutation of a source row causes verifier
     rejection.

Manifest schema (v1):

  {
    "manifest_version": "1",
    "generated_at": "<ISO 8601 UTC>",
    "world_model_mcp_version": "<pyproject version>",
    "genesis_hash": "sha256:...",           # tamper_evident.GENESIS_HASH
    "epoch_genesis_root": "sha256:...",     # tamper_evident.EPOCH_GENESIS_ROOT
    "public_keys": {
      "ed25519": "<base64 Raw>",
      "slh_dsa": "<base64 Raw>"
    },
    "tamper_evident_log": [
      {"seq": 1, "kind": "annotation_create", "row_id": "...",
       "row_hash": "sha256:...", "prev_hash": "sha256:...",
       "entry_hash": "sha256:...", "ts": "..."},
      ...
    ],
    "epochs": [
      {"seq": 1, "merkle_root": "sha256:...",
       "prev_epoch_root": "sha256:...",
       "first_entry_seq": 1, "last_entry_seq": 5,
       "entry_count": 5,
       "signature_envelope": {...parsed dict...},
       "closed_at": "..."},
      ...
    ],
    "source_rows": {
      "annotations": [
        {"id": "...", "session_id": "...",
         "event_range_start": "...", "event_range_end": "...",
         "author": "...", "rationale": "...",
         "annotation_type": "..."},
        ...
      ],
      "events": [
        {"id": "...", "session_id": "...",
         "event_type": "...", "entity_id": null,
         "tool_name": "...", "success": true},
        ...
      ]
    }
  }

Chain-only verification (chain integrity + signatures + epoch chain)
requires ONLY the top-level keys through `epochs`. Content lock
verification additionally consumes `source_rows`.

Facts, decisions, constraints, corrections — audit-integrated kinds
not covered by v0.15.0 pin_annotation work — are not exported to
`source_rows` yet. Chain-only verification still passes over them.
Extending `source_rows` for those kinds is a follow-up to enable full
content-lock coverage across every chained write path.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from . import __version__ as _wmm_version
from . import audit_keys, tamper_evident

MANIFEST_VERSION = "1"


async def _fetch_all_log_entries(db: Any) -> list[dict]:
    """Return every tamper_evident_log row in seq order. Preserves the
    exact fields the reference verifier needs to rebuild the chain."""
    cursor = await db.execute(
        "SELECT seq, kind, row_id, row_hash, prev_hash, entry_hash, ts "
        "FROM tamper_evident_log ORDER BY seq ASC"
    )
    rows = await cursor.fetchall()
    return [
        {
            "seq": r[0],
            "kind": r[1],
            "row_id": r[2],
            "row_hash": r[3],
            "prev_hash": r[4],
            "entry_hash": r[5],
            "ts": r[6],
        }
        for r in rows
    ]


async def _fetch_all_annotations(annotations_db_path: str) -> list[dict]:
    """Every row from annotations.db verbatim. Rationale text is
    included here even though it never enters the audit log, because
    the verifier needs it to reconstruct the canonical payload and
    recompute the rationale_hash."""
    async with aiosqlite.connect(annotations_db_path) as db:
        cursor = await db.execute(
            "SELECT id, session_id, event_range_start, event_range_end, "
            "author, rationale, annotation_type FROM annotations "
            "ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "session_id": r[1],
            "event_range_start": r[2],
            "event_range_end": r[3],
            "author": r[4],
            "rationale": r[5],
            "annotation_type": r[6],
        }
        for r in rows
    ]


async def _fetch_all_events(events_db_path: str) -> list[dict]:
    """Every event row that was chained into the audit log. Fields
    match the payload shape KnowledgeGraph.create_event() passes to
    _maybe_audit_write so the verifier can reconstruct the row_hash."""
    async with aiosqlite.connect(events_db_path) as db:
        cursor = await db.execute(
            "SELECT id, session_id, event_type, entity_id, "
            "tool_name, success FROM events ORDER BY timestamp ASC"
        )
        rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "session_id": r[1],
            "event_type": r[2],
            "entity_id": r[3],
            "tool_name": r[4],
            "success": bool(r[5]),
        }
        for r in rows
    ]


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


async def export_audit_dump(kg: Any) -> dict:
    """Build a self-contained audit-chain dump manifest.

    `kg` is a KnowledgeGraph. The dump captures every log entry,
    every closed epoch, both hybrid public keys (base64 Raw), and
    every source row from the tables v0.15.0 chains into the log
    (annotations + events).

    Raises ValueError if the audit chain is disabled on `kg` — there
    is no chain to dump.
    """
    if not kg.tamper_evident_enabled:
        raise ValueError(
            "audit chain is disabled — set WORLD_MODEL_AUDIT_LOG=1 "
            "on the process that generated the DBs and re-export"
        )

    async with aiosqlite.connect(kg.audit_db) as db:
        log_entries = await _fetch_all_log_entries(db)
        epochs = await tamper_evident._fetch_all_epochs(db)

    # Parse each epoch's signature_envelope string back to a dict
    # matching the shape verify_inclusion_bundle expects.
    for e in epochs:
        if isinstance(e["signature_envelope"], str):
            e["signature_envelope"] = json.loads(e["signature_envelope"])

    signer = audit_keys.load_or_create_signer(kg.db_path)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": (
            datetime.now(UTC)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z"
        ),
        "world_model_mcp_version": _wmm_version,
        "genesis_hash": tamper_evident.GENESIS_HASH,
        "epoch_genesis_root": tamper_evident.EPOCH_GENESIS_ROOT,
        "public_keys": {
            "ed25519": _b64(signer.ed25519_public_key_bytes()),
            "slh_dsa": _b64(signer.slh_dsa_public_key_bytes()),
        },
        "tamper_evident_log": log_entries,
        "epochs": epochs,
        "source_rows": {
            "annotations": await _fetch_all_annotations(
                str(kg.annotations_db)
            ),
            "events": await _fetch_all_events(str(kg.events_db)),
        },
    }
    return manifest


async def export_audit_dump_to_file(kg: Any, out_path: str) -> str:
    """Convenience wrapper: build the dump and write it to `out_path`
    as pretty-printed JSON. Returns the path for logging.

    JSON is sorted-key and 2-space indented so two dumps of the same
    state produce byte-identical files — useful for artifact hashing
    and diffing across audits.
    """
    manifest = await export_audit_dump(kg)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True, ensure_ascii=False)
    return out_path
