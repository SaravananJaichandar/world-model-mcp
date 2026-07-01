# World Model MCP

**Enforcement, provenance, and harness-neutral memory for AI coding agents.** A temporal knowledge graph that validates code changes against learned constraints at the edit boundary, re-injects relevant context after compaction, tracks contradictions with confidence-weighted resolution, and runs across Claude Code, Cursor, and pi.

> **Status: v0.9.2** — 26 MCP tools, 19 CLI subcommands, 375 tests, SWE-bench Verified repeat-mistake benchmark with a pre-registered methodology and a multi-seed replication appendix. v0.9.2 is a documentation patch over v0.9.1: it ships the multi-seed replication test that `SEED_PLAN.md` (locked 2026-06-25) committed to running. The seed-2 result tightens the confidence bounds on the v0.9 +10.2 pts paired delta significantly. On a pre-registered 17-instance subset, the load-bearing replication count is 0 of 7, the mean paired delta across two seeds is +0.24 per instance with bootstrap 95 percent CI [0.00, 0.47], and the v0.9 single-trial result was substantially attributable to an unlucky baseline draw rather than constraint effects alone. The wedge claims (lifecycle-hook capture, per-fact provenance, per-evidence-type decay, PreToolUse defer) are unchanged; the empirical headline is honestly bounded. Full appendix and per-instance results in [`benchmarks/repeat-mistake/RESULTS.md`](benchmarks/repeat-mistake/RESULTS.md). v0.9.1 restored the embedded telemetry token after a release-mechanics miss in v0.9.0. v0.9.0 shipped the empirical wedge proof. v0.8.1 expanded the contradiction-resolution benchmark to 105 pairs across 19 categories. v0.8.0 added domain-aware confidence decay with per-evidence-type TTL, per-item provenance fields `source_tool` and `confirmer`, slash command write operations, and a `confirmer` parameter on `resolve_contradiction`. Antigravity adapter held pending a `TransformCompactionHook` in the SDK. v0.7.6 added the `/world-model` slash command and `status-watch` TUI widget. v0.7.5 added the Codex CLI adapter. v0.7.0 introduced PostCompact auto-injection, the `defer` enforcement tier, confidence-weighted contradiction resolution, and a compaction audit log. Contributions welcome.

[![PyPI](https://img.shields.io/pypi/v/world-model-mcp.svg)](https://pypi.org/project/world-model-mcp/)
[![Downloads](https://img.shields.io/pypi/dm/world-model-mcp.svg)](https://pypi.org/project/world-model-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![world-model-mcp MCP server](https://glama.ai/mcp/servers/SaravananJaichandar/world-model-mcp/badges/card.svg)](https://glama.ai/mcp/servers/SaravananJaichandar/world-model-mcp)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20834509.svg)](https://doi.org/10.5281/zenodo.20834509)

mcp-name: io.github.SaravananJaichandar/world-model-mcp

> If world-model-mcp helped you, star the repo or open an issue with what worked or didn't. I read every one and the feedback shapes what ships next.

---

## What It Does

World Model MCP creates a **temporal knowledge graph** of your codebase that learns from every coding session to:

- **Prevent Hallucinations** -- Validates API/function references against known entities before use
- **Stop Repeated Mistakes** -- Learns constraints from corrections, applies them in future sessions
- **Reduce Regressions** -- Tracks bug fixes and warns when changes touch critical regions
- **Survive Compaction** -- Re-injects top constraints and recent facts after the agent's context window resets
- **Resolve Contradictions** -- Picks a winner between conflicting facts using confidence, recency, or source count

Think of it as a long-term memory layer that runs alongside Claude Code, Cursor, or any MCP-aware coding agent.

---

## What's new in v0.9.2

- **Multi-seed replication appendix shipped per `SEED_PLAN.md`**. The v0.9 paper's primary limitation was single-trial design. v0.9.2 ships the multi-seed test that SEED_PLAN.md (locked 2026-06-25) committed to running. The result is published verbatim per the pre-registered acceptance criteria.

- **Honest update to the v0.9 headline**. On the 17-instance pre-registered subset, baseline pass rate swung +41 percentage points between seed 1 and seed 2 with no methodology change. Load-bearing replication is 0 of 7 instances. Mean paired delta across both seeds is +0.24 per instance with bootstrap 95 percent CI [0.00, 0.47]. The v0.9 +10.2 pts paired delta should be read as a single-trial upper bound; the replicated effect size is small, possibly nonzero.

- **What is unchanged**: all v0.9.1 code, the 26 MCP tools, the 19 CLI subcommands, the 375 tests, the wedge claims at the architectural level (lifecycle-hook capture, per-fact provenance, per-evidence-type decay, PreToolUse defer). Architectural claims do not depend on the empirical effect size and survive the multi-seed update.

- **Documentation diffs**: `benchmarks/repeat-mistake/RESULTS.md` adds a "Multi-seed replication appendix (v0.9.2 update)". `benchmarks/repeat-mistake/paper.md` adds Appendix A with the same content. `benchmarks/repeat-mistake/paper.pdf` is regenerated. `benchmarks/repeat-mistake/SEED_PLAN.md` adds a status update (the locked plan above is unchanged). Raw seed-2 artifacts (`baseline_progress_seed2.jsonl`, `treatment_progress_seed2_treatment.jsonl`, predictions, results, and the `multi_seed_summary_seed2.json` from `multi_seed_aggregate.py`) committed.

- **The methodology discipline held**. Pre-registration prevented goalpost-moving. The honest update is published per the locked SEED_PLAN.md acceptance criteria. This is what pre-registration is for.

## What's new in v0.9.0

- **Repeat-mistake benchmark on SWE-bench Verified** — the central wedge proof. 50 SWE-bench Verified tasks across django, sympy, matplotlib, scikit-learn, and sphinx, run as a paired baseline-vs-treatment comparison. Methodology was locked at [`benchmarks/repeat-mistake/DESIGN.md`](benchmarks/repeat-mistake/DESIGN.md) on 2026-06-17 (before the data existed) so the result cannot be accused of goalpost-moving.

- **Headline results** — Subset 1 (within-domain: django + sympy) baseline 15/20 = 75.0 percent, treatment 18/20 = 90.0 percent, delta +15.0 pts with 4 FAIL to PASS flips and 1 regression. Subset 2 (cross-domain: matplotlib + scikit-learn + sphinx) baseline 18/29 = 62.1 percent, treatment 20/29 = 69.0 percent, delta +6.9 pts with 2 flips and zero regressions. Combined paired result across 49 instances: 33/49 to 38/49, delta +10.2 pts.

- **Cross-domain transfer isolated cleanly** — the Subset 2 treatment arm loaded ONLY the 4 Subset 1 constraints (django and sympy directives), holding out the 11 Subset 2 constraints to test whether learning from one repo family generalizes to a different one. Two cross-domain flips with plausible mechanistic explanations grounded in the loaded constraints. Sphinx-9461 is the strongest case: a sympy classmethod constraint transferred to a sphinx classmethod-wrapper unwrapping bug.

- **Honest caveats embedded in RESULTS.md** — seven explicit limitations including single-trial design, constraint-failure overlap on Subset 1, the small cross-domain transfer rate, one dropped instance due to an upstream SWE-bench pip flag issue, and judge-model self-reference risk. Stated verbatim rather than hidden in an appendix.

- **Full reproducibility artifacts** — every progress JSONL, predictions JSON, results JSONL, classification JSONL, constraints JSON, and harness report JSON committed in [`benchmarks/repeat-mistake/`](benchmarks/repeat-mistake/). Locked judge prompts in `failure_classifier.py` and `learning_hook.py`. Total agent cost across both arms was approximately 90 USD on a Claude Code subscription.

## What's new in v0.8.1

- **Contradiction-resolution benchmark expansion** -- the v0.7.4 24-pair benchmark grew to 105 hand-curated pairs across 19 categories. Six new categories exercise the v0.8.0 schema specifically: `source_tool_corroboration`, `confirmer_overrides_pending`, `decay_advantage_session_vs_source`, `decay_advantage_stale_session`, `evidence_type_user_correction`, `settled_beats_higher_confidence`. Deterministic runner at [`benchmarks/contradictions-200/run.py`](benchmarks/contradictions-200/run.py); full per-strategy + per-category breakdown at [`benchmarks/contradictions-200/RESULTS.md`](benchmarks/contradictions-200/RESULTS.md).

- **Honest framing on the numbers**: the new dataset is harder than v0.7.4's 24-pair set because the new categories deliberately test schema awareness (confirmer, evidence_type, decay) rather than raw confidence ranking. Headline numbers: `keep_most_sources` 99.0%, `keep_higher_confidence` 81.0%, `auto` 77.1%, `keep_higher_confidence_decayed` 90.5% (on the 21 pairs where evidence_type is present), overall 78.2% across all strategies. The original 24-pair v0.7.4 93.5% number is preserved unchanged at `benchmarks/contradictions/` and is not invalidated; it tested a different (smaller, easier) corpus.

- **The wedge benchmark is v0.9**: "does the learning loop measurably reduce repeated coding-agent mistakes on a public task corpus?" The contradiction-resolution work in this release is internal schema-correctness validation. The empirical artifact that maps to the published essay framing — the learning loop is the durable layer — lands in v0.9 with a SWE-bench-style repeat-mistake benchmark.

## What's new in v0.8.0

- **Domain-aware confidence decay** -- new `world_model_server/decay.py` module with exponential half-life decay per `evidence_type`. Half-lives: source_code 365d, test 180d, session 14d, user_correction 730d, bug_fix 365d. Decay applies on read (no background task), so the next `query_fact` call returns the time-corrected confidence. Settled facts (`canonical` status, or any fact with `confirmer != NULL`) never auto-transition. Synthesized facts that decay below 0.2 confidence and corroborated facts that decay below 0.1 confidence auto-supersede on read, surfacing rot to the next compaction injection.

- **Per-item provenance fields on facts** -- three additive columns (`source_tool TEXT`, `confirmer TEXT`, `last_decay_at TIMESTAMP`), all NULL-defaulted, no backfill. `source_tool` records which tool wrote the fact (e.g. `claude_code`, `codex`, `cursor`, `pi`, `user`). `confirmer` records who confirmed it, distinct from the asserter; NULL means pending, non-NULL means settled. Both are exposed on the `Fact` model and propagated through `create_fact`. Honors the public commitment to Patdolitse (anthropics/claude-code#47023) and ferhimedamine (openai/codex#19195).

- **Slash command write operations** -- two new subcommands. `/world-model resolve <id>` marks a contradiction as resolved (manual; for confidence-weighted picking use the `resolve_contradiction` MCP tool). `/world-model forget <id>` sets `invalid_at` on a fact (preserved in the audit log; current-only reads skip it from then on). Both are idempotent and report cleanly on unknown ids. Help text now lists both alongside the read-only subcommands shipped in v0.7.6.

- **`resolve_contradiction` accepts `confirmer`** -- when a `confirmer` argument is provided to the MCP tool or its underlying `resolve` function, the winning fact gets its `confirmer` column stamped with that value. This is the spec primitive that distinguishes "the asserter says X" from "X is confirmed by Y" per the working group sketch.

- **Antigravity adapter held for the third consecutive release.** The 2026-06-13 re-verification found `OnCompactionHook` declared as `InspectHook` in the SDK with no `TransformCompactionHook` and no `additional_context` return field. The load-bearing memory-injection contract still does not exist in the SDK.

## What's new in v0.7.6

- **In-agent `/world-model` slash command** -- typed by the user inside the agent harness, surfaces the world model state without leaving the chat. Read-only in v0.7.6 (`status`, `contradictions`, `recent`, `help`); write operations (`resolve`, `forget`) land in v0.8. Works across Claude Code, Cursor, Codex, and pi by intercepting `UserPromptSubmit` in the existing `inject_helper`. Returns `additionalContext` in the strict camelCase shape Codex enforces (`deny_unknown_fields`), so the same wire-up serves all four harnesses without a per-harness branch.
- **`world-model status-watch` TUI widget** -- terminal pane that runs alongside the agent and refreshes every 5 seconds. Shows constraints (total, severity=error, severity=warning), unresolved contradictions, facts (canonical / synthesized / superseded), and last compaction time. Built on the `rich` library already in the dependency tree; falls back to a plain-text one-shot dump when `rich` is not installed.
- **Antigravity CLI adapter intentionally NOT shipped in this release** -- the re-verification on 2026-06-13 against `google-antigravity/antigravity-sdk-python` HEAD surfaced an architectural gap: `OnCompactionHook` is declared as an `InspectHook` (read-only, non-blocking) with no `additional_context` return field and no `TransformCompactionHook` subclass. The load-bearing memory-injection contract does not exist in the SDK today. v0.7.6 ships without Antigravity rather than against a contract that cannot do the work.

## What's new in v0.7.5

- **Codex CLI adapter** -- new `install-codex` CLI subcommand appends a `[mcp_servers.world_model]` block plus PreToolUse, PostToolUse, PostCompact, and SessionStart hooks to `~/.codex/config.toml`. The bundled snippet was verified against `openai/codex@main` at v0.138.0-alpha (server name uses underscore to dodge the tool-name hyphen-strip in `codex-rs/codex-mcp/src/mcp/mod.rs`; hook output sticks to camelCase with `deny_unknown_fields` compliance). Schema regression tests in `tests/test_v075_features.py` lock the contract down. See [adapters/codex/README.md](adapters/codex/README.md).
- **Dual-shape payload normalization in `hook_helper` and `inject_helper`** -- both helpers now accept either Claude Code's payload shape (`event`, `project_dir`) or Codex's (`hook_event_name`, `cwd`), so the same Python code drives all four adapters (Claude Code, Cursor, pi, Codex).
- **Antigravity CLI adapter intentionally NOT shipped this release** -- the Antigravity API surface is still settling (six 1.0.x releases in three weeks, the `url` field for HTTP MCP servers landed June 3, hook JSON event-name casing remains undocumented). Targeting June 25 for that adapter after the API stabilizes. Detailed reasoning in the v0.7.5 RELEASE_NOTES entry.

## What's new in v0.7.4

- **AGENTS.md / `.agents/skills/` constraint reader** -- world-model-mcp now reads declarative project conventions from `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and `.agents/skills/*.md` files and mixes them into PreToolUse enforcement alongside the SQLite-backed constraints. Supports structured fence blocks (```` ```constraint ```` and YAML frontmatter) and heuristic imperative-sentence extraction for prose-style AGENTS.md files. New MCP tool: `get_agents_md_constraints`. ([anthropics/claude-code#6235](https://github.com/anthropics/claude-code/issues/6235) has 4,000+ thumbs-up for AGENTS.md as the cross-agent format.)
- **Self-hosted Claude Managed Agents deployment guide** -- Anthropic's [official position](https://claude.com/blog/claude-managed-agents-updates): *"Memory is not yet supported in self-hosted sessions."* world-model-mcp fills that gap. New guide at [`docs/deployment/managed-agents-self-hosted.md`](docs/deployment/managed-agents-self-hosted.md), with a [Modal quickstart](examples/managed-agents-self-hosted/) you can deploy in under five minutes.
- **Reproducible contradiction-resolution benchmark** -- 24-pair dataset at [`benchmarks/contradictions/dataset.jsonl`](benchmarks/contradictions/dataset.jsonl), runner at [`benchmarks/contradictions/run.py`](benchmarks/contradictions/run.py), results at [`benchmarks/contradictions/RESULTS.md`](benchmarks/contradictions/RESULTS.md). Headline: 93.5% overall accuracy, 100% on `keep_higher_confidence` and `keep_most_sources`, with documented honest weaknesses on tie-handling and small confidence gaps. Re-run with `python benchmarks/contradictions/run.py`. CI workflow guards regressions.

## What's new in v0.7.3

- **`world-model demo`** -- one command to see every primitive working. Initializes the knowledge graph, seeds reproducible demo data via `scripts/demo_seed.py`, then exercises each primitive (PreToolUse enforcement, contradiction detection, PostCompact injection, audit log) with real outputs. New users can see the value without writing any code.
- **Opt-in telemetry** -- off by default, prompted once during `world-model setup`, inspectable with `world-model telemetry --status`, disabled with `world-model telemetry --disable`. No file paths, no code, no identifiers tied to a person. See [Privacy and Security](#privacy-and-security) for the exact payload.
- **pi adapter** -- new `adapters/pi/` package. world-model-mcp now plugs into [earendil-works/pi](https://github.com/earendil-works/pi) via pi's extension API (`tool_call` -> PreToolUse, `context` -> auto-injection, `session_compact` -> audit log). Install with `world-model install-pi`.

## What v0.7.0 introduced (still active)

- **PostCompact / UserPromptSubmit auto-injection** -- when the agent's context is compacted, the hook automatically splices the top constraints and recent canonical facts back into the next turn. Configurable, fails open.
- **`defer` enforcement tier** -- PreToolUse now classifies recurring warning-level violations as `defer`, which pauses headless agents (with graceful fallback to `ask` on older clients) instead of either hard-denying or silently passing through.
- **Confidence-weighted contradiction resolution** -- the new `resolve_contradiction` tool picks a winner using `keep_higher_confidence`, `keep_most_recent`, `keep_most_sources`, or `auto`. The loser is marked superseded.
- **Compaction audit log** -- every PostCompact event writes a row with pre/post token counts and what was re-injected. Query with the `audit-compactions` CLI or export to JSONL.
- **Cursor adapter** -- harness-neutral hooks under `adapters/cursor/`. Same Python helpers, different manifest format.
- **Streamable HTTP transport (v0.7.2)** -- `WORLD_MODEL_TRANSPORT=http` so the same 25 MCP tools work behind an MCP tunnel for Claude Managed Agents with self-hosted sandboxes. See [docs/deployment/mcp-tunnel.md](docs/deployment/mcp-tunnel.md).

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
openclaw mcp add world-model \
    --command python3 \
    --arg -m \
    --arg world_model_server.server \
    --env WORLD_MODEL_DB_PATH=.claude/world-model
# Restart the OpenClaw gateway; verify with: openclaw mcp list
```

Pure additive integration — OpenClaw ships no native memory layer, so all 26 world-model tools become available to OpenClaw agent turns without capability overlap. This is MCP-registration only in v0.10; a TypeScript plugin bundle for typed lifecycle hooks (`before_prompt_build`, `before_tool_call`, `before_compaction`, `session_start`, ...) is on the v0.10.x roadmap. See [adapters/openclaw/README.md](adapters/openclaw/README.md).

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
- [x] Pre-registered paper preprint with DOI: [10.5281/zenodo.20834509](https://doi.org/10.5281/zenodo.20834509). CC-BY 4.0. PDF and markdown source at [`benchmarks/repeat-mistake/paper.pdf`](benchmarks/repeat-mistake/paper.pdf) / [`paper.md`](benchmarks/repeat-mistake/paper.md).
- [x] Constraint extraction pipeline grounded in the SWE-bench Pro 7-category failure taxonomy (arXiv:2509.16941). Locked classifier and extractor prompts in `failure_classifier.py` and `learning_hook.py`.
- [x] All raw artifacts committed (per-task progress, predictions, scores, classifications, constraints, harness reports) so the benchmark is reproducible from a fresh checkout.
- [x] v0.9.1 patch: restored embedded telemetry token after a release-mechanics miss in v0.9.0 (no methodology change; benchmark numbers unchanged).

### v0.9.2 (Shipped 2026-06-30) — Multi-seed replication appendix
- [x] Pre-registered 17-instance multi-seed test per `benchmarks/repeat-mistake/SEED_PLAN.md` (locked 2026-06-25). Outcome: load-bearing replication 0 of 7; mean paired delta across two seeds is +0.24 per instance, bootstrap 95 percent CI [0.00, 0.47]. The v0.9 +10.2 pts headline was substantially attributable to an unlucky baseline draw. Honest update published per the pre-registered acceptance criteria. Appendix in `RESULTS.md` and `paper.md`. Zenodo record updated to version 2.

### v0.10 (In progress)
- [x] **OpenClaw adapter (MCP registration)**. First v0.10 adapter shipped. Registers world-model-mcp as an MCP server inside OpenClaw via `openclaw mcp add` — pure additive since OpenClaw ships no native memory layer. See [`adapters/openclaw/`](./adapters/openclaw/). Follow-ups tracked: `install-openclaw` CLI subcommand, TypeScript plugin bundle for typed lifecycle hooks (`before_prompt_build`, `before_tool_call`, `before_compaction`, `session_start`, ...).
- [ ] Hermes Agent adapter (MCP route). Hermes v0.17.0 (NousResearch, MIT) has first-class MCP client support plus a documented `MemoryProvider` plugin ABC. Ship the MCP route first; native plugin only if MCP route shows traction — Hermes allows exactly one external memory provider active at a time and [yoloshii/ClawMem](https://github.com/yoloshii/ClawMem) already occupies that slot for many users.
- [ ] Continue adapter. Largest OSS coding agent not tied to a platform vendor; higher priority after the SpaceX/Cursor acquisition changes the platform-risk math.
- [ ] Full-corpus multi-seed replication: all 49 paired instances at 3-5 seeds (the v0.9.2 update covers a 17-instance subset only). The 17-instance subset surfaced the variance signal; the full-corpus run quantifies it across the entire benchmark.
- [ ] Larger task counts per repo; broader corpus coverage beyond the 50-task subset.
- [ ] Head-to-head benchmarks against other memory layers (mem0, Letta, Zep, piia-engram, ClawMem).
- [ ] Explicit failure-mode-similarity scoring to predict when cross-domain transfer will succeed.
- [ ] `auto` strategy rewrite to fold in `confirmer` + decay awareness (should lift the v0.8.1 contradiction-resolution benchmark's auto score from 77.1% past 90%).
- [ ] Antigravity CLI adapter (held pending a `TransformCompactionHook` in the SDK for the load-bearing memory-injection contract).
- [ ] MCP spec readiness for upcoming spec versions (stateless transport, `_meta` headers, `InputRequiredResult`).
- [ ] Cline adapter (lower urgency after they shipped global AGENTS rules in v3.86).
- [ ] Windsurf adapter.

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
