# World Model MCP - Release Notes

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
