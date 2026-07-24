# World Model MCP

**Coding agents remain blind to the codebase they operate on.** They infer structure late, reduce it to prompts, and ignore it when decisions are made in real time — repeating the same mistakes, hallucinating APIs that don't exist, and forgetting learned constraints the moment context compacts.

**World Model MCP is the memory-graph infrastructure that closes that gap.** A temporal knowledge graph that validates code changes against learned constraints at the edit boundary, re-injects relevant context after compaction, tracks contradictions with confidence-weighted resolution, adversarially verifies retrievals via an independent Coach LLM, and runs across Claude Code, Cursor, Codex, pi, OpenClaw, Hermes Agent, Continue, GitHub Copilot Chat, Cline, and Windsurf.

> **Latest: v0.15.5** — Streaming offline reference verifier (`ijson`-based, O(single row) memory), FIPS 205 SLH-DSA known-answer tests, and adversarial-input fuzz coverage on the verify path. `etch-verify` CLI streams by default. See [CHANGELOG.md](CHANGELOG.md) for full version history.

[![PyPI](https://img.shields.io/pypi/v/world-model-mcp.svg)](https://pypi.org/project/world-model-mcp/)
[![Downloads](https://img.shields.io/pypi/dm/world-model-mcp.svg)](https://pypi.org/project/world-model-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![world-model-mcp MCP server](https://glama.ai/mcp/servers/SaravananJaichandar/world-model-mcp/badges/card.svg)](https://glama.ai/mcp/servers/SaravananJaichandar/world-model-mcp)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20834508.svg)](https://doi.org/10.5281/zenodo.20834508)

mcp-name: io.github.SaravananJaichandar/world-model-mcp

## Hosted companion: Etch

world-model-mcp is the OSS memory + **authenticated audit chain** you can run locally. **[Etch (etch.systems)](https://etch.systems)** is the hosted companion — cryptographic notary + governance plane for AI agent decisions, built on this OSS core. Same crypto primitives (hybrid Ed25519 + SLH-DSA-SHA2-128f Merkle chain — signatures prove *authorship* and *non-repudiation*, not just order and integrity), same audit-log schema, additional hosted-only features: PII scan, secret detection, session narrative overlays, client-answer PDF export, and multi-tenant per-project stores.

You can run world-model-mcp entirely offline. Etch is optional and only used if you opt into the hosted service (signup gated) or opt into anonymous telemetry (off by default, inspectable payload, right-to-erasure supported).

---

## Numbers

| Benchmark | Score | Details |
|---|---|---|
| [SWE-bench Verified repeat-mistake](https://github.com/SaravananJaichandar/coding-agent-memory-benchmark) | **+10.2 pts** (67.3% → 77.6% on 49 paired instances) | Pre-registered, Claude Code 2.1.177 headless, Zenodo DOI [10.5281/zenodo.21076824](https://doi.org/10.5281/zenodo.21076824). Within-domain +15.0 pts, cross-domain +6.9 pts with zero regressions. Multi-seed appendix documents single-trial upper bound honestly. |
| [Contradiction-resolution](benchmarks/contradictions-200/RESULTS.md) | **100.0%** on `auto` strategy | 105 pairs × 19 categories, deterministic (no LLM). Shipped since v0.11.0. |
| [Coach-Player verification](benchmarks/coach-player/) | **100.0%** exact match | 12 hand-labeled pairs (4 grounded, 4 partial, 4 hallucinated). Layer 3 adversarial verification via independent Coach LLM. Shipped since v0.12.12. |

The SWE-bench number is the load-bearing empirical claim. The other two are internal correctness benchmarks for shipped components. Reproducibility scripts in each benchmark directory or the linked repo.

## Authenticated audit chain (v0.13, opt-in)

For compliance-track deployments where the audit trail must be cryptographically verifiable (SOC2, HIPAA, FISMA):

```bash
export WORLD_MODEL_AUDIT_LOG=on
world-model  # start server as usual
```

Every fact, constraint, event, and decision write chains into an append-only log. Every 1024 entries (env-tunable), an epoch closes with a Merkle root signed by a hybrid **Ed25519 + SLH-DSA-SHA2-128f** signature (both FIPS-approved; both required for verification). Compliance auditors call `prove_entry_inclusion(row_id)` via MCP, load the operator's public keys from `<db_path>/keys/public_keys.json`, and run the reference verifier locally — no round trip needed for verification.

- Full threat model, key management, auditor workflow: [docs/AUDIT_LOG.md](docs/AUDIT_LOG.md)
- Reference verifier (Python + TypeScript): `world-model-mcp-verifier` repo
- Storage overhead: ~3 MB per project per year for a median deployment
- Non-opt-in path is unchanged: no schema, no keys, no crypto imports if `WORLD_MODEL_AUDIT_LOG` is unset

The audit log is deliberately opt-in. If your deployment does not have a cryptographic-audit requirement, leave it off — the log adds storage, one hash per write, and crypto dependencies. None of that is worth paying for if nobody in your stack is going to audit the log.

> If world-model-mcp helped you, star the repo or open an issue with what worked or didn't. I read every one and the feedback shapes what ships next.

---

## What It Does

World Model MCP creates a **temporal knowledge graph** of your codebase that learns from every coding session to:

- **Prevent Hallucinations** -- Validates API/function references against known entities before use
- **Stop Repeated Mistakes** -- Learns constraints from corrections, applies them in future sessions
- **Reduce Regressions** -- Tracks bug fixes and warns when changes touch critical regions
- **Survive Compaction** -- Re-injects top constraints and recent facts after the agent's context window resets
- **Resolve Contradictions** -- Picks a winner between conflicting facts using confidence, recency, or source count

Think of it as a long-term memory layer that runs alongside Claude Code, Cursor, Codex, pi, OpenClaw, Hermes Agent, Continue, GitHub Copilot Chat, Cline, Windsurf, or any MCP-aware coding agent.

---

## See it working

Three cloneable starter repos show world-model-mcp wired into a real Python (FastAPI + SQLAlchemy) project across the three highest-adoption MCP runtimes. Each ships 5 seeded constraints, 1 bug-fix reflection, and a `WHAT_TO_TRY.md` with concrete workflows. Fork one, `pip install`, and see the memory layer catch a constraint violation on the first edit.

| Starter | Runtime | Config shape | Automatic enforcement |
| --- | --- | --- | --- |
| [world-model-mcp-claude-code-starter](https://github.com/SaravananJaichandar/world-model-mcp-claude-code-starter) | Claude Code CLI | `.mcp.json` + `.claude/settings.json` | Yes (4 lifecycle hook events) |
| [world-model-mcp-cursor-starter](https://github.com/SaravananJaichandar/world-model-mcp-cursor-starter) | Cursor Editor | `.cursor/mcp.json` + `.cursor/hooks.json` | Yes (3 lifecycle hook events) |
| [world-model-mcp-copilot-chat-starter](https://github.com/SaravananJaichandar/world-model-mcp-copilot-chat-starter) | VS Code + Copilot Chat | `.vscode/mcp.json` (`"servers"` key, not `"mcpServers"`) | No — Copilot Chat lacks lifecycle hooks; memory queryable via MCP tool calls only |

All three point at the same `.claude/world-model/` DB path, so installing multiple starters (or all three) on one repo produces a shared fact graph across runtimes.

---

See **[CHANGELOG.md](CHANGELOG.md)** for the full version history (v0.7.0 through v0.15.5).

---

## Quick Start

### Option 1: Desktop Extension (one-click for Claude Desktop)

Download the latest `.mcpb` from [Releases](https://github.com/SaravananJaichandar/world-model-mcp/releases/latest) and drag it into Claude Desktop. Auto-installs hooks, MCP server config, and dependencies.

### Option 2: pip install (Claude Code CLI / IDE plugins)

```bash
# 1. Install the package
pip install world-model-mcp

# 2. Setup in your project (auto-seeds the knowledge graph from existing code)
cd /path/to/your/project
python -m world_model_server.cli setup

# 3. Restart Claude Code
# Done! The world model is pre-populated and active
```

You can also re-seed or seed manually at any time:

```bash
# Seed from existing codebase
world-model seed

# Re-seed with force (re-processes already seeded files)
world-model seed --force
```

### Option 3: HTTP transport for remote / MCP-tunnel deployment

For Claude Managed Agents with self-hosted sandboxes, or any deployment where
the MCP server lives behind a firewall and the agent reaches it from
Anthropic-side infrastructure, run world-model-mcp in HTTP mode.

```bash
pip install 'world-model-mcp[http]'

export WORLD_MODEL_TRANSPORT=http
export WORLD_MODEL_HTTP_PORT=8765
python -m world_model_server.server
```

Or use the bundled image:

```bash
docker compose up -d                    # Dockerfile.http + persistent volume
curl http://127.0.0.1:8765/healthz      # {"status":"ok","version":"0.7.2"}
```

Full walkthrough including Anthropic MCP tunnels setup:
[docs/deployment/mcp-tunnel.md](docs/deployment/mcp-tunnel.md).

Stdio remains the default transport for Claude Code, Cursor, and `.mcpb`
installs. Nothing changes for those flows.

### Option 4: Run the guided demo (no Claude Code required)

To see every primitive working with real outputs from a real SQLite database before committing to a full install:

```bash
pip install world-model-mcp
cd /tmp/wm-test && mkdir -p wm-test && cd wm-test
world-model demo
```

The demo initializes a knowledge graph, seeds reproducible data, and exercises PreToolUse enforcement, contradiction detection, the PostCompact injection bundle, and the compaction audit log -- with the actual JSON outputs. Re-runs are idempotent.

### Option 5: Run inside pi (experimental)

For users of [earendil-works/pi](https://github.com/earendil-works/pi):

```bash
pip install world-model-mcp           # the Python helpers
world-model install-pi                # writes adapters/world-model-pi/
pi install local:./adapters/world-model-pi
```

The pi adapter wires the same `hook_helper` and `inject_helper` you'd use from Claude Code into pi's `tool_call`, `context`, and `session_compact` events. See [adapters/pi/README.md](adapters/pi/README.md).

### Option 6: Run inside Codex CLI (experimental)

For users of OpenAI's [Codex CLI](https://github.com/openai/codex):

```bash
pip install world-model-mcp                # the Python helpers
python -m world_model_server.cli install-codex
# (appends [mcp_servers.world_model] + hook blocks to ~/.codex/config.toml)
# Restart codex; verify with: codex mcp list
```

`--dry-run` prints what would be appended without writing; `--force` re-appends even if the adapter marker is already present. The bundled snippet uses `world_model` (underscore) as the MCP server name to dodge Codex's silent hyphen-strip in its tool-name sanitizer. Hook output is camelCase with `deny_unknown_fields` compliance against Codex's strict Rust schema; the contract is locked down by tests in `tests/test_v075_features.py`. See [adapters/codex/README.md](adapters/codex/README.md).

### Option 7: Run inside OpenClaw (experimental, v0.10)

For users of [OpenClaw](https://github.com/openclaw/openclaw), the local-first personal AI assistant that routes across WhatsApp, Telegram, Slack, and Discord:

```bash
pip install world-model-mcp
python -m world_model_server.cli setup
python -m world_model_server.cli install-openclaw
# Verify: openclaw mcp probe world-model  (should report 27 tools)
```

`install-openclaw` merges an `mcp.servers.world-model` entry into `~/.openclaw/openclaw.json` while preserving all other keys in the config file. It defaults the `command` field to `sys.executable` (absolute path to the interpreter running the CLI) — necessary because OpenClaw's process spawn does not inherit shell PATH; a bare `python3` fails probe with `MCP error -32000: Connection closed`. Flags: `--force` (overwrite existing entry), `--dry-run` (print without writing), `--python <abs-path>` (override interpreter), `--db-path <path>` (override `WORLD_MODEL_DB_PATH`, default `.claude/world-model`). Relative `--python` values are rejected as a hard error.

Pure additive integration — OpenClaw ships no native memory layer, so all 27 world-model tools become available to OpenClaw agent turns without capability overlap. Verified end-to-end against OpenClaw `2026.6.11 (e085fa1)` on macOS on 2026-07-01. MCP-registration only in v0.10; a TypeScript plugin bundle for typed lifecycle hooks (`before_prompt_build`, `before_tool_call`, `before_compaction`, `session_start`, ...) is on the v0.10.x roadmap. See [adapters/openclaw/README.md](adapters/openclaw/README.md).

### Option 8: Run inside Hermes Agent (experimental, v0.10)

For users of NousResearch's [Hermes Agent](https://github.com/NousResearch/hermes-agent):

```bash
pip install "world-model-mcp[hermes]"          # the [hermes] extra pulls ruamel.yaml
python -m world_model_server.cli setup
python -m world_model_server.cli install-hermes
# From inside a Hermes session: /reload-mcp   (loads the new server without restarting)
```

`install-hermes` merges an `mcp_servers.world-model` block into `~/.hermes/config.yaml` while preserving all other keys — including every comment and blank line in Hermes' heavily-commented 1327-line reference config, via `ruamel.yaml` round-trip mode. Defaults the `command` field to `sys.executable` (absolute path). Flags: `--force`, `--dry-run`, `--python <abs-path>`, `--db-path <path>`. Relative `--python` values are rejected as a hard error.

Hermes ships its own bounded memory system (`MEMORY.md` + `USER.md`, character-capped, no auto-decay per Hermes docs). world-model-mcp adds the temporal fact graph with per-fact provenance, per-evidence-type decay, and confidence-weighted contradiction resolution on top — additive, not overlapping. The overlap with the exclusive `MemoryProvider` plugin slot (currently held by ClawMem for many users) is documented in [adapters/hermes/README.md](adapters/hermes/README.md). Verified end-to-end against Hermes v0.17.0 (2026.6.19) on macOS: `hermes mcp test world-model` reports 27 tools. MCP-registration is the v0.10 track; a native `MemoryProvider` plugin is on the v0.10+ roadmap and ships only if MCP-route adoption warrants.

### Option 9: Run inside Continue (experimental, v0.10)

For users of [Continue](https://github.com/continuedev/continue), the OSS coding-agent extension for VS Code and JetBrains (largest OSS coding-agent extension not tied to a platform vendor — reprioritized after the SpaceX/Cursor acquisition):

```bash
pip install world-model-mcp
python -m world_model_server.cli setup
python -m world_model_server.cli install-continue
# Reload the Continue extension. In agent mode, world-model tools appear under the "world-model" server.
```

`install-continue` writes a standalone `<project>/.continue/mcpServers/world-model.yaml` following Continue's per-server-file pattern. No config merge is needed because Continue's own docs use one YAML per MCP server in that directory. Defaults the `command` field to `sys.executable` (absolute path); rejects relative `--python` overrides. Flags: `--force`, `--dry-run`, `--project-dir <path>`, `--python <abs-path>`, `--db-path <path>`. Continue watches `.continue/mcpServers/` in newer builds, so auto-discovery should pick up the new server; if not, reload the extension. MCP tools are available only in Continue's agent mode. See [adapters/continue/README.md](adapters/continue/README.md).

### What Gets Installed

```
your-project/
├── .mcp.json                    # MCP server configuration
├── .claude/
│   ├── settings.json           # Hook configuration
│   ├── hooks/                  # Compiled TypeScript hooks
│   └── world-model/            # SQLite databases (~155 KB)
```

---

## Features

### 1. Hallucination Prevention

Before:
```typescript
// Claude invents an API that doesn't exist
const user = await User.findByEmail(email); // This method doesn't exist
```

After:
```typescript
// Claude checks the world model first
const user = await User.findOne({ email }); // Verified to exist
```

**Goal**: Reduce non-existent API references by validating against the knowledge graph

### 2. Learning from Corrections

**Session 1**: User corrects Claude
```typescript
// Claude writes:
console.log('debug info');

// User corrects to:
logger.debug('debug info');

// World model learns: "Use logger.debug() not console.log()"
```

**Session 2**: Claude uses the learned pattern
```typescript
// Claude automatically writes:
logger.debug('debug info'); // No correction needed
```

**Goal**: Learned patterns persist across sessions and prevent repeat violations

### 3. Regression Prevention

```typescript
// Week 1: Bug fixed (null check added)
if (user && user.email) { ... }

// Week 2: Refactoring
// World model warns: "This line preserves a critical bug fix"
// Claude preserves the null check

// Result: Bug not re-introduced
```

**Goal**: Detect potential regressions before code execution

---

## How It Works

### Architecture

```
┌──────────────────────────────────────────────────────────┐
│ Claude Code + Hooks                                      │
│ Captures: file edits, tool calls, user corrections       │
└──────────────────────────────────────────────────────────┘
                         |
                         v
┌──────────────────────────────────────────────────────────┐
│ MCP Server (Python)                                      │
│ - 22 MCP tools for querying/recording/predicting          │
│ - LLM-powered entity extraction (Claude Haiku)           │
│ - External linter integration (ESLint, Pylint, Ruff)     │
└──────────────────────────────────────────────────────────┘
                         |
                         v
┌──────────────────────────────────────────────────────────┐
│ Knowledge Graph (SQLite + FTS5)                          │
│ - entities.db: APIs, functions, classes                  │
│ - facts.db: Temporal assertions with evidence            │
│ - relationships.db: Entity dependency graph              │
│ - constraints.db: Learned rules from corrections         │
│ - sessions.db: Session history and outcomes              │
│ - events.db: Activity log with reasoning chains          │
└──────────────────────────────────────────────────────────┘
```

### Key Concepts

1. **Temporal Facts**: Every fact has `validAt` and `invalidAt` timestamps
   - "Function X existed from 2024-01-15 to 2024-03-20"
   - Query: "What was true on March 1st?"

2. **Evidence Chains**: Every assertion traces back to source
   - Fact -> Session -> Event -> Source Code Location

3. **Constraint Learning**: Pattern recognition from user corrections
   - Automatic rule type inference (linting, architecture, testing)
   - Severity detection (error, warning, info)
   - Example generation for future reference

4. **Dual Validation**: Combines two validation sources
   - World model constraints (learned from user)
   - External linters (ESLint, Pylint, Ruff)

---

## MCP Tools

Twenty-two MCP tools available to Claude Code:

### 1. `query_fact`
Check if APIs/functions exist before using them
```python
result = query_fact(
    query="Does User.findByEmail exist?",
    entity_type="function"
)
# Returns: {exists: bool, confidence: float, facts: [...]}
```

### 2. `record_event`
Capture development activity with reasoning chains
```python
record_event(
    event_type="file_edit",
    file_path="src/api/auth.ts",
    reasoning="Added JWT authentication middleware"
)
```

### 3. `validate_change`
Pre-execution validation against constraints and linters
```python
result = validate_change(
    file_path="src/api/auth.ts",
    proposed_content="..."
)
# Returns: {safe: bool, violations: [...], suggestions: [...]}
```

### 4. `get_constraints`
Retrieve project-specific rules for a file
```python
constraints = get_constraints(
    file_path="src/**/*.ts",
    constraint_types=["linting", "architecture"]
)
```

### 5. `record_correction`
Learn from user edits (HIGH PRIORITY)
```python
record_correction(
    claude_action={...},
    user_correction={...},
    reasoning="Use logger.debug instead of console.log"
)
```

### 6. `get_related_bugs`
Regression risk assessment
```python
result = get_related_bugs(
    file_path="src/api/auth.ts",
    change_description="refactoring authentication logic"
)
# Returns: {bugs: [...], risk_score: float, critical_regions: [...]}
```

### 7. `seed_project`
Scan the codebase and populate the knowledge graph with entities and relationships
```python
result = seed_project(
    project_dir=".",
    force=False
)
# Returns: {files_seeded: int, entities_created: int, relationships_created: int}
```

### 8. `ingest_pr_reviews`
Pull GitHub PR review comments and convert team feedback into constraints
```python
result = ingest_pr_reviews(
    repo="owner/repo",  # Auto-detected from git remote if omitted
    count=10
)
# Returns: {prs_scanned: int, constraints_created: int, constraints_updated: int}
```

---

## Documentation

- **[QUICKSTART.md](./QUICKSTART.md)** - 5-minute setup guide
- **[CONTRIBUTING.md](./CONTRIBUTING.md)** - Contribution guidelines
- **[RELEASE_NOTES.md](./RELEASE_NOTES.md)** - Version history and features

---

## Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=world_model_server --cov-report=html
```

186 tests covering knowledge graph CRUD, FTS5 search, constraint management, bug tracking, auto-seeding, PR review ingestion, decision traces, outcome linkage, trajectory learning, prediction layer, memory health, contradiction detection, transcript pointers, project identity, and PreToolUse enforcement. See [tests/](./tests/) for details.

---

## Configuration

### Environment Variables

```bash
# Database location (default: ./.claude/world-model/)
export WORLD_MODEL_DB_PATH="/custom/path"

# Anthropic API key (optional - enables LLM extraction)
# IMPORTANT: Never commit this! Use .env file (see .env.example)
export ANTHROPIC_API_KEY="your-api-key-here"

# Model selection
export WORLD_MODEL_EXTRACTION_MODEL="claude-3-haiku-20240307"  # Fast
export WORLD_MODEL_REASONING_MODEL="claude-3-5-sonnet-20241022"  # Accurate

# Debug mode
export WORLD_MODEL_DEBUG=1
```

**Note**: Create a `.env` file in your project root (see `.env.example`) - it's automatically ignored by git.

### Customizing Hooks

Edit `.claude/settings.json` to customize which tools trigger world model hooks:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Edit|Write|Bash",
      "hooks": [...]
    }]
  }
}
```

---

## Language Support

**Currently Supported**:
- TypeScript / JavaScript
- Python

**Coming Soon**:
- Go, Rust, Java, C++

**Extensible Architecture**: Easy to add new language parsers (see [CONTRIBUTING.md](./CONTRIBUTING.md))

---

## Privacy and Security

- **Local-First**: All knowledge graph data stays on your machine.
- **Optional LLM**: Works without API key (uses regex patterns as fallback).
- **Encrypted Storage**: SQLite databases are local files (encrypt your disk for security).

### Telemetry (opt-in, off by default)

v0.7.3 added anonymous usage telemetry. It is:

- **Off by default.** You have to explicitly opt in.
- **Asked once** during `world-model setup`, with a clear `y/N` prompt.
- **Inspectable**: `world-model telemetry --status` shows the exact JSON payload that would be sent.
- **Disable any time** with `world-model telemetry --disable`, or globally with `WORLD_MODEL_TELEMETRY_DISABLE=1`.
- **Skipped in non-TTY environments** (CI, scripts) so it never blocks an automated setup.

**What we send (only if you opt in):**

| Field | Example | Why |
| --- | --- | --- |
| `event` | `setup_completed`, `demo_run`, `hook_fired` | Which lifecycle step ran |
| `version` | `0.7.3` | Which release you're on |
| `install_id` | random UUID at `~/.world-model/install_id` | Distinguish installs without identifying users |
| `ts` | unix timestamp | When the event fired |

**What we never send:** file paths, file contents, rule names, hostnames, IP addresses, API keys, decision-trace text, fact text, or anything else that could identify a person or leak business logic. The full payload schema lives in `world_model_server/telemetry.py`.

**Where it goes:** opt-in events are posted to a dedicated private GitHub repo (`SaravananJaichandar/world-model-telemetry`) as plain issues. There is no third-party analytics service, no cookie, no fingerprint. The PAT embedded in the client is scoped to that one repo with `Issues: write` only.

### API Key Usage (only if you provide `ANTHROPIC_API_KEY`)

- Entity extraction from code changes
- Constraint inference from corrections
- Never sends: Credentials, secrets, PII

### Security Best Practices

- Never commit `.env` files
- Use `.env.example` as template
- Store API keys in environment variables or `.env` files only
- The `.gitignore` automatically excludes sensitive files

---

## Roadmap

### v0.2.x
- [x] Auto-seeding: knowledge graph populates from existing codebase on setup
- [x] PR Review Intelligence: ingest GitHub review comments as constraints
- [x] Relationship tracking: import and dependency graph between entities
- [x] Multi-language support: Python, TypeScript/JavaScript, Solidity, Go, Rust
- [x] CLI query command for knowledge graph lookups
- [x] 40 tests, 8 MCP tools

### v0.3.0
- [x] Module-level matching: query by module name finds the file and its contents
- [x] Incremental re-seeding: only re-process files changed since last seed
- [x] Fuzzy entity matching: approximate name search for typos and abbreviations
- [x] Query caching: in-memory cache with TTL for repeated lookups
- [x] Java support: complete multi-language coverage
- [x] MCP server pipeline validation on real projects

### v0.4.0
- [x] Outcome linkage: test failures linked to code changes with facts
- [x] Trajectory learning: co-edit patterns tracked across sessions
- [x] Decision trace capture: structured log of agent proposals and human corrections
- [x] Cross-project entity search with project registry
- [x] 5 new MCP tools (13 total), 104 tests

### v0.5.0
- [x] Regression prediction, "what if" simulation, test failure prediction
- [x] Multi-project knowledge transfer, memory health, fact TTL/decay
- [x] get_context_for_action pre-edit bundle, constraint violation tracking, find_contradictions
- [x] 20 MCP tools, 151 tests

### v0.6.0 — Enforcement, Provenance, Identity
- [x] PreToolUse constraint enforcement hook: deny hard violations at the edit boundary
- [x] Indexed transcript pointers: hydrate any fact back to source conversation
- [x] Project identity decoupling: stable UUID across directory renames
- [x] Content-hash deduplication for facts and constraints
- [x] Auto-generate CLAUDE.md from the knowledge graph
- [x] BetaAbstractMemoryTool subclass for Anthropic SDK integration
- [x] Desktop Extension (.mcpb) packaging for Claude Desktop
- [x] 22 MCP tools, 13 CLI subcommands, 186 tests

### v0.7.0 — Auto-injection, defer tier, contradiction resolution, harness adapters
- [x] PostCompact and UserPromptSubmit auto-injection: re-emit top constraints and recent facts after context loss
- [x] `defer` enforcement tier in PreToolUse: pause headless agents on recurring warning-level violations, with graceful fallback to `ask`
- [x] Confidence-weighted contradiction resolution: pick a winner using confidence, recency, or source count, with an `auto` strategy
- [x] Compaction audit log: query and export what was remembered across each compaction boundary
- [x] Cursor adapter package
- [x] 25 MCP tools, 14 CLI subcommands, 220 tests

### v0.7.2 — Streamable HTTP transport
- [x] HTTP transport mode for remote / MCP-tunnel deployment
- [x] /healthz endpoint, Dockerfile.http, docker-compose.yml
- [x] docs/deployment/mcp-tunnel.md walkthrough for Claude Managed Agents
- [x] 236 tests

### v0.7.3 — Onboarding, telemetry, pi adapter
- [x] `world-model demo` guided tour for first-time users
- [x] Opt-in anonymous telemetry, off by default, inspectable
- [x] pi-package adapter (`adapters/pi/`, `install-pi` CLI)
- [x] 17 CLI subcommands, 256 tests

### v0.7.4 (Current) — Interop, deployment, benchmark
- [x] AGENTS.md / `.agents/skills/` constraint reader (new MCP tool: `get_agents_md_constraints`)
- [x] Self-hosted Claude Managed Agents deployment guide + Modal quickstart
- [x] Reproducible contradiction-resolution benchmark (24-pair dataset, CI workflow, RESULTS.md)
- [x] 26 MCP tools, 17 CLI subcommands, 283 tests

### v0.7.5
- [x] Codex CLI adapter (`install-codex`, shipped 2026-06-05)

### v0.7.6
- [x] In-agent `/world-model` slash command (read-only: status, contradictions, recent, help)
- [x] `world-model status-watch` TUI status widget

### v0.8.0
- [x] Decay + provenance schema: `source_tool`, `confirmer`, `last_decay_at` columns on facts. Per-evidence-type TTL with domain-aware half-lives (source_code 365d, test 180d, session 14d, user_correction 730d, bug_fix 365d).
- [x] Slash command write operations (`/world-model resolve <id>`, `/world-model forget <id>`).
- [x] `resolve_contradiction` accepts `confirmer` to stamp the winning fact as settled.

### v0.8.1
- [x] Expanded contradiction-resolution benchmark: 24 → 105 pairs across 19 categories, including 6 new categories that test the v0.8.0 schema (decay, provenance, confirmer).
- [x] Honest per-strategy + per-category RESULTS.md with the v0.7.4 number preserved as baseline.

### v0.9 (Shipped 2026-06-24) — Repeat-mistake benchmark on SWE-bench Verified
- [x] **Pre-registered SWE-bench Verified benchmark**. The empirical test of the central wedge: does the learning loop measurably reduce repeated agent mistakes on a public task corpus? Methodology locked in [`benchmarks/repeat-mistake/DESIGN.md`](benchmarks/repeat-mistake/DESIGN.md) on 2026-06-17, a week before the benchmark ran. Pre-registered hypothesis, interpretation thresholds, judge prompts, and SWE-bench Pro 7-category failure taxonomy. No goalpost-moving.
- [x] **Result: +10.2 pts combined paired delta across 49 SWE-bench Verified instances** (baseline 33/49 = 67.3% → treatment 38/49 = 77.6%). Within-domain delta +15.0 pts on django + sympy. Cross-domain delta +6.9 pts on matplotlib + scikit-learn + sphinx with zero observed regressions on 18 baseline passes. 6 FAIL-to-PASS flips, 1 regression. Full per-task tables, mechanistic analysis of the cross-domain flips, and seven explicit limitations in [`benchmarks/repeat-mistake/RESULTS.md`](benchmarks/repeat-mistake/RESULTS.md).
- [x] Pre-registered paper preprint with DOI: [10.5281/zenodo.20834508](https://doi.org/10.5281/zenodo.20834508). CC-BY 4.0. PDF and markdown source at [`benchmarks/repeat-mistake/paper.pdf`](benchmarks/repeat-mistake/paper.pdf) / [`paper.md`](benchmarks/repeat-mistake/paper.md).
- [x] Constraint extraction pipeline grounded in the SWE-bench Pro 7-category failure taxonomy (arXiv:2509.16941). Locked classifier and extractor prompts in `failure_classifier.py` and `learning_hook.py`.
- [x] All raw artifacts committed (per-task progress, predictions, scores, classifications, constraints, harness reports) so the benchmark is reproducible from a fresh checkout.
- [x] v0.9.1 patch: restored embedded telemetry token after a release-mechanics miss in v0.9.0 (no methodology change; benchmark numbers unchanged).

### v0.9.2 (Shipped 2026-06-30) — Multi-seed replication appendix
- [x] Pre-registered 17-instance multi-seed test per `benchmarks/repeat-mistake/SEED_PLAN.md` (locked 2026-06-25). Outcome: load-bearing replication 0 of 7; mean paired delta across two seeds is +0.24 per instance, bootstrap 95 percent CI [0.00, 0.47]. The v0.9 +10.2 pts headline was substantially attributable to an unlucky baseline draw. Honest update published per the pre-registered acceptance criteria. Appendix in `RESULTS.md` and `paper.md`. Zenodo record updated to version 2.

### v0.10 (Shipped 2026-07-01) — Three new adapters
- [x] **OpenClaw adapter (MCP registration) + `install-openclaw` CLI**. Registers world-model-mcp as an MCP server inside OpenClaw via `python -m world_model_server.cli install-openclaw`. Pure additive since OpenClaw ships no native memory layer. Verified end-to-end against OpenClaw `2026.6.11 (e085fa1)` on macOS on 2026-07-01: `openclaw mcp probe world-model` reports 27 tools discovered. See [`adapters/openclaw/`](./adapters/openclaw/).
- [x] **Hermes Agent adapter (MCP registration) + `install-hermes` CLI**. Registers world-model-mcp as an external MCP server inside Hermes Agent. Uses `ruamel.yaml` round-trip mode to preserve every comment and blank line in the 1327-line reference `config.yaml`. Verified end-to-end against Hermes Agent `v0.17.0 (2026.6.19)` on macOS on 2026-07-01: `hermes mcp test world-model` reports 27 tools discovered. See [`adapters/hermes/`](./adapters/hermes/).
- [x] **Continue adapter (MCP registration) + `install-continue` CLI**. Registers world-model-mcp as an MCP tool source inside [Continue](https://github.com/continuedev/continue) (VS Code + JetBrains). CLI-side E2E verified: the exact stdio spawn Continue would perform returns 27 tools via a live `tools/list` roundtrip. See [`adapters/continue/`](./adapters/continue/).
- [x] v0.10.1: fixed a stale Zenodo DOI reference (concept vs. version DOI) across README badge, roadmap link, `paper.md`, and `paper.pdf`. No code changes.

### v0.11 (Shipped 2026-07-02) — Depth after breadth

Depth release. v0.10 expanded surface area to seven runtimes; v0.11 solves real problems for the users we now have. Two signals shaped it: [Hermes #47349 (2026-07-01)](https://github.com/NousResearch/hermes-agent/issues/47349) surfaced the write-side routing gap (MCP surfaces tools but the agent still chooses the destination); and the `auto` strategy on the v0.8.1 contradiction-resolution benchmark still scored 77.1% because it did not fully consume the `confirmer` + decay-awareness fields shipped in v0.8.0.

- [x] **v0.11.0 A: `auto` strategy rewrite for `resolve_contradiction`.** Folds in `confirmer` awareness, per-evidence-type decay, distinct-source-tool counting, and tie-detection. Lifts the v0.8.1 contradiction-resolution benchmark's `auto` score from **77.1% to 100.0%** on the same 105-pair × 19-category dataset. Overall benchmark accuracy across four canonical strategies + the decayed strategy rises from 78.2% to 83.7%. See `benchmarks/contradictions-200/`.
- [x] **v0.11.0 B: Hermes native `MemoryProvider` plugin + `install-hermes-provider` CLI.** Python plugin implementing Hermes' `agent/memory_provider.py` ABC (`initialize`, `get_tool_schemas`, `handle_tool_call`, `get_config_schema`, `save_config`). Intercepts writes at Hermes' routing layer rather than only surfacing tools — the architectural distinction MCP alone cannot close. Priority was bumped from "conditional on MCP adoption" after [#47349](https://github.com/NousResearch/hermes-agent/issues/47349) demonstrated real user demand for write-side interception. Ships as `world_model_server/hermes_memory_provider/` in the wheel; `install-hermes-provider` copies the plugin into `<hermes_home>/plugins/memory/world-model/`. See [`adapters/hermes-memory-provider/`](./adapters/hermes-memory-provider/).
- [x] **v0.11.1: Content-type routing schema field.** Nullable `content_type` on the Fact model and the facts table, distinguishing `rule` (always-inject), `fact` (search-on-demand), and `procedure` (multi-step workflow). Additive-only migration; existing rows keep NULL and continue to work. Enables the v0.11.0 B MemoryProvider (and future providers) to route writes intelligently instead of dumping everything into one store. Sourced from Hermes #47349 architectural framing.
- [x] **v0.11.2: Dogfooding case study.** Publishes what the fact graph actually captured about the world-model-mcp codebase in `.claude/world-model/`: 3 learned constraints with real violation counts (including two release-mechanics rules that map directly to the v0.9.1 telemetry-token miss and the v0.10.1 tagging lesson), 1 bug_fix reflection, 608 facts, 600 entities. Honest about what was NOT captured (empty events / decisions / sessions tables). Reproducibility contract: `python scripts/dogfooding_snapshot.py` regenerates the committed JSON byte-for-byte. See [`case-studies/v011-dogfooding/`](./case-studies/v011-dogfooding/).

### v0.12 (Shipped 2026-07-06 / 2026-07-07) — Breadth + depth + adversarial verification

Nine substantive changes in the v0.12.0 umbrella release plus the v0.12.12 adversarial-verification follow-up. Two roadmap items (v0.12.8 OpenClaw TS plugin, v0.12.10 Antigravity CLI adapter) deferred per their roadmap-gated conditionals.

- [x] **v0.12.1: `world-model doctor` command.** Eight diagnostic checks, `--json`, `--fix`. Sourced directly from the v0.11.2 dogfooding trace.
- [x] **v0.12.2: `influence_state` + `expires_at` schema additions.** Storage-vs-planning-influence separation + hard drop-dead expiry, both additive nullable fields.
- [x] **v0.12.3: universal content-type routing consumers.** Closes the write- and consumer-side loop opened by v0.11.1. `create_fact` persists `content_type`; `query_facts` accepts a `content_type` filter; `get_injection_context` splits rules / facts / procedures into three routed pools.
- [x] **v0.12.4: GitHub Copilot Chat adapter (`install-copilot`).** Merges into `.vscode/mcp.json` with careful handling of the `"servers"` vs `"mcpServers"` divergence unique to Copilot Chat.
- [x] **v0.12.5: `install-continue --global` config-merge path.** ruamel.yaml round-trip preserves comments in `~/.continue/config.yaml`.
- [x] **v0.12.6: Cline adapter (`install-cline`).** Merges into `~/.cline/mcp.json`.
- [x] **v0.12.7: Windsurf adapter (`install-windsurf`).** Merges into `~/.codeium/windsurf/mcp_config.json`.
- [x] **v0.12.9: Hermes lifecycle hooks.** Five optional hooks (`sync_turn`, `on_pre_compress`, `prefetch`, `on_session_end`, `on_memory_write`) on top of the v0.11.0 MemoryProvider ABC.
- [x] **v0.12.11: MCP 2026-07-28 spec readiness scaffolding.** Non-behavior-changing observability + public audit; five-row `READINESS_STATE` matrix locked and tested.
- [x] **v0.12.12: Coach-Player adversarial verification.** `verify_retrieval` MCP tool + isolated Coach implementation + 12-pair hand-labeled benchmark. Pattern ported from the maintainer's earlier `y=c` project.
- [ ] **v0.12.8: OpenClaw TypeScript plugin bundle** — DEFERRED. Roadmap-gated on adoption signal; no explicit user ask within five days of v0.10.
- [ ] **v0.12.10: Antigravity CLI adapter** — DEFERRED. SDK still lacks `TransformCompactionHook` through v1.0.16.

### v0.13+ (Backlog)

**Near-term:**

- [ ] **Copilot CLI Windows shim in doctor** (v0.12.13 candidate). Extend `doctor --fix` to detect Copilot-target runtimes and rewrite unwrapped hook commands to `bash -c '...'` shape with `cwd`-from-stdin fallback. Sourced from [copilot-cli #4001](https://github.com/github/copilot-cli/issues/4001).
- [ ] **Expand Coach-Player benchmark to ≥30 pairs.** Once labeled set grows, the 95% hallucination-catch floor becomes enforceable (currently aspirational at N=12).
- [ ] **`answer_with_verification` end-to-end wrapper tool.** Combines `query_fact` → synthesize → `verify_retrieval` into a single MCP call for callers who want the whole pipeline in one shot.

**Medium-term — waits for signal:**

- [ ] **Citation polarity on retrieved facts** (`supporting` / `refuting` / `neutral`). Requires retrieval caller to know intent, which the schema layer doesn't control. Revisit when a specific integrator commits to instrumenting the annotation.
- [ ] **OpenClaw TypeScript plugin bundle** — moved from v0.12.8 to medium-term. Revisit when adoption signal warrants a TypeScript surface.
- [ ] **Antigravity CLI adapter.** Blocked pending `TransformCompactionHook` in the SDK. Unblocks whenever the SDK ships it.
- [ ] **Full 2026-07-28 MCP spec compliance** — HTTP header emission (`Mcp-Method`, `Mcp-Name`), `server/discover`, `InputRequiredResult`. v0.12.11 shipped the observability scaffolding; full compliance lands after the final spec ships on 2026-07-28.

**Long-term — v1.0 territory, expensive:**

- [ ] **Full-corpus multi-seed replication** of the SWE-bench Verified benchmark: all 49 paired instances at 3-5 seeds each. The v0.9.2 update covers a 17-instance subset only. Cost is ~60 hours agent time; the honest bounds from v0.9.2 are already published, so the marginal empirical gain is smaller than the operational cost. Save for a v1.0 push.
- [ ] **Head-to-head benchmarks** against other memory layers (mem0, Letta, Zep, piia-engram, ClawMem). Competitive-positioning value only; do it once, and only once the differentiators are stable enough that the head-to-head numbers are worth locking in.
- [ ] **Explicit failure-mode-similarity scoring** to predict when cross-domain transfer will succeed. Research-heavy; needs the multi-seed data as a precondition.
- [ ] **Larger task counts per repo; broader corpus coverage** beyond the 50-task subset.

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) for:
- Development setup
- Coding standards
- Adding language support
- Writing tests
- Submitting PRs

**Areas where help is needed**:
- Language parsers (Go, Rust, Java, C++)
- Performance optimization
- Documentation improvements
- Real-world testing feedback

---

## Stats

**Project Size**:
- ~4,800 lines of code
- 13 Python modules
- 3 TypeScript hook implementations

**Storage Efficiency**:
- Empty database: ~155 KB
- Per entity: ~500 bytes
- Per fact: ~800 bytes

---

## License

[MIT License](./LICENSE) - Free for commercial and personal use

---

## Support

- **Issues**: [GitHub Issues](https://github.com/SaravananJaichandar/world-model-mcp/issues)
- **Discussions**: [GitHub Discussions](https://github.com/SaravananJaichandar/world-model-mcp/discussions)
