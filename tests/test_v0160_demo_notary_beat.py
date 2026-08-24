"""v0.16.0: `world-model demo` ends with a live notary beat.

The notary beat runs against a throwaway temp DB, records 3 sample
decisions, closes a real hybrid-signed epoch, builds the same
manifest ``etch-verify`` consumes, verifies VALID, mutates one byte
to prove the chain-integrity check catches it (INVALID), restores
the byte, and re-verifies (VALID again). Drops a
``world-model-demo-receipt.json`` in cwd and emits a
copy-paste ``etch.systems/verify#…`` URL packing the manifest into
the fragment (gzip + base64url).

The beat is the aha moment that turns installers into activations,
so this suite locks:

  1. `_run_notary_beat` returns True on the happy path (VALID →
     INVALID after tamper → VALID after restore).
  2. The receipt file is written to cwd with a well-formed
     manifest carrying the expected top-level fields plus
     entries + a closed epoch signed by the ephemeral hybrid signer.
  3. The manifest verifies cleanly through the same
     ``etch_verify.verify_manifest`` code path auditors run — no
     bespoke verify shim.
  4. The banner + URL are emitted to the console (regression guard
     against a future refactor accidentally silencing them).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path

import pytest
from rich.console import Console

from world_model_server import cli, etch_verify


def _run_beat_in_cwd(tmp_cwd: Path) -> tuple[bool, str]:
    """Run the notary beat with cwd rebased to ``tmp_cwd`` so the
    receipt lands where the test can find it, and return
    (return_value, captured_console_output)."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    prev_cwd = Path.cwd()
    os.chdir(tmp_cwd)
    try:
        ok = asyncio.run(cli._run_notary_beat(console))
    finally:
        os.chdir(prev_cwd)
    return ok, buf.getvalue()


def test_notary_beat_happy_path_returns_true(tmp_path):
    ok, _ = _run_beat_in_cwd(tmp_path)
    assert ok is True


def test_notary_beat_writes_receipt_with_expected_shape(tmp_path):
    _, _ = _run_beat_in_cwd(tmp_path)
    receipt = tmp_path / "world-model-demo-receipt.json"
    assert receipt.exists(), "notary beat did not drop a receipt"
    manifest = json.loads(receipt.read_text(encoding="utf-8"))
    # Top-level fields the etch-verify manifest schema requires.
    for field in (
        "manifest_version", "generated_at", "world_model_mcp_version",
        "genesis_hash", "epoch_genesis_root", "public_keys",
        "tamper_evident_log", "epochs", "source_rows",
    ):
        assert field in manifest, (
            f"receipt manifest missing required field {field!r}"
        )
    # Three sample decisions + one closed epoch, freshly signed.
    assert len(manifest["tamper_evident_log"]) == 3
    assert len(manifest["epochs"]) == 1
    assert "ed25519" in manifest["public_keys"]
    assert "slh_dsa" in manifest["public_keys"]
    # Source rows are intentionally empty for the demo beat because
    # the beat does not exercise the annotations / events tables.
    assert manifest["source_rows"] == {
        "annotations": [], "events": [],
    }


def test_receipt_verifies_via_etch_verify_manifest(tmp_path):
    """End-to-end: the manifest the beat writes must verify cleanly
    through the same `etch_verify.verify_manifest` code path an
    auditor runs. Any drift between demo output and auditor input
    breaks the whole demo->verify->hosted-verify handshake."""
    _run_beat_in_cwd(tmp_path)
    manifest = json.loads(
        (tmp_path / "world-model-demo-receipt.json").read_text(
            encoding="utf-8",
        ),
    )
    report = etch_verify.verify_manifest(manifest)
    assert report.ok, (
        "manifest failed to verify through etch_verify: "
        + json.dumps([c for c in report.checks if not c["ok"]])
    )


def test_console_output_carries_valid_invalid_restore_and_url(
    tmp_path,
):
    _, output = _run_beat_in_cwd(tmp_path)
    lower = output.lower()
    # The three-beat sequence must be visible in the output.
    assert "valid" in lower, output
    assert "invalid" in lower, output
    assert "restored" in lower, output
    # The shareable verify URL must be present and point at the
    # hosted /verify# route.
    assert "etch.systems/verify#" in output, output


def test_receipt_manifest_is_tamper_evident_at_bit_level(tmp_path):
    """Regression guard for the tamper beat itself: mutating one
    byte of the persisted receipt's first row_hash MUST cause
    verify_manifest to reject."""
    _run_beat_in_cwd(tmp_path)
    receipt_path = tmp_path / "world-model-demo-receipt.json"
    manifest = json.loads(receipt_path.read_text(encoding="utf-8"))
    manifest["tamper_evident_log"][0]["row_hash"] = "sha256:" + "00" * 32
    report = etch_verify.verify_manifest(manifest)
    assert not report.ok
