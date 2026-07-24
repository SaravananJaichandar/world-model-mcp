# fuzz — verify-path fuzz targets

Fuzz targets for the two externally-attackable JSON parsers on the
Etch audit-chain verify path:

- `verify_manifest(manifest_dict)` — in-memory verifier
- `verify_manifest_streaming(file_path)` — ijson-based streaming verifier

Both surfaces run against auditor-supplied artifacts, so any
adversarial or malformed byte sequence must either return a
verification report or raise one of the declared "expected
malformed input" exceptions. Never an unhandled crash, never a
hang.

## Files

- `fuzz_verify_manifest.py` — Atheris entry point. Feeds bytes to
  both verify paths, asserts the invariant above.
- `corpus/` — seed inputs Atheris mutates from. Representative
  shapes: empty, valid-v1 stub, wrong-version, deeply nested,
  adversarial-typed fields, invalid UTF-8.

## Run locally with Atheris

```
pip install atheris
python fuzz/fuzz_verify_manifest.py fuzz/corpus/ -max_total_time=60
```

Atheris requires a Clang toolchain and libFuzzer at build time.
On macOS: `brew install llvm`. On Debian/Ubuntu: `apt install clang`.
Prebuilt wheels ship for common Linux + macOS targets, so a plain
`pip install atheris` usually works.

## CI coverage without Atheris

Atheris is not required for CI. The same "no unexpected exception"
invariant is exercised as parametrized adversarial + random-byte
smoke fuzz in `tests/test_fuzz_smoke_verify.py`, which runs on
every PR alongside the rest of the pytest suite. Atheris adds
coverage-guided mutation on top of that fixed input set for
longer-running local fuzz sessions.

If Atheris ever moves into the CI matrix, use the same targets
in this directory unchanged.
