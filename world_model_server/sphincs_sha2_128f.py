"""
Pure-Python verification for SPHINCS+-SHA2-128f-simple (round 3.1).

Every epoch signature this package has ever produced is SPHINCS+ round
3.1, signed through liboqs under the mechanism name
"SPHINCS+-SHA2-128f-simple". liboqs 0.16 removed that mechanism, and
its FIPS 205 SLH-DSA is a different byte format, so it rejects these
signatures. Verifying here, with only hashlib, keeps existing evidence
checkable on any machine regardless of the installed liboqs.

Follows the SPHINCS+ round 3.1 reference implementation (sign.c,
fors.c, wots.c, utils.c, hash_sha2.c, thash_sha2_simple.c) for the
parameter set n=16, h=66, d=22, a=6, k=33, w=16. Signing stays in
liboqs; this module only verifies.
"""

from __future__ import annotations

import hashlib

N = 16
FULL_HEIGHT = 66
D = 22
TREE_HEIGHT = FULL_HEIGHT // D
FORS_HEIGHT = 6
FORS_TREES = 33
WOTS_W = 16
WOTS_LEN1 = 8 * N // 4
WOTS_LEN2 = 3
WOTS_LEN = WOTS_LEN1 + WOTS_LEN2

PUBLIC_KEY_BYTES = 2 * N
SECRET_KEY_BYTES = 4 * N
SIGNATURE_BYTES = N * (1 + FORS_TREES * (FORS_HEIGHT + 1) + FULL_HEIGHT + D * WOTS_LEN)

_FORS_MSG_BYTES = (FORS_HEIGHT * FORS_TREES + 7) // 8
_TREE_BITS = TREE_HEIGHT * (D - 1)
_TREE_BYTES = (_TREE_BITS + 7) // 8
_LEAF_BYTES = (TREE_HEIGHT + 7) // 8
_DIGEST_BYTES = _FORS_MSG_BYTES + _TREE_BYTES + _LEAF_BYTES

_ADDR_WOTS = 0
_ADDR_WOTS_PK = 1
_ADDR_HASH_TREE = 2
_ADDR_FORS_TREE = 3
_ADDR_FORS_PK = 4


def _address(
    layer: int, tree: int, kind: int, keypair: int = 0, chain_or_height: int = 0, index: int = 0,
) -> bytes:
    """The 22-byte compressed SHA2 address.

    Byte 17 is the chain address (WOTS) or the tree height (trees). For
    WOTS the hash address is byte 21; for trees the 4-byte tree index
    occupies bytes 18..21, so one big-endian u32 covers both.
    """
    return (
        layer.to_bytes(1, "big")
        + tree.to_bytes(8, "big")
        + kind.to_bytes(1, "big")
        + keypair.to_bytes(4, "big")
        + b"\x00\x00\x00"
        + chain_or_height.to_bytes(1, "big")
        + index.to_bytes(4, "big")
    )


class _Hasher:
    def __init__(self, pub_seed: bytes) -> None:
        self._seeded = hashlib.sha256(pub_seed + bytes(64 - N))

    def thash(self, address: bytes, data: bytes) -> bytes:
        h = self._seeded.copy()
        h.update(address + data)
        return h.digest()[:N]


def _mgf1_sha256(seed: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    return out[:length]


def _hash_message(r: bytes, pk: bytes, message: bytes) -> tuple[bytes, int, int]:
    inner = hashlib.sha256(r + pk + message).digest()
    buf = _mgf1_sha256(r + pk[:N] + inner, _DIGEST_BYTES)
    digest = buf[:_FORS_MSG_BYTES]
    tree = int.from_bytes(buf[_FORS_MSG_BYTES:_FORS_MSG_BYTES + _TREE_BYTES], "big")
    tree &= (1 << _TREE_BITS) - 1
    leaf = int.from_bytes(buf[_FORS_MSG_BYTES + _TREE_BYTES:], "big")
    leaf &= (1 << TREE_HEIGHT) - 1
    return digest, tree, leaf


def _compute_root(
    hasher: _Hasher, leaf: bytes, leaf_idx: int, idx_offset: int, auth: bytes,
    height: int, layer: int, tree: int, kind: int, keypair: int,
) -> bytes:
    node = leaf
    for h in range(height):
        sibling = auth[h * N:(h + 1) * N]
        pair = sibling + node if (leaf_idx >> h) & 1 else node + sibling
        index = (leaf_idx >> (h + 1)) + (idx_offset >> (h + 1))
        node = hasher.thash(_address(layer, tree, kind, keypair, h + 1, index), pair)
    return node


def _fors_pk_from_sig(
    hasher: _Hasher, sig: bytes, digest: bytes, tree: int, keypair: int,
) -> bytes:
    bits = int.from_bytes(digest, "little")
    roots = b""
    step = N * (FORS_HEIGHT + 1)
    for i in range(FORS_TREES):
        index = (bits >> (i * FORS_HEIGHT)) & ((1 << FORS_HEIGHT) - 1)
        offset = i << FORS_HEIGHT
        part = sig[i * step:(i + 1) * step]
        leaf = hasher.thash(
            _address(0, tree, _ADDR_FORS_TREE, keypair, 0, index + offset), part[:N],
        )
        roots += _compute_root(
            hasher, leaf, index, offset, part[N:], FORS_HEIGHT, 0, tree, _ADDR_FORS_TREE, keypair,
        )
    return hasher.thash(_address(0, tree, _ADDR_FORS_PK, keypair), roots)


def _chain_lengths(message: bytes) -> list[int]:
    digits = []
    for byte in message:
        digits += [byte >> 4, byte & 0x0F]
    checksum = sum(WOTS_W - 1 - d for d in digits) << 4
    checksum_bytes = checksum.to_bytes(2, "big")
    digits += [checksum_bytes[0] >> 4, checksum_bytes[0] & 0x0F, checksum_bytes[1] >> 4]
    return digits


def _wots_pk_from_sig(
    hasher: _Hasher, sig: bytes, message: bytes, layer: int, tree: int, keypair: int,
) -> bytes:
    pk = b""
    for chain, start in enumerate(_chain_lengths(message)):
        node = sig[chain * N:(chain + 1) * N]
        for step in range(start, WOTS_W - 1):
            node = hasher.thash(_address(layer, tree, _ADDR_WOTS, keypair, chain, step), node)
        pk += node
    return pk


def verify(message: bytes, signature: bytes, public_key: bytes) -> bool:
    """True iff `signature` is a valid SPHINCS+-SHA2-128f-simple
    signature over `message` under `public_key`."""
    if len(signature) != SIGNATURE_BYTES or len(public_key) != PUBLIC_KEY_BYTES:
        return False
    hasher = _Hasher(public_key[:N])
    r, sig = signature[:N], signature[N:]
    digest, tree, leaf_idx = _hash_message(r, public_key, message)

    fors_bytes = FORS_TREES * (FORS_HEIGHT + 1) * N
    root = _fors_pk_from_sig(hasher, sig[:fors_bytes], digest, tree, leaf_idx)
    sig = sig[fors_bytes:]

    wots_bytes = WOTS_LEN * N
    layer_bytes = wots_bytes + TREE_HEIGHT * N
    for layer in range(D):
        part = sig[layer * layer_bytes:(layer + 1) * layer_bytes]
        wots_pk = _wots_pk_from_sig(hasher, part[:wots_bytes], root, layer, tree, leaf_idx)
        leaf = hasher.thash(_address(layer, tree, _ADDR_WOTS_PK, leaf_idx), wots_pk)
        root = _compute_root(
            hasher, leaf, leaf_idx, 0, part[wots_bytes:], TREE_HEIGHT, layer, tree,
            _ADDR_HASH_TREE, 0,
        )
        leaf_idx = tree & ((1 << TREE_HEIGHT) - 1)
        tree >>= TREE_HEIGHT
    return root == public_key[N:]
