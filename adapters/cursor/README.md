# Cursor adapter (experimental)

This adapter wires `world-model-mcp` into Cursor as a memory and enforcement
layer. The same Python `inject_helper` and `hook_helper` that power the Claude
Code hooks are reused here -- only the manifest format changes.

Status: experimental. Cursor's hook system landed in 1.7 and has been
iterating through 2026. PRs welcome for any breakage you hit; see the
**Reporting issues** section.

## What you get

- `world-model-mcp` configured as an MCP server inside Cursor
- A `preToolUse` hook that enforces learned constraints before Cursor edits or
  writes a file (deny / ask / proceed; see the `defer` note below)
- A `preCompact` hook that re-injects the top constraints and recent facts so
  Cursor doesn't lose them across compaction
- A `beforeSubmitPrompt` hook that biases injected facts toward what the user
  is asking about

## Install (from your project root)

```bash
# 1. Install the package
pip install -U world-model-mcp

# 2. Initialize the world-model database in this project
python -m world_model_server.cli setup

# 3. Wire the adapter into .cursor/
python -m world_model_server.cli install-cursor
```

Step 3 copies `mcp.json` + `hooks.json` into `.cursor/`, copies the compiled
Node hook wrappers into `.cursor/hooks/`, and prints what changed. Rerun it
any time you want to refresh the files after upgrading the package.

Then restart Cursor. On first run Cursor will show a one-click MCP install
prompt asking you to approve `world-model` -- accept it. The hooks load
automatically.

## What gets installed

```
your-project/
  .cursor/
    mcp.json                          # MCP server config
    hooks.json                        # preToolUse, preCompact, beforeSubmitPrompt
    hooks/
      world-model-validate.js         # PreToolUse helper (constraint check)
      world-model-inject.js           # injection helper (PostCompact / prompt)
  .claude/
    world-model/                      # SQLite databases (shared layout)
```

The `.claude/world-model/` path is intentional -- the adapter shares the same
database location as the Claude Code setup so a project that runs in both
clients sees the same memory.

## How the hooks map

| Cursor event | What `world-model-mcp` does |
| --- | --- |
| `beforeSubmitPrompt` | Calls `inject_helper` to splice top constraints + recent facts into the prompt context |
| `preToolUse` (matcher `Edit\|Write\|MultiEdit`) | Calls `hook_helper` to check the proposed change. Returns Cursor `permissionDecision`: `deny` for hard violations, `ask` for ambiguous, `allow` otherwise |
| `preCompact` | Calls `inject_helper` and writes a row to `compaction_audit` so the next session has a record of what was lost |

Both helpers fail open on any error so Cursor never gets stuck on a broken
hook.

## Project vs user scope

The adapter installs to your project's `.cursor/` folder, which means the
configuration travels with the repo. If you'd rather wire it user-wide,
copy `mcp.json` and `hooks.json` to `~/.cursor/` instead. Cursor merges user
and project configs with the project version winning on conflicts.

## Overlap with Cursor Memories and Cursor Rules

Cursor ships its own `.cursor/rules/*.mdc` files and an auto-generated
"Memories" feature. world-model-mcp doesn't replace those -- it sits one
layer below them:

- **Cursor Rules**: static markdown rules you write. Best for project
  conventions you already know.
- **Cursor Memories**: auto-generated facts from conversations. Best for
  ephemeral or per-conversation notes.
- **world-model-mcp**: learned constraints with violation counts and hard /
  defer / warn tiers, plus a temporal fact graph with confidence and
  contradiction resolution. Best for rules that should *enforce* and for
  facts that need provenance.

If you have Cursor Memories enabled, you'll have two stores. That's fine --
they don't conflict, but expect some duplication.

## Caveats

- Cursor exposes `preCompact` but not `postCompact`. The adapter injects
  *before* compaction summarization rather than after. In practice this is
  close enough for the world-model use case: the injected context becomes
  part of what gets summarized.
- The `defer` enforcement tier from `hook_helper` maps to `ask` in Cursor
  today, since Cursor's permission system does not yet expose a separate
  "pause headless" decision.
- `${workspaceFolder}` expansion inside `mcp.json` env values is not a
  documented Cursor feature, so the adapter uses a relative path
  (`.claude/world-model`). If you launch Cursor from a different working
  directory you may need to set `WORLD_MODEL_DB_PATH` explicitly.

## Reporting issues

Open an issue on the main repo:
<https://github.com/SaravananJaichandar/world-model-mcp/issues>

Tag it with `adapter:cursor`.
