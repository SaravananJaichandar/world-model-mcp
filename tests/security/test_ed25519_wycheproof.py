"""
Ed25519 signature verification tests against Google Wycheproof's ed25519_test.json.

Why this exists (see also tests/security/vectors/README.md):

The audit chain's tamper-evident property depends on the Ed25519 verifier
rejecting non-canonical signatures. RFC 8032 §5.1.7 requires the scalar `S`
in an Ed25519 signature to be in the canonical range `[0, L)`. OpenSSL 1.1.1
accepted signatures with `S` outside this range as valid — a bug that
enabled signature malleability: given a valid signature, an attacker could
produce a second valid signature over the same message under the same key,
defeating non-repudiation.

The `cryptography` library (which the audit chain's `Ed25519Signer` and
`verify_ed25519` wrap) uses the underlying OpenSSL 3 primitive. That
primitive rejects the malleable class by default. These tests lock in that
behavior against the Wycheproof corpus, so a future `cryptography` upgrade
that quietly regresses to OpenSSL-1.1.1-style acceptance fails our CI
instead of silently reintroducing forgeability into our audit chain.

The corpus is vendored at pinned commit
b61843a9a5115bb758134b6a1f5d5e502d445342 (2026-07-13). See
tests/security/vectors/README.md for provenance and refresh procedure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

VECTORS_PATH = Path(__file__).parent / "vectors" / "ed25519_test.json"


def _load_corpus() -> dict:
    with VECTORS_PATH.open("rb") as fh:
        return json.load(fh)


def _iter_cases() -> list[dict]:
    """Flatten the Wycheproof groups into per-case dicts carrying the
    group's public key alongside the test-case fields."""
    corpus = _load_corpus()
    out: list[dict] = []
    for group in corpus["testGroups"]:
        assert group["type"] == "EddsaVerify", (
            f"unexpected group type {group['type']!r}; corpus may have "
            f"drifted from the pinned schema"
        )
        pubkey_hex = group["publicKey"]["pk"]
        for tc in group["tests"]:
            out.append({
                "tcId": tc["tcId"],
                "comment": tc.get("comment", ""),
                "pubkey_hex": pubkey_hex,
                "msg_hex": tc["msg"],
                "sig_hex": tc["sig"],
                "result": tc["result"],
                "flags": tc.get("flags", []),
            })
    return out


# Load once at module-import time so parametrize gets stable case ids.
_ALL_CASES: list[dict] = _iter_cases()
_VALID_CASES: list[dict] = [tc for tc in _ALL_CASES if tc["result"] == "valid"]
_INVALID_CASES: list[dict] = [tc for tc in _ALL_CASES if tc["result"] == "invalid"]
_MALLEABILITY_CASES: list[dict] = [
    tc for tc in _ALL_CASES if "SignatureMalleability" in tc["flags"]
]
_UNKNOWN_RESULT_CASES: list[dict] = [
    tc for tc in _ALL_CASES if tc["result"] not in ("valid", "invalid")
]


def _verify_raw(pubkey_hex: str, msg_hex: str, sig_hex: str) -> None:
    """Verify an Ed25519 signature via the same `cryptography` API our
    `verify_ed25519` wraps. Raises `InvalidSignature` on any rejection."""
    pubkey = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pubkey_hex))
    pubkey.verify(bytes.fromhex(sig_hex), bytes.fromhex(msg_hex))


class TestCorpusSanity:
    """The corpus must load and cover meaningful cases. Fails loudly if
    the vendored file is empty, truncated, or has been swapped for a
    schema that no longer matches our test expectations."""

    def test_corpus_loads_and_has_valid_and_invalid_cases(self) -> None:
        assert VECTORS_PATH.exists(), f"vectors file missing: {VECTORS_PATH}"
        assert _VALID_CASES, "no 'valid' cases loaded from Wycheproof corpus"
        assert _INVALID_CASES, "no 'invalid' cases loaded from Wycheproof corpus"

    def test_all_results_are_known(self) -> None:
        """If Wycheproof introduces a new result label (e.g. 'acceptable'
        in a future corpus refresh), this fails so a maintainer must
        consciously decide how to treat the new class."""
        assert not _UNKNOWN_RESULT_CASES, (
            f"corpus contains {len(_UNKNOWN_RESULT_CASES)} cases with "
            f"unrecognized result labels (not 'valid' or 'invalid'). "
            f"Refresh the corpus AND update this test to handle the new "
            f"class before merging. Example tcIds: "
            f"{[tc['tcId'] for tc in _UNKNOWN_RESULT_CASES[:5]]}"
        )

    def test_malleability_class_present(self) -> None:
        """If a corpus refresh drops the SignatureMalleability flag or
        renames it, this test fails so we know our malleability coverage
        has been lost."""
        assert _MALLEABILITY_CASES, (
            "no 'SignatureMalleability' flagged cases in corpus — the "
            "flag name may have changed upstream or the corpus was "
            "trimmed. Malleability coverage is load-bearing for the "
            "audit chain's non-forgeability. Investigate before merge."
        )


class TestEd25519WycheproofValid:
    """Every case Wycheproof marks 'valid' MUST verify. If any fail, our
    Ed25519 verifier is rejecting signatures it should accept — either
    a library regression or a wrong test-case interpretation."""

    @pytest.mark.parametrize(
        "case",
        _VALID_CASES,
        ids=[f"tc{tc['tcId']}" for tc in _VALID_CASES],
    )
    def test_valid_case_verifies(self, case: dict) -> None:
        try:
            _verify_raw(case["pubkey_hex"], case["msg_hex"], case["sig_hex"])
        except InvalidSignature as exc:  # noqa: BLE001 — test-side error surfacing
            pytest.fail(
                f"Wycheproof tcId {case['tcId']} marked 'valid' but our "
                f"Ed25519 verifier rejected it. Comment: {case['comment']!r}. "
                f"Flags: {case['flags']}. Underlying: {exc}. This is a "
                f"regression against the `cryptography` library's Ed25519 "
                f"contract; do NOT weaken this test to work around it — "
                f"root-cause the library or backend change first."
            )


class TestEd25519WycheproofInvalid:
    """Every case Wycheproof marks 'invalid' MUST be rejected. If any
    verify successfully, our audit chain is at risk of accepting forgeries."""

    @pytest.mark.parametrize(
        "case",
        _INVALID_CASES,
        ids=[f"tc{tc['tcId']}" for tc in _INVALID_CASES],
    )
    def test_invalid_case_rejected(self, case: dict) -> None:
        with pytest.raises(InvalidSignature):
            _verify_raw(case["pubkey_hex"], case["msg_hex"], case["sig_hex"])


class TestEd25519MalleabilityRejection:
    """Specifically the RFC 8032 §5.1.7 S-out-of-range class. This is the
    class OpenSSL 1.1.1 accepted (bug); modern verifiers must reject to
    preserve non-forgeability of the audit chain."""

    @pytest.mark.parametrize(
        "case",
        _MALLEABILITY_CASES,
        ids=[f"tc{tc['tcId']}" for tc in _MALLEABILITY_CASES],
    )
    def test_malleability_case_rejected(self, case: dict) -> None:
        # All malleability cases in the current corpus are also marked
        # 'invalid'. This test is redundant coverage for the specific
        # class of bug that surfaced in OpenSSL 1.1.1; keep it as
        # a load-bearing standalone assertion so a future corpus refresh
        # that reclassifies these cases (e.g. to 'acceptable') fails
        # loudly and forces a re-decision.
        assert case["result"] == "invalid", (
            f"tcId {case['tcId']}: SignatureMalleability case is no longer "
            f"marked 'invalid' (now {case['result']!r}). Re-decide our "
            f"policy before merging."
        )
        with pytest.raises(InvalidSignature):
            _verify_raw(case["pubkey_hex"], case["msg_hex"], case["sig_hex"])
