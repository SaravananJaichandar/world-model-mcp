"""
Verification must not depend on the installed liboqs.

Prod signed every existing epoch with SPHINCS+-SHA2-128f-simple
(round 3.1) through liboqs 0.15. liboqs 0.16 dropped that mechanism, and
its FIPS 205 SLH-DSA rejects these signatures, so a verifier that went
through liboqs reported genuine evidence as unverifiable on a fresh
`pip install`. Verification now runs in pure Python. These tests block
liboqs entirely and check genuine prod epochs still verify.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from world_model_server import hybrid_signer as hs
from world_model_server import sphincs_sha2_128f, tamper_evident

FIXTURES = Path(__file__).parent / "fixtures"
PROD_EPOCHS = json.loads((FIXTURES / "prod_demo_epochs.json").read_text())
KAT = json.loads((FIXTURES / "slh_dsa_kat_vectors.json").read_text())

_NO_OQS = "import sys; sys.modules['oqs'] = None\n"


def _epoch_message(epoch: dict) -> bytes:
    return tamper_evident.canonical_json({
        k: epoch[k] for k in (
            "merkle_root", "prev_epoch_root", "first_entry_seq",
            "last_entry_seq", "entry_count", "closed_at",
        )
    })


def _run_without_oqs(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", _NO_OQS + code],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def test_kat_vectors_verify_in_pure_python():
    pk = bytes.fromhex(KAT["pk_hex"])
    results = [
        sphincs_sha2_128f.verify(bytes.fromhex(v["msg_hex"]), bytes.fromhex(v["sig_hex"]), pk)
        for v in KAT["vectors"]
    ]
    assert results == [True, True, True]


def test_kat_vector_rejects_a_flipped_bit():
    pk = bytes.fromhex(KAT["pk_hex"])
    v = KAT["vectors"][2]
    sig = bytearray(bytes.fromhex(v["sig_hex"]))
    sig[5000] ^= 0x01
    assert sphincs_sha2_128f.verify(bytes.fromhex(v["msg_hex"]), bytes(sig), pk) is False


def test_wrong_length_signature_or_key_is_rejected():
    import base64

    epoch = PROD_EPOCHS["epochs"][0]
    pk = base64.b64decode(PROD_EPOCHS["public_keys"]["slh_dsa"])
    msg = _epoch_message(epoch)
    sig = bytes.fromhex(epoch["signature_envelope"]["slh_dsa"])
    assert [
        hs.verify_slh_dsa(pk, msg, b"too short"),
        hs.verify_slh_dsa(pk, msg, sig + b"\x00"),
        hs.verify_slh_dsa(pk[:-1], msg, sig),
        hs.verify_slh_dsa(pk, msg, sig),
    ] == [False, False, False, True]


def test_genuine_prod_epochs_verify():
    import base64

    keys = PROD_EPOCHS["public_keys"]
    verdicts = [
        hs.verify_hybrid(
            envelope=e["signature_envelope"],
            message=_epoch_message(e),
            ed25519_public_key=base64.b64decode(keys["ed25519"]),
            slh_dsa_public_key=base64.b64decode(keys["slh_dsa"]),
        )
        for e in PROD_EPOCHS["epochs"]
    ]
    assert verdicts == [True, True]


def test_edited_prod_epoch_fails():
    import base64

    epoch = dict(PROD_EPOCHS["epochs"][0], entry_count=1023)
    keys = PROD_EPOCHS["public_keys"]
    assert hs.verify_slh_dsa(
        base64.b64decode(keys["slh_dsa"]),
        _epoch_message(epoch),
        bytes.fromhex(epoch["signature_envelope"]["slh_dsa"]),
    ) is False


def test_prod_epochs_verify_with_liboqs_blocked():
    out = _run_without_oqs(
        "import json, base64\n"
        "from world_model_server import hybrid_signer as hs, tamper_evident as te\n"
        f"d = json.load(open({str(FIXTURES / 'prod_demo_epochs.json')!r}))\n"
        "k = d['public_keys']\n"
        "fields = ('merkle_root','prev_epoch_root','first_entry_seq','last_entry_seq','entry_count','closed_at')\n"
        "print([hs.verify_hybrid(e['signature_envelope'], te.canonical_json({f: e[f] for f in fields}),"
        " base64.b64decode(k['ed25519']), base64.b64decode(k['slh_dsa'])) for e in d['epochs']])\n"
    )
    assert out == "[True, True]"


def test_importing_the_verifier_does_not_load_liboqs():
    out = _run_without_oqs(
        "import world_model_server.etch_verify, world_model_server.audit_dump\n"
        "print('loaded')\n"
    )
    assert out == "loaded"

