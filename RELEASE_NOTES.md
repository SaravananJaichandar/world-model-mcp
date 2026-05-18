# World Model MCP - Release Notes

## v0.7.0 (May 2026)

### Headline

Enforcement, provenance, and harness-neutral memory. v0.7.0 extends the
v0.6 enforcement boundary with a new `defer` tier for headless agents,
re-injects context after compaction, resolves contradictions with
confidence weighting, audits every compaction event, and ships a
Cursor adapter so the same primitives run outside Claude Code.

### New features

- **PostCompact + UserPromptSubmit auto-injection (F1)** -- the new
  `world-model-inject` hook calls a Python helper that returns a compact
  bundle of top constraints and recent canonical facts to splice into
  the agent's working context after compaction or on user prompt. The
  helper reads constraints and facts read-only and fails open on any
  error. New MCP tool: `get_injection_context`.
- **`defer` enforcement tier in PreToolUse (F2)** -- warning-severity
  violations seen 5+ times now return `permissionDecision: "defer"`
  (configurable threshold) when the client supports it, falling back
  to `ask` otherwise. The `ValidationResult.enforcement_decision`
  enum now includes `defer`.
- **Confidence-weighted contradiction resolution (F3)** -- new
  `resolve_contradiction` MCP tool picks a winner with strategies
  `keep_higher_confidence`, `keep_most_recent`, `keep_most_sources`,
  `supersede_a`, `supersede_b`, `manual`, or `auto` (chooses based on
  the largest signal gap). The loser is marked `status='superseded'`
  with `invalid_at=now`. `find_contradictions` now returns
  `confidence_a`, `confidence_b`, `source_count_a`, `source_count_b`
  on every pair.
- **Compaction audit log (F5)** -- new `audit.db` and
  `compaction_audit` table. Each PostCompact write records pre/post
  token counts and what was re-injected. New MCP tools:
  `record_compaction_audit`, `get_compaction_audit`. New CLI:
  `world-model audit-compactions [--export <path>]`.
- **Cursor adapter (F4)** -- `adapters/cursor/` ships `hooks.json` and
  `mcp.json` templates that wire the same `inject_helper` and
  `hook_helper` into Cursor's `beforeSubmitPrompt`, `beforeEdit`, and
  `afterCompact` events. Experimental.

### Schema changes (backward-compatible)

- `facts.source_count INTEGER DEFAULT 1` -- number of independent
  sources supporting a fact
- `facts.last_confirmed_at TIMESTAMP` -- most recent re-observation
- New `audit.db` with `compaction_audit` table

All migrations run idempotently via `_existing_columns()`.

### Tool and CLI surface

- 25 MCP tools (was 22): added `get_injection_context`,
  `record_compaction_audit`, `get_compaction_audit`,
  `resolve_contradiction`
- 14 CLI subcommands (was 13): added `audit-compactions`

### Tests

220 passing (was 186): added 34 v0.7.0 tests in `tests/test_v070_features.py`
covering each feature plus backward-compat regression checks.

### Compatibility

- All 22 v0.6 MCP tools continue to work unchanged
- All 13 v0.6 CLI subcommands continue to work unchanged
- v0.6 databases auto-migrate on first `initialize()` call
- Older MCP clients that do not understand `defer` see `ask` instead

---

## v0.1.1 (March 2026)

### Bug Fixes
- Fixed `get_constraints()` failing to match `**` glob patterns (e.g. `src/api/**/*.ts` now correctly matches `src/api/users.ts`)
- Replaced `fnmatch` with a custom `_glob_match` method that handles recursive directory patterns

### Improvements
- Cleaned up README and documentation to remove placeholder URLs and inaccurate claims
- Updated QUICKSTART.md with correct repository URLs and PyPI install option

### Tests
- Added `test_constraint_double_star_glob` to verify recursive glob matching
- Total: 18 tests passing

---

## v0.1.0 (January 2026)

### Initial Release

First public release of World Model MCP. Core knowledge graph and MCP tools are functional but early-stage.

### Core Features

#### 1. LLM-Powered Entity Extraction
- Automatically extracts entities (APIs, functions, classes) from code changes
- Uses Claude Haiku for fast, cost-effective extraction
- Fallback to regex patterns when API key not available
- Supports TypeScript, JavaScript, Python with extensible architecture

#### 2. External Linter Integration
- Integrates with ESLint, Pylint, and Ruff
- Pre-execution validation catches errors before code runs
- Combines world model constraints with linter rules

#### 3. Intelligent Constraint Inference
- LLM-powered pattern recognition from user corrections
- Automatically learns project conventions
- Infers constraint type, severity, and applicability
- Generates reusable examples

#### 4. Temporal Knowledge Graph
- 6 SQLite databases with full-text search (FTS5)
- Temporal facts with validity periods (`validAt`/`invalidAt`)
- Evidence chains for every assertion

**Databases:**
- `entities.db` - Resolved identities (files, APIs, functions)
- `facts.db` - Temporal assertions with FTS5 search
- `relationships.db` - Entity relationship graph
- `constraints.db` - Learned rules with violation tracking
- `sessions.db` - Session history and outcomes
- `events.db` - Activity log with reasoning chains

#### 5. Claude Code Hooks
- TypeScript hooks for event capture and validation
- Non-blocking async execution
- Full session lifecycle management

**Hooks:**
- `PostToolUse` - Capture file edits, test runs, tool calls
- `PreToolUse` - Validate changes before execution
- `SessionStart/End` - Manage session lifecycle

#### 6. MCP Tools

Six MCP tools:

1. **`query_fact`** - Check if APIs/functions exist
2. **`record_event`** - Capture development actions
3. **`validate_change`** - Pre-lint and constraint check
4. **`get_constraints`** - Retrieve rules for a file
5. **`record_correction`** - Learn from user edits
6. **`get_related_bugs`** - Find bugs fixed in a file

#### 7. Ingest Bridge
- Bridge between hooks flat files (.jsonl) and SQLite knowledge graph
- `ingest_queued_events()` reads events-queue.jsonl into events.db
- `ingest_session_files()` reads session-*.json into sessions.db
- Automatic cleanup of source files after ingestion

---

### Known Issues

1. **Pydantic Deprecation Warnings** - Using class-based config instead of ConfigDict (cosmetic only)
2. **Hook Path Resolution** - Requires absolute paths in some environments

### Limitations

- **Language Support**: Currently optimized for TypeScript/JavaScript and Python
- **LLM Dependency**: Best results with Anthropic API key (falls back to patterns without it)
- **Cold Start**: First session has minimal knowledge (improves with each session)

---

### Roadmap

#### v0.2.0
- Enhanced entity resolution with fuzzy matching
- Multi-language support (Go, Rust, Java)
- Performance optimizations (caching, batch processing)

#### v0.3.0
- Trajectory learning (co-edit patterns)
- Structural embeddings
- Relationship graph visualization

#### v0.4.0
- World model simulation ("what if" queries)
- Test failure prediction
- Multi-project knowledge transfer

---

## Support

- **Issues**: https://github.com/SaravananJaichandar/world-model-mcp/issues
- **Discussions**: https://github.com/SaravananJaichandar/world-model-mcp/discussions

---

**License**: MIT
**Python**: 3.11+
