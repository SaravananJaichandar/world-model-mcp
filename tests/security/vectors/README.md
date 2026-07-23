# Test vectors vendored for security tests

This directory contains third-party cryptographic test vectors vendored into the repo at pinned upstream commits so security tests are self-contained and network-independent.

## `ed25519_test.json`

- **Source:** [C2SP/wycheproof — `testvectors_v1/ed25519_test.json`](https://github.com/C2SP/wycheproof/tree/main/testvectors_v1)
- **Pinned commit:** `b61843a9a5115bb758134b6a1f5d5e502d445342` (2026-07-13)
- **Raw URL used:** `https://raw.githubusercontent.com/C2SP/wycheproof/b61843a9a5115bb758134b6a1f5d5e502d445342/testvectors_v1/ed25519_test.json`
- **Size:** 122,087 bytes
- **SHA-256:** `70471c053c711731f2195ef4875b60ea7f5d6793939d99058ac12da810cb8e00`
- **License:** Apache 2.0 (per upstream repo)
- **Coverage:** 150 test cases across 77 groups. 88 `valid`, 62 `invalid`, 0 `acceptable`. 8 flagged `SignatureMalleability` (all `invalid`).

### Why we vendor rather than fetch at test time

- Reproducibility: the same commit hash guarantees the same corpus across every CI run and every developer's machine.
- Offline / air-gapped runs: security tests must not depend on network.
- Supply-chain integrity: a compromised upstream at a random future commit cannot silently poison our test corpus. Any corpus change requires a deliberate re-pin.

### Refresh procedure

Do NOT auto-refresh. When a maintainer decides to move to a newer Wycheproof commit:

1. Pick the new upstream commit (`git rev-parse HEAD` on a checkout of `C2SP/wycheproof`, or the `sha` from the GitHub API).
2. Download to a temp file, verify the size and sha256 are what the maintainer expects, then overwrite the vendored file.
3. Update the commit hash, size, and SHA-256 in this README in the same commit as the vector update.
4. Run the full test suite. Any test that starts failing after a corpus refresh must be triaged as a real regression (behavior of the underlying `cryptography` library changed) or a real Wycheproof expansion (new class of vector added). Do NOT loosen the test to make the refresh green; the mandate is root-cause fixes only.

### What these vectors cover

- `valid` (88): well-formed Ed25519 signatures over the accompanying message with the accompanying public key. Any conformant Ed25519 verifier MUST accept these.
- `invalid` (62): signatures that MUST be rejected. Covers:
  - Wire-format defects (wrong length, wrong encoding).
  - Point-at-infinity edge cases.
  - Signatures where `S` is out of the canonical range `[0, L)` per RFC 8032 §5.1.7 — a class OpenSSL 1.1.1 accepted (bug); modern verifiers must reject to preserve non-forgeability.
  - Signatures over a modified message.
- `SignatureMalleability`-flagged (8, subset of `invalid`): specifically the RFC 8032 §5.1.7 `S`-out-of-range class. Our audit chain's tamper-evident property depends on rejecting these — a malleable Ed25519 verifier lets an attacker produce a second valid signature over the same message from any given valid signature, defeating non-repudiation.

### Failure modes these tests catch

- A future `cryptography` library upgrade that regresses to accepting malleable signatures (or any other class marked `invalid`).
- A local build that links against an older / patched libcrypto where the malleability check is stripped.
- A cryptography backend swap (e.g. moving from OpenSSL 3 to LibreSSL, BoringSSL, etc.) whose Ed25519 verifier makes different decisions than what we assume.

pytest alone catches none of these because our own signing path always produces canonical signatures; these vectors specifically probe the verifier under adversarial inputs.
