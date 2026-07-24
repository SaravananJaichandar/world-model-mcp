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

    Memory footprint: O(chain_size). For chains where the in-memory
    manifest would exceed available RAM (verified 2026-07-24 on a
    760MB audit.db exceeding 2GB after Python dict expansion), use
    `export_audit_dump_to_file_streaming` instead — same output
    shape, O(row) memory.
    """
    manifest = await export_audit_dump(kg)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True, ensure_ascii=False)
    return out_path


# ---------------------------------------------------------------------------
# Streaming export
#
# The in-memory export materializes the full manifest as a Python dict,
# then json.dumps it. Fine for small chains, blows memory on real
# production chains — a 760MB audit.db exceeds 2GB in-memory once the
# dict expansion + JSON encoding happens on top of it. Verified on
# prod 2026-07-24: cgroup OOM killed the export subprocess consistently.
#
# The streaming variant below writes JSON directly to disk one DB row
# at a time. Memory is O(single row) instead of O(chain). Output shape
# matches the in-memory version byte-for-byte for the same chain state
# so `etch-verify` and any downstream artifact-hashing consumer treat
# either output identically.
# ---------------------------------------------------------------------------


async def _iter_log_entries(db: Any):
    """Yield tamper_evident_log rows one at a time in seq order. Same
    shape as _fetch_all_log_entries but streaming; used by the
    streaming exporter."""
    async with db.execute(
        "SELECT seq, kind, row_id, row_hash, prev_hash, entry_hash, ts "
        "FROM tamper_evident_log ORDER BY seq ASC"
    ) as cursor:
        async for r in cursor:
            yield {
                "seq": r[0],
                "kind": r[1],
                "row_id": r[2],
                "row_hash": r[3],
                "prev_hash": r[4],
                "entry_hash": r[5],
                "ts": r[6],
            }


async def _iter_epochs(db: Any):
    """Yield tamper_evident_epochs rows in seq order, with
    signature_envelope parsed from its stored JSON string to a
    dict (same shape verify_hybrid expects)."""
    async with db.execute(
        "SELECT seq, merkle_root, prev_epoch_root, first_entry_seq, "
        "last_entry_seq, entry_count, signature_envelope, closed_at "
        "FROM tamper_evident_epochs ORDER BY seq ASC"
    ) as cursor:
        async for r in cursor:
            envelope = r[6]
            if isinstance(envelope, str):
                envelope = json.loads(envelope)
            yield {
                "seq": r[0],
                "merkle_root": r[1],
                "prev_epoch_root": r[2],
                "first_entry_seq": r[3],
                "last_entry_seq": r[4],
                "entry_count": r[5],
                "signature_envelope": envelope,
                "closed_at": r[7],
            }


async def _iter_annotations(annotations_db_path: str):
    async with aiosqlite.connect(annotations_db_path) as db:
        async with db.execute(
            "SELECT id, session_id, event_range_start, event_range_end, "
            "author, rationale, annotation_type FROM annotations "
            "ORDER BY created_at ASC"
        ) as cursor:
            async for r in cursor:
                yield {
                    "id": r[0],
                    "session_id": r[1],
                    "event_range_start": r[2],
                    "event_range_end": r[3],
                    "author": r[4],
                    "rationale": r[5],
                    "annotation_type": r[6],
                }


async def _iter_events(events_db_path: str):
    async with aiosqlite.connect(events_db_path) as db:
        async with db.execute(
            "SELECT id, session_id, event_type, entity_id, "
            "tool_name, success FROM events ORDER BY timestamp ASC"
        ) as cursor:
            async for r in cursor:
                yield {
                    "id": r[0],
                    "session_id": r[1],
                    "event_type": r[2],
                    "entity_id": r[3],
                    "tool_name": r[4],
                    "success": bool(r[5]),
                }


def _render_element(row: dict, indent_depth: int) -> str:
    """Render a dict as a single element sitting inside a container
    at `indent_depth` spaces from column 0. Matches the shape
    json.dumps(indent=2, sort_keys=True) would produce if this
    element lived inside such a container."""
    rendered = json.dumps(row, indent=2, sort_keys=True, ensure_ascii=False)
    prefix = " " * indent_depth
    return prefix + rendered.replace("\n", "\n" + prefix)


async def _write_streaming_array(f, key: str, indent_depth: int,
                                 async_iterator) -> None:
    """Write `"<key>": [<elements>]` where the elements come from the
    async iterator, one at a time. Output matches json.dump's format
    exactly: empty arrays render as `[]`, non-empty as
    `[\\n  {...},\\n  {...}\\n]`."""
    prefix = " " * indent_depth
    f.write(f'{prefix}"{key}": [')
    first = True
    async for row in async_iterator:
        if not first:
            f.write(",")
        f.write("\n" + _render_element(row, indent_depth + 2))
        first = False
    if first:
        f.write("]")
    else:
        f.write(f"\n{prefix}]")


async def _write_source_rows_streaming(f, kg: Any) -> None:
    """Emit source_rows as a nested dict with annotations + events
    streamed. Keys sorted alphabetically (annotations before events)."""
    f.write('  "source_rows": {\n')
    await _write_streaming_array(
        f, "annotations", 4, _iter_annotations(str(kg.annotations_db)),
    )
    f.write(",\n")
    await _write_streaming_array(
        f, "events", 4, _iter_events(str(kg.events_db)),
    )
    f.write("\n  }")


async def export_audit_dump_to_file_streaming(
    kg: Any, out_path: str,
) -> str:
    """Streaming variant of export_audit_dump_to_file.

    Writes the manifest to disk one DB row at a time. Memory usage
    is O(single row) instead of O(chain size). Use this for
    audit chains that would OOM the in-memory export path — verified
    2026-07-24 that a 760MB audit.db from prod OOM-killed the
    in-memory path on a 2GB droplet while this streaming path runs
    in a few tens of MB.

    Output is byte-identical to `export_audit_dump_to_file` for the
    same chain state: same sort_keys=True order, same 2-space
    indent, same ensure_ascii=False, no trailing newline. Byte-parity
    is a load-bearing property because auditors hash the manifest
    file itself as an artifact of record — the two exporters must
    produce identical bytes so a chain that hashes to X via one
    exporter hashes to X via the other.

    Raises ValueError if the audit chain is disabled on `kg`, matching
    export_audit_dump.
    """
    if not kg.tamper_evident_enabled:
        raise ValueError(
            "audit chain is disabled — set WORLD_MODEL_AUDIT_LOG=1 "
            "on the process that generated the DBs and re-export"
        )

    signer = audit_keys.load_or_create_signer(kg.db_path)
    generated_at = (
        datetime.now(UTC)
        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        + "Z"
    )
    scalar_keys = {
        "epoch_genesis_root": tamper_evident.EPOCH_GENESIS_ROOT,
        "generated_at": generated_at,
        "genesis_hash": tamper_evident.GENESIS_HASH,
        "manifest_version": MANIFEST_VERSION,
        "public_keys": {
            "ed25519": _b64(signer.ed25519_public_key_bytes()),
            "slh_dsa": _b64(signer.slh_dsa_public_key_bytes()),
        },
        "world_model_mcp_version": _wmm_version,
    }
    # Sorted key list matches json.dump(sort_keys=True).
    all_keys = sorted(
        list(scalar_keys.keys())
        + ["epochs", "source_rows", "tamper_evident_log"]
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("{\n")
        async with aiosqlite.connect(kg.audit_db) as audit_db:
            for i, key in enumerate(all_keys):
                is_last = i == len(all_keys) - 1
                if key in scalar_keys:
                    value_str = json.dumps(
                        scalar_keys[key], indent=2, sort_keys=True,
                        ensure_ascii=False,
                    )
                    # Indent each subsequent line by 2 spaces so it
                    # aligns under the key at indent depth 2.
                    indented = value_str.replace("\n", "\n  ")
                    f.write(f'  "{key}": {indented}')
                elif key == "epochs":
                    await _write_streaming_array(
                        f, "epochs", 2, _iter_epochs(audit_db),
                    )
                elif key == "tamper_evident_log":
                    await _write_streaming_array(
                        f, "tamper_evident_log", 2,
                        _iter_log_entries(audit_db),
                    )
                elif key == "source_rows":
                    await _write_source_rows_streaming(f, kg)
                if not is_last:
                    f.write(",\n")
                else:
                    f.write("\n")
        f.write("}")

    return out_path
