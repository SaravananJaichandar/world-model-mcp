"""
Non-Atheris smoke fuzz for verify_manifest + verify_manifest_streaming.

The Atheris fuzz targets in fuzz/ require Clang + libFuzzer to
build; not every CI runner has that. This test module locks the
same "no unexpected exception" invariant using deterministic
adversarial inputs plus hypothesis-shaped random bytes, so every
PR gets fuzz-shaped coverage regardless of the Atheris install
state.

The invariant on both verify paths: given ANY bytes as input,
either

  a. verify returns a VerificationReport (possibly failed), OR
  b. verify raises one of the EXPECTED malformed-input exception
     types.

An unexpected exception type is a bug — either an unhandled input
shape or a real crash — and the test surfaces it as a failure
naming the specific input that triggered it.

Adversarial input set spans:
  - empty / near-empty JSON
  - wrong top-level type (list, string, number, null, non-JSON bytes)
  - missing required keys
  - wrong-typed required keys
  - integer bignum overflow candidates
  - non-UTF8 bytes inside JSON strings
  - deeply nested structural bombs
  - invalid base64 in public_keys
  - malformed hex in signature envelopes
  - manifest with a single entry containing every wrong-type variant

Then 100 uniformly-random 200-byte seeds locked by index so a
regression on a specific seed reproduces without external state.

Runs entirely without Atheris. Does not need SLH-DSA — verify
short-circuits before signature checks on any of these inputs.
"""

from __future__ import annotations

import json
import random

import pytest

from world_model_server import etch_verify


EXPECTED_MALFORMED: tuple[type[BaseException], ...] = (
    json.JSONDecodeError,
    KeyError,
    TypeError,
    ValueError,
    UnicodeDecodeError,
    AttributeError,
)


def _is_ijson_exception(exc: BaseException) -> bool:
    module = getattr(type(exc), "__module__", "") or ""
    name = type(exc).__name__
    return (
        module.startswith("ijson")
        or "JSONError" in name
        or "IncompleteJSON" in name
    )


def _run_in_memory(payload: bytes) -> None:
    try:
        parsed = json.loads(payload)
    except EXPECTED_MALFORMED:
        return
    if not isinstance(parsed, dict):
        return
    try:
        etch_verify.verify_manifest(parsed)
    except EXPECTED_MALFORMED:
        pass


def _run_streaming(payload: bytes, tmp_path) -> None:
    p = tmp_path / "in.json"
    p.write_bytes(payload)
    try:
        etch_verify.verify_manifest_streaming(p)
    except EXPECTED_MALFORMED:
        pass
    except Exception as exc:
        if not _is_ijson_exception(exc):
            raise


ADVERSARIAL_INPUTS: list[tuple[str, bytes]] = [
    ("empty_bytes", b""),
    ("empty_object", b"{}"),
    ("open_brace_only", b"{"),
    ("just_null", b"null"),
    ("just_string", b'"hello"'),
    ("top_level_array", b"[]"),
    ("top_level_number", b"1"),
    ("top_level_true", b"true"),
    ("nul_bytes", b"\x00\x00\x00\x00"),
    ("non_utf8", b"\xff" * 200),
    ("deeply_nested",
     b'{' + b'"a":{' * 500 + b'"x":1' + b'}' * 501),
    ("version_null",
     b'{"manifest_version": null}'),
    ("version_wrong_type",
     b'{"manifest_version": []}'),
    ("version_wrong_string",
     b'{"manifest_version": "999"}'),
    ("missing_genesis_hash",
     b'{"manifest_version": "1"}'),
    ("log_null",
     b'{"manifest_version": "1", "tamper_evident_log": null}'),
    ("log_with_null_entry",
     b'{"manifest_version": "1", "tamper_evident_log": [null]}'),
    ("log_entry_wrong_types",
     b'{"manifest_version": "1", "tamper_evident_log": '
     b'[{"seq": "not_int"}]}'),
    ("bignum_seq",
     b'{"manifest_version": "1", "tamper_evident_log": '
     b'[{"seq": 99999999999999999999999999999}]}'),
    ("public_keys_not_object",
     b'{"manifest_version": "1", "public_keys": []}'),
    ("public_keys_bad_base64",
     b'{"manifest_version": "1", "public_keys": '
     b'{"ed25519": "not!base64", "slh_dsa": "also!not"}}'),
    ("public_keys_empty_string",
     b'{"manifest_version": "1", "public_keys": '
     b'{"ed25519": "", "slh_dsa": ""}}'),
    ("epochs_bad_envelope",
     b'{"manifest_version": "1", "epoch_genesis_root": "sha256:00", '
     b'"public_keys": {"ed25519": "AAAA", "slh_dsa": "AAAA"}, '
     b'"epochs": [{"seq": 1, "prev_epoch_root": "sha256:00", '
     b'"merkle_root": "sha256:00", "first_entry_seq": 1, '
     b'"last_entry_seq": 1, "entry_count": 1, "closed_at": "x", '
     b'"signature_envelope": {"version": "not_int"}}]}'),
    ("source_rows_wrong_type",
     b'{"manifest_version": "1", "source_rows": []}'),
    ("annotation_missing_id",
     b'{"manifest_version": "1", "source_rows": '
     b'{"annotations": [{}], "events": []}}'),
    ("many_empty_log_entries",
     b'{"manifest_version": "1", "tamper_evident_log": ['
     + b'{},' * 200 + b'{}]}'),
    ("open_string_no_close",
     b'{"manifest_version": "hello'),
    ("escape_at_end",
     b'{"manifest_version": "\\'),
    ("mixed_ascii_control",
     b'{"manifest_version": "\x01\x02\x03"}'),
]


class TestAdversarialInputsSurfaceOnlyExpectedExceptions:
    @pytest.mark.parametrize(
        "label,payload",
        ADVERSARIAL_INPUTS,
        ids=[name for name, _ in ADVERSARIAL_INPUTS],
    )
    def test_in_memory_verify_never_unexpected(self, label, payload):
        _run_in_memory(payload)

    @pytest.mark.parametrize(
        "label,payload",
        ADVERSARIAL_INPUTS,
        ids=[name for name, _ in ADVERSARIAL_INPUTS],
    )
    def test_streaming_verify_never_unexpected(
        self, tmp_path, label, payload,
    ):
        _run_streaming(payload, tmp_path)


class TestRandomBytesNeverUnexpected:
    """100 deterministic random 200-byte payloads. Locked-by-seed so a
    regression on a specific seed reproduces without external state.
    Verify functions must NOT crash the interpreter on any of them —
    either a valid parse and verify verdict, or an expected
    malformed-input exception."""

    @pytest.mark.parametrize("seed", list(range(100)))
    def test_streaming_verify_random_bytes(self, tmp_path, seed):
        rng = random.Random(seed)
        payload = bytes(rng.randrange(0, 256) for _ in range(200))
        _run_streaming(payload, tmp_path)

    @pytest.mark.parametrize("seed", list(range(100)))
    def test_in_memory_verify_random_bytes(self, seed):
        rng = random.Random(seed)
        payload = bytes(rng.randrange(0, 256) for _ in range(200))
        _run_in_memory(payload)
