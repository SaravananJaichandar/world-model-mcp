# Hermes Agent — MemoryProvider plugin (v0.11.0 experimental)

This is the **write-side** integration between world-model-mcp and
Hermes Agent. It complements — does not replace — the MCP adapter
shipped in v0.10.0.

## Why the plugin instead of (or alongside) MCP

The v0.10 MCP adapter (`install-hermes`) surfaces 27 world-model
tools to Hermes agent turns. That is a **read/write-side tool
surface**: the agent can call `query_fact`, `assert_fact`,
`get_injection_context`, and so on when the LLM decides to. It does
not change where the agent's memory writes actually go — those still
default to `MEMORY.md`.

The MemoryProvider ABC is a **routing-side interception**. Hermes
consults the active provider when memory-shaped writes happen,
before the agent picks a destination. See discussion in
[Hermes #47349](https://github.com/NousResearch/hermes-agent/issues/47349)
for the architectural distinction.

Only one MemoryProvider slot is active at a time. If you install
this plugin, it takes the slot ClawMem or Hermes' built-in provider
would otherwise hold.

## What this plugin ships

- A Python `MemoryProvider` implementation backed by the world-model
  fact graph
- Seven surfaced tools:
  - `query_fact` — fact lookup by entity
  - `get_constraints` — learned constraints for a file
  - `get_injection_context` — top-N constraints and facts for
    session-boundary reinjection (complements Hermes'
    `on_pre_compress` hook)
  - `record_event` — capture a development event
  - `record_correction` — high-priority user-correction signal
  - `find_contradictions` — surface contradicting fact pairs
  - `resolve_contradiction` — pick a winner with the v0.11
    confirmer-aware + decay-aware `auto` strategy
- A soft dependency on the Hermes ABC — the plugin imports even when
  Hermes is not installed, so tests run without Hermes in the loop

## What this plugin does NOT ship (v0.11.0 first cut)

- Optional hooks (`sync_turn`, `on_pre_compress`, `prefetch`,
  `on_session_end`, `on_memory_write`) — a v0.11.x follow-up. The
  required ABC methods are in place; the optional hooks layer on top
  without changing the plugin contract.
- Full 27-tool parity with the MCP adapter. Only the seven highest-
  value tools are surfaced to keep the Hermes tool namespace clean.
  Users who want the full 27 can additionally register the v0.10 MCP
  adapter (they are non-exclusive from world-model's side).

## Install

```bash
pip install -U "world-model-mcp[hermes]"

# Install the MemoryProvider plugin into ~/.hermes/plugins/memory/world-model/
python -m world_model_server.cli install-hermes-provider

# Restart Hermes or reload plugins. In a Hermes session, verify with:
#   /memory provider   -> should list "world-model"
```

The install command copies the plugin files (`__init__.py`,
`plugin.yaml`, this README) into `~/.hermes/plugins/memory/world-model/`.
The plugin then imports `world_model_server` from the site-packages
that `pip install world-model-mcp` populated, so the fact graph, decay
function, and contradiction-resolution logic are the same code shipped
to Claude Code / Cursor / OpenClaw / Continue.

## Flags

`install-hermes-provider` supports:

| Flag | Purpose |
| --- | --- |
| `--hermes-home PATH` | Override the Hermes state directory (default: `~/.hermes`) |
| `--force` | Overwrite an existing world-model plugin directory |
| `--dry-run` | Print what would be copied without touching disk |

## Where the database lives

By default the plugin uses `<hermes_home>/world-model/` as the
SQLite location. This is the natural sibling to Hermes' own
state files at `<hermes_home>/`. To share the fact graph with a
project-scoped Claude Code / Cursor install, set
`WORLD_MODEL_DB_PATH` before starting Hermes.

## Caveats

- **Sync ↔ async bridge.** Hermes' ABC is synchronous;
  `WorldModelTools` methods are async. Each `handle_tool_call`
  opens a fresh event loop, runs the target method, and returns.
  Acceptable for a first ship. A persistent loop is a v0.11.x
  follow-up if per-call cost becomes measurable.
- **Slot-competition.** MemoryProvider is exclusive per Hermes
  install. Installing this plugin displaces ClawMem or the built-in
  provider from that slot.
- **v0.11.0 is experimental.** The plugin ships against the
  documented Hermes ABC as of 2026-07-02
  ([docs](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin/)).
  Report breakage against the current Hermes release on
  [the main repo](https://github.com/SaravananJaichandar/world-model-mcp/issues)
  with the `adapter:hermes-provider` label.

## Reporting issues

Open an issue on the main repo with the `adapter:hermes-provider`
label:
<https://github.com/SaravananJaichandar/world-model-mcp/issues>

Include:
- Hermes Agent version
- The `hermes plugin list` output
- The full stderr from `hermes` around a failing `handle_tool_call`
