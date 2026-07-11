"""
Tamper-evident append-only log for world-model-mcp (v0.13).

Every write into the knowledge graph — fact create, constraint update, event,
decision, correction — is appended as a hash-chained entry into an append-only
SQLite table (`tamper_evident_log`). Each entry carries the SHA-256 hash of a
canonical serialization of the underlying row and the hash of the previous
entry, so any tampering with an earlier entry invalidates the chain from that
point forward.

This module is scoped to the write-path primitives:
- canonical serialization
- SHA-256 hashing
- schema DDL (table + append-only triggers)
- chained append function

The Merkle tree + epoch signing + proof APIs land in subsequent PRs. See
`docs/AUDIT_LOG.md` (to be added) and the design memo in the maintainer's
scratchpad for the full v0.13 rollout.

Threat model (summary — full model in the design memo):
- Prevents: backdating, post-hoc rewriting, selective deletion, forged log
  entries via chain invalidation.
- Does NOT prevent: compromise of a currently-live write endpoint, selective
  non-inclusion at write time, collusion of the log operator with all
  monitors.

License: MIT. Reference verifier ships in the SDK and as a standalone repo so
independent auditors can verify without trusting the log operator.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Version tag baked into the genesis hash. If the entry schema ever needs a
# breaking migration, bump this — old chains stay verifiable under the old
# genesis, new chains anchor on a new one.
_GENESIS_SEED = b"world-model-mcp tamper-evident log v1"

GENESIS_HASH: str = "sha256:" + hashlib.sha256(_GENESIS_SEED).hexdigest()

# Recognized entry kinds. Kept permissive at the type level; callers pass any
# short string. This list is the canonical set the world-model-mcp write paths
# emit — external tools that read the log can rely on it.
ENTRY_KINDS = frozenset(
    {
        "fact_create",
        "fact_update",
        "constraint_create",
        "constraint_update",
        "event_create",
        "decision_create",
        "correction_create",
    }
)


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> bytes:
    """
    Deterministic JSON serialization suitable for hashing.

    Contract: for any two logically equal objects (same keys, same values,
    modulo dict ordering), this returns byte-identical output. That is what
    makes the row hash stable across process restarts and across Python
    versions.

    Rules:
    - Keys are sorted alphabetically at every level.
    - Separators use no whitespace (`(",", ":")`).
    - Non-ASCII characters pass through as UTF-8 rather than being escaped.
    - `datetime` values are serialized to ISO 8601 with `Z` suffix for UTC.
    - `None` becomes JSON `null`; booleans and ints/floats pass through.
    - Anything else raises TypeError — the caller must render exotic types
      into a serializable shape before hashing.
    """
    return json.dumps(
        obj,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        # Force UTC + drop microsecond variance for stable hashing.
        # Callers that need microsecond precision should encode it explicitly
        # in the row before serialization.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    if isinstance(value, set):
        return sorted(value)
    if hasattr(value, "model_dump"):  # pydantic BaseModel
        return value.model_dump()
    raise TypeError(f"tamper_evident: cannot canonicalize type {type(value)!r}")


def row_hash(row: Any) -> str:
    """
    SHA-256 of the canonical JSON serialization of `row`, prefixed `sha256:`.

    The prefix is a hedge against future migrations to a different hash
    family. Verifiers must match the prefix before decoding.
    """
    digest = hashlib.sha256(canonical_json(row)).hexdigest()
    return f"sha256:{digest}"


def chain_hash(prev_hash: str, entry_row_hash: str, kind: str, seq: int, ts: str) -> str:
    """
    Compute the entry's own hash — what the NEXT entry's `prev_hash` will
    reference. Binds all fields together so that a mutation of any single
    field invalidates the chain.
    """
    payload = {
        "prev_hash": prev_hash,
        "row_hash": entry_row_hash,
        "kind": kind,
        "seq": seq,
        "ts": ts,
    }
    return row_hash(payload)


# ---------------------------------------------------------------------------
# Schema (DDL)
# ---------------------------------------------------------------------------

CREATE_TAMPER_EVIDENT_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS tamper_evident_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    row_id TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    ts TEXT NOT NULL
)
"""

# Append-only enforcement. SQLite triggers RAISE(ABORT, ...) reject any
# UPDATE or DELETE at the storage layer, closing the "an operator with SQL
# access silently rewrites history" attack vector. The primary defense is
# still that the chain would break — this is belt-and-braces.
CREATE_APPEND_ONLY_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS tamper_evident_log_no_update
    BEFORE UPDATE ON tamper_evident_log
    BEGIN
        SELECT RAISE(ABORT, 'tamper_evident_log is append-only: UPDATE forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS tamper_evident_log_no_delete
    BEFORE DELETE ON tamper_evident_log
    BEGIN
        SELECT RAISE(ABORT, 'tamper_evident_log is append-only: DELETE forbidden');
    END
    """,
]

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tamper_evident_row_id ON tamper_evident_log(row_id)",
    "CREATE INDEX IF NOT EXISTS idx_tamper_evident_kind ON tamper_evident_log(kind)",
]


async def create_schema(db: Any) -> None:
    """
    Idempotent DDL to create the tamper-evident log table, triggers, and
    indexes on the supplied aiosqlite connection.

    `db` is an aiosqlite connection object; typed as Any to keep this module
    dependency-free.
    """
    await db.execute(CREATE_TAMPER_EVIDENT_LOG_TABLE)
    for trigger in CREATE_APPEND_ONLY_TRIGGERS:
        await db.execute(trigger)
    for index in CREATE_INDEXES:
        await db.execute(index)
    await db.commit()


# ---------------------------------------------------------------------------
# Append primitive
# ---------------------------------------------------------------------------


async def append_entry(
    db: Any,
    kind: str,
    row_id: str,
    row_payload: Any,
) -> dict:
    """
    Append one entry to the tamper-evident log.

    Fetches the previous entry's `entry_hash` (or `GENESIS_HASH` if the log
    is empty), computes the new entry's `row_hash` from the canonical
    serialization of `row_payload`, computes the new `entry_hash` binding
    all fields together, and inserts atomically.

    Returns the new entry as a dict for callers that want to log or
    downstream-consume it.

    This function does NOT wrap the primary write it accompanies in a
    transaction — the caller does that by opening the aiosqlite connection
    in a `with` block and calling `db.commit()` after both writes succeed.
    That gives us "either both writes land or neither does" without this
    module owning transaction lifecycle.
    """
    if kind not in ENTRY_KINDS:
        # Kept as a warning-shaped error rather than a hard rejection so a
        # forward-compat migration that ships a new kind doesn't require
        # coordinated deploys. Verifiers can accept any kind; producers
        # SHOULD stick to ENTRY_KINDS.
        pass  # noqa: E501 — intentional pass; see docstring

    # Fetch last entry_hash for the prev_hash chain.
    cursor = await db.execute(
        "SELECT entry_hash FROM tamper_evident_log ORDER BY seq DESC LIMIT 1"
    )
    row = await cursor.fetchone()
    prev_hash = row[0] if row is not None else GENESIS_HASH

    # Compute row + entry hashes.
    rh = row_hash(row_payload)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Determine what seq this insert will land at. sqlite AUTOINCREMENT
    # guarantees monotonic-per-table, but we need the value to compute
    # entry_hash BEFORE insert. So peek at sqlite_sequence and use +1.
    cursor = await db.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM tamper_evident_log"
    )
    max_seq_row = await cursor.fetchone()
    next_seq = (max_seq_row[0] or 0) + 1

    entry_hash = chain_hash(
        prev_hash=prev_hash,
        entry_row_hash=rh,
        kind=kind,
        seq=next_seq,
        ts=ts,
    )

    await db.execute(
        """
        INSERT INTO tamper_evident_log (seq, kind, row_id, row_hash, prev_hash, entry_hash, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (next_seq, kind, row_id, rh, prev_hash, entry_hash, ts),
    )

    return {
        "seq": next_seq,
        "kind": kind,
        "row_id": row_id,
        "row_hash": rh,
        "prev_hash": prev_hash,
        "entry_hash": entry_hash,
        "ts": ts,
    }


# ---------------------------------------------------------------------------
# Verifier — read-side chain integrity check
# ---------------------------------------------------------------------------


def verify_chain(entries: Iterable[dict]) -> tuple[bool, str | None]:
    """
    Walk an ordered sequence of log entries and verify the hash chain is
    intact. Returns `(True, None)` on success, `(False, reason)` on the
    first inconsistency.

    Callers can pull entries via `SELECT * FROM tamper_evident_log ORDER BY seq`
    and pass them to this function. External auditors can also implement this
    check from a downloaded log dump without contacting the operator.

    Reference implementation. The SDK and standalone verifier ship a
    byte-identical Python + TypeScript pair against this contract.
    """
    prev_hash = GENESIS_HASH
    expected_seq = 1
    for entry in entries:
        if entry["seq"] != expected_seq:
            return False, f"seq gap at {expected_seq} (got {entry['seq']})"
        if entry["prev_hash"] != prev_hash:
            return False, f"prev_hash mismatch at seq={entry['seq']}"
        recomputed = chain_hash(
            prev_hash=entry["prev_hash"],
            entry_row_hash=entry["row_hash"],
            kind=entry["kind"],
            seq=entry["seq"],
            ts=entry["ts"],
        )
        if recomputed != entry["entry_hash"]:
            return False, f"entry_hash mismatch at seq={entry['seq']}"
        prev_hash = entry["entry_hash"]
        expected_seq += 1
    return True, None
