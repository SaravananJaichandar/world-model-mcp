# World Model MCP

**Persistent memory + optional signed audit for AI coding agents.**

`world-model-mcp` ships a local SQLite knowledge graph your agent queries every turn: hallucinations become verifiable, corrections stick across sessions, and regressions get caught before they land. Flip on the audit chain and every event is signed with FIPS 205 hybrid Ed25519 + SLH-DSA, verifiable offline forever. MIT-licensed, runs entirely local, works with 10+ AI coding agents including Claude Code, Cursor, Codex, Continue, Cline, Windsurf, GitHub Copilot Chat, pi, OpenClaw, and Hermes Agent.

> **Latest: v0.15.5.** Streaming offline reference verifier (`ijson`-based, O(single row) memory), FIPS 205 SLH-DSA known-answer tests, adversarial-input fuzz coverage on the verify path. Full version history in [CHANGELOG.md](CHANGELOG.md).

[![PyPI](https://img.shields.io/pypi/v/world-model-mcp.svg)](https://pypi.org/project/world-model-mcp/)
[![Downloads](https://img.shields.io/pypi/dm/world-model-mcp.svg)](https://pypi.org/project/world-model-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![world-model-mcp MCP server](https://glama.ai/mcp/servers/SaravananJaichandar/world-model-mcp/badges/card.svg)](https://glama.ai/mcp/servers/SaravananJaichandar/world-model-mcp)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20834508.svg)](https://doi.org/10.5281/zenodo.20834508)

mcp-name: io.github.SaravananJaichandar/world-model-mcp

---

## Compared to other agent-memory + agent-audit projects

Head-to-head positioning against eight named peers on the audit / signing / anchoring dimensions the agent-memory space is converging on. Every `world-model-mcp` cell carries a provenance flag: `own` = measured / observed in the shipped product; `cited` = pulled from the competitor's own public landing, repo, or press.

Source of truth for this table lives in the hosted service repo: [world-model-mcp-hosted/src/etch/competitor_matrix.py](https://github.com/SaravananJaichandar/etch/blob/main/src/etch/competitor_matrix.py). Update in both places if you change either.

| | world-model-mcp (this repo) + Etch (hosted) | Mem0 | Letta | agentmemory | Unicity AOS | Repowise | Trinitite | Caura | FailproofAI |
|---|---|---|---|---|---|---|---|---|---|
| Signature scheme | **Ed25519 + SLH-DSA-SHA2-128f (hybrid)** `[own]` | None (encryption at rest only) | None | None | BLAKE3 hash-chain | Deterministic signals (no signing) | Signed + hash-chained (algo not disclosed) | None | None disclosed |
| Post-quantum ready | **Yes (SLH-DSA, NIST FIPS 205)** `[own]` | No | No | No | No (BLAKE3 hash only) | No | No disclosed | No | No |
| Offline reference verifier | **etch-verify CLI, streaming, ships with PyPI package** `[own]` | No | No | No | Not disclosed | No (SaaS-only) | Browser-based verifier | No | No |
| Attestation cadence enforcement | **Scheduled systemd timer + on-demand verify** `[own]` | No | No | No | Not disclosed | No | Scheduled attestation runs | No | No |
| Framework mapping (Article / control IDs) | **EU AI Act Art. 12-15, SOC 2 CC6.6/7.2/7.3, ISO 27001 A.12.4/A.14.2** `[own]` | SOC 2 + HIPAA (badges, no per-control mapping) | Not disclosed | No | Not disclosed | EU AI Act (negative-space claim) | Per-Article citations (EU AI Act, SOC 2, SR 11-7) | SOC 2 in-progress badge | SOC 2 enterprise tier only |
| Drift detection | **Per-epoch chain integrity + attestation trend** `[own]` | No | Success-rate + error tracking | No | Not disclosed | Code-health score deltas | Deterministic replay + risk-score deltas | No | Evaluator score deltas |
| Annotation support (signed human notes in-chain) | **pin_annotation MCP tool, signed into same chain** `[own]` | No | No | No | Not disclosed | No | Not disclosed | No | No |
| Browser verifier | **Yes, chain-integrity widget on /auditor/&lt;slug&gt;** `[own]` | No | No | No | Not disclosed | No | Yes | No | No |
| Share link (auditor access, no login) | **Yes, expiring share tokens (bs_ prefix, 30d max)** `[own]` | No | No | No | Not disclosed | No | Yes, expiring auditor access | No | No |
| External anchor (independent witness log) | **Dual: Sigstore Rekor + Bitcoin OpenTimestamps (public, opt-out per project)** `[own]` | No | No | No | Internal hash-chain only (no external log) | No | Not disclosed | No | No |
| OSS license | **MIT (world-model-mcp)** `[own]` | Apache 2.0 | Apache 2.0 | Apache 2.0 | Not disclosed | AGPL v3 (core) | No OSS | Apache 2.0 | No OSS |
| GitHub stars | Snapshot via etch.systems/api/oss-stats `[cited]` | 61.6k `[cited]` | 23.9k `[cited]` | 24.9k `[cited]` | 7.1k `[cited]` | 4.2k `[cited]` | No public repo | 373 `[cited]` | No public repo |
| Public funding | **Bootstrapped** `[own]` | $24.5M `[cited]` | $10M `[cited]` | Not disclosed | $3M seed Feb 2026 `[cited]` | Not disclosed | Not disclosed | Not disclosed | Not disclosed |
| Compliance posture (as claimed on landing) | **SOC 2 Type I in progress (Aug 2026 target)** `[own]` | SOC 2 + HIPAA (badges on trust subdomain) | Not disclosed | No compliance posture | No compliance posture | No SOC 2; EU AI Act negative-space claim | Per-Article compliance framing | SOC 2 in-progress badge | SOC 2 enterprise-tier |

**How to read this table:**
- Bold cells describe mechanisms shipped in this repo (OSS) or the hosted service (etch.systems).
- Peer cells are cited from each competitor's public landing / repo / press. Not our own measurement.
- `[own]` on a `world-model-mcp` cell means we measured / observed the mechanism ourselves. `[cited]` means the value came from a third-party source.
- Only the audit / signing / anchoring dimensions are in this table. General memory features (retrieval accuracy, adapter breadth, LLM support) are covered in the [Features](#features) section below.

---

## Numbers

| Benchmark | Score | Details |
|---|---|---|
| [SWE-bench Verified repeat-mistake](https://github.com/SaravananJaichandar/coding-agent-memory-benchmark) | **+10.2 pts** (67.3% → 77.6% on 49 paired instances) | Pre-registered, Claude Code 2.1.177 headless, Zenodo DOI [10.5281/zenodo.21076824](https://doi.org/10.5281/zenodo.21076824). Within-domain +15.0 pts, cross-domain +6.9 pts with zero regressions. Multi-seed appendix documents single-trial upper bound honestly. |
| [Contradiction-resolution](benchmarks/contradictions-200/RESULTS.md) | **100.0%** on `auto` strategy | 105 pairs × 19 categories, deterministic (no LLM). Shipped since v0.11.0. |
| [Coach-Player verification](benchmarks/coach-player/) | **100.0%** exact match | 12 hand-labeled pairs (4 grounded, 4 partial, 4 hallucinated). Layer 3 adversarial verification via independent Coach LLM. Shipped since v0.12.12. |

The SWE-bench number is the load-bearing empirical claim. The other two are internal correctness benchmarks for shipped components. Reproducibility scripts in each benchmark directory or the linked repo.

---

## Testing

**1,494 unit + integration + fuzz tests** across the shipped codebase. Coverage floor gated at 71% / 66% (two-tier for CI runners with and without SLH-DSA in liboqs).

```bash
# Run tests
pytest -q

# With coverage
pytest --cov=world_model_server --cov-report=term-missing

# Fuzz targets (Atheris, requires Clang / libFuzzer)
python fuzz/fuzz_verify_manifest.py fuzz/corpus/ -max_total_time=60

# Non-Atheris smoke fuzz (runs in every CI pass, no system deps)
pytest tests/test_fuzz_smoke_verify.py -q
```

Selected test suites worth calling out:
- **FIPS 205 SLH-DSA known-answer tests** (`tests/test_fips_205_slh_dsa_kat.py`): locks parameter sizes for SLH-DSA-SHA2-128f (public key = 32 bytes, secret key = 64 bytes, signature = 17,088 bytes), sign/verify round-trip with wrong-key + wrong-message + mutated-signature rejection, plus a fixed KAT-vector fixture (`tests/fixtures/slh_dsa_kat_vectors.json`) that must verify true forever.
- **Streaming verifier byte-parity** (`tests/test_etch_verify_streaming.py`): locks byte-identical output between in-memory and streaming exporters so an auditor hashing the manifest as an artifact of record gets the same `manifest_sha256` regardless of which exporter the operator used.
- **Contradiction benchmark** (`benchmarks/contradictions-200/`): 105 pairs × 19 categories, deterministic. Locks the `auto` strategy at 100% on every commit.

CI gates coverage floor + full test suite on every push. See [.github/workflows/pytest.yml](.github/workflows/pytest.yml).

---

## Authenticated audit chain (v0.13+, opt-in)

For deployments where the audit trail must be cryptographically verifiable (SOC 2, HIPAA, EU AI Act, or your own internal control list):

```bash
export WORLD_MODEL_AUDIT_LOG=on
# then start the world-model-mcp server as normal
```

On the first opt-in start, the server creates two new SQLite tables in the existing `audit.db` file (`tamper_evident_log` and `tamper_evident_epochs`) and generates a new hybrid keypair on the first epoch close. Every subsequent event is appended to a SHA-256 Merkle chain. When an epoch closes (default: 1024 events), the chain root gets signed with a hybrid **Ed25519 + SLH-DSA-SHA2-128f** envelope: classical + post-quantum, so a hypothetical break of elliptic-curve cryptography leaves the audit trail intact.

- **Verifiable offline**, forever. `etch-verify` CLI ships with the PyPI package; auditors run it on their laptop, no network access needed after the initial download.
- **Signatures prove authorship**, not just order. Hash-chain alternatives can prove nothing was reordered, but cannot prove WHO signed. This chain answers both.
- **No dashboard, no signup, no external service**. Runs entirely in your process against local SQLite.

Full write-up in [docs/AUDIT_LOG.md](docs/AUDIT_LOG.md).

For a hosted version that adds KMS-backed keys, a public transparency log, external anchoring to Sigstore Rekor + Bitcoin OpenTimestamps, and a compliance-facing operator dashboard, see the [Etch companion](#hosted-companion-etch) below.

---

## Quick Start

Three most-common install paths. For every other supported client (Cursor, Cline, Codex, Continue, Copilot, Windsurf, Goose, pi, OpenClaw, Hermes, and more), see [etch.systems/docs/install](https://etch.systems/docs/install).

### Option 1: Claude Desktop (one-click)

Download the latest `.mcpb` from [Releases](https://github.com/SaravananJaichandar/world-model-mcp/releases/latest) and drag it into Claude Desktop. Auto-installs hooks, MCP server config, and dependencies.

### Option 2: Claude Code / IDE plugins (pip install)

```bash
# 1. Install the package
pip install world-model-mcp

# 2. Set up in your project (auto-seeds the knowledge graph from existing code)
cd /path/to/your/project
python -m world_model_server.cli setup

# 3. Restart Claude Code
# Done. The world model is pre-populated and active.
```

**Next (optional):** turn the local signed audit log into an auditor-verifiable chain with the hosted [**Etch**](https://etch.systems) notary — KMS-backed keys, public transparency log, external anchoring, and a share link for auditors, with no infra to run. See [Hosted companion: Etch](#hosted-companion-etch).

### Option 3: HTTP transport for remote / MCP-tunnel deployment

```bash
pip install 'world-model-mcp[http]'
python -m world_model_server.server --transport http --port 8000
```

Exposes MCP over Streamable HTTP so remote agents can connect over the wire. See [docs/http_transport.md](docs/http_transport.md) for auth, CORS, and reverse-proxy setup.

### Other clients

Install via the OSS CLI. Each command writes the correct config for that client (defaults to `sys.executable` as the interpreter path, per-client-format-aware, safe against overwrite via `--force` and `--dry-run` flags):

```bash
python -m world_model_server.cli install-cursor     # Cursor
python -m world_model_server.cli install-cline      # Cline
python -m world_model_server.cli install-codex      # Codex
python -m world_model_server.cli install-continue   # Continue (also --global)
python -m world_model_server.cli install-copilot    # GitHub Copilot Chat (VS Code Insider)
python -m world_model_server.cli install-windsurf   # Windsurf
python -m world_model_server.cli install-pi         # pi
python -m world_model_server.cli install-openclaw   # OpenClaw
python -m world_model_server.cli install-hermes     # Hermes (MCP mode)
python -m world_model_server.cli install-hermes-provider  # Hermes (Elixir-native provider)
```

Full per-client walkthroughs (with verify commands + troubleshooting) at [etch.systems/docs/install](https://etch.systems/docs/install).

---

## What it does

`world-model-mcp` is a temporal knowledge graph that sits between your AI coding agent and its work. It records facts, entities, and constraints from your codebase; validates every code change against learned constraints at the edit boundary; re-injects relevant context after context-window compaction; tracks contradictions with confidence-weighted resolution; and adversarially verifies retrievals via an independent Coach LLM.

### Features

**1. Hallucination prevention.** Every fact recorded carries provenance (`asserted_by`, `confirmer`, `confirmation_state`, `evidence_type`). When the agent queries a fact, it gets the confidence score along with the answer. When two facts contradict, the newer/higher-confidence one wins; the loser is retained with `superseded_by`. The agent never has to guess whether a stored fact is still current.

**2. Learning from corrections.** When you correct an agent (`record_correction`), the correction is stored as a first-class event with the entities involved. Next time the agent queries anything touching those entities, the correction surfaces first. Corrections stick across sessions, across context compactions, across agent restarts.

**3. Regression prevention.** Every code-change proposal runs through `validate_change`, which walks the constraint graph and returns violations before the edit lands. Constraints are learned automatically from your codebase (`seed_project`), reinforced by PR review comments (`ingest_pr_reviews`), and hand-authored (`record_event`). The agent sees the violation, the suggestion, and the source of the constraint.

**4. Coach-Player adversarial verification.** A Player agent drafts a decision. An independent Coach agent independently requeries the graph for precedent, walks the Merkle proof, checks the hybrid signature, and only then approves. The Coach never trusts the Player's summary; it reverifies against a signed ledger every time. 100% exact match on 12 hand-labeled pairs.

Full technical architecture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## MCP Tools

Eight tools shipped. One-line summary; click each for signature + examples in `docs/mcp/`.

| Tool | What it does |
|---|---|
| `query_fact` | Fetch a stored fact with provenance + confidence + source |
| `record_event` | Append an event to the audit chain (`event_type` from a fixed enum) |
| `validate_change` | Check a code-change proposal against learned constraints, returns violations + suggestions |
| `get_constraints` | List all constraints matching an entity or file pattern |
| `record_correction` | Store a user correction so it re-surfaces on next relevant query |
| `get_related_bugs` | Fetch bugs touching the given files + a per-file risk score |
| `seed_project` | Bulk-ingest an existing codebase into the knowledge graph |
| `ingest_pr_reviews` | Turn recent PR review comments into constraints |

Full tool documentation at [docs/mcp/](docs/mcp/).

---

## Hosted companion: Etch

Running `world-model-mcp` locally? [**Etch** (etch.systems)](https://etch.systems) is the hosted governance plane built on the same OSS core, adding what a compliance team needs to sign off on production use:

- KMS-encrypted signing keys (never plaintext at rest)
- Public transparency log with signed head (split-view resistance)
- External anchoring to Sigstore Rekor + Bitcoin OpenTimestamps (dual independent witness)
- Operator dashboard with session traces, chain integrity view, PII scanning, and a client-answer PDF export
- Standalone auditor CLI + browser verifier at `/auditor/<slug>`
- Free tier, pay-as-you-grow beyond that

Same crypto primitives, same audit-log schema, zero source changes to the shipped OSS. Skip if you're running standalone.

---

## How it works

`world-model-mcp` is an MCP server (stdio or Streamable HTTP) that exposes a temporal knowledge graph backed by SQLite. Every agent turn queries the graph; every code change or correction writes back. On startup the server auto-seeds from your existing codebase; on shutdown it flushes cleanly.

Six databases under `.claude/world-model/`:
- `entities.db`: files, functions, classes, symbols
- `facts.db`: semantic facts with provenance + confidence
- `relationships.db`: dependencies, calls, imports
- `constraints.db`: learned rules the agent must respect
- `sessions.db`: per-session context tracking
- `events.db`: immutable event log (audit chain when opted in)

Full architecture with diagrams in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Configuration

Environment variables (all optional):

| Variable | Purpose | Default |
|---|---|---|
| `WORLD_MODEL_AUDIT_LOG` | Enable the signed audit chain | `off` |
| `WORLD_MODEL_DB_PATH` | Override the SQLite databases directory | `.claude/world-model` |
| `WORLD_MODEL_TELEMETRY` | Opt in to anonymous OSS telemetry (see Privacy) | `off` |
| `ANTHROPIC_API_KEY` | Only used if you enable optional LLM-backed features | none |

Full configuration reference in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

---

## Privacy and Security

- **Telemetry is off by default.** If you opt in with `WORLD_MODEL_TELEMETRY=on`, aggregated install-level metrics ship to `etch.systems/api/telemetry/ingest` (endpoint URL, no source code, no prompts, no PII). Right-to-erasure supported via `DELETE /api/telemetry/install/{install_id}`.
- **No `ANTHROPIC_API_KEY` is required for core operation.** Some optional features (Coach-Player Layer 3 verification, LLM-backed reranking) use the API if a key is provided. Without it, everything else works.
- **Report a security issue:** email [security@etch.systems](mailto:security@etch.systems). PGP key on request.

Full write-up in [docs/PRIVACY.md](docs/PRIVACY.md).

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) for development setup, coding standards, adding language support, writing tests, and submitting PRs.

Areas where help is especially wanted:
- Language parsers (Go, Rust, Java, C++)
- Additional MCP-client adapters
- Framework integrations (LangGraph, CrewAI, AutoGen, LlamaIndex; starter shims exist in the hosted repo)
- Benchmark contributions in `benchmarks/`

Please read [CLA.md](./CLA.md) before your first PR.

---

## License

[MIT License](./LICENSE). Free for commercial and personal use.

---

## Links

- **Full version history:** [CHANGELOG.md](CHANGELOG.md)
- **Documentation:** [docs/](docs/)
- **Hosted service:** [etch.systems](https://etch.systems)
- **Benchmarks:** [benchmarks/](benchmarks/) + [coding-agent-memory-benchmark](https://github.com/SaravananJaichandar/coding-agent-memory-benchmark)
- **Support:** [support@etch.systems](mailto:support@etch.systems)
- **Zenodo (formal citation):** [DOI 10.5281/zenodo.20834508](https://doi.org/10.5281/zenodo.20834508)
