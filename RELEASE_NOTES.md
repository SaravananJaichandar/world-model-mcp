# World Model MCP - Release Notes

## v0.7.5 (June 2026)

Codex CLI adapter. Antigravity adapter intentionally deferred.

### Headline

OpenAI's Codex CLI shipped first-class hook and MCP support that maps cleanly onto world-model-mcp's existing primitives. v0.7.5 adds the adapter that wires both together. The Antigravity CLI adapter was on the same roadmap but is held until late June because the Antigravity API surface is still settling (six 1.0.x releases in three weeks; the `url` field for HTTP MCP servers landed June 3; hook event-name casing remains undocumented in primary sources). Shipping it today would produce the same patch-release pattern that hit the Cursor adapter at v0.7.0 / v0.7.1.

### New features

- **Codex CLI adapter (F1)** -- new `install-codex` CLI subcommand reads bundled `world_model_server/adapters/codex/{config.toml, hooks_snippet.toml}` and appends them to `~/.codex/config.toml`. Idempotent: the second run refuses to write without `--force`. Supports `--dry-run` to preview, `--config-path` to target a non-default config location. Creates parent directories. New adapter README at `adapters/codex/README.md`.

  Concrete schema details locked down by tests:

  - MCP server name is `world_model` (underscore), not `world-model` (hyphen). Codex's `sanitize_responses_api_tool_name` in `codex-rs/codex-mcp/src/mcp/mod.rs` silently strips hyphens before exposing tool names to the model, which would create model-visible name collisions and trigger Codex's hash-suffix disambiguation path.
  - Hook event names use Codex's exact 10-value enum (PreToolUse, PostToolUse, PreCompact, PostCompact, SessionStart, UserPromptSubmit, SubagentStart, SubagentStop, PermissionRequest, Stop). Anything else is rejected at config load.
  - Hook output JSON is camelCase only and compliant with Codex's `deny_unknown_fields` Rust schema (`codex-rs/hooks/src/schema.rs`). PR #24962 in v0.136 tightened this further by constraining `hookEventName` to a literal string matching the registered event; the bundled helpers return the correct event name per hook.
  - MCP server config uses current field names (`default_tools_approval_mode`, `startup_timeout_sec`, `tool_timeout_sec`, `enabled_tools`, `disabled_tools`), not the pre-v0.130 names (`trust`, `timeout`, `includeTools`, `excludeTools`).

- **Dual-shape payload normalization (F2)** -- `world_model_server.inject_helper._normalize_payload` and `world_model_server.hook_helper.classify` accept either Claude Code's payload shape (`event`, `project_dir`) or Codex's shape (`hook_event_name`, `cwd`). Same Python code now drives Claude Code, Cursor, pi, and Codex adapters; the four adapters live in separate config files so there is no cross-talk. Backward compatible: existing Claude Code adapter behavior unchanged.

- **Schema regression tests (F3)** -- 21 new tests in `tests/test_v075_features.py` cover TOML parse validity, valid event-name enum, current-not-deprecated MCP field names, camelCase-not-snake-case hook output, `hookEventName == event` strict matching per PR #24962, install-codex CLI behavior (write / idempotent / dry-run / parent-dir creation), and backward-compat CLI subcommand presence.

### Antigravity adapter -- explicit hold note

This is documented here because skipping a planned adapter is a roadmap signal worth being honest about.

The Antigravity CLI adapter was on the v0.8 roadmap (Gemini CLI sunsets June 18). Deep verification against primary sources surfaced five issues that together exceed the ship-this-week risk threshold:

1. The MCP config path migrated from `~/.gemini/antigravity/mcp_config.json` to `~/.gemini/config/mcp_config.json` in 1.0.3, with documentation still split between blogs citing the old path and the changelog citing the new.
2. The `url` field for HTTP MCP servers was added on 2026-06-03 in 1.0.5, less than 36 hours before this release date. Anything shipped today will look stale by next week.
3. The hook JSON event-name casing is undocumented in any primary source. Python SDK uses `PreToolCallDecideHook` style; third-party blogs use `PreToolUse` Claude-style. Google's docs site renders client-side and is not scrapeable.
4. The compaction context-injection contract -- the load-bearing feature for world-model-mcp -- is undocumented in the SDK README.
5. The repo has 259 open issues with active regressions (sandbox ignored in headless 1.0.4, broken first-launch OAuth, Windows IDE path mismatch). The team is fixing fundamentals rather than stabilizing APIs.

Target: re-verify around June 25, ship v0.7.6 by July 1 if the API has settled. The Cursor adapter at v0.7.0 needed a same-day patch release for similar reasons; the cost of avoiding that repeat is two weeks of waiting.

### Tools and CLI surface

- 26 MCP tools (unchanged from v0.7.4)
- 18 CLI subcommands (was 17): added `install-codex`

### Tests

304 passing (was 283): 21 new in `tests/test_v075_features.py`.

### Backward compatibility

- All v0.7.4 MCP tools and CLI subcommands work unchanged.
- `hook_helper.classify` and `inject_helper.build_injection` accept the previous Claude Code payload shape exactly as before; new Codex shape is additive.
- No schema migrations.
- No new required dependencies. The adapter snippet uses `python3` from PATH, same pattern as the other adapters.
- Cursor / pi adapters unaffected (separate config files, separate server names: `world-model` with hyphen for Cursor/pi; `world_model` with underscore for Codex).

### Upgrade path

```bash
pip install -U world-model-mcp
python -m world_model_server.cli install-codex   # if you use Codex
```

Existing Cursor / pi / Claude Code installs do not need any action.

---

## v0.7.4 (May 2026)

Interop, deployment, benchmark. No new adapters this release -- positioning over distribution surface.

### Headline

v0.7.0 through v0.7.3.1 shipped the primitives and the channels. v0.7.4 ships three things that connect them to what the ecosystem actually asked for: read the format the community standardized on, deploy where the platform left a memory gap, and publish numbers for the contradiction-resolution claim instead of just asserting it.

### New features

- **AGENTS.md / `.agents/skills/` constraint reader (F1)** -- new `world_model_server/agents_md_reader.py` parses `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and `.agents/skills/*.md` files into virtual constraints that mix into PreToolUse enforcement alongside the SQLite-backed constraints. Supports three extraction modes:
  1. Structured fence blocks (`` ```constraint `` ... `` ``` ``)
  2. YAML frontmatter with a `constraints:` list
  3. Heuristic imperative-sentence extraction ("Use X", "Never Y", "Always Z", "Prefer A over B") for prose-style AGENTS.md files

  The fence + frontmatter modes use no external YAML dependency -- the parser is hand-rolled to keep stdio installs zero-dep. Heuristic mode produces virtual constraints with severity `warning` (for strong verbs) or `info` (for soft verbs) so they never hard-deny on their own. New MCP tool: `get_agents_md_constraints`. Motivated by [anthropics/claude-code#6235](https://github.com/anthropics/claude-code/issues/6235) (4,000+ thumbs-up).

- **Self-hosted Claude Managed Agents deployment guide (F2)** -- new doc at `docs/deployment/managed-agents-self-hosted.md` plus a complete Modal quickstart under `examples/managed-agents-self-hosted/` (`deploy_modal.py` + `ant-setup.sh`). Targets the exact gap Anthropic's [May 19 blog post](https://claude.com/blog/claude-managed-agents-updates) named: *"Memory is not yet supported in self-hosted sessions."* world-model-mcp's streamable HTTP transport plus MCP tunnels covers that case. Deploy on Modal in ~5 minutes; wire into Anthropic with two `ant` CLI commands.

- **Contradiction-resolution benchmark (F3)** -- new `benchmarks/contradictions/` with a 24-pair dataset, deterministic runner, and committed results. Headline numbers: **93.5% overall**, **100% on `keep_higher_confidence` and `keep_most_sources`**, 90.9% on `keep_most_recent`, 87.5% on `auto`. RESULTS.md documents four honest failures (true-tie handling, sub-0.1 confidence gaps) instead of hiding them. New GitHub Actions workflow re-runs the benchmark on every push to `world_model_server/contradictions.py` and fails on regressions. Reproducible with one command: `python benchmarks/contradictions/run.py`.

### Why these three, not more adapters

The deep ecosystem sweep (run May 29) showed three things:

1. **mcp-memory-service shipped 22 releases in May**, including NLI contradiction detection (v10.67.0 May 28), a `/memory` slash + TUI sidebar (v10.65.0 May 24), and passive observation (v10.70.1 May 29). Building another web dashboard would have been me-too.
2. **The single most-engaged feature request across the entire space is AGENTS.md adoption** (claude-code #6235, 4,000+ thumbs-up). Already adopted natively by Zed (v1.4) and Cline (v3.86). Reading AGENTS.md is the highest-leverage interop bet available.
3. **Anthropic's own blog admits self-hosted Managed Agents have no memory primitive yet.** We already ship the HTTP transport. What was missing was the *deployment recipe* that lets enterprise users connect the dots.

The next-most-time-sensitive opportunities (Codex CLI adapter, Antigravity CLI adapter before June 18, MCP-spec 2026-07-28 refactor) move to v0.8.0. The standalone web dashboard, Continue adapter, and superagent-ai/grok-cli adapter were dropped from the roadmap entirely.

### Tool and CLI surface

- 26 MCP tools (was 25): added `get_agents_md_constraints`
- 17 CLI subcommands (unchanged)

### Tests

283 passing (was 262): 21 new tests in `tests/test_v074_features.py` cover the AGENTS.md reader (10 tests including fence extraction, frontmatter, imperative sentences, glob filtering, dedup across files, severity normalization, MCP tool, hook integration), the self-hosted deployment doc artifacts (3 tests), and the benchmark (4 tests including end-to-end run). Plus 3 backward-compat regression tests and an updated version assertion.

### Backward compatibility

- All 25 v0.7.3.1 MCP tools and all 17 CLI subcommands work unchanged
- AGENTS.md mixing into `hook_helper.classify()` is additive -- when no AGENTS.md files exist, the behavior is identical to v0.7.3.1
- No schema migrations
- No new required dependencies (the AGENTS.md parser uses only stdlib)
- Cursor / pi adapters, .mcpb desktop extension, stdio transport, HTTP transport (v0.7.2), MCP tunnel deployment (v0.7.2), `world-model demo` (v0.7.3), telemetry (v0.7.3.1) all unaffected

### Upgrade path

```bash
pip install -U world-model-mcp
# Existing projects auto-pick-up AGENTS.md / .agents/skills/ on the next
# PreToolUse hook fire -- no setup step required.
```

For self-hosted Managed Agents users: see `docs/deployment/managed-agents-self-hosted.md`.

For the benchmark: `python benchmarks/contradictions/run.py` from a clone.

---

## v0.7.3.1 (May 2026)

Patch release that activates the opt-in telemetry path introduced in v0.7.3.

### What changed

- The PAT used to write opt-in telemetry events to
  `SaravananJaichandar/world-model-telemetry` is now embedded in the wheel
  at `world_model_server/_embedded_token.py`.
- v0.7.3 shipped with that file as an empty stub (`EMBEDDED_TOKEN = ""`),
  which made `record()` silently no-op even for users who opted in.
  v0.7.3.1 ships the same file populated.
- No code changes besides the version bumps. The embed mechanism
  (`scripts/embed_token.py`, the gitignored `.env.release` file, the
  release procedure in `RELEASE.md`) was added in the prior commit;
  this release is the first one to actually use it end to end.

### Security model recap

The embedded token is scoped only to the telemetry repo with
`Issues: Read and write`. Anyone who installs the wheel can extract it
from `_embedded_token.py` -- this is intentional and standard for OSS
telemetry. The worst-case attack is spamming the private telemetry repo
with issues. If that happens: revoke, regenerate, ship v0.7.3.2.

### User-visible behavior

- Telemetry is still **off by default**. Existing installs behave
  identically until the user explicitly opts in.
- `world-model setup` still prompts once for consent.
- `world-model telemetry --status` shows the install ID and a sample
  payload. The status output's "Repo:" field now correlates with where
  events would actually land.
- `WORLD_MODEL_TELEMETRY_DISABLE=1` continues to override everything.

### Tests

262 passing (unchanged from v0.7.3). No new test surface; the embed-flow
tests added in v0.7.3 cover the wiring.

### Backward compatibility

All v0.7.3 surface unchanged: 17 CLI subcommands, 25 MCP tools, Cursor /
pi / .mcpb / HTTP transport all unaffected.

---

## v0.7.3 (May 2026)

Onboarding, opt-in telemetry, and a pi adapter. Existing surface unchanged.

### Headline

v0.7.0 - v0.7.2 added the load-bearing primitives (constraint enforcement, PostCompact injection, contradiction resolution, HTTP transport). v0.7.3 attacks the second-order problem: a new user installing for the first time sees an empty database and has no path to the value. v0.7.3 ships three things to close that gap:

1. **`world-model demo`** - a one-command guided tour that seeds reproducible data and exercises each primitive with real outputs.
2. **Opt-in telemetry** - so future product decisions are informed by actual usage data, not download counts. Off by default, prompted once, inspectable.
3. **pi adapter** - audience expansion to the 51k-star [earendil-works/pi](https://github.com/earendil-works/pi) ecosystem via a pi-package extension.

### New features

- **`world-model demo` (F1)** - new CLI subcommand. Initializes the world-model database (if missing), runs `scripts/demo_seed.py --reset --seed-after-reset` to populate realistic constraints, facts, a contradiction pair, and a compaction audit row, then prints the actual JSON output of each primitive (PreToolUse classify, find_contradictions, get_injection_context, get_compaction_audit). Reproducible end-to-end on a fresh clone.
- **Opt-in telemetry (F2)** - new `world_model_server/telemetry.py` module. urllib-only (no new required deps), fail-open on any error, rate-limited to 1 event/60s per install, async fire-and-forget. New CLI subcommand `world-model telemetry` with `--enable / --disable / --status`. `world-model setup` prompts once for consent in interactive sessions; `--no-prompt` flag and `WORLD_MODEL_NO_PROMPT=1` env var skip the prompt for CI/scripted setup. Stable opaque `install_id` at `~/.world-model/install_id`. Destination: dedicated private GitHub repo `SaravananJaichandar/world-model-telemetry` (issues-write only). Global kill switch `WORLD_MODEL_TELEMETRY_DISABLE=1`. Never collects file paths, code, hostnames, IPs, rule names, or fact text. Full payload schema documented in README.
- **pi adapter (F3)** - new `adapters/pi/` package and bundled copy at `world_model_server/adapters/pi/`. TypeScript extension subscribes to pi's `tool_call`, `context`, and `session_compact` events; spawns the existing Python `hook_helper` / `inject_helper` as subprocesses so the enforcement and injection logic stays in one place across Claude Code, Cursor, and pi. The `defer` enforcement tier is surfaced to pi as an advisory `block` with `[review]` prefix because pi has no defer tier. New CLI subcommand `world-model install-pi` writes the adapter into `<project>/adapters/world-model-pi/` for `pi install local:` consumption.

### CLI surface

- 17 CLI subcommands (was 14): added `demo`, `telemetry`, `install-pi`
- 25 MCP tools (unchanged)

### Tests

256 passing (was 236). 20 new tests in `tests/test_v073_features.py` cover:
- Telemetry off-by-default state, kill-switch precedence, install-id stability, no-token silent no-op, sync record returns False when disabled, preview payload omits sensitive keys, CLI subcommand status/enable/disable, setup `--no-prompt` flag
- `world-model demo` runs cleanly on a fresh project, creates `.claude/world-model/`, exercises each primitive
- pi adapter file existence, package.json schema, index.ts event wiring (`tool_call`/`context`/`session_compact` subscribed + correct helper modules invoked), bundled-in-package fixture, `install-pi` CLI with and without `--force`
- Backward-compat regression: all v0.6 + v0.7.0 + v0.7.2 subcommands still registered, v0.7.2 HTTP transport still boots, setup in non-TTY environment doesn't hang

### Backward compatibility

- All 22 v0.6 MCP tools work unchanged
- All 14 v0.7.2 CLI subcommands work unchanged (`setup`, `seed`, `query`, `decisions`, `register`, `projects`, `search-global`, `health`, `decay`, `recall`, `export-claude-md`, `migrate`, `status`, `audit-compactions`, `install-cursor`)
- No schema migrations
- No new required dependencies (telemetry uses stdlib `urllib`; HTTP transport extras unchanged)
- Cursor adapter, .mcpb desktop extension, stdio transport, MCP tunnel deployment all unaffected
- The Glama Dockerfile keeps its stdio shape

### Versioning note

`__version__` is now `0.7.3`. The v0.7.2 `test_f6_version_is_072` assertion was relaxed to `test_f6_version_is_at_least_072` to make future patch releases pass without manual test updates.

### Upgrade path

```bash
pip install -U world-model-mcp
world-model demo   # see all primitives running on a fresh project
```

For existing users running `world-model setup` on a project that already has `.claude/world-model/`: the telemetry prompt appears once if you've never answered it, then never again.

### Known gaps (still in v0.8 scope)

- Antigravity adapter (Google's agentic IDE; replaces Gemini CLI which sunsets June 18, 2026)
- Codex CLI adapter (OpenAI)
- Cline + Continue adapters
- Local web dashboard for the knowledge graph
- AST-based extraction substrate

---

## v0.7.2 (May 2026)

Streamable HTTP transport for remote and MCP-tunnel deployments.

### What's new

Until v0.7.1 the server only spoke stdio, which is the right transport for
Claude Code, Cursor, and `.mcpb` installs but does not work for deployments
where the MCP server lives behind a firewall and the agent reaches it from
Anthropic-side infrastructure. v0.7.2 adds an opt-in streamable HTTP
transport so world-model-mcp can be deployed as a long-lived HTTP service
inside the customer's own perimeter -- the configuration Claude Managed
Agents' MCP tunnels (research preview) target.

- **Streamable HTTP transport** -- set `WORLD_MODEL_TRANSPORT=http` to expose
  the same 25 MCP tools over HTTP instead of stdio. Default stays stdio so
  existing Claude Code / Cursor / .mcpb installs are unaffected.
- **Environment variables**: `WORLD_MODEL_TRANSPORT`, `WORLD_MODEL_HTTP_HOST`
  (default `0.0.0.0`), `WORLD_MODEL_HTTP_PORT` (default `8765`),
  `WORLD_MODEL_HTTP_PATH` (default `/mcp`).
- **`GET /healthz` endpoint** -- returns `{"status":"ok","version":"0.7.2"}`.
  Cheap probe for Docker / Kubernetes / `ant tunnels` upstream health.
- **`Dockerfile.http`** -- pre-built image that installs the `http` extras,
  exposes port 8765, and includes a `HEALTHCHECK` directive. The original
  `Dockerfile` (stdio, used by Glama) is unchanged.
- **`docker-compose.yml`** -- reference compose file with persistent volume
  for the SQLite database.
- **`docs/deployment/mcp-tunnel.md`** -- end-to-end walkthrough including
  `ant tunnels` setup for Claude Managed Agents.
- **`[http]` optional extras** -- `pip install 'world-model-mcp[http]'`
  pulls `uvicorn` and `starlette`. Stdio installs do not see these as
  required dependencies.

### Tests

236 passing (was 223): added 13 v0.7.2 tests in
`tests/test_v072_http_transport.py` covering transport selection, the
`/healthz` endpoint, MCP path mounting, custom `WORLD_MODEL_HTTP_PATH`,
helpful error on missing `http` extras, and backward-compat regression on
the stdio path.

### Backward compatibility

- All 22 v0.6 MCP tools and all 25 v0.7 MCP tools work unchanged in both
  transports
- Default transport stays stdio: existing Claude Code / Cursor / .mcpb users
  see zero behavior change
- The Glama Dockerfile (no suffix) keeps its shape: stdio entrypoint, no port
  exposed, no http extras
- The Cursor adapter and PyPI install path are not affected
- No schema migrations

---

## v0.7.1 (May 2026)

Patch release fixing the Cursor adapter shipped in v0.7.0.

### Cursor adapter rewrite

The v0.7.0 adapter declared hook events (`beforeEdit`, `afterCompact`) and
used a config shape that did not match Cursor's actual hooks API. v0.7.1
rewrites the adapter against Cursor's real schema:

- `hooks.json` now uses the object-keyed `{ "version": 1, "hooks": { eventName: [...] } }` shape
- Event names corrected to `beforeSubmitPrompt`, `preToolUse` (with `matcher`), and `preCompact`
- `timeout` is in seconds (was `timeout_ms`)
- `failClosed: false` replaces the old `fail_open: true` (inverted semantics)
- Node wrappers now live in `.cursor/hooks/` (was `.claude/hooks/`, which did not exist after the adapter install)
- `mcp.json` uses a relative `WORLD_MODEL_DB_PATH` instead of the un-documented `${workspaceFolder}` variable

### New CLI: `install-cursor`

Replaces the brittle copy-paste install step with `python -m world_model_server.cli install-cursor`. The command copies `mcp.json`, `hooks.json`, and the compiled Node hook wrappers into `.cursor/` from the installed package. Supports `--force` to overwrite existing files.

Adapter resources are now bundled inside the wheel at `world_model_server/adapters/cursor/`, so installs from PyPI ship the adapter files correctly.

### Adapter README updates

- Real install steps using the new CLI
- Note about Cursor's one-click MCP approval prompt on first run
- Section explaining the overlap with Cursor Memories and Cursor Rules
- Note that `defer` maps to `ask` in Cursor (no separate headless decision)
- Note that `preCompact` runs before summarization (no `postCompact` in Cursor yet)

### Tests

- Updated `test_f4_cursor_adapter_hooks_json_is_valid` to assert the new object-keyed schema
- 220 tests still passing

### Backward compatibility

- v0.7.0 PyPI / MCP registry / .mcpb release is unchanged; the broken Cursor adapter in that release will simply fail to load when Cursor parses it -- it does not break Claude Code
- Users on v0.7.0 should upgrade with `pip install -U world-model-mcp` then rerun `python -m world_model_server.cli install-cursor --force` to refresh the adapter files

---

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
