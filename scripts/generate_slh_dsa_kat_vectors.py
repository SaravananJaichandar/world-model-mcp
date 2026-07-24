"""
Regenerate tests/fixtures/slh_dsa_kat_vectors.json.

The KAT test file `tests/test_fips_205_slh_dsa_kat.py` loads these
vectors and asserts they all verify. Vectors are pre-generated so
verify semantics are locked forever against the specific fixed
inputs — any liboqs update that shifts SLH-DSA verify behavior on
these deterministic (pk, msg, sig) triples fails the KAT test.

Regenerate ONLY when:
  1. The SLH-DSA mechanism name changes (e.g. new FIPS 205
     revision that renames the parameter set), OR
  2. This box's liboqs is verified to match the desired parameter
     set and previous vectors are known-broken.

Regenerating without a specific reason silently overwrites the
lock. The KAT fixture is intentionally checked into git so a
review sees the diff.

Usage:
  python scripts/generate_slh_dsa_kat_vectors.py

Runs from the world-model-mcp repo root.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path

import oqs

from world_model_server import hybrid_signer as hs


def main() -> int:
    if not hs.SLH_DSA_AVAILABLE:
        print("SLH-DSA is not available in this liboqs build; cannot generate.")
        return 1

    fixtures_dir = Path(__file__).parent.parent / "tests" / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    output = fixtures_dir / "slh_dsa_kat_vectors.json"

    signer = oqs.Signature(hs._SLH_DSA_ALG)
    pk = signer.generate_keypair()

    messages = [
        ("empty", b""),
        ("single_byte", b"a"),
        ("sequential_64B", bytes(range(64))),
    ]

    vectors = []
    for i, (label, msg) in enumerate(messages):
        sig = signer.sign(msg)
        vectors.append({
            "index": i,
            "label": label,
            "msg_hex": msg.hex(),
            "sig_hex": sig.hex(),
        })

    payload = {
        "algorithm": hs._SLH_DSA_ALG,
        "generated_on": platform.system().lower() + "-" + platform.release(),
        "note": (
            "Pre-generated vectors used by "
            "tests/test_fips_205_slh_dsa_kat.py. Any liboqs regression "
            "that breaks verify on these fails the KAT test immediately."
        ),
        "pk_hex": pk.hex(),
        "vectors": vectors,
    }
    output.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
