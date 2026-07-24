"""
etch-verify: offline reference verifier CLI.

Reads a dump manifest produced by `world_model_server.audit_dump` and
independently proves:

  1. Chain integrity — every tamper_evident_log entry chains via
     `prev_hash` back to `GENESIS_HASH`, and each entry's own
     `entry_hash` recomputes byte-for-byte from its stored fields.
  2. Epoch signatures — every closed epoch's hybrid Ed25519 + SLH-DSA
     signature verifies under the operator's on-disk public keys,
     and each epoch's `prev_epoch_root` links to the previous epoch's
     `merkle_root` (or `EPOCH_GENESIS_ROOT` for the first).
  3. Annotation content lock — every annotation in `source_rows`
     reconstructs to the exact `row_hash` the log locked in. Any
     post-hoc rationale rewrite / author swap / range retarget /
     type change fails this check.
  4. Event content lock — every event in `source_rows` reconstructs
     to the same `row_hash` KnowledgeGraph.create_event committed.

Usage:
    etch-verify <manifest.json> [--json]

Exit codes:
    0  every check passed
    1  at least one check failed (details in report)
    2  manifest could not be read or is not a v1 manifest

Verifier is fully offline: no network, no DB access, no keys beyond
what the manifest declares. Auditors run this from a sealed
workstation against a signed dump artifact.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path

import ijson

# The liboqs-python C library prints "liboqs-python faulthandler is
# disabled" to stdout on first import. Auditors piping
# `etch-verify --json` to jq would hit unparseable input otherwise.
# Redirect stdout to /dev/null while the crypto import chain warms up,
# then restore before any user-facing output happens.
with contextlib.redirect_stdout(io.StringIO()):
    from . import audit_dump as _dump  # noqa: E402
    from . import hybrid_signer as hs  # noqa: E402
    from . import tamper_evident  # noqa: E402


class VerificationReport:
    """Structured verdict per check + overall exit status."""

    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.entries_checked: int = 0
        self.epochs_checked: int = 0
        self.annotations_checked: int = 0
        self.events_checked: int = 0

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)

    def add(self, name: str, ok: bool, detail: str | None = None) -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checks": self.checks,
            "counts": {
                "entries": self.entries_checked,
                "epochs": self.epochs_checked,
                "annotations": self.annotations_checked,
                "events": self.events_checked,
            },
        }


def _verify_chain_integrity(manifest: dict, report: VerificationReport) -> None:
    """Every log entry's entry_hash recomputes from its stored fields;
    every prev_hash chains to the previous entry's entry_hash."""
    entries = manifest["tamper_evident_log"]
    prev_hash = manifest["genesis_hash"]
    for e in entries:
        if e["prev_hash"] != prev_hash:
            report.add(
                "chain_integrity", False,
                f"entry seq={e['seq']} prev_hash {e['prev_hash']} "
                f"does not match previous entry_hash {prev_hash}",
            )
            return
        recomputed = tamper_evident.chain_hash(
            prev_hash=e["prev_hash"],
            entry_row_hash=e["row_hash"],
            kind=e["kind"],
            seq=e["seq"],
            ts=e["ts"],
        )
        if recomputed != e["entry_hash"]:
            report.add(
                "chain_integrity", False,
                f"entry seq={e['seq']} entry_hash recomputation mismatch "
                f"(expected {e['entry_hash']}, got {recomputed})",
            )
            return
        prev_hash = e["entry_hash"]
        report.entries_checked += 1
    report.add("chain_integrity", True)


def _verify_epoch_chain_and_signatures(
    manifest: dict, report: VerificationReport,
) -> None:
    """Every epoch's prev_epoch_root chains + its hybrid signature
    verifies under the public keys declared in the manifest."""
    ed_pub = base64.b64decode(manifest["public_keys"]["ed25519"])
    slh_pub = base64.b64decode(manifest["public_keys"]["slh_dsa"])
    prev_root = manifest["epoch_genesis_root"]
    for e in manifest["epochs"]:
        if e["prev_epoch_root"] != prev_root:
            report.add(
                "epoch_chain", False,
                f"epoch seq={e['seq']} prev_epoch_root does not chain "
                f"(expected {prev_root}, got {e['prev_epoch_root']})",
            )
            return
        payload = {
            "merkle_root": e["merkle_root"],
            "prev_epoch_root": e["prev_epoch_root"],
            "first_entry_seq": e["first_entry_seq"],
            "last_entry_seq": e["last_entry_seq"],
            "entry_count": e["entry_count"],
            "closed_at": e["closed_at"],
        }
        signed_bytes = tamper_evident.canonical_json(payload)
        try:
            verified = hs.verify_hybrid(
                envelope=e["signature_envelope"],
                message=signed_bytes,
                ed25519_public_key=ed_pub,
                slh_dsa_public_key=slh_pub,
            )
        except RuntimeError as exc:
            # SLH-DSA unavailable in this environment. Fail the whole
            # verify with a distinct check-name so an operator reading
            # the output can tell "cannot verify" apart from "verified
            # as tampered." Both fail the report, but the reason
            # column names the environment problem explicitly.
            report.add(
                "epoch_signatures_unverifiable", False,
                f"cannot verify epoch seq={e['seq']}: {exc}",
            )
            return
        if not verified:
            report.add(
                "epoch_signatures", False,
                f"epoch seq={e['seq']} hybrid signature failed to verify",
            )
            return
        prev_root = e["merkle_root"]
        report.epochs_checked += 1
    report.add("epoch_chain", True)
    report.add("epoch_signatures", True)


def _verify_annotation_content_lock(
    manifest: dict, report: VerificationReport,
) -> None:
    """Every annotation row in source_rows reconstructs to the
    row_hash the log locked in for that annotation_id. Catches any
    post-hoc mutation of annotations.db content."""
    log_by_row_id = {e["row_id"]: e for e in manifest["tamper_evident_log"]}
    for row in manifest["source_rows"]["annotations"]:
        entry = log_by_row_id.get(row["id"])
        if entry is None:
            report.add(
                "annotation_content_lock", False,
                f"annotation {row['id']!r} has no matching log entry",
            )
            return
        if entry["kind"] != "annotation_create":
            report.add(
                "annotation_content_lock", False,
                f"annotation {row['id']!r} log entry kind is "
                f"{entry['kind']!r}, not 'annotation_create'",
            )
            return
        reconstructed = tamper_evident.reconstruct_annotation_payload(row)
        recomputed = tamper_evident.row_hash(reconstructed)
        if recomputed != entry["row_hash"]:
            report.add(
                "annotation_content_lock", False,
                f"annotation {row['id']!r} row_hash mismatch "
                f"(reconstructed {recomputed}, logged {entry['row_hash']}) "
                "— annotations.db content was mutated after signing",
            )
            return
        report.annotations_checked += 1
    report.add("annotation_content_lock", True)


def _reconstruct_event_payload(row: dict) -> dict:
    """Rebuild the exact payload KnowledgeGraph.create_event passes
    to _maybe_audit_write. Kept as a small local helper because event
    audit payload lives in knowledge_graph.py, not tamper_evident.py."""
    return {
        "id": row["id"],
        "event_type": row["event_type"],
        "session_id": row["session_id"],
        "entity_id": row["entity_id"],
        "tool_name": row["tool_name"],
        "success": row["success"],
    }


def _verify_event_content_lock(
    manifest: dict, report: VerificationReport,
) -> None:
    """Every event row in source_rows reconstructs to the row_hash
    the log locked in for that event_id."""
    log_by_row_id = {e["row_id"]: e for e in manifest["tamper_evident_log"]}
    for row in manifest["source_rows"]["events"]:
        entry = log_by_row_id.get(row["id"])
        if entry is None:
            report.add(
                "event_content_lock", False,
                f"event {row['id']!r} has no matching log entry",
            )
            return
        if entry["kind"] != "event_create":
            report.add(
                "event_content_lock", False,
                f"event {row['id']!r} log entry kind is {entry['kind']!r}, "
                "not 'event_create'",
            )
            return
        reconstructed = _reconstruct_event_payload(row)
        recomputed = tamper_evident.row_hash(reconstructed)
        if recomputed != entry["row_hash"]:
            report.add(
                "event_content_lock", False,
                f"event {row['id']!r} row_hash mismatch "
                f"(reconstructed {recomputed}, logged {entry['row_hash']}) "
                "— events.db content was mutated after signing",
            )
            return
        report.events_checked += 1
    report.add("event_content_lock", True)


def verify_manifest(manifest: dict) -> VerificationReport:
    """Run all four verification passes. Ordered so a failure early
    in the chain (bad chain integrity) short-circuits before we try
    to reason about signatures over that chain."""
    report = VerificationReport()

    if manifest.get("manifest_version") != _dump.MANIFEST_VERSION:
        report.add(
            "manifest_version", False,
            f"expected version {_dump.MANIFEST_VERSION!r}, got "
            f"{manifest.get('manifest_version')!r}",
        )
        return report
    report.add("manifest_version", True)

    _verify_chain_integrity(manifest, report)
    if not report.ok:
        return report

    _verify_epoch_chain_and_signatures(manifest, report)
    if not report.ok:
        return report

    _verify_annotation_content_lock(manifest, report)
    if not report.ok:
        return report

    _verify_event_content_lock(manifest, report)
    return report


# ---------------------------------------------------------------------------
# Streaming verify (v0.15.5)
#
# verify_manifest(dict) works only when the caller already json.loaded the
# manifest into a Python dict. On real production chains that dict pins
# multiple gigabytes of RSS — verified 2026-07-24 on a 760MB manifest
# exceeding 3GB after dict expansion + verify allocations.
#
# verify_manifest_streaming parses the manifest with ijson row-by-row and
# produces the identical verdict without ever materializing the manifest
# in memory. Memory footprint: O(single row + compact row_hash lookup).
# The lookup is `row_id -> (row_hash, kind)`, roughly a few hundred bytes
# per chained row — far smaller than holding every log entry as a full dict.
#
# Byte-parity of the VERDICT is a load-bearing property: verify_manifest
# and verify_manifest_streaming return equivalent report structures for
# the same manifest bytes, so hosted-side + auditor-side + offline CLI
# all produce identical evidence for the same audit chain state.
# ---------------------------------------------------------------------------


def _stream_scalar_header(file_path: str | Path) -> dict:
    """Walk the manifest and pluck the small scalar header fields we
    need for later passes (manifest_version, genesis_hash,
    epoch_genesis_root, public_keys.ed25519, public_keys.slh_dsa).

    Uses ijson.parse which walks the whole file's token stream but
    never allocates for arrays. Peak memory is bounded by the size
    of the largest single scalar (< 1KB) regardless of manifest size.
    """
    header: dict = {"public_keys": {}}
    with open(file_path, "rb") as f:
        for prefix, event, value in ijson.parse(f):
            if event != "string":
                continue
            if prefix == "manifest_version":
                header["manifest_version"] = value
            elif prefix == "genesis_hash":
                header["genesis_hash"] = value
            elif prefix == "epoch_genesis_root":
                header["epoch_genesis_root"] = value
            elif prefix == "public_keys.ed25519":
                header["public_keys"]["ed25519"] = value
            elif prefix == "public_keys.slh_dsa":
                header["public_keys"]["slh_dsa"] = value
    return header


def _stream_verify_chain_integrity(
    file_path: str | Path, header: dict, report: VerificationReport,
) -> dict[str, tuple[str, str]] | None:
    """Stream tamper_evident_log entries. Verify each chain link and
    recompute entry_hash from the entry's own fields.

    On success returns a compact `row_id -> (row_hash, kind)` dict
    for the annotation + event content-lock passes to consume.
    Returns None if any chain-integrity check failed (the failing
    check is appended to `report`).
    """
    row_lookup: dict[str, tuple[str, str]] = {}
    prev_hash = header["genesis_hash"]

    with open(file_path, "rb") as f:
        for entry in ijson.items(f, "tamper_evident_log.item"):
            # Cast ijson-produced Decimal ints to plain int so
            # canonical_json (which uses json.dumps under the hood
            # and rejects Decimal) accepts them. Everything else is
            # already str per the manifest schema.
            seq = int(entry["seq"])
            row_hash_val = str(entry["row_hash"])
            kind = str(entry["kind"])
            ts = str(entry["ts"])
            row_prev_hash = str(entry["prev_hash"])
            entry_hash_stored = str(entry["entry_hash"])
            row_id = str(entry["row_id"])

            if row_prev_hash != prev_hash:
                report.add(
                    "chain_integrity", False,
                    f"entry seq={seq} prev_hash {row_prev_hash} "
                    f"does not match previous entry_hash {prev_hash}",
                )
                return None
            recomputed = tamper_evident.chain_hash(
                prev_hash=row_prev_hash,
                entry_row_hash=row_hash_val,
                kind=kind,
                seq=seq,
                ts=ts,
            )
            if recomputed != entry_hash_stored:
                report.add(
                    "chain_integrity", False,
                    f"entry seq={seq} entry_hash recomputation mismatch "
                    f"(expected {entry_hash_stored}, got {recomputed})",
                )
                return None
            row_lookup[row_id] = (row_hash_val, kind)
            prev_hash = entry_hash_stored
            report.entries_checked += 1
    report.add("chain_integrity", True)
    return row_lookup


def _normalize_envelope(envelope: object) -> dict:
    """Coerce an ijson-produced envelope back to a plain dict with
    `version` cast to int. Everything else (hex strings, fingerprints)
    is already str in the manifest schema, so a shallow copy is
    sufficient."""
    e = dict(envelope) if envelope is not None else {}
    if "version" in e:
        try:
            e["version"] = int(e["version"])
        except (TypeError, ValueError):
            pass
    return e


def _stream_verify_epochs(
    file_path: str | Path, header: dict, report: VerificationReport,
) -> bool:
    """Stream `epochs`. Verify prev_epoch_root chains + hybrid
    signatures. Distinguishes 'unverifiable' (SLH-DSA not available
    in this environment) from 'verified as invalid' via a distinct
    check name — same semantics as verify_manifest."""
    ed_pub = base64.b64decode(header["public_keys"]["ed25519"])
    slh_pub = base64.b64decode(header["public_keys"]["slh_dsa"])
    prev_root = header["epoch_genesis_root"]

    with open(file_path, "rb") as f:
        for e in ijson.items(f, "epochs.item"):
            seq = int(e["seq"])
            first_entry_seq = int(e["first_entry_seq"])
            last_entry_seq = int(e["last_entry_seq"])
            entry_count = int(e["entry_count"])
            merkle_root = str(e["merkle_root"])
            prev_epoch_root_val = str(e["prev_epoch_root"])
            closed_at = str(e["closed_at"])

            if prev_epoch_root_val != prev_root:
                report.add(
                    "epoch_chain", False,
                    f"epoch seq={seq} prev_epoch_root does not chain "
                    f"(expected {prev_root}, got {prev_epoch_root_val})",
                )
                return False
            payload = {
                "merkle_root": merkle_root,
                "prev_epoch_root": prev_epoch_root_val,
                "first_entry_seq": first_entry_seq,
                "last_entry_seq": last_entry_seq,
                "entry_count": entry_count,
                "closed_at": closed_at,
            }
            signed_bytes = tamper_evident.canonical_json(payload)
            envelope = _normalize_envelope(e["signature_envelope"])
            try:
                verified = hs.verify_hybrid(
                    envelope=envelope,
                    message=signed_bytes,
                    ed25519_public_key=ed_pub,
                    slh_dsa_public_key=slh_pub,
                )
            except RuntimeError as exc:
                report.add(
                    "epoch_signatures_unverifiable", False,
                    f"cannot verify epoch seq={seq}: {exc}",
                )
                return False
            if not verified:
                report.add(
                    "epoch_signatures", False,
                    f"epoch seq={seq} hybrid signature failed to verify",
                )
                return False
            prev_root = merkle_root
            report.epochs_checked += 1
    report.add("epoch_chain", True)
    report.add("epoch_signatures", True)
    return True


def _stream_verify_annotation_content_lock(
    file_path: str | Path,
    row_lookup: dict[str, tuple[str, str]],
    report: VerificationReport,
) -> bool:
    """Stream `source_rows.annotations`. For each annotation row,
    look up the row_hash the chain locked in and recompute it. Fail
    on any mismatch or missing chain entry — same behavior and
    diagnostics as _verify_annotation_content_lock."""
    with open(file_path, "rb") as f:
        for raw in ijson.items(f, "source_rows.annotations.item"):
            row = dict(raw)
            row_id = str(row["id"])
            entry = row_lookup.get(row_id)
            if entry is None:
                report.add(
                    "annotation_content_lock", False,
                    f"annotation {row_id!r} has no matching log entry",
                )
                return False
            logged_row_hash, kind = entry
            if kind != "annotation_create":
                report.add(
                    "annotation_content_lock", False,
                    f"annotation {row_id!r} log entry kind is "
                    f"{kind!r}, not 'annotation_create'",
                )
                return False
            reconstructed = tamper_evident.reconstruct_annotation_payload(row)
            recomputed = tamper_evident.row_hash(reconstructed)
            if recomputed != logged_row_hash:
                report.add(
                    "annotation_content_lock", False,
                    f"annotation {row_id!r} row_hash mismatch "
                    f"(reconstructed {recomputed}, logged {logged_row_hash}) "
                    "— annotations.db content was mutated after signing",
                )
                return False
            report.annotations_checked += 1
    report.add("annotation_content_lock", True)
    return True


def _stream_verify_event_content_lock(
    file_path: str | Path,
    row_lookup: dict[str, tuple[str, str]],
    report: VerificationReport,
) -> None:
    """Stream `source_rows.events`. Same shape as annotation content
    lock but with the event-specific payload reconstruction."""
    with open(file_path, "rb") as f:
        for raw in ijson.items(f, "source_rows.events.item"):
            row = dict(raw)
            row_id = str(row["id"])
            entry = row_lookup.get(row_id)
            if entry is None:
                report.add(
                    "event_content_lock", False,
                    f"event {row_id!r} has no matching log entry",
                )
                return
            logged_row_hash, kind = entry
            if kind != "event_create":
                report.add(
                    "event_content_lock", False,
                    f"event {row_id!r} log entry kind is {kind!r}, "
                    "not 'event_create'",
                )
                return
            reconstructed = _reconstruct_event_payload(row)
            recomputed = tamper_evident.row_hash(reconstructed)
            if recomputed != logged_row_hash:
                report.add(
                    "event_content_lock", False,
                    f"event {row_id!r} row_hash mismatch "
                    f"(reconstructed {recomputed}, logged {logged_row_hash}) "
                    "— events.db content was mutated after signing",
                )
                return
            report.events_checked += 1
    report.add("event_content_lock", True)


def verify_manifest_streaming(file_path: str | Path) -> VerificationReport:
    """Streaming counterpart to verify_manifest(dict).

    Parses the manifest file row-by-row via ijson so memory usage is
    O(single row + compact row_hash lookup) rather than O(manifest
    size). Use this for chains large enough that json.load into a
    Python dict would OOM the verifier host.

    Same verdict, same check names, same counts as verify_manifest
    for the same manifest bytes. Byte-parity of the verdict is a
    load-bearing property so hosted-side, auditor-side, and offline
    CLI all produce identical evidence for the same audit chain state.

    Requires the `ijson` package (v0.15.5+ core dependency).
    """
    report = VerificationReport()

    header = _stream_scalar_header(file_path)
    if header.get("manifest_version") != _dump.MANIFEST_VERSION:
        report.add(
            "manifest_version", False,
            f"expected version {_dump.MANIFEST_VERSION!r}, got "
            f"{header.get('manifest_version')!r}",
        )
        return report
    report.add("manifest_version", True)

    row_lookup = _stream_verify_chain_integrity(file_path, header, report)
    if row_lookup is None:
        return report

    if not _stream_verify_epochs(file_path, header, report):
        return report

    if not _stream_verify_annotation_content_lock(
        file_path, row_lookup, report,
    ):
        return report

    _stream_verify_event_content_lock(file_path, row_lookup, report)
    return report


def _format_human(report: VerificationReport, path: str) -> str:
    lines: list[str] = []
    header = f"etch-verify: {path}"
    lines.append(header)
    lines.append("-" * len(header))
    for c in report.checks:
        mark = "PASS" if c["ok"] else "FAIL"
        lines.append(f"[{mark}] {c['name']}")
        if not c["ok"] and c.get("detail"):
            lines.append(f"       {c['detail']}")
    lines.append("")
    lines.append(
        f"entries={report.entries_checked}  "
        f"epochs={report.epochs_checked}  "
        f"annotations={report.annotations_checked}  "
        f"events={report.events_checked}"
    )
    lines.append("VERDICT: " + ("OK" if report.ok else "FAILED"))
    return "\n".join(lines)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file so `manifest_sha256` is available
    without loading the whole manifest into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="etch-verify",
        description=(
            "Offline reference verifier for world-model-mcp audit "
            "chain dumps (v1 manifest format)."
        ),
    )
    parser.add_argument(
        "manifest",
        help="Path to the manifest JSON file exported by audit_dump.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON for machine consumption.",
    )
    parser.add_argument(
        "--in-memory",
        action="store_true",
        help=(
            "Force the pre-v0.15.5 in-memory verifier (json.load + "
            "verify_manifest). Only useful for debugging the streaming "
            "path. The default streaming verifier handles any manifest "
            "size without materializing the whole manifest in memory."
        ),
    )
    args = parser.parse_args(argv)

    path = Path(args.manifest)

    if args.in_memory:
        try:
            raw = path.read_bytes()
        except OSError as e:
            sys.stderr.write(f"etch-verify: cannot read {args.manifest}: {e}\n")
            return 2
        try:
            manifest = json.loads(raw)
        except json.JSONDecodeError as e:
            sys.stderr.write(
                f"etch-verify: {args.manifest} is not valid JSON: {e}\n"
            )
            return 2
        report = verify_manifest(manifest)
        manifest_sha256 = _sha256_hex(raw)
    else:
        try:
            report = verify_manifest_streaming(path)
        except (FileNotFoundError, PermissionError, IsADirectoryError) as e:
            sys.stderr.write(f"etch-verify: cannot read {args.manifest}: {e}\n")
            return 2
        except ijson.JSONError as e:
            sys.stderr.write(
                f"etch-verify: {args.manifest} is not valid JSON: {e}\n"
            )
            return 2
        manifest_sha256 = _sha256_file(path)

    if args.json:
        payload = report.as_dict()
        payload["manifest_sha256"] = manifest_sha256
        payload["manifest_path"] = str(path)
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(_format_human(report, args.manifest) + "\n")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
