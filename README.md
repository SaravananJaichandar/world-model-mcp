# World Model MCP

**An experimental MCP server that builds a "world model" for your codebase -- a temporal knowledge graph that learns from Claude Code sessions to reduce hallucinations, repeated mistakes, and regressions.**

> **Status: Alpha (v0.4.0)** -- Knowledge graph auto-populates from existing code on setup. 8 MCP tools, 40 tests. Contributions welcome.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

---

## What It Does

World Model MCP creates a **temporal knowledge graph** of your codebase that learns from every Claude Code session to:

- **Prevent Hallucinations** - Validates API/function references against known entities before use
- **Stop Repeated Mistakes** - Learns constraints from corrections, applies them in future sessions
- **Reduce Regressions** - Tracks bug fixes and warns when changes touch critical regions

Think of it as giving Claude a **long-term memory** of your project.

---

## Quick Start

### Installation

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
│ - 13 MCP tools for querying/recording/learning            │
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

Thirteen MCP tools available to Claude Code:

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

40 tests covering knowledge graph CRUD, FTS5 search, constraint management, bug tracking, auto-seeding, and PR review ingestion. See [tests/](./tests/) for details.

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

- **Local-First**: All data stays on your machine
- **No Telemetry**: Zero tracking or external data transmission
- **Optional LLM**: Works without API key (uses regex patterns as fallback)
- **Encrypted Storage**: SQLite databases are local files (encrypt your disk for security)

**API Key Usage** (only if you provide `ANTHROPIC_API_KEY`):
- Entity extraction from code changes
- Constraint inference from corrections
- Never sends: Credentials, secrets, PII

**Security Best Practices**:
- Never commit `.env` files
- Use `.env.example` as template
- Store API keys in environment variables or `.env` files only
- The `.gitignore` automatically excludes sensitive files

---

## Roadmap

### v0.2.x (Current)
- [x] Auto-seeding: knowledge graph populates from existing codebase on setup
- [x] PR Review Intelligence: ingest GitHub review comments as constraints
- [x] Relationship tracking: import and dependency graph between entities
- [x] Multi-language support: Python, TypeScript/JavaScript, Solidity, Go, Rust
- [x] CLI query command for knowledge graph lookups
- [x] 40 tests, 8 MCP tools

### v0.3.0 (Next)
- [ ] Module-level matching: query by module name finds the file and its contents
- [ ] Incremental re-seeding: only re-process files changed since last seed
- [ ] Fuzzy entity matching: approximate name search for typos and abbreviations
- [ ] Query caching: in-memory cache with TTL for repeated lookups
- [ ] Java support: complete multi-language coverage
- [ ] MCP server pipeline validation on real projects

### v0.4.0 (Current)
- [x] Outcome linkage: test failures linked to code changes with facts
- [x] Trajectory learning: co-edit patterns tracked across sessions
- [x] Decision trace capture: structured log of agent proposals and human corrections
- [x] Cross-project entity search with project registry
- [x] 5 new MCP tools (13 total), 104 tests

### v0.5.0
- [ ] Regression prediction from historical outcome data
- [ ] World model simulation: "what if" queries for proposed changes
- [ ] Test failure prediction based on change patterns
- [ ] Multi-project knowledge transfer: promote constraints across repos
- [ ] AST-based extraction via tree-sitter

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
