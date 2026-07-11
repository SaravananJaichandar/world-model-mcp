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
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

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
    Idempotent DDL to create the tamper-evident log table, the epoch
    table, all append-only triggers, and secondary indexes on the supplied
    aiosqlite connection.

    `db` is an aiosqlite connection object; typed as Any to keep this module
    dependency-free.
    """
    await db.execute(CREATE_TAMPER_EVIDENT_LOG_TABLE)
    for trigger in CREATE_APPEND_ONLY_TRIGGERS:
        await db.execute(trigger)
    for index in CREATE_INDEXES:
        await db.execute(index)
    await _create_epoch_schema(db)
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


# ---------------------------------------------------------------------------
# Epoch close (v0.13 — Merkle + hybrid signing)
# ---------------------------------------------------------------------------

# Default threshold: an epoch closes when its unclosed-entry count reaches
# this value. Operators can override via WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE.
# Small enough to keep proof paths short; large enough that closing does
# not dominate write-path latency. 1024 → 10-level Merkle tree.
_DEFAULT_EPOCH_ENTRY_COUNT = 1024


def epoch_entry_count_threshold() -> int:
    """
    Resolve the threshold: env var override if set (non-empty positive int),
    otherwise the default. Cached at read time — callers who mutate the
    env var mid-process must re-read.
    """
    raw = os.environ.get("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass  # fall through to default
    return _DEFAULT_EPOCH_ENTRY_COUNT


CREATE_EPOCHS_TABLE = """
CREATE TABLE IF NOT EXISTS tamper_evident_epochs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    merkle_root TEXT NOT NULL,
    prev_epoch_root TEXT NOT NULL,
    first_entry_seq INTEGER NOT NULL,
    last_entry_seq INTEGER NOT NULL,
    entry_count INTEGER NOT NULL,
    signature_envelope TEXT NOT NULL,
    closed_at TEXT NOT NULL
)
"""

# Append-only enforcement for epochs, same pattern as the entries table.
CREATE_EPOCH_APPEND_ONLY_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS tamper_evident_epochs_no_update
    BEFORE UPDATE ON tamper_evident_epochs
    BEGIN
        SELECT RAISE(ABORT, 'tamper_evident_epochs is append-only: UPDATE forbidden');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS tamper_evident_epochs_no_delete
    BEFORE DELETE ON tamper_evident_epochs
    BEGIN
        SELECT RAISE(ABORT, 'tamper_evident_epochs is append-only: DELETE forbidden');
    END
    """,
]

# Genesis prev_epoch_root anchors the first epoch. Distinct from the
# entry-chain GENESIS_HASH so the two chains cannot cross-verify by
# accident.
_EPOCH_GENESIS_SEED = b"world-model-mcp tamper-evident epochs v1"
EPOCH_GENESIS_ROOT: str = (
    "sha256:" + hashlib.sha256(_EPOCH_GENESIS_SEED).hexdigest()
)


async def _create_epoch_schema(db: Any) -> None:
    """Idempotent DDL for the epoch table + triggers."""
    await db.execute(CREATE_EPOCHS_TABLE)
    for trigger in CREATE_EPOCH_APPEND_ONLY_TRIGGERS:
        await db.execute(trigger)


async def _last_closed_epoch(db: Any) -> Optional[dict]:
    """
    Return the most recent closed epoch as a dict, or None if no epoch has
    ever closed.
    """
    cursor = await db.execute(
        "SELECT seq, merkle_root, prev_epoch_root, first_entry_seq, "
        "last_entry_seq, entry_count, signature_envelope, closed_at "
        "FROM tamper_evident_epochs ORDER BY seq DESC LIMIT 1"
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(
        seq=row[0],
        merkle_root=row[1],
        prev_epoch_root=row[2],
        first_entry_seq=row[3],
        last_entry_seq=row[4],
        entry_count=row[5],
        signature_envelope=row[6],
        closed_at=row[7],
    )


async def _unclosed_entry_count(db: Any) -> int:
    """
    Count entries whose seq is greater than the last epoch's
    last_entry_seq. On a fresh log, this is simply the total entry count.
    """
    last = await _last_closed_epoch(db)
    watermark = last["last_entry_seq"] if last else 0
    cursor = await db.execute(
        "SELECT COUNT(*) FROM tamper_evident_log WHERE seq > ?",
        (watermark,),
    )
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def should_close_epoch(db: Any, threshold: Optional[int] = None) -> bool:
    """
    True when the unclosed-entry count has reached the threshold. Callers
    check this after each append and close if so. Time-based epoch close
    is a v0.14 addition; v0.13 is size-based only.
    """
    t = threshold if threshold is not None else epoch_entry_count_threshold()
    return await _unclosed_entry_count(db) >= t


async def _fetch_unclosed_entry_row_hashes(db: Any) -> tuple[list[bytes], int, int]:
    """
    Return (leaf hashes ready for Merkle input, first_entry_seq,
    last_entry_seq) for all entries strictly above the last closed epoch's
    watermark. The row_hash stored in the log is `sha256:HEX`; strip the
    prefix and hex-decode to feed into the Merkle module's leaf_hash().
    """
    last = await _last_closed_epoch(db)
    watermark = last["last_entry_seq"] if last else 0
    cursor = await db.execute(
        "SELECT seq, row_hash FROM tamper_evident_log "
        "WHERE seq > ? ORDER BY seq ASC",
        (watermark,),
    )
    rows = await cursor.fetchall()
    if not rows:
        return [], 0, 0
    leaves = [bytes.fromhex(row[1].split(":", 1)[1]) for row in rows]
    return leaves, rows[0][0], rows[-1][0]


async def close_epoch(db: Any, signer: Any) -> dict:
    """
    Close the current epoch: Merkle-tree the unclosed entries, chain the
    root to the previous epoch's root, sign the resulting message with the
    hybrid signer, persist the epoch row.

    `signer` must be a HybridSigner from `hybrid_signer`. Kept as Any at
    the type level to avoid a circular import at module load — the crypto
    is only pulled in when opt-in is on.

    Returns the persisted epoch as a dict. Raises ValueError if there is
    nothing to close.

    The signed message is a canonical JSON binding:
      {"merkle_root": "...", "prev_epoch_root": "...",
       "first_entry_seq": N, "last_entry_seq": M, "entry_count": K,
       "closed_at": "..."}
    All fields are covered so tampering with any one invalidates the
    signature.
    """
    from . import merkle  # local import to keep this module import-cheap

    leaves, first_seq, last_seq = await _fetch_unclosed_entry_row_hashes(db)
    if not leaves:
        raise ValueError("no entries to close in this epoch")

    # RFC 6962 leaf hash of each stored row_hash. The row_hash was already
    # sha256 of canonical row JSON; wrapping through leaf_hash() adds the
    # 0x00 domain separator so external verifiers get a spec-conformant
    # RFC 6962 tree.
    hashed_leaves = [merkle.leaf_hash(leaf) for leaf in leaves]
    root_bytes = merkle.merkle_root(hashed_leaves)
    merkle_root_hex = "sha256:" + root_bytes.hex()

    last_epoch = await _last_closed_epoch(db)
    prev_epoch_root = (
        last_epoch["merkle_root"] if last_epoch else EPOCH_GENESIS_ROOT
    )

    entry_count = len(leaves)
    closed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Canonical JSON of the signed payload. Covers every field so field
    # tampering breaks the signature.
    payload = {
        "merkle_root": merkle_root_hex,
        "prev_epoch_root": prev_epoch_root,
        "first_entry_seq": first_seq,
        "last_entry_seq": last_seq,
        "entry_count": entry_count,
        "closed_at": closed_at,
    }
    signed_bytes = canonical_json(payload)
    envelope = signer.sign(signed_bytes)
    envelope_json = json.dumps(envelope, sort_keys=True, separators=(",", ":"))

    await db.execute(
        """
        INSERT INTO tamper_evident_epochs
        (merkle_root, prev_epoch_root, first_entry_seq, last_entry_seq,
         entry_count, signature_envelope, closed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (merkle_root_hex, prev_epoch_root, first_seq, last_seq,
         entry_count, envelope_json, closed_at),
    )

    # Fetch the new seq to return.
    cursor = await db.execute("SELECT last_insert_rowid()")
    row = await cursor.fetchone()
    epoch_seq = int(row[0]) if row else 0

    return {
        "seq": epoch_seq,
        "merkle_root": merkle_root_hex,
        "prev_epoch_root": prev_epoch_root,
        "first_entry_seq": first_seq,
        "last_entry_seq": last_seq,
        "entry_count": entry_count,
        "signature_envelope": envelope_json,
        "closed_at": closed_at,
    }


# ---------------------------------------------------------------------------
# Proof generation (v0.13 week 5 — read side)
# ---------------------------------------------------------------------------


async def _find_entry_by_row_id(db: Any, row_id: str) -> Optional[dict]:
    """
    Return the single most-recent entry for this row_id, or None. Used when
    building an inclusion proof from a caller-supplied row_id. Uses the
    existing idx_tamper_evident_row_id index.
    """
    cursor = await db.execute(
        "SELECT seq, kind, row_id, row_hash, prev_hash, entry_hash, ts "
        "FROM tamper_evident_log WHERE row_id = ? ORDER BY seq DESC LIMIT 1",
        (row_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(
        seq=row[0], kind=row[1], row_id=row[2], row_hash=row[3],
        prev_hash=row[4], entry_hash=row[5], ts=row[6],
    )


async def _epoch_containing_seq(db: Any, entry_seq: int) -> Optional[dict]:
    """
    Return the closed epoch that contains `entry_seq`, or None if the entry
    is not yet sealed in any closed epoch.
    """
    cursor = await db.execute(
        "SELECT seq, merkle_root, prev_epoch_root, first_entry_seq, "
        "last_entry_seq, entry_count, signature_envelope, closed_at "
        "FROM tamper_evident_epochs "
        "WHERE first_entry_seq <= ? AND last_entry_seq >= ? "
        "LIMIT 1",
        (entry_seq, entry_seq),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(
        seq=row[0], merkle_root=row[1], prev_epoch_root=row[2],
        first_entry_seq=row[3], last_entry_seq=row[4], entry_count=row[5],
        signature_envelope=row[6], closed_at=row[7],
    )


async def _fetch_epoch_row_hashes(
    db: Any, first_seq: int, last_seq: int
) -> list[bytes]:
    """
    Return the raw row_hash bytes (post-`sha256:` prefix strip) for all
    entries in [first_seq, last_seq] inclusive, in seq order. These feed
    directly into merkle.leaf_hash() to reconstruct the epoch's tree.
    """
    cursor = await db.execute(
        "SELECT row_hash FROM tamper_evident_log "
        "WHERE seq BETWEEN ? AND ? ORDER BY seq ASC",
        (first_seq, last_seq),
    )
    rows = await cursor.fetchall()
    return [bytes.fromhex(row[0].split(":", 1)[1]) for row in rows]


async def _fetch_all_epochs(db: Any) -> list[dict]:
    """Return all closed epochs in seq order."""
    cursor = await db.execute(
        "SELECT seq, merkle_root, prev_epoch_root, first_entry_seq, "
        "last_entry_seq, entry_count, signature_envelope, closed_at "
        "FROM tamper_evident_epochs ORDER BY seq ASC"
    )
    rows = await cursor.fetchall()
    return [
        dict(
            seq=r[0], merkle_root=r[1], prev_epoch_root=r[2],
            first_entry_seq=r[3], last_entry_seq=r[4], entry_count=r[5],
            signature_envelope=r[6], closed_at=r[7],
        )
        for r in rows
    ]


async def get_inclusion_proof(db: Any, row_id: str) -> dict:
    """
    Build a full inclusion-proof bundle for a persisted row_id.

    Returns everything an external verifier needs to independently confirm:
      1. The entry with this row_id was included in a signed epoch, and
      2. The epoch chain from genesis up to that epoch is intact.

    Raises ValueError if the entry does not exist OR is not yet sealed in
    any closed epoch. The second case is transient — the auditor should
    retry after the next epoch closes.
    """
    from . import merkle

    entry = await _find_entry_by_row_id(db, row_id)
    if entry is None:
        raise ValueError(f"row_id not found in tamper-evident log: {row_id!r}")

    epoch = await _epoch_containing_seq(db, entry["seq"])
    if epoch is None:
        raise ValueError(
            f"entry seq={entry['seq']} is not yet sealed in a closed epoch — "
            "retry after the next epoch close"
        )

    # Rebuild the epoch's Merkle tree to derive the inclusion proof.
    row_hashes = await _fetch_epoch_row_hashes(
        db, epoch["first_entry_seq"], epoch["last_entry_seq"]
    )
    hashed_leaves = [merkle.leaf_hash(rh) for rh in row_hashes]
    leaf_index = entry["seq"] - epoch["first_entry_seq"]
    proof = merkle.inclusion_proof(leaf_index, hashed_leaves)

    # All closed epochs up to and including the containing one — the
    # auditor walks this to verify prev_epoch_root chaining.
    all_epochs = await _fetch_all_epochs(db)
    chain = [e for e in all_epochs if e["seq"] <= epoch["seq"]]

    # Parse signature envelopes so the caller does not double-parse.
    envelope = json.loads(epoch["signature_envelope"])
    for e in chain:
        if isinstance(e["signature_envelope"], str):
            e["signature_envelope"] = json.loads(e["signature_envelope"])

    return {
        "row_id": row_id,
        "entry_seq": entry["seq"],
        "entry_kind": entry["kind"],
        "row_hash": entry["row_hash"],
        "prev_hash": entry["prev_hash"],
        "entry_hash": entry["entry_hash"],
        "entry_ts": entry["ts"],
        "epoch": {
            "seq": epoch["seq"],
            "merkle_root": epoch["merkle_root"],
            "prev_epoch_root": epoch["prev_epoch_root"],
            "first_entry_seq": epoch["first_entry_seq"],
            "last_entry_seq": epoch["last_entry_seq"],
            "entry_count": epoch["entry_count"],
            "signature_envelope": envelope,
            "closed_at": epoch["closed_at"],
        },
        "inclusion": {
            "leaf_index": leaf_index,
            "tree_size": len(hashed_leaves),
            "proof": [h.hex() for h in proof],
        },
        "epoch_chain": chain,
    }


async def get_audit_log_head(db: Any) -> dict:
    """
    Return the current audit-log head + full closed-epoch chain, without a
    specific entry to prove. Auditors use this for periodic chain-integrity
    checks: verify every signature, verify every prev_epoch_root link,
    confirm the head is still advancing.
    """
    cursor = await db.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM tamper_evident_log"
    )
    row = await cursor.fetchone()
    head_entry_seq = int(row[0]) if row else 0

    last_epoch = await _last_closed_epoch(db)
    all_epochs = await _fetch_all_epochs(db)
    for e in all_epochs:
        if isinstance(e["signature_envelope"], str):
            e["signature_envelope"] = json.loads(e["signature_envelope"])

    unclosed = await _unclosed_entry_count(db)

    return {
        "head_entry_seq": head_entry_seq,
        "head_epoch_seq": last_epoch["seq"] if last_epoch else 0,
        "unclosed_entry_count": unclosed,
        "epoch_chain": all_epochs,
        "genesis_entry_hash": GENESIS_HASH,
        "genesis_epoch_root": EPOCH_GENESIS_ROOT,
    }


def verify_inclusion_bundle(
    bundle: dict,
    ed25519_public_key: bytes,
    slh_dsa_public_key: bytes,
) -> tuple[bool, Optional[str]]:
    """
    Read-side verifier for the bundle produced by `get_inclusion_proof`.

    Checks, in order:
      1. Every epoch in the chain has a signature envelope that verifies
         under the supplied hybrid public keys against the canonical
         payload.
      2. Each `prev_epoch_root` matches the previous epoch's `merkle_root`
         (or `EPOCH_GENESIS_ROOT` for the first).
      3. The containing epoch's Merkle inclusion proof verifies for the
         entry's `row_hash` at `leaf_index` against `epoch.merkle_root`.

    Returns `(True, None)` on success, `(False, reason)` on the first
    failure.

    This is the contract the standalone TypeScript reference verifier must
    reproduce byte-for-byte.
    """
    from . import hybrid_signer as hs
    from . import merkle

    # Step 1 + 2: verify epoch signatures and prev-root chaining.
    prev_root = EPOCH_GENESIS_ROOT
    for e in bundle.get("epoch_chain", []):
        if e["prev_epoch_root"] != prev_root:
            return (
                False,
                f"epoch {e['seq']}: prev_epoch_root does not chain "
                f"(expected {prev_root}, got {e['prev_epoch_root']})",
            )
        payload = {
            "merkle_root": e["merkle_root"],
            "prev_epoch_root": e["prev_epoch_root"],
            "first_entry_seq": e["first_entry_seq"],
            "last_entry_seq": e["last_entry_seq"],
            "entry_count": e["entry_count"],
            "closed_at": e["closed_at"],
        }
        signed_bytes = canonical_json(payload)
        env = e["signature_envelope"]
        if not hs.verify_hybrid(
            envelope=env,
            message=signed_bytes,
            ed25519_public_key=ed25519_public_key,
            slh_dsa_public_key=slh_dsa_public_key,
        ):
            return False, f"epoch {e['seq']}: hybrid signature does not verify"
        prev_root = e["merkle_root"]

    # Step 3: verify entry inclusion in the containing epoch's Merkle tree.
    epoch = bundle["epoch"]
    inclusion = bundle["inclusion"]
    row_hash_hex = bundle["row_hash"].split(":", 1)[1]
    leaf = merkle.leaf_hash(bytes.fromhex(row_hash_hex))
    proof_bytes = [bytes.fromhex(h) for h in inclusion["proof"]]
    expected_root_hex = epoch["merkle_root"].split(":", 1)[1]
    expected_root = bytes.fromhex(expected_root_hex)

    if not merkle.verify_inclusion(
        leaf=leaf,
        index=inclusion["leaf_index"],
        tree_size=inclusion["tree_size"],
        proof=proof_bytes,
        expected_root=expected_root,
    ):
        return (
            False,
            f"inclusion proof failed for row_id {bundle['row_id']!r} "
            f"at index {inclusion['leaf_index']} in epoch {epoch['seq']}",
        )

    return True, None


# ---------------------------------------------------------------------------
# Entry-chain verifier — checks the hash chain of the tamper_evident_log itself
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
