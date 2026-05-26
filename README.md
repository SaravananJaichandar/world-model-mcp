# World Model MCP

**Enforcement, provenance, and harness-neutral memory for AI coding agents.** A temporal knowledge graph that validates code changes against learned constraints at the edit boundary, re-injects relevant context after compaction, tracks contradictions with confidence-weighted resolution, and runs across Claude Code, Cursor, and pi.

> **Status: v0.7.3** -- 25 MCP tools, 17 CLI subcommands, 256 tests. Adds a `world-model demo` guided tour, opt-in telemetry, and a pi-package adapter. v0.7.0 introduced PostCompact / UserPromptSubmit auto-injection, the `defer` enforcement tier for headless agents, confidence-weighted contradiction resolution, and a compaction audit log. v0.7.2 added streamable HTTP transport for remote / MCP-tunnel deployment. Contributions welcome.

[![PyPI](https://img.shields.io/pypi/v/world-model-mcp.svg)](https://pypi.org/project/world-model-mcp/)
[![Downloads](https://img.shields.io/pypi/dm/world-model-mcp.svg)](https://pypi.org/project/world-model-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![SafeSkill 50/100](https://img.shields.io/badge/SafeSkill-50%2F100_Use%20with%20Caution-orange)](https://safeskill.dev/scan/saravananjaichandar-world-model-mcp)

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

### v0.7.3 (Current) — Onboarding, telemetry, pi adapter
- [x] `world-model demo` guided tour for first-time users
- [x] Opt-in anonymous telemetry, off by default, inspectable
- [x] pi-package adapter (`adapters/pi/`, `install-pi` CLI)
- [x] 17 CLI subcommands, 256 tests

### v0.8.0 (Next)
- [ ] Antigravity adapter (Google's agentic IDE, replaces Gemini CLI)
- [ ] Codex CLI adapter (OpenAI)
- [ ] Cline and Continue adapters
- [ ] Local web dashboard for the knowledge graph
- [ ] Evidence-weighted decay: constraints persist, low-evidence assertions expire

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
