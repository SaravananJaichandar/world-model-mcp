"""
Merkle tree for world-model-mcp's tamper-evident audit log (v0.13).

RFC 6962 (Certificate Transparency) compatible: SHA-256 with domain-separated
leaf and internal-node hashing. This is deliberately the same pattern CT logs
use because compliance auditors and external verifiers already know RFC 6962.

Contract:
- leaf_hash(data)     = sha256(0x00 || data)
- node_hash(l, r)     = sha256(0x01 || l || r)
- empty tree root     = sha256(b"") — the canonical empty-tree hash per RFC 6962
- inclusion proof     = sibling hashes from leaf up to root
- consistency proof   = proves tree at size N+k is an append-only extension

Determinism: this module operates only on byte arrays and produces byte
arrays. Given the same leaves, `merkle_root()` returns byte-identical output
across Python versions, platforms, and processes. That is the property that
lets a Python-side signer and a TypeScript-side verifier agree.

Zero external dependencies — pure `hashlib`. Ships in the world-model-mcp
Python package and in a matching TypeScript reference verifier in the SDK.

License: MIT. Both implementations MUST produce byte-identical roots for
shared test vectors; drift is treated as a release-blocking bug.
"""

from __future__ import annotations

import hashlib
from typing import Sequence


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Domain-separation prefixes per RFC 6962 section 2.1.
LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


# ---------------------------------------------------------------------------
# Primitive hashes
# ---------------------------------------------------------------------------


def leaf_hash(data: bytes) -> bytes:
    """
    RFC 6962 leaf hash: sha256(0x00 || data).

    `data` is the raw bytes to be committed. In the tamper-evident log
    integration, this is typically the SHA-256 hash of a canonicalized JSON
    row (see `tamper_evident.row_hash`). Passing the same shape twice
    produces byte-identical output.
    """
    return hashlib.sha256(LEAF_PREFIX + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """
    RFC 6962 internal node hash: sha256(0x01 || left || right).

    Order matters — unlike Ethereum-style sorted-pair Merkle, RFC 6962
    binds order into the hash. That is what lets consistency proofs distinguish
    a legitimate append from a rewritten history.
    """
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


def empty_root() -> bytes:
    """
    The canonical empty-tree root per RFC 6962: sha256("").
    """
    return hashlib.sha256(b"").digest()


# ---------------------------------------------------------------------------
# Tree construction
# ---------------------------------------------------------------------------


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    """
    Compute the Merkle Tree Hash of a sequence of already-hashed leaves.

    Note: the caller is responsible for wrapping raw data through
    `leaf_hash()` first if they want RFC 6962 leaf semantics. This function
    accepts pre-hashed leaves so callers building a tree from an existing
    audit log (where `row_hash` was already computed at write time) do not
    double-hash.

    Empty input returns `empty_root()`.

    RFC 6962 handles odd-length levels by promoting the unpaired node
    unchanged to the next level up (rather than duplicating it, which is
    the Ethereum-style Merkle convention). This matters for consistency
    proofs.
    """
    if not leaves:
        return empty_root()

    if len(leaves) == 1:
        return leaves[0]

    # Split at the largest power of 2 less than len(leaves). This yields
    # a full left subtree and a smaller-or-equal right subtree, per RFC 6962
    # section 2.1. Recursive; O(n log n) hashes total.
    k = _largest_power_of_2_at_most(len(leaves))
    left = merkle_root(leaves[:k])
    right = merkle_root(leaves[k:])
    return node_hash(left, right)


def _largest_power_of_2_at_most(n: int) -> int:
    """Largest 2^k such that 2^k < n. RFC 6962 section 2.1 splitting rule."""
    if n <= 1:
        return n
    k = 1
    while k * 2 < n:
        k *= 2
    return k


# ---------------------------------------------------------------------------
# Inclusion proofs
# ---------------------------------------------------------------------------


def inclusion_proof(index: int, leaves: Sequence[bytes]) -> list[bytes]:
    """
    RFC 6962 audit path (inclusion proof) for the leaf at `index`.

    Returns a list of sibling hashes from leaf level up to (but not
    including) the root. The verifier reconstructs the root by combining
    the leaf with each sibling in order, choosing left/right based on the
    index bits at each level.

    Raises ValueError if the index is out of range or the tree is empty.
    """
    n = len(leaves)
    if n == 0:
        raise ValueError("cannot build inclusion proof over empty tree")
    if index < 0 or index >= n:
        raise ValueError(f"index {index} out of range for tree of size {n}")

    proof: list[bytes] = []
    _inclusion_path(index, list(leaves), 0, n, proof)
    return proof


def _inclusion_path(
    m: int,
    leaves: list[bytes],
    lo: int,
    hi: int,
    proof: list[bytes],
) -> None:
    """
    Recursive audit-path builder. `m` is the target leaf's index within the
    ORIGINAL tree; `lo`, `hi` bound the current subtree being descended.
    """
    n = hi - lo
    if n <= 1:
        return
    k = _largest_power_of_2_at_most(n)
    if m - lo < k:
        # Target is in the left subtree — right subtree hash is a sibling.
        _inclusion_path(m, leaves, lo, lo + k, proof)
        proof.append(merkle_root(leaves[lo + k:hi]))
    else:
        # Target is in the right subtree — left subtree hash is a sibling.
        _inclusion_path(m, leaves, lo + k, hi, proof)
        proof.append(merkle_root(leaves[lo:lo + k]))


def verify_inclusion(
    leaf: bytes,
    index: int,
    tree_size: int,
    proof: Sequence[bytes],
    expected_root: bytes,
) -> bool:
    """
    Verify a leaf's inclusion in a tree of known size.

    Reconstructs the root by walking `proof` bottom-up, combining with
    `leaf` at each level per RFC 6962 section 2.1.1. Returns True if the
    reconstructed root matches `expected_root`.
    """
    if index < 0 or index >= tree_size:
        return False

    fn = leaf
    r = index
    sn = tree_size - 1  # last index of the tree

    for sibling in proof:
        if sn == 0:
            # We ran out of levels but still have proof siblings — invalid.
            return False
        if r % 2 == 1 or r == sn:
            # Node at this level is a right child, OR is on the right
            # boundary of the tree (unpaired promoted node case). Sibling
            # is on the left.
            fn = node_hash(sibling, fn)
            # After combining, shift up levels until we are no longer on
            # an even-indexed right-boundary node.
            while r % 2 == 0 and r != 0:
                r //= 2
                sn //= 2
        else:
            # Node at this level is a left child with a right sibling.
            fn = node_hash(fn, sibling)
        r //= 2
        sn //= 2

    return sn == 0 and fn == expected_root


# ---------------------------------------------------------------------------
# Consistency proofs
# ---------------------------------------------------------------------------


def consistency_proof(
    old_size: int, leaves: Sequence[bytes]
) -> list[bytes]:
    """
    RFC 6962 consistency proof between the tree at size `old_size` and the
    current tree (size `len(leaves)`).

    Proves that the current tree is an append-only extension of the older
    tree — no old entries were mutated. This is the check external monitors
    run periodically to detect an operator who has tampered with historical
    entries.

    Raises ValueError if old_size is out of range.
    """
    new_size = len(leaves)
    if old_size < 0 or old_size > new_size:
        raise ValueError(
            f"old_size {old_size} out of range for new_size {new_size}"
        )
    if old_size == 0 or old_size == new_size:
        return []

    proof: list[bytes] = []
    _consistency_path(old_size, list(leaves), 0, new_size, True, proof)
    return proof


def _consistency_path(
    m: int,
    leaves: list[bytes],
    lo: int,
    hi: int,
    is_old_boundary: bool,
    proof: list[bytes],
) -> None:
    """
    Recursive consistency-proof builder per RFC 6962 section 2.1.2.

    `m` is old_size relative to the original tree; `lo`, `hi` bound the
    current subtree; `is_old_boundary` tracks whether we are still on the
    right edge of the OLD tree (which affects when we include a node in
    the proof).
    """
    n = hi - lo
    if m == n:
        # Boundary hits a whole subtree — include its root if we are not
        # on the top-level boundary case.
        if not is_old_boundary:
            proof.append(merkle_root(leaves[lo:hi]))
        return
    k = _largest_power_of_2_at_most(n)
    if m <= k:
        _consistency_path(m, leaves, lo, lo + k, is_old_boundary, proof)
        proof.append(merkle_root(leaves[lo + k:hi]))
    else:
        _consistency_path(
            m - k, leaves, lo + k, hi, False, proof
        )
        proof.append(merkle_root(leaves[lo:lo + k]))


def verify_consistency(
    old_size: int,
    new_size: int,
    old_root: bytes,
    new_root: bytes,
    proof: Sequence[bytes],
) -> bool:
    """
    Verify a consistency proof between two tree states.

    Returns True if the proof establishes that the tree at `new_size` with
    root `new_root` is a valid append-only extension of the tree at
    `old_size` with root `old_root`.

    Follows RFC 6962 section 2.1.2 verifier algorithm.
    """
    if old_size < 0 or new_size < old_size:
        return False
    if old_size == 0:
        # Empty old tree is consistent with anything; proof must be empty.
        return len(proof) == 0
    if old_size == new_size:
        # Same tree — proof must be empty and roots must match.
        return len(proof) == 0 and old_root == new_root

    # Ported from RFC 6962 section 2.1.2.
    proof_list = list(proof)

    # If old_size is a power of 2, the first proof element is the old_root
    # itself and is omitted from the wire representation. Insert it back.
    if _is_power_of_2(old_size):
        proof_list = [old_root] + proof_list

    fn = old_size - 1
    sn = new_size - 1
    # Shift both down until fn is at an odd position (aligns to subtree
    # boundary).
    while fn % 2 == 1:
        fn //= 2
        sn //= 2

    if not proof_list:
        return False

    fr = proof_list[0]
    sr = proof_list[0]
    for c in proof_list[1:]:
        if sn == 0:
            return False
        if fn % 2 == 1 or fn == sn:
            fr = node_hash(c, fr)
            sr = node_hash(c, sr)
            while fn % 2 == 0 and fn != 0:
                fn //= 2
                sn //= 2
        else:
            sr = node_hash(sr, c)
        fn //= 2
        sn //= 2

    return sn == 0 and fr == old_root and sr == new_root


def _is_power_of_2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0
