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
import hashlib
import json
import sys
from pathlib import Path

from . import audit_dump as _dump
from . import hybrid_signer as hs
from . import tamper_evident


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
        if not hs.verify_hybrid(
            envelope=e["signature_envelope"],
            message=signed_bytes,
            ed25519_public_key=ed_pub,
            slh_dsa_public_key=slh_pub,
        ):
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
    args = parser.parse_args(argv)

    path = Path(args.manifest)
    try:
        raw = path.read_bytes()
    except OSError as e:
        sys.stderr.write(f"etch-verify: cannot read {args.manifest}: {e}\n")
        return 2
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"etch-verify: {args.manifest} is not valid JSON: {e}\n")
        return 2

    report = verify_manifest(manifest)

    if args.json:
        payload = report.as_dict()
        payload["manifest_sha256"] = _sha256_hex(raw)
        payload["manifest_path"] = str(path)
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(_format_human(report, args.manifest) + "\n")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
