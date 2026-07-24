"""
Atheris fuzz target: `etch_verify.verify_manifest` +
`etch_verify.verify_manifest_streaming`.

Both entry points parse externally-supplied JSON, so both are on
the attack surface for an auditor-side path (offline CLI, hosted
timer, and any downstream integration). This fuzz target feeds
random and mutated bytes into both and asserts:

  1. No unhandled exception type escapes. Malformed input MUST
     raise one of the declared "expected malformed" exceptions.
     Any other exception is a crash and Atheris reports it.
  2. No hang. Atheris' watchdog kills the process if a single
     input causes the target to spin.
  3. No integer or resource exhaustion. If a manifest with an
     absurdly large chain size makes the row_lookup dict blow
     memory, Atheris' memory limit catches it.

Run locally:

    pip install atheris
    python fuzz/fuzz_verify_manifest.py -atheris_runs=100000
    # or a wall-clock budget:
    python fuzz/fuzz_verify_manifest.py -max_total_time=60

Seed the corpus with representative inputs so mutation starts from
plausible-shaped bytes, not from zero:

    python fuzz/fuzz_verify_manifest.py fuzz/corpus/

Atheris is not required for CI — the same shape is exercised as a
non-Atheris smoke fuzz in tests/test_fuzz_smoke_verify.py, which
uses parametrized adversarial + random inputs and locks the same
"no unexpected exception" invariant. See fuzz/README.md for the
split reasoning.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import atheris

# Make the world_model_server package importable when this file is
# invoked directly from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

with atheris.instrument_imports():
    from world_model_server import etch_verify  # noqa: E402


EXPECTED_MALFORMED_EXCEPTIONS: tuple[type[BaseException], ...] = (
    json.JSONDecodeError,
    KeyError,
    TypeError,
    ValueError,
    UnicodeDecodeError,
    AttributeError,
)


def _is_ijson_exception(exc: BaseException) -> bool:
    """Every JSON-parse failure from ijson (Incomplete, JSONError,
    backend-specific variants) counts as expected malformed input.
    We check by module prefix + exception name to survive backend
    swaps."""
    module = getattr(type(exc), "__module__", "") or ""
    name = type(exc).__name__
    return (
        module.startswith("ijson")
        or "JSONError" in name
        or "IncompleteJSON" in name
    )


def _fuzz_target(data: bytes) -> None:
    # In-memory path via json.loads. Skip non-dict payloads —
    # verify_manifest requires a dict shape at the top level.
    try:
        parsed = json.loads(data)
    except EXPECTED_MALFORMED_EXCEPTIONS:
        parsed = None
    if isinstance(parsed, dict):
        try:
            etch_verify.verify_manifest(parsed)
        except EXPECTED_MALFORMED_EXCEPTIONS:
            pass

    # Streaming path via ijson. Requires bytes on disk since ijson
    # reads a file object.
    with tempfile.NamedTemporaryFile(
        prefix="fuzz_verify_", suffix=".json", delete=False,
    ) as f:
        f.write(data)
        tmp_path = f.name
    try:
        try:
            etch_verify.verify_manifest_streaming(tmp_path)
        except EXPECTED_MALFORMED_EXCEPTIONS:
            pass
        except Exception as e:
            if not _is_ijson_exception(e):
                raise
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def main() -> None:
    atheris.Setup(sys.argv, _fuzz_target)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
