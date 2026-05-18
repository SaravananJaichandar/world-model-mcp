# Cursor adapter (experimental)

This adapter wires `world-model-mcp` into Cursor as a harness-neutral memory
and enforcement layer. The same Python `inject_helper` and `hook_helper` that
power the Claude Code hooks are reused here; only the manifest format changes.

Status: experimental. The hook events Cursor exposes (`beforeSubmitPrompt`,
`afterEdit`, `afterCompact`) are still stabilizing as of v0.7.0. PRs welcome
for any breakage you hit.

## What you get

- `world-model-mcp` configured as an MCP server inside Cursor
- Pre-edit constraint check that mirrors the Claude Code PreToolUse hook
- Post-compaction context re-injection so Cursor doesn't lose your constraints
  and recent facts when the agent compacts its conversation

## Install

From your project root:

```bash
pip install world-model-mcp
python -m world_model_server.cli setup
cp -r $(python -c 'import world_model_server, os; print(os.path.dirname(world_model_server.__file__))')/../adapters/cursor/. .cursor/
```

That copies `hooks.json` and `mcp.json` from this adapter into your project's
`.cursor/` folder.

## Files

- `hooks.json` — Cursor hook declarations (PreToolUse + PostCompact + UserPromptSubmit)
- `mcp.json` — Cursor MCP server config pointing at `world-model-mcp`

## How the hooks fire

| Cursor event | What `world-model-mcp` does |
| --- | --- |
| `beforeSubmitPrompt` | Calls `inject_helper` to splice top constraints + recent facts into the prompt context |
| `beforeEdit` | Calls `hook_helper` to check the proposed change; returns `deny` / `defer` / `ask` / `allow` |
| `afterCompact` | Calls `inject_helper` and writes a row to `compaction_audit` |

Both helpers fail open — any error returns `{}` so Cursor never gets stuck on a
broken hook.

## Limitations

- Cursor's hook payload shape is not identical to Claude Code's. The Node
  wrapper in `hooks/world-model-inject.js` normalizes the common fields
  (`session_id`, `cwd`, `prompt`) but unknown fields are dropped.
- The `defer` enforcement tier maps to `ask` in Cursor today, since Cursor's
  permission system does not yet expose a separate "pause headless" decision.

## Reporting issues

Open an issue on the main repo:
<https://github.com/SaravananJaichandar/world-model-mcp/issues>

Tag it with `adapter:cursor`.
