"""
etch-verify: offline reference verifier CLI.

Reads a dump manifest produced by `world_model_server.audit_dump` and
independently proves:

  1. Keys. The manifest's public keys match the fingerprints the
     auditor pinned with --pin-ed25519 / --pin-slh-dsa. Without pins
     the verifier says so and prints the fingerprints to compare.
  2. Epoch chain. Epochs are numbered 1..E, each `prev_epoch_root`
     links to the previous `merkle_root` (or `EPOCH_GENESIS_ROOT`),
     and their entry ranges cover the log from seq 1 with no gap or
     overlap. Every epoch's hybrid Ed25519 + SPHINCS+ signature
     verifies.
  3. Chain integrity. Log entries are numbered 1..N, every
     `entry_hash` recomputes from its stored fields, and every
     `prev_hash` chains back to `GENESIS_HASH`.
  4. Merkle roots. Every epoch's `merkle_root` recomputes from the
     row hashes of the entries it covers.
  5. Content lock. Every annotation and event row reconstructs to the
     `row_hash` its log entry committed, and every annotation_create
     and event_create entry has its content row.

Usage:
    etch-verify <manifest.json> [--json]
        [--pin-ed25519 sha256:...] [--pin-slh-dsa sha256:...]

Verdicts and exit codes:
    0  OK        every check passed and at least one epoch is signed
    1  FAILED    at least one check failed (details in report)
    2  manifest could not be read or is not valid JSON
    3  UNSIGNED  every check passed but no epoch is sealed, so nothing
                 in the manifest carries an epoch signature

Not detected: entries or whole epochs removed from the end of the
manifest. Nothing in the v1 format commits to where the log ends.

Verifier is fully offline: no network, no DB access, and no native
crypto library (SPHINCS+ verification is pure Python). Auditors run
this from a sealed workstation against a signed dump artifact.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import ijson
from ijson.common import ObjectBuilder

with contextlib.redirect_stdout(io.StringIO()):
    from . import audit_dump as _dump  # noqa: E402
    from . import hybrid_signer as hs  # noqa: E402
    from . import merkle, tamper_evident  # noqa: E402

VERDICT_OK = "OK"
VERDICT_UNSIGNED = "UNSIGNED"
VERDICT_FAILED = "FAILED"
_EXIT_CODES = {VERDICT_OK: 0, VERDICT_FAILED: 1, VERDICT_UNSIGNED: 3}

_ARRAY_KEYS = frozenset({"tamper_evident_log", "epochs", "source_rows"})
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class KeyPins:
    """sha256: fingerprints the auditor obtained out of band."""

    ed25519: str | None = None
    slh_dsa: str | None = None


@dataclass(frozen=True)
class _EpochSpan:
    seq: int
    first: int
    last: int
    merkle_root: str


@dataclass(frozen=True)
class _ContentKind:
    noun: str
    log_kind: str
    reconstruct: Callable[[dict], dict]
    db_name: str


def _reconstruct_event_payload(row: dict) -> dict:
    """Rebuild the exact payload KnowledgeGraph.create_event passes
    to _maybe_audit_write."""
    return {
        "id": row["id"],
        "event_type": row["event_type"],
        "session_id": row["session_id"],
        "entity_id": row["entity_id"],
        "tool_name": row["tool_name"],
        "success": row["success"],
    }


_ANNOTATIONS = _ContentKind(
    "annotation", "annotation_create",
    tamper_evident.reconstruct_annotation_payload, "annotations.db",
)
_EVENTS = _ContentKind(
    "event", "event_create", _reconstruct_event_payload, "events.db",
)


class VerificationReport:
    """Structured verdict per check + overall verdict."""

    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.notes: list[str] = []
        self.entries_checked: int = 0
        self.epochs_checked: int = 0
        self.annotations_checked: int = 0
        self.events_checked: int = 0
        self.unsealed_entries: int = 0
        self.key_fingerprints: dict[str, str] = {}

    @property
    def verdict(self) -> str:
        if not all(c["ok"] for c in self.checks):
            return VERDICT_FAILED
        if self.epochs_checked == 0:
            return VERDICT_UNSIGNED
        return VERDICT_OK

    @property
    def ok(self) -> bool:
        return self.verdict == VERDICT_OK

    def add(self, name: str, ok: bool, detail: str | None = None) -> bool:
        self.checks.append({"name": name, "ok": ok, "detail": detail})
        return ok

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "verdict": self.verdict,
            "checks": self.checks,
            "notes": self.notes,
            "public_key_fingerprints": self.key_fingerprints,
            "counts": {
                "entries": self.entries_checked,
                "epochs": self.epochs_checked,
                "annotations": self.annotations_checked,
                "events": self.events_checked,
                "unsealed": self.unsealed_entries,
            },
        }


# ---------------------------------------------------------------------------
# Manifest sources. One verifier walks either an in-memory dict or a
# file streamed with ijson, so both paths give the same verdict by
# construction. The streaming path keeps memory at O(one row + one
# epoch of leaves + a row_id lookup), which a 760MB prod manifest needs.
# ---------------------------------------------------------------------------


class _DictSource:
    def __init__(self, manifest: dict) -> None:
        self._m = manifest
        self.header = {k: v for k, v in manifest.items() if k not in _ARRAY_KEYS}

    def items(self, path: str) -> Iterator[dict]:
        node = self._m
        for key in path.split("."):
            node = node[key]
        return iter(node)


class _FileSource:
    def __init__(self, path: str | Path) -> None:
        self._path = path
        self.header = self._read_header()

    def _read_header(self) -> dict:
        """Every top-level value except the three large arrays."""
        header: dict = {}
        with open(self._path, "rb") as f:
            events = ijson.parse(f)
            for prefix, event, value in events:
                if prefix or event != "map_key" or value in _ARRAY_KEYS:
                    continue
                builder, depth = ObjectBuilder(), 0
                for _, inner_event, inner_value in events:
                    builder.event(inner_event, inner_value)
                    if inner_event in ("start_map", "start_array"):
                        depth += 1
                    elif inner_event in ("end_map", "end_array"):
                        depth -= 1
                    if depth == 0:
                        break
                header[value] = builder.value
        return header

    def items(self, path: str) -> Iterator[dict]:
        with open(self._path, "rb") as f:
            yield from ijson.items(f, path + ".item")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _check_keys(
    public_keys: dict, pins: KeyPins, report: VerificationReport,
) -> tuple[bytes, bytes] | None:
    ed_pub = base64.b64decode(public_keys["ed25519"])
    slh_pub = base64.b64decode(public_keys["slh_dsa"])
    served = {
        "ed25519": hs.pubkey_fingerprint(ed_pub),
        "slh_dsa": hs.pubkey_fingerprint(slh_pub),
    }
    report.key_fingerprints = served
    pinned = {"ed25519": pins.ed25519, "slh_dsa": pins.slh_dsa}
    if not any(pinned.values()):
        report.notes.append(
            "public keys are taken from the manifest itself, not pinned. A "
            "re-signed forgery verifies against its own keys. Compare these "
            "fingerprints with the operator's published keys, or pass "
            f"--pin-ed25519 {served['ed25519']} --pin-slh-dsa {served['slh_dsa']}"
        )
        return ed_pub, slh_pub
    for alg, pin in pinned.items():
        if pin is not None and pin != served[alg]:
            report.add(
                "key_pins", False,
                f"{alg} key in the manifest is {served[alg]}, pinned {pin}",
            )
            return None
    for alg, pin in pinned.items():
        if pin is None:
            report.notes.append(f"{alg} key is not pinned")
    report.add("key_pins", True)
    return ed_pub, slh_pub


def _epoch_signing_bytes(e: dict) -> bytes:
    return tamper_evident.canonical_json({
        "merkle_root": e["merkle_root"],
        "prev_epoch_root": e["prev_epoch_root"],
        "first_entry_seq": e["first_entry_seq"],
        "last_entry_seq": e["last_entry_seq"],
        "entry_count": e["entry_count"],
        "closed_at": e["closed_at"],
    })


def _check_epochs(
    epochs: Iterator[dict], genesis_root: str, keys: tuple[bytes, bytes],
    report: VerificationReport,
) -> list[_EpochSpan] | None:
    """Numbering, root links, contiguous coverage from seq 1, and every
    signature. Returns the spans for the Merkle recompute."""
    spans: list[_EpochSpan] = []
    prev_root, next_first = genesis_root, 1
    for e in epochs:
        seq, first, last = e["seq"], e["first_entry_seq"], e["last_entry_seq"]
        problem = None
        if seq != len(spans) + 1:
            problem = f"epoch seq={seq} is out of order (expected {len(spans) + 1})"
        elif e["prev_epoch_root"] != prev_root:
            problem = (
                f"epoch seq={seq} prev_epoch_root does not chain "
                f"(expected {prev_root}, got {e['prev_epoch_root']})"
            )
        elif first != next_first or last < first:
            problem = (
                f"epoch seq={seq} covers entries {first}..{last}; coverage "
                f"must continue at seq {next_first} with no gap or overlap"
            )
        elif e["entry_count"] != last - first + 1:
            problem = (
                f"epoch seq={seq} entry_count {e['entry_count']} does not "
                f"match its range {first}..{last}"
            )
        if problem:
            report.add("epoch_chain", False, problem)
            return None
        if not hs.verify_hybrid(
            envelope=e["signature_envelope"],
            message=_epoch_signing_bytes(e),
            ed25519_public_key=keys[0],
            slh_dsa_public_key=keys[1],
        ):
            report.add(
                "epoch_signatures", False,
                f"epoch seq={seq} hybrid signature failed to verify",
            )
            return None
        spans.append(_EpochSpan(seq, first, last, e["merkle_root"]))
        prev_root, next_first = e["merkle_root"], last + 1
        report.epochs_checked += 1
    report.add("epoch_chain", True)
    report.add("epoch_signatures", True)
    return spans


@dataclass
class _LogWalk:
    """What the log pass hands to the content checks."""

    row_lookup: dict[str, tuple[str, str]]
    last_seq: int
    last_hash: str


def _check_log(
    entries: Iterator[dict], genesis_hash: str, spans: list[_EpochSpan],
    report: VerificationReport,
) -> _LogWalk | None:
    """Chain links and numbering, then each epoch's Merkle root over
    the row hashes it covers, recomputed one epoch at a time."""
    walk = _LogWalk({}, 0, genesis_hash)
    pending = iter(spans)
    span = next(pending, None)
    leaves: list[bytes] = []
    merkle_problem = None
    for e in entries:
        seq = e["seq"]
        if seq != walk.last_seq + 1:
            report.add(
                "chain_integrity", False,
                f"entry seq={seq} follows seq={walk.last_seq}; entries must "
                "be numbered 1..N with no gap",
            )
            return None
        if e["prev_hash"] != walk.last_hash:
            report.add(
                "chain_integrity", False,
                f"entry seq={seq} prev_hash {e['prev_hash']} "
                f"does not match previous entry_hash {walk.last_hash}",
            )
            return None
        recomputed = tamper_evident.chain_hash(
            prev_hash=e["prev_hash"], entry_row_hash=e["row_hash"],
            kind=e["kind"], seq=seq, ts=e["ts"],
        )
        if recomputed != e["entry_hash"]:
            report.add(
                "chain_integrity", False,
                f"entry seq={seq} entry_hash recomputation mismatch "
                f"(expected {e['entry_hash']}, got {recomputed})",
            )
            return None
        walk.row_lookup[e["row_id"]] = (e["row_hash"], e["kind"])
        walk.last_seq, walk.last_hash = seq, e["entry_hash"]
        report.entries_checked += 1

        if span is None or merkle_problem:
            continue
        leaves.append(merkle.leaf_hash(bytes.fromhex(e["row_hash"].split(":", 1)[1])))
        if seq == span.last:
            root = "sha256:" + merkle.merkle_root(leaves).hex()
            if root != span.merkle_root:
                merkle_problem = (
                    f"epoch seq={span.seq} merkle_root {span.merkle_root} does "
                    f"not match the root {root} recomputed over entries "
                    f"{span.first}..{span.last}"
                )
            leaves = []
            span = next(pending, None)
    report.add("chain_integrity", True)
    if span is not None and not merkle_problem:
        merkle_problem = (
            f"epoch seq={span.seq} covers entries {span.first}..{span.last} "
            f"but the log ends at seq {walk.last_seq}"
        )
    if not report.add("epoch_merkle_roots", merkle_problem is None, merkle_problem):
        return None
    report.unsealed_entries = walk.last_seq - (spans[-1].last if spans else 0)
    return walk


def _check_content(
    rows: Iterator[dict], kind: _ContentKind, walk: _LogWalk,
    report: VerificationReport,
) -> int | None:
    """Rows checked, or None after recording the failure."""
    lock = f"{kind.noun}_content_lock"
    seen: set[str] = set()
    for row in rows:
        row_id = row["id"]
        entry = walk.row_lookup.get(row_id)
        if entry is None:
            problem = f"{kind.noun} {row_id!r} has no matching log entry"
        elif entry[1] != kind.log_kind:
            problem = (
                f"{kind.noun} {row_id!r} log entry kind is {entry[1]!r}, "
                f"not {kind.log_kind!r}"
            )
        elif (recomputed := tamper_evident.row_hash(kind.reconstruct(row))) != entry[0]:
            problem = (
                f"{kind.noun} {row_id!r} row_hash mismatch "
                f"(reconstructed {recomputed}, logged {entry[0]}); "
                f"{kind.db_name} content was mutated after signing"
            )
        else:
            seen.add(row_id)
            continue
        report.add(lock, False, problem)
        return None
    report.add(lock, True)

    missing = [
        row_id for row_id, (_, logged_kind) in walk.row_lookup.items()
        if logged_kind == kind.log_kind and row_id not in seen
    ]
    present = f"{kind.noun}_content_present"
    if missing:
        report.add(
            present, False,
            f"{len(missing)} {kind.log_kind} log entries have no {kind.noun} "
            f"row in the manifest (first: {missing[0]!r})",
        )
        return None
    report.add(present, True)
    return len(seen)


def _verify(
    src: _DictSource | _FileSource, pins: KeyPins,
) -> VerificationReport:
    """Each pass needs the previous one to have held, so the first
    failure ends the walk."""
    report = VerificationReport()
    header = src.header
    if header.get("manifest_version") != _dump.MANIFEST_VERSION:
        report.add(
            "manifest_version", False,
            f"expected version {_dump.MANIFEST_VERSION!r}, got "
            f"{header.get('manifest_version')!r}",
        )
        return report
    report.add("manifest_version", True)

    keys = _check_keys(header["public_keys"], pins, report)
    if keys is None:
        return report
    spans = _check_epochs(src.items("epochs"), header["epoch_genesis_root"], keys, report)
    if spans is None:
        return report
    walk = _check_log(
        src.items("tamper_evident_log"), header["genesis_hash"], spans, report,
    )
    if walk is None:
        return report
    annotations = _check_content(src.items("source_rows.annotations"), _ANNOTATIONS, walk, report)
    if annotations is None:
        return report
    report.annotations_checked = annotations
    events = _check_content(src.items("source_rows.events"), _EVENTS, walk, report)
    if events is None:
        return report
    report.events_checked = events

    if not spans:
        report.notes.append(
            "no epoch is sealed: nothing in this manifest carries an epoch "
            "signature, so the verdict is UNSIGNED"
        )
    elif report.unsealed_entries:
        report.notes.append(
            f"{report.unsealed_entries} entries after seq {spans[-1].last} are "
            "not yet sealed into an epoch (not covered by any signature)"
        )
    return report


def verify_manifest(manifest: dict, pins: KeyPins = KeyPins()) -> VerificationReport:
    """Verify a manifest already loaded into a dict."""
    return _verify(_DictSource(manifest), pins)


def verify_manifest_streaming(
    file_path: str | Path, pins: KeyPins = KeyPins(),
) -> VerificationReport:
    """Verify a manifest file row by row via ijson, without loading it.
    Same checks, verdict and counts as verify_manifest."""
    return _verify(_FileSource(file_path), pins)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


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
    for note in report.notes:
        lines.append(f"[NOTE] {note}")
    lines.append("")
    if report.key_fingerprints:
        lines.append(
            f"keys: ed25519 {report.key_fingerprints['ed25519']}  "
            f"slh_dsa {report.key_fingerprints['slh_dsa']}"
        )
    lines.append(
        f"entries={report.entries_checked}  "
        f"epochs={report.epochs_checked}  "
        f"annotations={report.annotations_checked}  "
        f"events={report.events_checked}  "
        f"unsealed={report.unsealed_entries}"
    )
    lines.append("VERDICT: " + report.verdict)
    return "\n".join(lines)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _fingerprint_arg(value: str) -> str:
    value = value.strip().lower()
    if not _FINGERPRINT.match(value):
        raise argparse.ArgumentTypeError("expected sha256:<64 hex characters>")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="etch-verify",
        description=(
            "Offline reference verifier for world-model-mcp audit "
            "chain dumps (v1 manifest format)."
        ),
        epilog=(
            "This command checks a world-model-mcp manifest. To verify an "
            "Etch hosted-chain export (etch-chain.json), install the "
            "separate etch-verify package from PyPI and run "
            "etch-verify-chain."
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
        "--pin-ed25519", type=_fingerprint_arg, metavar="sha256:...",
        help="Fail unless the manifest's Ed25519 key has this fingerprint.",
    )
    parser.add_argument(
        "--pin-slh-dsa", type=_fingerprint_arg, metavar="sha256:...",
        help="Fail unless the manifest's SLH-DSA key has this fingerprint.",
    )
    parser.add_argument(
        "--in-memory",
        action="store_true",
        help=(
            "Load the whole manifest with json.load instead of streaming "
            "it. Same checks; only useful for debugging."
        ),
    )
    args = parser.parse_args(argv)

    path = Path(args.manifest)
    pins = KeyPins(args.pin_ed25519, args.pin_slh_dsa)
    try:
        if args.in_memory:
            report = verify_manifest(json.loads(path.read_bytes()), pins)
        else:
            report = verify_manifest_streaming(path, pins)
        manifest_sha256 = _sha256_file(path)
    except OSError as e:
        sys.stderr.write(f"etch-verify: cannot read {args.manifest}: {e}\n")
        return 2
    except (json.JSONDecodeError, ijson.JSONError) as e:
        sys.stderr.write(f"etch-verify: {args.manifest} is not valid JSON: {e}\n")
        return 2

    if args.json:
        payload = report.as_dict()
        payload["manifest_sha256"] = manifest_sha256
        payload["manifest_path"] = str(path)
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(_format_human(report, args.manifest) + "\n")

    return _EXIT_CODES[report.verdict]


if __name__ == "__main__":
    raise SystemExit(main())
