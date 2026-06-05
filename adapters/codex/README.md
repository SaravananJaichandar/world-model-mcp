# Codex CLI adapter (experimental)

This adapter wires `world-model-mcp` into OpenAI's Codex CLI as a memory
and enforcement layer. The same Python `inject_helper` and `hook_helper`
that power the Claude Code, Cursor, and pi adapters are reused here -
only the config format changes (Codex uses TOML, in `~/.codex/config.toml`).

Status: experimental. Tested against Codex CLI v0.135 - v0.138-alpha
on macOS and Linux. See the **Caveats** section for known sharp edges
on Windows and inside trust-prompted projects.

## What you get

- `world-model-mcp` registered as an MCP server inside Codex, exposing
  26 tools to the agent (query_fact, validate_change, find_contradictions,
  resolve_contradiction, get_injection_context, get_compaction_audit, etc.).
- A `PreToolUse` hook that blocks Edit / Write / MultiEdit / Bash calls
  matching learned constraints. Returns the Codex hook output schema
  (`permissionDecision: "deny" | "ask" | "allow"`) on stdout.
- A `PostCompact` hook that injects top constraints and recent canonical
  facts as `additionalContext` after compaction.
- A `PostToolUse` hook that captures the edit into the knowledge graph.
- A `SessionStart` hook that warms the agent context on session resume.

## Install (from your project root)

```bash
# 1. Install the package (if not already)
pip install -U world-model-mcp

# 2. Initialize the world-model database in this project
python -m world_model_server.cli setup

# 3. Wire the adapter into ~/.codex/config.toml
python -m world_model_server.cli install-codex
```

Step 3 reads the bundled `config.toml` and `hooks_snippet.toml` from the
installed package and appends them to your `~/.codex/config.toml`. It
will not clobber existing config - if a `[mcp_servers.world_model]`
block already exists, the command skips and tells you. Use `--force`
to overwrite.

If you prefer to do it by hand, copy the contents of
`world_model_server/adapters/codex/config.toml` and
`world_model_server/adapters/codex/hooks_snippet.toml` from the
installed package and merge them into your existing `~/.codex/config.toml`.

After installing, restart `codex` and verify with:

```bash
codex mcp list
```

You should see `world_model` in the list. From inside a Codex session,
calling any tool prefixed `mcp__world_model__` (for example
`mcp__world_model__query_fact`) confirms the wire-up worked.

## Server name: `world_model`, not `world-model`

Codex's tool name sanitizer (in `codex-rs/codex-mcp/src/mcp/mod.rs`)
silently strips hyphens from MCP server names before exposing tools to
the model. To avoid collisions and the cryptic hash-suffix
disambiguation path, this adapter uses `world_model` (underscore) as
the server name. The Claude Code, Cursor, and pi adapters all use
`world-model` (hyphen) - they live in separate config files so there is
no cross-talk.

## How the hooks map

| Codex event | world-model-mcp helper | What it does |
| --- | --- | --- |
| `PreToolUse` (Edit/Write/MultiEdit/Bash) | `world_model_server.hook_helper` | Returns `permissionDecision: "deny"` for hard violations of learned constraints (severity=error AND count>=3), `"ask"` for soft violations, otherwise `"allow"`. |
| `PostToolUse` (Edit/Write/MultiEdit) | `world_model_server.inject_helper` | Captures the edit as a fact with provenance. |
| `PostCompact` (all) | `world_model_server.inject_helper` | Returns `additionalContext` with top constraints + recent canonical facts. |
| `SessionStart` (all) | `world_model_server.inject_helper` | Same as PostCompact, fires on resume/clear. |

All helpers fail open. If `python3` is missing or world-model-mcp is
not installed, Codex sees an empty `{}` response and runs as if the
hook were not there.

## Caveats

- **Schema strictness**: Codex enforces `deny_unknown_fields` on every
  hook output type. The bundled helpers emit only documented fields.
  If you patch them, run `tests/test_v075_features.py::test_codex_hook_output_schema`
  to confirm output stays compliant.
- **Windows**: the snippet uses `python3` which may not be on PATH on
  fresh Windows installs. If `codex` complains the hook command fails,
  replace `python3` with `python` or the absolute path to your Python
  binary. The `commandWindows` field is not used in this snippet because
  the command itself is portable when Python is on PATH.
- **Trust prompt**: Codex requires the project to be marked as
  trusted (`[projects."/absolute/path"] trust_level = "trusted"` in
  `~/.codex/config.toml`) before project-scoped configs and hooks fire.
  When you open the project in `codex` for the first time it will
  prompt; answer yes. There is an open Codex issue (#14547) about the
  trust setting sometimes not persisting - if hooks stop firing
  unexpectedly, re-check the `[projects.*]` table.
- **Hook command resolution**: `python3 -m world_model_server.server`
  resolves through the shell's PATH, which Codex inherits from your
  login shell. If you use a virtualenv or `pyenv`, make sure your shell
  config (`.zshrc`, `.bashrc`) activates the right environment for
  non-interactive shells - or replace `python3` with the absolute path
  to the right interpreter.
- **Project vs user config layers**: Codex's TOML merge does NOT
  concatenate arrays. If you have `[[hooks.PreToolUse]]` blocks in both
  `~/.codex/config.toml` and `<project>/.codex/config.toml`, only the
  project's PreToolUse hooks fire (the project array replaces the
  user array). To keep adapter hooks active across all projects, leave
  them in `~/.codex/config.toml` and avoid declaring `[[hooks.*]]`
  arrays of the same event name at the project level.
- **Streamable HTTP transport**: Codex v0.135+ supports streamable HTTP
  MCP servers via `url = "..."` instead of `command = "..."`. If you
  deploy world-model-mcp on Modal or similar (see
  [docs/deployment/managed-agents-self-hosted.md](../../docs/deployment/managed-agents-self-hosted.md)),
  swap the `[mcp_servers.world_model]` block for the HTTP form. The
  hooks stay the same.

## Reporting issues

Open an issue on the main repo with the `adapter:codex` label:
<https://github.com/SaravananJaichandar/world-model-mcp/issues>

Include the Codex version (`codex --version`), the relevant section
of your `~/.codex/config.toml`, and any stderr output Codex logs for
the hook commands (`MCP server stderr (world_model): ...` lines).
