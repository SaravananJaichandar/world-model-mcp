# OpenClaw adapter (experimental)

This adapter wires `world-model-mcp` into [OpenClaw](https://github.com/openclaw/openclaw)
as an external MCP server. OpenClaw is a local-first personal AI assistant
that routes conversations across WhatsApp, Telegram, Slack, Discord and
other channels. It ships no native memory layer, so world-model-mcp is
pure additive here: persistent facts, per-fact provenance, and per-evidence
decay for a runtime that had none of those.

Status: experimental. Verified end-to-end against OpenClaw
`2026.6.11 (e085fa1)` on macOS on 2026-07-01: `openclaw mcp probe
world-model` reports 27 tools discovered. PRs welcome for any breakage;
see the **Reporting issues** section.

## What you get

- `world-model-mcp` registered as an MCP server inside OpenClaw, exposing
  the same 26 tools that the Claude Code, Cursor, Codex, and pi adapters
  expose (`query_fact`, `validate_change`, `find_contradictions`,
  `resolve_contradiction`, `get_injection_context`, `get_compaction_audit`,
  and so on).
- No overlap with any built-in OpenClaw memory layer — because OpenClaw
  does not ship one. All persistence is world-model-mcp's schema.

This first cut is **MCP-only**. OpenClaw exposes an extensive typed
lifecycle hook surface (`before_prompt_build`, `before_tool_call`,
`session_start`, `before_compaction`, ...). Wiring those into
world-model-mcp requires an OpenClaw plugin bundle (TypeScript) and is
tracked separately — see **Roadmap for this adapter** below.

## Install (from your project root)

```bash
# 1. Install the package
pip install -U world-model-mcp

# 2. Initialize the world-model database in this project
python -m world_model_server.cli setup

# 3. Register world-model as an MCP server in OpenClaw
#    Use the ABSOLUTE path to python3 — OpenClaw's process spawn does
#    not inherit your shell's PATH, so `python3` alone will fail with
#    "MCP error -32000: Connection closed" during probe.
openclaw mcp add world-model \
    --command "$(which python3)" \
    --arg -m \
    --arg world_model_server.server \
    --env WORLD_MODEL_DB_PATH=.claude/world-model
```

Step 3 writes into `~/.openclaw/openclaw.json` under the `mcp.servers`
object. The equivalent JSON snippet, if you prefer to edit the config
file by hand, is bundled as [`openclaw.json`](./openclaw.json). Replace
`/absolute/path/to/python3` with the output of `which python3`:

```json
{
  "mcp": {
    "servers": {
      "world-model": {
        "command": "/absolute/path/to/python3",
        "args": ["-m", "world_model_server.server"],
        "env": {
          "WORLD_MODEL_DB_PATH": ".claude/world-model"
        }
      }
    }
  }
}
```

Then restart the OpenClaw gateway. Verify the wire-up with:

```bash
openclaw mcp list
```

`world-model` should appear in the list. From an OpenClaw agent session,
any tool with the `world-model` prefix confirms the server is reachable.

## Where the database lives

The adapter uses `.claude/world-model/` as the database path, matching
the Claude Code and Cursor adapters. If you run world-model-mcp with both
OpenClaw and one of those clients against the same project, they share
the same store.

If you want OpenClaw's world-model DB in a different location (for
example, OpenClaw is a user-wide install and you want a user-wide
world-model DB), override with an absolute path:

```bash
openclaw mcp add world-model \
    --command python3 \
    --arg -m \
    --arg world_model_server.server \
    --env WORLD_MODEL_DB_PATH=/absolute/path/to/world-model
```

## What OpenClaw sees

OpenClaw's LLM turn will see `world-model` in its available tool list,
same as any other MCP server registered via `openclaw mcp add`. The
LLM can then call `query_fact` to check what has been learned about
the user, `validate_change` before writing to files (if OpenClaw is
running a coding-adjacent workflow), or `get_injection_context` to pull
the top constraints and recent facts into its own prompt.

For channel workflows (WhatsApp/Telegram/Slack) the most useful tools
are typically:

| Tool | Why it helps |
| --- | --- |
| `query_fact` | Look up what has been established about the user, a project, or a channel |
| `assert_fact` | Record a new fact from the conversation |
| `find_contradictions` | Catch when a new user statement conflicts with a stored fact |
| `resolve_contradiction` | Mark the winner (usually the newer statement) and archive the loser |
| `get_injection_context` | Pull the top-N facts to inject into the next LLM turn |

## Roadmap for this adapter

The MCP-only integration ships in v0.10. Two follow-ups are on the roadmap:

**v0.10.x — `install-openclaw` CLI subcommand.** Parity with
`install-cursor` and `install-codex`: a single command that runs
`openclaw mcp add` for you with sensible defaults and prints what
changed. Until this ships, the manual `openclaw mcp add` command in
Step 3 above is the supported path.

**v0.10.x or later — OpenClaw plugin bundle for typed lifecycle hooks.**
OpenClaw exposes a rich set of hook events documented at
[docs.openclaw.ai/plugins/hooks](https://docs.openclaw.ai/plugins/hooks):
`before_prompt_build`, `before_tool_call`, `after_tool_call`,
`session_start`, `session_end`, `before_compaction`, `after_compaction`,
and more. Wiring these into world-model-mcp gives OpenClaw the same
PreToolUse-defer, PostCompact-inject, and SessionStart-warm behavior
the Claude Code and Cursor adapters ship. This requires a TypeScript
plugin bundle. It will land only if MCP-only adoption of the OpenClaw
adapter justifies the plugin work.

## Overlap with ClawMem

[yoloshii/ClawMem](https://github.com/yoloshii/ClawMem) ships a
cross-runtime memory adapter for Claude Code + OpenClaw + Hermes
against a shared SQLite vault. If you already run ClawMem and are
weighing whether to switch:

- **ClawMem** is a plain SQLite vault: `key -> value` with timestamps.
  Simple, cross-runtime, zero opinionated schema.
- **world-model-mcp** is a schema-first store: per-fact provenance
  (`asserted_by`, `confirmer`, `confirmation_state`, `evidence_type`,
  `last_decay_at`), per-evidence-type decay half-lives (test 180d,
  bug_fix 365d, user_correction 730d, source_code 365d, session 14d),
  a `PreToolUse` defer enforcement tier for hooks, and a pre-registered
  SWE-bench Verified benchmark with published methodology (see
  [`benchmarks/repeat-mistake/`](../../benchmarks/repeat-mistake/) and
  Zenodo DOI 10.5281/zenodo.20834509).

The two are not drop-in replacements. Pick ClawMem if you want a lightweight
shared vault. Pick world-model-mcp if you want the schema and enforcement
depth described above.

## Caveats

- **MCP-only, no hooks yet.** The current adapter only wires the MCP
  server. Lifecycle hook integration (defer, PostCompact-inject,
  SessionStart-warm) requires a TypeScript plugin bundle — not shipped
  in this cut.
- **Interpreter env vars are blocked.** OpenClaw blocks
  interpreter-startup environment variables like `PYTHONPATH` and
  `NODE_OPTIONS` in the `env` block for security. If your Python setup
  requires `PYTHONPATH` to find `world_model_server`, install it into
  the environment `python3` resolves to instead of relying on the env
  block.
- **`python3` MUST be an absolute path — bare `python3` will fail.**
  OpenClaw's process spawn does not inherit your shell's PATH. If you
  register the server with `--command python3` you will see
  `MCP error -32000: Connection closed` on probe even though running
  `python3 -m world_model_server.server` works fine from your shell.
  Fix: use `$(which python3)` in the CLI command, or hand-edit
  `~/.openclaw/openclaw.json` to substitute the absolute path.
  Verified against OpenClaw `2026.6.11 (e085fa1)` on macOS on 2026-07-01.
- **Working directory.** Because `WORLD_MODEL_DB_PATH=.claude/world-model`
  is a relative path, it resolves against OpenClaw's process working
  directory. If OpenClaw is a user-wide gateway (not launched from a
  project root), use an absolute `WORLD_MODEL_DB_PATH` instead.
- **HTTP transport.** If you deploy world-model-mcp over HTTP (Modal or
  similar; see [docs/deployment/managed-agents-self-hosted.md](../../docs/deployment/managed-agents-self-hosted.md)),
  register the server with `transport: "streamable-http"` and a `url:`
  field instead of `command`/`args`.

## Reporting issues

Open an issue on the main repo with the `adapter:openclaw` label:
<https://github.com/SaravananJaichandar/world-model-mcp/issues>

Include:
- OpenClaw version (`openclaw --version`)
- Relevant `~/.openclaw/openclaw.json` `mcp.servers` block
- Any gateway stderr for the world-model process
