"""
Hybrid signer for the world-model-mcp tamper-evident audit log (v0.13).

Signs Merkle epoch roots with BOTH Ed25519 and SLH-DSA-SHA2-128f
(post-quantum). Verification requires BOTH signatures to check out. This
is the defense-in-depth guarantee we make to compliance-track buyers:
even if a future quantum attack breaks Ed25519, the SLH-DSA half stands.

## Algorithm choices

- **Ed25519**: FIPS 186-5 (2023) approved. Fast, small (64-byte
  signatures), decades of classical scrutiny. cryptography.hazmat
  implementation used verbatim.
- **SLH-DSA-SHA2-128f-simple**: FIPS 205 (2024) approved, round-3-finalized
  variant. Hash-based post-quantum signatures with conservative security
  assumptions (only breaks if SHA-256 breaks). Fast variant: ~17 KB
  signatures, 128-bit classical security. Via liboqs-python — the same
  PQClean C reference implementation that the `pqclean` npm package
  reads, so the TypeScript verifier cross-verifies these signatures
  byte-for-byte.

Both are FIPS-approved. Compliance-track buyers get both boxes checked.

## Serialization format

Signatures serialize into a versioned JSON envelope:

    {
      "version": 1,
      "ed25519": "hex-signature",
      "slh_dsa": "hex-signature",
      "ed25519_pubkey_fingerprint": "sha256:...",
      "slh_dsa_pubkey_fingerprint": "sha256:..."
    }

Fingerprints let verifiers pick the right public key without downloading
every operator's full key history. Full public keys resolve from the
fingerprint via a separate `public-keys.json` published by the operator
(see docs/AUDIT_LOG.md, forthcoming).

## Domain separation

Every signed message is prefixed with a domain string before signing. This
prevents cross-context signature reuse: a signature valid over an audit-log
root cannot be replayed as a signature over an unrelated blob.

Current domain: `world-model-mcp/audit-log/epoch-root/v1`

If a v2 message layout ships, bump the version suffix — old signatures
remain independently verifiable under the v1 domain.

License: MIT.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

import oqs
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

# liboqs algorithm name for the FIPS 205 SLH-DSA parameter set we sign
# with. Different liboqs versions use different names for the SAME
# underlying primitive:
#
#   liboqs >= 0.14 (post FIPS 205, Aug 2024): "SLH-DSA-SHA2-128f"
#   liboqs <  0.14                          : "SPHINCS+-SHA2-128f-simple"
#
# Existing signatures under either name verify under the other because
# the wire format is identical — only the mechanism identifier changed
# in liboqs when SPHINCS+ was standardized as SLH-DSA. We resolve at
# import time by probing which name this liboqs build enables, so the
# code runs against both older prod boxes and newer CI runners without
# per-environment forks.
_SLH_DSA_ALG_FIPS205 = "SLH-DSA-SHA2-128f"
_SLH_DSA_ALG_LEGACY = "SPHINCS+-SHA2-128f-simple"


def _resolve_slh_dsa_alg() -> Optional[str]:
    """Return the mechanism name this liboqs installation accepts, or
    None if neither the FIPS 205 name nor the legacy SPHINCS+ name
    is enabled. Returning None lets the module import successfully
    in environments without a SLH-DSA-enabled liboqs — CI runners
    whose bundled liboqs-python wheel omits the mechanism, for
    example. Any actual signing call raises SLH_DSA_UNAVAILABLE_ERR
    with the enabled-mechanisms list so the failure is unambiguous.
    """
    enabled = set(oqs.get_enabled_sig_mechanisms())
    if _SLH_DSA_ALG_FIPS205 in enabled:
        return _SLH_DSA_ALG_FIPS205
    if _SLH_DSA_ALG_LEGACY in enabled:
        return _SLH_DSA_ALG_LEGACY
    return None


_SLH_DSA_ALG: Optional[str] = _resolve_slh_dsa_alg()

# Truthy iff this liboqs build enables SLH-DSA under either name.
# External callers (tests, conftest fixtures) read this to decide
# whether to skip chain-signing coverage.
SLH_DSA_AVAILABLE: bool = _SLH_DSA_ALG is not None


def _slh_dsa_unavailable_message() -> str:
    """Human-readable diagnostic used by every SLH-DSA-touching code
    path when the mechanism isn't enabled in this liboqs build."""
    enabled = sorted(oqs.get_enabled_sig_mechanisms())
    preview = enabled[:5]
    return (
        f"SLH-DSA is not available in this liboqs build. Neither "
        f"{_SLH_DSA_ALG_FIPS205!r} nor {_SLH_DSA_ALG_LEGACY!r} is "
        f"enabled. This environment cannot sign or verify audit-chain "
        f"epochs. Install a liboqs build with SLH-DSA support "
        f"(liboqs >= 0.14 from source, or a distro package that "
        f"includes it). Enabled here: {preview}... ({len(enabled)} total)"
    )


# Cached algorithm parameter sizes. Set from liboqs at import when
# SLH-DSA IS enabled; None when it isn't. Downstream code that
# validates key/signature lengths must guard on SLH_DSA_AVAILABLE
# before dereferencing these — see _require_slh_dsa() helper.
if SLH_DSA_AVAILABLE:
    _slh_probe = oqs.Signature(_SLH_DSA_ALG)
else:
    _slh_probe = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIGNATURE_ENVELOPE_VERSION = 1

# Signed messages are prefixed with this string so a signature valid in
# this context cannot be replayed in any other. Bump `/v1` → `/v2` if a
# breaking change to the audit-log message layout ever ships.
DOMAIN_AUDIT_LOG_EPOCH_ROOT = b"world-model-mcp/audit-log/epoch-root/v1"

# SLH-DSA-SHA2-128f parameter sizes (from liboqs). Documented here so
# verifiers can validate lengths before decoding. None when the
# mechanism isn't enabled — every consumer must guard on
# SLH_DSA_AVAILABLE before dereferencing.
SLH_DSA_PUBLIC_KEY_BYTES = (
    _slh_probe.details["length_public_key"] if _slh_probe else None
)
SLH_DSA_SECRET_KEY_BYTES = (
    _slh_probe.details["length_secret_key"] if _slh_probe else None
)
SLH_DSA_SIGNATURE_BYTES = (
    _slh_probe.details["length_signature"] if _slh_probe else None
)


def _require_slh_dsa() -> None:
    """Guard called by every SLH-DSA-touching function. Raises a
    clear error at USE time so the environment problem doesn't
    show up as a subtle type error elsewhere."""
    if not SLH_DSA_AVAILABLE:
        raise RuntimeError(_slh_dsa_unavailable_message())


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def pubkey_fingerprint(public_key_bytes: bytes) -> str:
    """
    Short, stable identifier for a public key. `sha256:` prefixed for the
    same future-migration hedge used elsewhere in the tamper-evident stack.
    """
    return "sha256:" + hashlib.sha256(public_key_bytes).hexdigest()


def domain_separate(domain: bytes, message: bytes) -> bytes:
    """
    Bind a domain string to the message before signing. Prevents signature
    replay across contexts. The null-byte separator prevents length-extension
    attacks between the domain and the message.
    """
    return domain + b"\x00" + message


# ---------------------------------------------------------------------------
# Ed25519 half
# ---------------------------------------------------------------------------


class Ed25519Signer:
    """
    Domain-separated Ed25519 signer for the audit log's epoch roots.

    Not intended for direct use outside HybridSigner. If you need Ed25519
    for a different context, use `cryptography.hazmat.primitives.asymmetric.ed25519`
    directly with a domain string that names your context.
    """

    def __init__(
        self,
        private_key: ed25519.Ed25519PrivateKey,
        domain: bytes = DOMAIN_AUDIT_LOG_EPOCH_ROOT,
    ):
        self._private_key = private_key
        self._public_key = private_key.public_key()
        self._domain = domain

    @classmethod
    def generate(cls, domain: bytes = DOMAIN_AUDIT_LOG_EPOCH_ROOT) -> "Ed25519Signer":
        return cls(ed25519.Ed25519PrivateKey.generate(), domain=domain)

    @classmethod
    def from_private_bytes(
        cls,
        private_bytes: bytes,
        domain: bytes = DOMAIN_AUDIT_LOG_EPOCH_ROOT,
    ) -> "Ed25519Signer":
        key = ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)
        return cls(key, domain=domain)

    def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(domain_separate(self._domain, message))

    def public_key_bytes(self) -> bytes:
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def private_key_bytes(self) -> bytes:
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )


def verify_ed25519(
    public_key_bytes: bytes,
    message: bytes,
    signature: bytes,
    domain: bytes = DOMAIN_AUDIT_LOG_EPOCH_ROOT,
) -> bool:
    """
    Verify an Ed25519 signature over the domain-separated message. Returns
    False on any signature or key error rather than raising, so callers
    treat verification as a boolean check.
    """
    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature, domain_separate(domain, message))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# SLH-DSA half (SLH-DSA-SHA2-128f per FIPS 205)
# ---------------------------------------------------------------------------


class SlhDsaSigner:
    """
    Domain-separated SLH-DSA-SHA2-128f-simple signer.

    Uses liboqs-python (bindings to the canonical liboqs C library), which
    implements the round-3-finalized `simple` variant that PQClean ships.
    That's the same source pqclean npm reads, so the TypeScript verifier
    cross-verifies these signatures byte-for-byte.

    Signature size: 17 KB. Not observable in end-to-end latency for
    epoch-close operations, but visible in the signature envelope —
    verifiers must be prepared for larger payloads than Ed25519's 64 bytes.

    Requires liboqs installed as a system library. On macOS:
      brew install liboqs
    On Debian/Ubuntu:
      apt install liboqs-dev

    liboqs is the canonical PQC reference library, used by Cloudflare,
    AWS, and OpenSSL's PQC support. Compliance-track security teams already
    know it.
    """

    def __init__(
        self,
        public_key: bytes,
        secret_key: bytes,
        domain: bytes = DOMAIN_AUDIT_LOG_EPOCH_ROOT,
    ):
        _require_slh_dsa()
        if len(public_key) != SLH_DSA_PUBLIC_KEY_BYTES:
            raise ValueError(
                f"SLH-DSA public key must be {SLH_DSA_PUBLIC_KEY_BYTES} bytes, "
                f"got {len(public_key)}"
            )
        if len(secret_key) != SLH_DSA_SECRET_KEY_BYTES:
            raise ValueError(
                f"SLH-DSA secret key must be {SLH_DSA_SECRET_KEY_BYTES} bytes, "
                f"got {len(secret_key)}"
            )
        self._public_key = public_key
        self._secret_key = secret_key
        self._domain = domain

    @classmethod
    def generate(cls, domain: bytes = DOMAIN_AUDIT_LOG_EPOCH_ROOT) -> "SlhDsaSigner":
        _require_slh_dsa()
        # liboqs generates the keypair; we extract public + secret bytes
        # and store them for reuse via the `sign` method below.
        signer = oqs.Signature(_SLH_DSA_ALG)
        public_key = signer.generate_keypair()
        secret_key = signer.export_secret_key()
        return cls(public_key=public_key, secret_key=secret_key, domain=domain)

    def sign(self, message: bytes) -> bytes:
        _require_slh_dsa()
        # Import the secret key into a fresh oqs.Signature instance and
        # sign. liboqs binds the secret key to the signer object at import
        # time; each sign() call is a stateless operation over the bound key.
        signer = oqs.Signature(_SLH_DSA_ALG, self._secret_key)
        return signer.sign(domain_separate(self._domain, message))

    def public_key_bytes(self) -> bytes:
        return self._public_key

    def secret_key_bytes(self) -> bytes:
        return self._secret_key


def verify_slh_dsa(
    public_key_bytes: bytes,
    message: bytes,
    signature: bytes,
    domain: bytes = DOMAIN_AUDIT_LOG_EPOCH_ROOT,
) -> bool:
    """
    Verify an SLH-DSA-SHA2-128f-simple signature over the domain-separated
    message.

    Returns False on any error rather than raising. Rejects signatures of
    wrong length up front to avoid liboqs-internal errors on malformed input.
    Returns False if SLH-DSA is not available in this build — the caller
    treating this as "signature does not verify" is compliance-correct.
    """
    if not SLH_DSA_AVAILABLE:
        return False
    if len(signature) != SLH_DSA_SIGNATURE_BYTES:
        return False
    if len(public_key_bytes) != SLH_DSA_PUBLIC_KEY_BYTES:
        return False
    try:
        verifier = oqs.Signature(_SLH_DSA_ALG)
        return verifier.verify(
            domain_separate(domain, message), signature, public_key_bytes
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Hybrid signer (composition — BOTH halves required)
# ---------------------------------------------------------------------------


class HybridSigner:
    """
    Compose Ed25519 + SLH-DSA-SHA2-128f into a single signer producing a
    single envelope. Both halves are required at sign time. Both signatures
    must verify at verify time.

    This is the compliance-grade guarantee: a signed epoch root is valid if
    and only if the operator possesses BOTH private keys AND the underlying
    algorithms are unbroken. A future quantum attack that breaks Ed25519
    still leaves SLH-DSA standing. A hash-function break (SHA-256 → SLH-DSA
    security compromised) still leaves Ed25519 standing.
    """

    def __init__(
        self,
        ed25519_signer: Ed25519Signer,
        slh_dsa_signer: SlhDsaSigner,
    ):
        self._ed = ed25519_signer
        self._slh = slh_dsa_signer

    @classmethod
    def generate(cls) -> "HybridSigner":
        """Generate a fresh HybridSigner with new Ed25519 and SLH-DSA keypairs."""
        return cls(
            ed25519_signer=Ed25519Signer.generate(),
            slh_dsa_signer=SlhDsaSigner.generate(),
        )

    def sign(self, message: bytes) -> dict:
        """
        Sign `message` with both halves. Returns the envelope dict ready
        to persist as JSON.
        """
        ed_sig = self._ed.sign(message)
        slh_sig = self._slh.sign(message)
        return {
            "version": SIGNATURE_ENVELOPE_VERSION,
            "ed25519": ed_sig.hex(),
            "slh_dsa": slh_sig.hex(),
            "ed25519_pubkey_fingerprint": pubkey_fingerprint(self._ed.public_key_bytes()),
            "slh_dsa_pubkey_fingerprint": pubkey_fingerprint(self._slh.public_key_bytes()),
        }

    def ed25519_public_key_bytes(self) -> bytes:
        return self._ed.public_key_bytes()

    def ed25519_private_key_bytes(self) -> bytes:
        return self._ed.private_key_bytes()

    def slh_dsa_public_key_bytes(self) -> bytes:
        return self._slh.public_key_bytes()

    def slh_dsa_secret_key_bytes(self) -> bytes:
        return self._slh.secret_key_bytes()


def verify_hybrid(
    envelope: dict,
    message: bytes,
    ed25519_public_key: bytes,
    slh_dsa_public_key: bytes,
) -> bool:
    """
    Verify a HybridSigner envelope. Returns True if and only if BOTH:

    - `envelope["ed25519"]` is a valid Ed25519 signature over `message`
      under `ed25519_public_key`, AND
    - `envelope["slh_dsa"]` is a valid SLH-DSA-SHA2-128f signature over
      `message` under `slh_dsa_public_key`.

    Either failure invalidates the whole envelope. This is the whole point
    of the hybrid construction.
    """
    if envelope.get("version") != SIGNATURE_ENVELOPE_VERSION:
        return False

    ed_hex = envelope.get("ed25519")
    slh_hex = envelope.get("slh_dsa")
    if not isinstance(ed_hex, str) or not isinstance(slh_hex, str):
        return False

    try:
        ed_sig = bytes.fromhex(ed_hex)
        slh_sig = bytes.fromhex(slh_hex)
    except ValueError:
        return False

    if not verify_ed25519(ed25519_public_key, message, ed_sig):
        return False
    if not verify_slh_dsa(slh_dsa_public_key, message, slh_sig):
        return False
    return True


# ---------------------------------------------------------------------------
# Envelope JSON helpers
# ---------------------------------------------------------------------------


def envelope_to_json(envelope: dict) -> str:
    """Canonical JSON serialization of a signature envelope for storage."""
    return json.dumps(
        envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def envelope_from_json(text: str) -> dict:
    return json.loads(text)
