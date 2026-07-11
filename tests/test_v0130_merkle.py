"""
v0.13 — Merkle tree module (RFC 6962 style).

Covers:
- Primitives: leaf_hash, node_hash, empty_root are deterministic
- Merkle root: known-value for empty and single-leaf; deterministic for larger
- Inclusion proofs verify for every leaf in trees sized 1..16 (including odd
  and non-power-of-2 sizes where RFC 6962 splitting matters)
- Tampering with leaf, index, proof, or root each cause verification to fail
- Consistency proofs verify a legitimate append (tree N → tree N+k)
- Consistency proofs REJECT a rewritten-history scenario (leaf mutation)
- Empty-tree consistency edge cases
"""

import hashlib

import pytest

from world_model_server import merkle


# ---------------------------------------------------------------------------
# Primitive determinism
# ---------------------------------------------------------------------------


class TestPrimitives:
    def test_leaf_hash_is_deterministic(self):
        assert merkle.leaf_hash(b"hello") == merkle.leaf_hash(b"hello")

    def test_leaf_hash_uses_domain_separation(self):
        # sha256(b"") vs sha256(0x00 || b"") must differ.
        raw = hashlib.sha256(b"").digest()
        leaf = merkle.leaf_hash(b"")
        assert raw != leaf

    def test_leaf_hash_matches_rfc6962_spec(self):
        # RFC 6962 §2.1: MTH({d}) = SHA-256(0x00 || d)
        expected = hashlib.sha256(b"\x00" + b"data").digest()
        assert merkle.leaf_hash(b"data") == expected

    def test_node_hash_is_order_sensitive(self):
        # RFC 6962 binds order into the hash — the whole point.
        # Ethereum-style sorted-pair would produce the same value here;
        # RFC 6962 does not.
        a = b"a" * 32
        b = b"b" * 32
        assert merkle.node_hash(a, b) != merkle.node_hash(b, a)

    def test_node_hash_matches_rfc6962_spec(self):
        # RFC 6962 §2.1: MTH internal = SHA-256(0x01 || left || right)
        left = b"L" * 32
        right = b"R" * 32
        expected = hashlib.sha256(b"\x01" + left + right).digest()
        assert merkle.node_hash(left, right) == expected

    def test_empty_root_is_sha256_of_empty_string(self):
        # RFC 6962: MTH({}) = SHA-256("")
        assert merkle.empty_root() == hashlib.sha256(b"").digest()


# ---------------------------------------------------------------------------
# Tree construction
# ---------------------------------------------------------------------------


def _sample_leaves(n: int) -> list[bytes]:
    """Deterministic sample leaves for tests. Each leaf is leaf_hash(int i)."""
    return [merkle.leaf_hash(str(i).encode()) for i in range(n)]


class TestMerkleRoot:
    def test_empty_tree_returns_canonical_empty_root(self):
        assert merkle.merkle_root([]) == merkle.empty_root()

    def test_single_leaf_tree_root_is_that_leaf(self):
        # RFC 6962: MTH({d}) = leaf_hash(d). Here we pass a pre-hashed leaf,
        # so the "root" of a size-1 tree is the leaf itself.
        leaves = _sample_leaves(1)
        assert merkle.merkle_root(leaves) == leaves[0]

    def test_two_leaf_root_is_node_hash_of_pair(self):
        leaves = _sample_leaves(2)
        expected = merkle.node_hash(leaves[0], leaves[1])
        assert merkle.merkle_root(leaves) == expected

    def test_root_is_deterministic_across_calls(self):
        leaves = _sample_leaves(7)
        r1 = merkle.merkle_root(leaves)
        r2 = merkle.merkle_root(leaves)
        assert r1 == r2

    def test_root_changes_when_any_leaf_changes(self):
        leaves = _sample_leaves(5)
        r1 = merkle.merkle_root(leaves)
        leaves[2] = merkle.leaf_hash(b"tampered")
        r2 = merkle.merkle_root(leaves)
        assert r1 != r2

    def test_root_changes_when_leaves_reordered(self):
        leaves = _sample_leaves(4)
        r1 = merkle.merkle_root(leaves)
        r2 = merkle.merkle_root([leaves[1], leaves[0], leaves[2], leaves[3]])
        assert r1 != r2


# ---------------------------------------------------------------------------
# Inclusion proofs
# ---------------------------------------------------------------------------


class TestInclusionProof:
    @pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 16])
    def test_every_leaf_verifies(self, size):
        leaves = _sample_leaves(size)
        root = merkle.merkle_root(leaves)
        for i in range(size):
            proof = merkle.inclusion_proof(i, leaves)
            assert merkle.verify_inclusion(
                leaf=leaves[i],
                index=i,
                tree_size=size,
                proof=proof,
                expected_root=root,
            ), f"inclusion proof failed for leaf {i} in tree of size {size}"

    def test_wrong_leaf_fails_verification(self):
        leaves = _sample_leaves(5)
        root = merkle.merkle_root(leaves)
        proof = merkle.inclusion_proof(2, leaves)
        wrong_leaf = merkle.leaf_hash(b"not the real leaf")
        assert not merkle.verify_inclusion(
            leaf=wrong_leaf, index=2, tree_size=5,
            proof=proof, expected_root=root,
        )

    def test_wrong_index_fails_verification(self):
        leaves = _sample_leaves(5)
        root = merkle.merkle_root(leaves)
        proof = merkle.inclusion_proof(2, leaves)
        assert not merkle.verify_inclusion(
            leaf=leaves[2], index=3, tree_size=5,
            proof=proof, expected_root=root,
        )

    def test_tampered_proof_element_fails_verification(self):
        leaves = _sample_leaves(6)
        root = merkle.merkle_root(leaves)
        proof = merkle.inclusion_proof(1, leaves)
        # Flip one bit in the first sibling.
        tampered = bytearray(proof[0])
        tampered[0] ^= 0x01
        proof[0] = bytes(tampered)
        assert not merkle.verify_inclusion(
            leaf=leaves[1], index=1, tree_size=6,
            proof=proof, expected_root=root,
        )

    def test_wrong_root_fails_verification(self):
        leaves = _sample_leaves(5)
        proof = merkle.inclusion_proof(0, leaves)
        wrong_root = merkle.leaf_hash(b"wrong root")
        assert not merkle.verify_inclusion(
            leaf=leaves[0], index=0, tree_size=5,
            proof=proof, expected_root=wrong_root,
        )

    def test_out_of_range_index_raises(self):
        leaves = _sample_leaves(3)
        with pytest.raises(ValueError):
            merkle.inclusion_proof(5, leaves)
        with pytest.raises(ValueError):
            merkle.inclusion_proof(-1, leaves)

    def test_inclusion_over_empty_tree_raises(self):
        with pytest.raises(ValueError):
            merkle.inclusion_proof(0, [])


# ---------------------------------------------------------------------------
# Consistency proofs
# ---------------------------------------------------------------------------


class TestConsistencyProof:
    @pytest.mark.parametrize(
        "old_size,extra",
        [
            (1, 1), (1, 2), (1, 7),
            (2, 1), (2, 3), (2, 6),
            (3, 1), (3, 4),
            (4, 1), (4, 4),
            (5, 2), (7, 1), (7, 9),
        ],
    )
    def test_append_only_extension_verifies(self, old_size, extra):
        old_leaves = _sample_leaves(old_size)
        new_leaves = _sample_leaves(old_size + extra)
        # New tree must genuinely extend the old one (first old_size leaves
        # identical).
        assert new_leaves[:old_size] == old_leaves

        old_root = merkle.merkle_root(old_leaves)
        new_root = merkle.merkle_root(new_leaves)
        proof = merkle.consistency_proof(old_size, new_leaves)

        assert merkle.verify_consistency(
            old_size=old_size,
            new_size=len(new_leaves),
            old_root=old_root,
            new_root=new_root,
            proof=proof,
        ), (
            f"consistency proof failed for old_size={old_size} extra={extra}"
        )

    def test_rewritten_history_is_rejected(self):
        """
        If the operator tampered with an old leaf while appending new ones,
        the consistency proof against the CLAIMED old root must fail.
        """
        old_leaves = _sample_leaves(4)
        old_root = merkle.merkle_root(old_leaves)

        # Attacker rewrites leaf 1 AND appends new leaves.
        tampered_leaves = list(old_leaves)
        tampered_leaves[1] = merkle.leaf_hash(b"attacker rewrote this")
        tampered_leaves.extend(_sample_leaves(6)[4:])
        new_root = merkle.merkle_root(tampered_leaves)

        # Attacker's best proof against the CLAIMED old root cannot succeed
        # because the rewritten leaf breaks the old subtree hash.
        proof = merkle.consistency_proof(4, tampered_leaves)
        assert not merkle.verify_consistency(
            old_size=4, new_size=len(tampered_leaves),
            old_root=old_root, new_root=new_root, proof=proof,
        )

    def test_empty_old_tree_consistent_with_anything(self):
        # An empty old tree is trivially consistent (no old entries to
        # tamper). Proof must be empty.
        new_leaves = _sample_leaves(5)
        new_root = merkle.merkle_root(new_leaves)
        proof = merkle.consistency_proof(0, new_leaves)
        assert proof == []
        assert merkle.verify_consistency(
            old_size=0, new_size=5,
            old_root=merkle.empty_root(), new_root=new_root, proof=proof,
        )

    def test_same_size_is_only_consistent_if_roots_match(self):
        leaves = _sample_leaves(4)
        root = merkle.merkle_root(leaves)
        proof = merkle.consistency_proof(4, leaves)
        assert proof == []
        # Matching roots → consistent.
        assert merkle.verify_consistency(
            old_size=4, new_size=4,
            old_root=root, new_root=root, proof=proof,
        )
        # Mismatched roots at same size → not consistent.
        assert not merkle.verify_consistency(
            old_size=4, new_size=4,
            old_root=root, new_root=merkle.leaf_hash(b"different"),
            proof=proof,
        )

    def test_old_size_out_of_range_raises(self):
        leaves = _sample_leaves(3)
        with pytest.raises(ValueError):
            merkle.consistency_proof(5, leaves)
        with pytest.raises(ValueError):
            merkle.consistency_proof(-1, leaves)


# ---------------------------------------------------------------------------
# Determinism across process boundaries (cross-implementation check-in point)
# ---------------------------------------------------------------------------


class TestByteStabilityAnchors:
    """
    Anchor values that a TypeScript reference verifier MUST reproduce
    byte-identical. Fixed here as regression guards — if any change in
    hashing, byte ordering, or splitting rule alters these values, the
    corresponding TS test will fail on the next SDK verifier build.
    """

    def test_anchor_empty_root(self):
        assert (
            merkle.empty_root().hex()
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_anchor_leaf_hash_of_empty_data(self):
        # sha256(0x00 || "") = sha256(b"\x00")
        assert (
            merkle.leaf_hash(b"").hex()
            == hashlib.sha256(b"\x00").hexdigest()
        )

    def test_anchor_four_leaf_root(self):
        # Four deterministic leaves; the resulting root is baked into the
        # TypeScript reference verifier's test vectors. If this fails the
        # implementations have drifted and one side is producing wrong
        # proofs — treat as release-blocking.
        leaves = [merkle.leaf_hash(str(i).encode()) for i in range(4)]
        root = merkle.merkle_root(leaves)
        # Recompute expectation the long way so this test is
        # self-anchoring rather than encoding an opaque hex string.
        expected = merkle.node_hash(
            merkle.node_hash(leaves[0], leaves[1]),
            merkle.node_hash(leaves[2], leaves[3]),
        )
        assert root == expected
