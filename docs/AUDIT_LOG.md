# Tamper-evident audit log (v0.13)

world-model-mcp v0.13 ships an opt-in tamper-evident audit log. Every fact, constraint, event, and decision that the knowledge graph persists is appended to an append-only, hash-chained log. Every 1024 entries (default), an epoch closes: a Merkle tree over the epoch's entries is signed with a hybrid Ed25519 + SLH-DSA-SHA2-128f signature and the root is persisted. A compliance auditor can then independently verify that a specific fact was recorded in a signed epoch — without trusting the operator running the server.

The design targets one specific customer segment: **regulated engineering deployments where the audit trail must be cryptographically verifiable** (fintech, healthtech, defense, insurance, gov contractors under SOC2, HIPAA, or FISMA).

If your deployment does not have that requirement, leave it off. The audit log is opt-in for a reason — it adds storage, adds one hash per write, and adds crypto dependencies. None of that is worth paying for if nobody in your stack is going to audit the log.

## Table of contents

- [Enabling the audit log](#enabling-the-audit-log)
- [What gets logged](#what-gets-logged)
- [Threat model — what the audit log prevents (and what it does not)](#threat-model)
- [Key management](#key-management)
- [Auditor workflow](#auditor-workflow)
- [Storage overhead](#storage-overhead)
- [Algorithm choices](#algorithm-choices)
- [Rollout roadmap](#rollout-roadmap)
- [FAQ](#faq)

## Enabling the audit log

Two environment variables. That is the whole configuration surface.

```bash
export WORLD_MODEL_AUDIT_LOG=on
# Optional: override the epoch entry-count threshold (default 1024).
# Smaller values close epochs faster (useful for testing and low-throughput
# deployments where you want tighter audit granularity). Larger values
# amortize the epoch-close cost over more entries.
export WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE=1024
```

Then start the world-model-mcp server as normal. On the first opt-in start, the server creates two new SQLite tables in the existing `audit.db` file — `tamper_evident_log` and `tamper_evident_epochs` — and generates a new hybrid keypair on the first epoch close. That is it. No dashboard, no signup, no external service.

Signing keys are written to `<WORLD_MODEL_DB_PATH>/keys/` with mode 0600 on the private key files and mode 0700 on the directory. If you use the default `WORLD_MODEL_DB_PATH=./.claude/world-model/`, keys land inside a directory that is already covered by the project's `.gitignore` — you will not accidentally commit them.

## What gets logged

Every write into the knowledge graph that a compliance auditor would care about:

| Kind | Trigger |
|---|---|
| `fact_create` | `create_fact` — a new fact enters the graph |
| `constraint_create` | `create_or_update_constraint` INSERT branch — a new rule is learned |
| `constraint_update` | `create_or_update_constraint` UPDATE branch — an existing rule's violation count or description changes |
| `event_create` | `create_event` — a file edit, test run, tool call, or user correction is captured |
| `decision_create` | `record_decision` — an agent-vs-human decision trace is captured |

Corrections are captured via `create_event(event_type="user_correction")` and `record_decision(decision_type="correction")` — both are covered by the two hooks above.

Each log entry carries:

- `seq` — strictly monotonic, per-DB
- `kind` — one of the values above
- `row_id` — the ID of the persisted row
- `row_hash` — SHA-256 of a canonical JSON serialization of a stable subset of the row (identity + purpose-shaped fields; deliberately excludes volatile server-side timestamps and PII-heavy free-text fields)
- `prev_hash` — the previous entry's `entry_hash`, chaining every entry back to a versioned genesis
- `entry_hash` — SHA-256 of `{prev_hash, row_hash, kind, seq, ts}` bound together

Any mutation of any field — a rewritten `row_hash`, a swapped `kind`, a fabricated `seq` — invalidates the chain from that point forward. A read-side verifier walks the log in `seq` order and catches the break.

## Threat model

**What the audit log prevents:**

- **Backdating.** An operator (or intruder with operator access) cannot claim a fact was written at time `T` when it was actually written at `T + delta` — the `entry_hash` binds the persistence timestamp into the chain, and the epoch signature binds it into the signed root.
- **Post-hoc rewriting.** An operator cannot mutate a fact that has been in the graph for weeks and have the mutation survive an audit. The chain would break at that entry; every subsequent entry's `entry_hash` would need to be recomputed too.
- **Selective deletion.** Removing an entry breaks the `seq` sequence and the chain. Removing a whole epoch breaks the `prev_epoch_root` link on the next epoch.
- **Forged epoch roots.** An operator without the private keys cannot produce a valid signature envelope. The verifier checks the envelope under the operator's published public keys.

**What the audit log deliberately does NOT prevent:**

- **Compromise of a currently-live write endpoint.** If an attacker owns the running server process, they can write malicious facts in real time — those facts get audit entries just like any other write. The audit log proves what happened, not who ought to have been allowed to write.
- **Selective non-inclusion at write time.** An operator who never records a fact at all cannot be caught by this log. Detection of unwritten facts is a different problem — out-of-band monitoring, dual-write architectures, or third-party attestation.
- **Collusion of the log operator with all monitors.** The soundness of the eventual public-transparency layer (v0.14+) assumes gossip between independent monitors, in the Certificate Transparency mold. A design where the operator, all monitors, and all clients are one entity does not benefit from the design.

The pitch is honest about scope. It is not "cryptographically prove anything about your entire system." It is "cryptographically prove that facts stored in this specific log have not been tampered with after the fact." That is what compliance auditors need to check.

## Key management

Two private keys, both stored in `<db_path>/keys/`:

- `ed25519_private.key` — 32 bytes raw
- `slh_dsa_secret.key` — 64 bytes raw

And one public artifact:

- `public_keys.json` — versioned JSON with public keys and fingerprints for external verifiers

**File modes** enforced by the code, not left to umask:

- `keys/` directory: `0700`
- Private key files: `0600`
- `public_keys.json`: `0644` (world-readable — it is the auditor's reference file)

**Rotation:** v0.13 does not ship a rotation command. Rotating means (a) generating fresh keys, (b) closing the current epoch under the OLD keys, (c) writing new keys to disk, (d) publishing the new `public_keys.json`, (e) keeping the old public keys accessible for verifying historical epochs. The design memo covers this; the CLI command lands in v0.14.

**For the SaaS layer** (post-Phase-B validation gate): keys move to KMS (AWS KMS or GCP Cloud KMS), signing goes through the KMS API, private keys never leave the HSM boundary. Public keys publish to a stable URL (e.g. an S3 bucket with object-lock enabled) with a CT-style signed statement. External witnesses gossip these roots.

## Auditor workflow

An external compliance auditor verifies a specific fact was recorded in a signed epoch by running four steps.

**Step 1 — Ask the server for a proof bundle.** Via MCP:

```
call_tool(
    name="prove_entry_inclusion",
    arguments={"row_id": "fact-uuid-of-interest"}
)
```

Response is JSON. Full shape documented in `tamper_evident.py:get_inclusion_proof`. Key fields: `row_hash`, `epoch`, `inclusion.proof` (RFC 6962 sibling hashes), `epoch_chain` (all closed epochs from genesis up to the one containing the entry).

If the response has `"error"` with `"kind": "unclosed"`, the entry is in the current unclosed backlog; retry after the next epoch closes.

**Step 2 — Load the operator's public keys.** Fetch `public_keys.json` from `<db_path>/keys/`. In the SaaS layer this will be a stable URL; in v0.13 opt-in on-prem, the auditor gets it from the operator directly.

Verify the fingerprints match what the auditor has pinned. If the fingerprints do not match, the operator has rotated keys — the auditor should confirm the rotation was legitimate before proceeding.

**Step 3 — Run the reference verifier locally.** Ships in this repo as `world_model_server.tamper_evident.verify_inclusion_bundle`; a standalone Python package and a matching TypeScript implementation ship in a separate `world-model-mcp-verifier` repo (v0.13 release).

```python
from world_model_server import tamper_evident

ok, reason = tamper_evident.verify_inclusion_bundle(
    bundle=bundle,
    ed25519_public_key=ed25519_public_key_bytes,
    slh_dsa_public_key=slh_dsa_public_key_bytes,
)
```

`ok == True` means:
- Every signature envelope in the chain verifies under the operator's public keys.
- Every `prev_epoch_root` chains correctly (or equals the versioned `EPOCH_GENESIS_ROOT` for the first).
- The Merkle inclusion proof verifies for the entry's `row_hash` at `leaf_index` against the containing epoch's `merkle_root`.

`ok == False` returns a specific reason — which epoch failed, which check failed. The auditor traces the failure back to a specific operator misbehavior.

**Step 4 — Periodic head check.** For continuous monitoring rather than one-off verification:

```
call_tool(name="get_audit_log_head", arguments={})
```

Response includes the current `head_entry_seq`, `head_epoch_seq`, `unclosed_entry_count`, and the full epoch chain. The auditor verifies every signature and every `prev_epoch_root` link, and confirms the head is advancing (an operator sitting on a growing unclosed backlog is a signal that something is wrong — either they have stopped closing epochs, or the write path is broken).

## Storage overhead

Measured on the reference implementation:

- Per log entry: ~150 bytes (SQLite row + indexes)
- Per epoch row: ~17 KB (dominated by the SLH-DSA signature; Ed25519 signature is 64 bytes)
- Per epoch on disk: `entry_count * ~150B` for entries plus one 17 KB epoch row

For a median deployment at 50 writes/day (~18,000 writes/year):
- ~2.7 MB of log entries per year
- ~18 epochs per year at default threshold
- ~300 KB of epoch metadata
- **~3 MB per project per year total**

For a large deployment at 500 writes/day (~180,000/year):
- ~27 MB of log entries
- ~180 epochs
- ~3 MB of epoch metadata
- **~30 MB per project per year**

Storage is not the constraint. If it ever becomes one, epoch pruning (retain the Merkle root + signature envelope for old epochs but drop the individual entries) is a straightforward v0.14 addition. The Merkle root binds the entries whether we keep them or not.

## Algorithm choices

**Hash function: SHA-256** — not Keccak-256, not BLAKE3. Reasons:
- FIPS 180-4 approved. Compliance-track buyers require FIPS conformance.
- Python stdlib via `hashlib`. No extra package needed. Compliance security teams may not have vetted keccak libraries.
- RFC 6962 (Certificate Transparency) uses SHA-256. Auditors already know this pattern.

**Signature primitives: Ed25519 + SLH-DSA-SHA2-128f hybrid** — both required for verification. Reasons:
- Ed25519: FIPS 186-5 (2023). Fast, small (64-byte signatures), decades of classical scrutiny. Via `cryptography.hazmat`.
- SLH-DSA-SHA2-128f: FIPS 205 (2024). Post-quantum, hash-based security assumptions, 17 KB signatures. Via `pyspx` (pure Python SPHINCS+, no C dependencies — compliance teams do not have to vet a C runtime).
- Hybrid means BOTH signatures must verify. A future quantum attack that breaks Ed25519 still leaves SLH-DSA standing; a hash-function break that compromises SLH-DSA still leaves Ed25519 standing.

**Merkle tree: RFC 6962** — leaf hash is `SHA-256(0x00 || data)`, internal node is `SHA-256(0x01 || left || right)`. Same construction Certificate Transparency uses. Auditors already know it; external verifiers can port existing tooling.

**Domain separation:** every signed message is prefixed with `world-model-mcp/audit-log/epoch-root/v1\0` before signing. Prevents cross-context signature replay. If a v2 message layout ever ships, bump the `/v1` suffix — old signatures under the v1 domain remain independently verifiable.

## Rollout roadmap

**v0.13 (this release):** opt-in via env var. Ed25519 + SLH-DSA hybrid signing. RFC 6962 Merkle inclusion proofs. MCP tools for `prove_entry_inclusion` and `get_audit_log_head`. Reference verifier in Python + TypeScript.

**v0.14 (planned):** rotation CLI. Time-based epoch close (in addition to size-based). Publishing epoch roots to an external witness (a static file on GitHub Pages, S3 with object-lock, or a Sigstore-style Rekor entry). External monitor scripts that periodically fetch and verify.

**SaaS layer (post-Phase-B validation gate):** KMS-held keys. Public transparency-log witnessing infrastructure. Multi-tenant isolation. Compliance-track pilot customer onboarding.

## FAQ

**Does enabling the audit log slow down the write path?**

Per fact: one additional SHA-256 hash, one SQLite INSERT into `tamper_evident_log`. Measured at <200 microseconds on the reference implementation. Not observable in end-to-end latency. When an epoch closes, the Merkle tree computation is ~200 ms for a 1024-entry epoch, then the hybrid signature (Ed25519 + SLH-DSA) takes ~50 ms combined. That happens once per 1024 writes on average.

**Can I opt out after enabling?**

Yes. Unset `WORLD_MODEL_AUDIT_LOG` and restart. The log tables remain in `audit.db` for historical audit but no new entries are appended. If you want to remove the log entirely, drop the two tables and the `keys/` directory.

**What if a private key file is corrupted or deleted?**

The next epoch-close attempt will regenerate keys, breaking the epoch chain — the new epoch's signature will use new keys that the auditor's pinned fingerprints will not recognize. Detection is immediate on the auditor side; recovery is manual (restore the keys from backup, or accept the rotation as legitimate by publishing new fingerprints).

**Is v0.13 SOC2-ready today?**

The audit log by itself is not a SOC2 certification. It is a technical primitive that a SOC2 auditor can use to check specific claims about your system's memory layer. SOC2 Type II certification is your organization's responsibility and takes 6-18 months. What the audit log gives you is one specific, verifiable technical claim to check in that process.

**Does the SaaS layer exist yet?**

No. The OSS server ships this technical foundation; the hosted layer with KMS-held keys and public transparency-log witnessing is planned post-Phase-B validation gate. See the SaaS build plan in `project-saas-build-plan` for the gate criteria and timeline.

**Can I use the audit log without the coding-agent memory features?**

Technically yes — the audit-log tables and code are independent of the fact/constraint/event schema. But the write-path integration is bound to the four durable-write methods (`create_fact`, `create_or_update_constraint`, `create_event`, `record_decision`). If your use case is "audit trail for a different data model," the tamper-evident primitive in `world_model_server/tamper_evident.py` is reusable but you would need to wire your own write paths.

**What compliance regimes does this help satisfy?**

The audit log is designed to help satisfy the audit-trail requirements of SOC2 (specifically CC7.2 System Monitoring and CC8.1 Change Management), HIPAA (§164.312(b) Audit Controls), and FISMA (AU-2 Audit Events, AU-10 Non-repudiation). It does NOT by itself satisfy any of these regimes — it is a technical control that an auditor evaluates as part of a broader compliance program.
