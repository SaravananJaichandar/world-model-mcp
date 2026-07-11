"""
On-disk key management for the v0.13 tamper-evident audit log.

Persists Ed25519 + SLH-DSA-SHA2-128f private keys to `<db_path>/keys/` so
epoch signing keys survive across process restarts. Private keys are
stored with mode 0600; public keys are world-readable in a `public_keys.json`
file that operators can serve to external verifiers.

The default DB path lives under `.claude/world-model/`, which is already
in the project `.gitignore`. Keys inherit that protection — a user who
opts into the audit log at the default path will not accidentally commit
their signing material.

## File layout

    <db_path>/keys/
        ed25519_private.key         mode 0600, 32 bytes raw
        slh_dsa_secret.key          mode 0600, 64 bytes raw
        public_keys.json            mode 0644, JSON with public keys + fingerprints

## public_keys.json shape

    {
        "version": 1,
        "ed25519": {
            "public_key_hex": "...",
            "fingerprint": "sha256:..."
        },
        "slh_dsa": {
            "public_key_hex": "...",
            "fingerprint": "sha256:...",
            "algorithm": "SLH-DSA-SHA2-128f"
        }
    }

External verifiers download this file, pin the fingerprints, and use the
public keys to verify epoch signatures. Rotation ships in v0.14.

License: MIT.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from . import hybrid_signer as hs


PUBLIC_KEYS_JSON_VERSION = 1

_ED25519_PRIVATE_FILENAME = "ed25519_private.key"
_SLH_DSA_SECRET_FILENAME = "slh_dsa_secret.key"
_PUBLIC_KEYS_FILENAME = "public_keys.json"

_PRIVATE_MODE = 0o600
_DIR_MODE = 0o700


def _keys_dir_for(db_path: str | os.PathLike) -> Path:
    """Return the keys directory path (does not create it)."""
    return Path(db_path) / "keys"


def _write_private_bytes(path: Path, data: bytes) -> None:
    """
    Write raw private-key bytes with mode 0600. Uses os.open with O_CREAT
    | O_WRONLY | O_TRUNC | O_EXCL is deliberately NOT used — callers may
    overwrite during rotation. Existing file mode is enforced after write.
    """
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, _PRIVATE_MODE)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    # Enforce mode even if umask stripped it during open.
    os.chmod(path, _PRIVATE_MODE)


def _write_public_keys_json(path: Path, signer: hs.HybridSigner) -> None:
    """Serialize the public keys + fingerprints for external verifiers."""
    ed_pub = signer.ed25519_public_key_bytes()
    slh_pub = signer.slh_dsa_public_key_bytes()
    payload = {
        "version": PUBLIC_KEYS_JSON_VERSION,
        "ed25519": {
            "public_key_hex": ed_pub.hex(),
            "fingerprint": hs.pubkey_fingerprint(ed_pub),
        },
        "slh_dsa": {
            "public_key_hex": slh_pub.hex(),
            "fingerprint": hs.pubkey_fingerprint(slh_pub),
            "algorithm": "SLH-DSA-SHA2-128f",
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True, separators=(",", ":"))


def load_or_create_signer(db_path: str | os.PathLike) -> hs.HybridSigner:
    """
    Load the HybridSigner from `<db_path>/keys/`, creating fresh keys on
    first call.

    On first call: generates a new HybridSigner, writes private keys with
    mode 0600, writes public_keys.json with mode 0644, returns the signer.

    On subsequent calls: reads the raw bytes, reconstructs the signer,
    returns it. Does not re-verify that public_keys.json matches — if it
    has drifted, the operator has manual intervention to do.

    Directory is created with mode 0700 if it does not exist.
    """
    keys_dir = _keys_dir_for(db_path)
    ed_priv_path = keys_dir / _ED25519_PRIVATE_FILENAME
    slh_secret_path = keys_dir / _SLH_DSA_SECRET_FILENAME
    public_keys_path = keys_dir / _PUBLIC_KEYS_FILENAME

    if ed_priv_path.exists() and slh_secret_path.exists():
        # Restore.
        with open(ed_priv_path, "rb") as f:
            ed_priv_bytes = f.read()
        with open(slh_secret_path, "rb") as f:
            slh_secret_bytes = f.read()

        ed_signer = hs.Ed25519Signer.from_private_bytes(ed_priv_bytes)
        # SLH-DSA needs the public key too; derive it from the secret.
        # pyspx's secret key contains the seed material; the public key
        # is the second half of the SK per SPHINCS+ convention. Read it
        # back from public_keys.json to avoid reimplementing the derivation.
        if public_keys_path.exists():
            with open(public_keys_path, "r", encoding="utf-8") as f:
                pk_payload = json.load(f)
            slh_pub_bytes = bytes.fromhex(pk_payload["slh_dsa"]["public_key_hex"])
        else:
            # Fall back: pyspx SPHINCS+ secret keys are 64 bytes and
            # contain the public seed + public root at bytes 32..64.
            # Documented in the SPHINCS+ reference implementation.
            slh_pub_bytes = slh_secret_bytes[32:64]
        slh_signer = hs.SlhDsaSigner(
            public_key=slh_pub_bytes, secret_key=slh_secret_bytes
        )
        return hs.HybridSigner(ed_signer, slh_signer)

    # Fresh generate + persist.
    keys_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(keys_dir, _DIR_MODE)

    signer = hs.HybridSigner.generate()
    _write_private_bytes(ed_priv_path, signer.ed25519_private_key_bytes())
    _write_private_bytes(slh_secret_path, signer.slh_dsa_secret_key_bytes())
    _write_public_keys_json(public_keys_path, signer)
    return signer


def read_public_keys(db_path: str | os.PathLike) -> Optional[dict]:
    """
    Return the parsed public_keys.json, or None if it does not exist yet
    (opt-in not enabled or first-epoch not yet closed).

    Used by verifiers and by the health check to confirm an operator has
    published their public key material.
    """
    path = _keys_dir_for(db_path) / _PUBLIC_KEYS_FILENAME
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
