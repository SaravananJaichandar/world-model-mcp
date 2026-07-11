"""
v0.13 — hybrid signer (Ed25519 + SLH-DSA-SHA2-128f).

Both halves are required. Verification succeeds if and only if both
signatures check out. Either failure invalidates the envelope.

Covers:
- Ed25519 and SLH-DSA each: sign/verify round-trips, tampering detection,
  domain separation, key roundtrips via raw bytes
- HybridSigner: envelope shape, hex encoding, verification requires BOTH
  halves, tampering either half fails
- Wrong version, malformed hex, mismatched public keys all rejected
- Envelope JSON round-trips
- Domain constants versioned correctly
"""

import os

import pytest

from world_model_server import hybrid_signer as hs


# ---------------------------------------------------------------------------
# Ed25519 half
# ---------------------------------------------------------------------------


class TestEd25519Signer:
    def test_sign_verify_roundtrip(self):
        signer = hs.Ed25519Signer.generate()
        msg = b"payload to sign"
        sig = signer.sign(msg)
        assert hs.verify_ed25519(signer.public_key_bytes(), msg, sig)

    def test_wrong_public_key_fails(self):
        signer = hs.Ed25519Signer.generate()
        other = hs.Ed25519Signer.generate()
        sig = signer.sign(b"payload")
        assert not hs.verify_ed25519(other.public_key_bytes(), b"payload", sig)

    def test_tampered_message_fails(self):
        signer = hs.Ed25519Signer.generate()
        sig = signer.sign(b"original")
        assert not hs.verify_ed25519(signer.public_key_bytes(), b"tampered", sig)

    def test_tampered_signature_fails(self):
        signer = hs.Ed25519Signer.generate()
        sig = bytearray(signer.sign(b"payload"))
        sig[0] ^= 0x01
        assert not hs.verify_ed25519(signer.public_key_bytes(), b"payload", bytes(sig))

    def test_domain_separation(self):
        d1 = b"context-A/v1"
        d2 = b"context-B/v1"
        signer = hs.Ed25519Signer.generate(domain=d1)
        sig = signer.sign(b"payload")
        assert hs.verify_ed25519(signer.public_key_bytes(), b"payload", sig, domain=d1)
        assert not hs.verify_ed25519(signer.public_key_bytes(), b"payload", sig, domain=d2)

    def test_private_key_roundtrip(self):
        original = hs.Ed25519Signer.generate()
        restored = hs.Ed25519Signer.from_private_bytes(original.private_key_bytes())
        sig = restored.sign(b"roundtrip")
        assert hs.verify_ed25519(original.public_key_bytes(), b"roundtrip", sig)
        assert restored.public_key_bytes() == original.public_key_bytes()


# ---------------------------------------------------------------------------
# SLH-DSA half
# ---------------------------------------------------------------------------


class TestSlhDsaSigner:
    def test_sign_verify_roundtrip(self):
        signer = hs.SlhDsaSigner.generate()
        msg = b"payload to sign"
        sig = signer.sign(msg)
        assert len(sig) == hs.SLH_DSA_SIGNATURE_BYTES
        assert hs.verify_slh_dsa(signer.public_key_bytes(), msg, sig)

    def test_wrong_public_key_fails(self):
        signer = hs.SlhDsaSigner.generate()
        other = hs.SlhDsaSigner.generate()
        sig = signer.sign(b"payload")
        assert not hs.verify_slh_dsa(other.public_key_bytes(), b"payload", sig)

    def test_tampered_message_fails(self):
        signer = hs.SlhDsaSigner.generate()
        sig = signer.sign(b"original")
        assert not hs.verify_slh_dsa(signer.public_key_bytes(), b"tampered", sig)

    def test_tampered_signature_fails(self):
        signer = hs.SlhDsaSigner.generate()
        sig = bytearray(signer.sign(b"payload"))
        # SLH-DSA signatures are 17088 bytes; flipping a byte deep in the
        # structure. Position 5000 is in the WOTS+ signature material.
        sig[5000] ^= 0x01
        assert not hs.verify_slh_dsa(signer.public_key_bytes(), b"payload", bytes(sig))

    def test_wrong_length_signature_rejected(self):
        signer = hs.SlhDsaSigner.generate()
        assert not hs.verify_slh_dsa(
            signer.public_key_bytes(), b"payload", b"short"
        )

    def test_wrong_length_public_key_rejected(self):
        signer = hs.SlhDsaSigner.generate()
        sig = signer.sign(b"payload")
        assert not hs.verify_slh_dsa(b"short", b"payload", sig)

    def test_domain_separation(self):
        d1 = b"context-A/v1"
        d2 = b"context-B/v1"
        signer = hs.SlhDsaSigner.generate(domain=d1)
        sig = signer.sign(b"payload")
        assert hs.verify_slh_dsa(signer.public_key_bytes(), b"payload", sig, domain=d1)
        assert not hs.verify_slh_dsa(signer.public_key_bytes(), b"payload", sig, domain=d2)

    def test_construction_validates_key_sizes(self):
        with pytest.raises(ValueError):
            hs.SlhDsaSigner(public_key=b"short", secret_key=b"x" * hs.SLH_DSA_SECRET_KEY_BYTES)
        with pytest.raises(ValueError):
            hs.SlhDsaSigner(
                public_key=b"x" * hs.SLH_DSA_PUBLIC_KEY_BYTES,
                secret_key=b"short",
            )


# ---------------------------------------------------------------------------
# Pubkey fingerprint
# ---------------------------------------------------------------------------


class TestPubkeyFingerprint:
    def test_deterministic(self):
        key = hs.Ed25519Signer.generate().public_key_bytes()
        assert hs.pubkey_fingerprint(key) == hs.pubkey_fingerprint(key)

    def test_prefix(self):
        key = hs.Ed25519Signer.generate().public_key_bytes()
        assert hs.pubkey_fingerprint(key).startswith("sha256:")

    def test_different_keys_different_fingerprints(self):
        k1 = hs.Ed25519Signer.generate().public_key_bytes()
        k2 = hs.Ed25519Signer.generate().public_key_bytes()
        assert hs.pubkey_fingerprint(k1) != hs.pubkey_fingerprint(k2)


# ---------------------------------------------------------------------------
# HybridSigner: both halves required
# ---------------------------------------------------------------------------


class TestHybridSigner:
    def test_envelope_shape(self):
        signer = hs.HybridSigner.generate()
        env = signer.sign(b"epoch-root-bytes")
        assert env["version"] == hs.SIGNATURE_ENVELOPE_VERSION
        # BOTH halves present as hex strings in v0.13.
        assert isinstance(env["ed25519"], str)
        assert isinstance(env["slh_dsa"], str)
        assert env["ed25519_pubkey_fingerprint"].startswith("sha256:")
        assert env["slh_dsa_pubkey_fingerprint"].startswith("sha256:")
        # Signature length sanity.
        assert len(bytes.fromhex(env["ed25519"])) == 64  # Ed25519 sig length
        assert len(bytes.fromhex(env["slh_dsa"])) == hs.SLH_DSA_SIGNATURE_BYTES

    def test_valid_envelope_verifies(self):
        signer = hs.HybridSigner.generate()
        msg = b"epoch-root-bytes"
        env = signer.sign(msg)
        assert hs.verify_hybrid(
            envelope=env,
            message=msg,
            ed25519_public_key=signer.ed25519_public_key_bytes(),
            slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
        )

    def test_tampered_ed25519_signature_fails_even_if_slh_valid(self):
        signer = hs.HybridSigner.generate()
        env = signer.sign(b"payload")
        # Corrupt only the Ed25519 half. SLH-DSA half is untouched and
        # would verify on its own. Hybrid still MUST reject.
        env["ed25519"] = "00" * 64
        assert not hs.verify_hybrid(
            envelope=env,
            message=b"payload",
            ed25519_public_key=signer.ed25519_public_key_bytes(),
            slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
        )

    def test_tampered_slh_dsa_signature_fails_even_if_ed25519_valid(self):
        signer = hs.HybridSigner.generate()
        env = signer.sign(b"payload")
        # Corrupt only the SLH-DSA half. Ed25519 half is untouched.
        # Hybrid still MUST reject. This is the whole point of hybrid.
        env["slh_dsa"] = "00" * hs.SLH_DSA_SIGNATURE_BYTES
        assert not hs.verify_hybrid(
            envelope=env,
            message=b"payload",
            ed25519_public_key=signer.ed25519_public_key_bytes(),
            slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
        )

    def test_wrong_ed25519_public_key_fails(self):
        signer = hs.HybridSigner.generate()
        other_ed = hs.Ed25519Signer.generate()
        env = signer.sign(b"payload")
        assert not hs.verify_hybrid(
            envelope=env,
            message=b"payload",
            ed25519_public_key=other_ed.public_key_bytes(),
            slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
        )

    def test_wrong_slh_dsa_public_key_fails(self):
        signer = hs.HybridSigner.generate()
        other_slh = hs.SlhDsaSigner.generate()
        env = signer.sign(b"payload")
        assert not hs.verify_hybrid(
            envelope=env,
            message=b"payload",
            ed25519_public_key=signer.ed25519_public_key_bytes(),
            slh_dsa_public_key=other_slh.public_key_bytes(),
        )

    def test_tampered_message_fails(self):
        signer = hs.HybridSigner.generate()
        env = signer.sign(b"original")
        assert not hs.verify_hybrid(
            envelope=env,
            message=b"tampered",
            ed25519_public_key=signer.ed25519_public_key_bytes(),
            slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
        )

    def test_wrong_envelope_version_rejected(self):
        signer = hs.HybridSigner.generate()
        env = signer.sign(b"payload")
        env["version"] = 999
        assert not hs.verify_hybrid(
            envelope=env,
            message=b"payload",
            ed25519_public_key=signer.ed25519_public_key_bytes(),
            slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
        )

    def test_malformed_hex_rejected(self):
        signer = hs.HybridSigner.generate()
        env = signer.sign(b"payload")
        env["ed25519"] = "not hex"
        assert not hs.verify_hybrid(
            envelope=env,
            message=b"payload",
            ed25519_public_key=signer.ed25519_public_key_bytes(),
            slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
        )

    def test_missing_signature_field_rejected(self):
        signer = hs.HybridSigner.generate()
        env = signer.sign(b"payload")
        del env["slh_dsa"]
        assert not hs.verify_hybrid(
            envelope=env,
            message=b"payload",
            ed25519_public_key=signer.ed25519_public_key_bytes(),
            slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
        )

    def test_slh_dsa_null_is_not_accepted(self):
        """
        A malicious operator MUST NOT be able to strip the SLH-DSA half
        (set slh_dsa: null) and have the envelope still verify on Ed25519
        alone. That would defeat the whole hybrid guarantee.
        """
        signer = hs.HybridSigner.generate()
        env = signer.sign(b"payload")
        env["slh_dsa"] = None
        assert not hs.verify_hybrid(
            envelope=env,
            message=b"payload",
            ed25519_public_key=signer.ed25519_public_key_bytes(),
            slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
        )

    def test_envelope_json_roundtrip(self):
        signer = hs.HybridSigner.generate()
        env = signer.sign(b"payload")
        text = hs.envelope_to_json(env)
        restored = hs.envelope_from_json(text)
        assert restored == env
        assert hs.verify_hybrid(
            envelope=restored,
            message=b"payload",
            ed25519_public_key=signer.ed25519_public_key_bytes(),
            slh_dsa_public_key=signer.slh_dsa_public_key_bytes(),
        )


class TestDomainConstants:
    def test_audit_log_domain_is_versioned(self):
        assert hs.DOMAIN_AUDIT_LOG_EPOCH_ROOT.endswith(b"/v1")
