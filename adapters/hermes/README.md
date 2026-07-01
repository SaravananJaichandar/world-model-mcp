# Hermes Agent adapter (experimental)

This adapter wires `world-model-mcp` into
[Hermes Agent](https://github.com/NousResearch/hermes-agent) (NousResearch,
MIT) as an external MCP server. Hermes is a self-improving personal agent
runtime with lifecycle hooks (`pre_tool_call`, `post_tool_call`,
`on_session_start`, `on_pre_compress`, ...) and its own bounded memory
system (`MEMORY.md` + `USER.md`, FTS5 search, Honcho dialectic user modeling).

Status: experimental. Verified end-to-end against Hermes Agent
`v0.17.0 (2026.6.19)` on macOS on 2026-07-01: `hermes mcp test
world-model` reports 27 tools discovered. YAML merge preserves the
1327-line reference `~/.hermes/config.yaml` including all inline
comments and blank lines (regression test in
[`tests/test_v010_hermes_features.py`](../../tests/test_v010_hermes_features.py)).

## Why this adapter

Hermes ships a memory system. So why plug in a second one?

Hermes memory is **bounded and manual**: strict character caps
(2,200 chars for agent notes, 1,375 for user profile) and no
auto-decay. The docs are explicit: *"Memory does not auto-compact:
when a write would exceed the limit, the memory tool returns an error
instead of silently dropping entries."* The intended workflow is
manual consolidation via `replace` and `remove`.

world-model-mcp complements that with:

| Dimension | Hermes built-in memory | world-model-mcp |
| --- | --- | --- |
| Capacity | Character-bounded (2,200 + 1,375) | Unbounded (SQLite; ~800 bytes/fact) |
| Aging | Manual curation via `replace` / `remove` | Per-evidence-type decay half-lives (test 180d, bug_fix 365d, user_correction 730d, source_code 365d, session 14d) |
| Provenance | Entry content only | `asserted_by`, `confirmer`, `confirmation_state`, `evidence_type`, `last_decay_at` |
| Contradictions | None | Confidence-weighted resolution (`auto`, `keep_higher_confidence`, `keep_most_recent`, `keep_most_sources`) |
| Enforcement | Prompt-level | `PreToolUse` defer tier (hard-block on constraint violations) |
| Cross-agent share | `MEMORY.md` per Hermes install | Same SQLite path shared across Claude Code / Cursor / Codex / OpenClaw when co-installed |

The two are additive. Hermes handles short-form self-notes and user
profile; world-model-mcp handles the temporal fact graph with
provenance and decay.

## Overlap with the `MemoryProvider` plugin ABC

Hermes also exposes a Python `MemoryProvider` ABC
([docs](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin/))
for deeper integration than an MCP server can offer. Only ONE
external memory provider can be active at a time. That slot is
currently occupied for many users by
[yoloshii/ClawMem](https://github.com/yoloshii/ClawMem),
a shared-SQLite-vault adapter for Claude Code + OpenClaw + Hermes.

**This adapter takes the MCP route first, not the MemoryProvider
route.** Reasons:

1. Lower integration cost: MCP is already the world-model-mcp native
   surface. The `MemoryProvider` route would require a Python plugin
   package on `PYTHONPATH` and a wrapper around the 26 MCP tools.
2. Non-exclusive: MCP servers stack. The `MemoryProvider` slot is
   binary. Users who already run ClawMem in that slot can add
   world-model-mcp via MCP without displacing ClawMem.
3. Distribution: users can discover this adapter via
   `hermes mcp` docs without hunting for a separate plugin registry.

A native `MemoryProvider` plugin remains on the v0.10+ roadmap and
will ship only if MCP-route adoption justifies the plugin work.

## Install (recommended: CLI subcommand)

Requires the optional `hermes` extra so `pyyaml` is available for
the YAML merge:

```bash
# 1. Install the package with the Hermes extra
pip install -U "world-model-mcp[hermes]"

# 2. Initialize the world-model database in this project
python -m world_model_server.cli setup

# 3. Register world-model as an MCP server in Hermes
python -m world_model_server.cli install-hermes
```

Step 3 merges an `mcp_servers.world-model` block into
`~/.hermes/config.yaml`, preserving all other keys. Defaults the
`command` field to `sys.executable` (absolute path to the interpreter
running the CLI). If the OpenClaw PATH-spawn gotcha applies to
Hermes as well, the absolute-path default side-steps it; if not, no
harm done.

Then reload Hermes:

```bash
# From inside a Hermes session:
/reload-mcp
```

**Flags** — `install-hermes` supports:

| Flag | Purpose |
| --- | --- |
| `--config-path PATH` | Override `~/.hermes/config.yaml` (used by tests; not usually needed) |
| `--python PATH` | Absolute path to the python3 you want Hermes to spawn. Default: the interpreter running the CLI. Relative values are rejected. |
| `--db-path PATH` | Value for `WORLD_MODEL_DB_PATH`. Default: `.claude/world-model` |
| `--dry-run` | Print the proposed `mcp_servers.world-model` entry without writing |
| `--force` | Replace the entry if `mcp_servers.world-model` already exists |

## Manual install (fallback)

If you prefer to edit `~/.hermes/config.yaml` by hand, add the
following block. Replace the placeholder with the output of
`which python3`:

```yaml
mcp_servers:
  world-model:
    command: /absolute/path/to/python3
    args:
      - -m
      - world_model_server.server
    env:
      WORLD_MODEL_DB_PATH: .claude/world-model
    enabled: true
    timeout: 30
```

Same block is bundled as [`config-snippet.yaml`](./config-snippet.yaml)
for copy-paste.

Then run `/reload-mcp` inside a Hermes session.

## What Hermes sees

Hermes' LLM turn will see `world-model` in its available MCP tool
list, alongside any other MCP servers registered under `mcp_servers`.
All 27 world-model tools become callable (see the top-level README
for the tool list).

For channel-agent workflows (Hermes routes across chat channels),
the highest-value tools are typically:

| Tool | Why it helps |
| --- | --- |
| `query_fact` | Look up what has been established about the user, a project, or a channel across sessions |
| `assert_fact` | Record a new fact from the conversation |
| `find_contradictions` | Catch when a new user statement conflicts with a stored fact — pairs well with Hermes' Honcho dialectic user modeling |
| `resolve_contradiction` | Pick the winner using confidence, recency, or source count |
| `get_injection_context` | Pull top constraints + recent facts to inject into the next Hermes turn (complements Hermes' own `on_pre_compress` hook) |

## Where the database lives

The adapter uses `.claude/world-model/` as the database path, matching
the Claude Code, Cursor, and OpenClaw adapters. If you run
world-model-mcp with both Hermes and one of those clients against the
same project directory, they share the same store.

To keep Hermes' world-model DB in a different location (for example,
Hermes is a user-wide install and you want a user-wide world-model
DB), override with an absolute path:

```bash
python -m world_model_server.cli install-hermes \
    --db-path /absolute/path/to/world-model
```

## Roadmap for this adapter

The MCP-only integration ships in v0.10. Two follow-ups on the
roadmap:

**v0.10.x — Native `MemoryProvider` plugin package.** Implement the
Hermes `agent/memory_provider.py` ABC (`initialize`,
`get_tool_schemas`, `handle_tool_call`, `get_config_schema`,
`save_config`) as a proper Python plugin so world-model-mcp can
occupy the exclusive external-memory-provider slot when the user
chooses it over ClawMem or other MemoryProvider implementations.
Will ship only if MCP-route adoption justifies the plugin work.

**v0.10.x — Lifecycle hook integration.** Hermes exposes typed
hooks (`pre_tool_call`, `post_tool_call`, `on_session_start`,
`on_session_end`, `on_pre_compress`, ...). Wiring these gives
Hermes the same PreToolUse-defer, PostCompact-inject, and
SessionStart-warm behavior the Claude Code and Cursor adapters
ship. Requires either an MCP-side extension or a native Hermes
plugin; will be scoped after the MemoryProvider plugin decision.

## Caveats

- **MCP-only, no hooks yet.** Same trade-off as the OpenClaw
  adapter first cut. Hermes' lifecycle hooks (`pre_tool_call`,
  `on_pre_compress`, ...) are not wired in this release.
- **YAML dep is optional.** `install-hermes` requires `ruamel.yaml`
  (round-trip mode for comment preservation), installed via the
  `[hermes]` extra. If you `pip install world-model-mcp` without the
  extra, `install-hermes` will fail fast with a message pointing you
  to `pip install "world-model-mcp[hermes]"`. The manual install
  path (edit `~/.hermes/config.yaml` yourself) needs no extra deps.
  Why `ruamel.yaml` and not plain `pyyaml`: Hermes ships a heavily
  commented reference `~/.hermes/config.yaml` (~1300 lines,
  ~1000 of documentation comments). A round-trip loader is required
  to keep those comments intact through the merge — verified via
  the `test_f2_install_hermes_preserves_comments_and_blank_lines`
  regression test after an initial `pyyaml`-based implementation
  destroyed 1170 lines of comments during E2E testing.
- **`python3` should be an absolute path.** OpenClaw's process spawn
  is known to not inherit shell PATH, causing `--command python3`
  to fail probe. Hermes' spawn behavior has not been directly
  verified as of 2026-07-01. As a precaution, `install-hermes`
  defaults `--command` to `sys.executable` (absolute) and rejects
  relative `--python` overrides. If you hand-edit
  `~/.hermes/config.yaml`, use an absolute path for `command`.
- **`.claude/world-model` is a relative path.** It resolves against
  Hermes' process working directory. If Hermes is a user-wide
  gateway rather than launched per-project, use an absolute
  `--db-path`.
- **HTTP transport.** For `world-model-mcp` deployed over HTTP
  (see [docs/deployment/managed-agents-self-hosted.md](../../docs/deployment/managed-agents-self-hosted.md)),
  register the server with the Hermes HTTP transport shape instead:
  drop `command`/`args`/`env`, add `url:` and `transport:
  streamable-http` (or `sse`).

## Reporting issues

Open an issue on the main repo with the `adapter:hermes` label:
<https://github.com/SaravananJaichandar/world-model-mcp/issues>

Include:
- Hermes Agent version (`hermes --version` or the value at the
  top of `~/.hermes/config.yaml`)
- Relevant `mcp_servers.world-model` block from
  `~/.hermes/config.yaml`
- Any Hermes stderr for the world-model process
