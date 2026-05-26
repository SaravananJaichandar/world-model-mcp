# world-model-pi (experimental)

A pi-package extension that wires `world-model-mcp` into pi's extension
lifecycle. Reuses the same Python helpers that power the Claude Code and
Cursor adapters -- only the manifest format changes.

Status: experimental. The pi extension API is moving fast (May 2026) and the
event names may change. PRs welcome.

## What you get

- `tool_call` -> PreToolUse-equivalent enforcement. The Python `hook_helper`
  validates every Edit / Write / Bash against learned constraints; hard
  violations (severity=error, count>=3) return `{block: true, reason}` to
  pi so the edit never lands.
- `context` -> auto-injection on every LLM call. The `inject_helper` returns
  the top constraints plus recent canonical facts as a system-prefix message
  so the agent does not drop project conventions across compaction.
- `session_compact` -> records each compaction event in the world-model
  audit log (pre/post token counts plus what was re-injected).
- Slash command `wm-status` for a quick state check.

The `defer` enforcement tier from `hook_helper` is surfaced to pi as an
advisory block prefixed `[review]`, because pi's hook contract does not yet
expose a separate "pause headless" decision.

## Install

```bash
# 1. Install the Python side (world-model-mcp + helpers)
pip install -U world-model-mcp

# 2. Install the pi extension
pi install npm:world-model-pi
# or, from a local checkout:
pi install local:/path/to/world-model-mcp/adapters/pi
```

The extension expects `python3` on `PATH` and `world-model-mcp` installed
in the active Python environment.

## Storage

The pi adapter writes to `~/.pi/agent/world-model/` by default. Set
`WORLD_MODEL_PI_DB` to share a graph with your Claude Code install, e.g.

```bash
export WORLD_MODEL_PI_DB="$HOME/projects/myproject/.claude/world-model"
```

## How the hooks map

| Pi event | world-model-mcp helper | What it does |
| --- | --- | --- |
| `tool_call` (Edit/Write/Bash) | `world_model_server.hook_helper` | Returns `deny` / `defer` / `ask` / `allow` based on learned constraints |
| `context` (every LLM call) | `world_model_server.inject_helper` | Splices a constraints + recent-facts bundle as a system message |
| `session_compact` | `world_model_server.inject_helper` (PostCompact event) | Writes a row to the compaction audit log |

All helpers fail open. If `python3` is missing or world-model-mcp is not
installed, the hooks return silently and pi runs as if the extension were
not there.

## Build (for contributors)

The package ships TypeScript that pi loads via `jiti` directly -- no
compile step required for use. To run the type-checker locally:

```bash
cd adapters/pi
npx tsc --noEmit index.ts
```

## Reporting issues

Open an issue on the main repo with the `adapter:pi` label:
<https://github.com/SaravananJaichandar/world-model-mcp/issues>
