# Continue adapter (experimental)

This adapter wires `world-model-mcp` into
[Continue](https://github.com/continuedev/continue) — the largest
open-source coding-agent extension not tied to a platform vendor,
available for VS Code and JetBrains — as an MCP tool source.

Status: experimental. Verified against the Continue MCP configuration
schema documented at
[docs.continue.dev/customize/deep-dives/mcp](https://docs.continue.dev/customize/deep-dives/mcp)
as of 2026-07-01. E2E verification of the last-mile "does Continue's
LLM actually see the 27 tools" step requires a running VS Code / JetBrains
session; the CLI-side install and file-write path is verified by tests.

## Why this adapter matters

Continue is the largest open-source coding-agent extension that is
not tied to a single platform vendor. After the SpaceX/Cursor
acquisition (2026), platform-vendor risk became a real concern for
teams standardizing on an OSS-neutral coding-agent workflow.
Wiring world-model-mcp into Continue extends the "harness-neutral
memory" story to that constituency.

## What you get

- `world-model-mcp` registered as a stdio MCP server inside
  Continue via a single standalone YAML file at
  `<project>/.continue/mcpServers/world-model.yaml`
- All 27 world-model tools available in Continue's agent mode
- Same shared `.claude/world-model/` database used by the Claude
  Code, Cursor, OpenClaw, and Hermes adapters — so a project running
  in multiple clients sees the same memory

MCP-only in this cut. Continue's rules system (`.continue/rules/`)
and system-prompt customization are documented separately by
Continue and are not touched by this adapter.

## Install (recommended: CLI subcommand)

Run from your project root:

```bash
# 1. Install the package
pip install -U world-model-mcp

# 2. Initialize the world-model database
python -m world_model_server.cli setup

# 3. Register world-model in this project's .continue/mcpServers/
python -m world_model_server.cli install-continue
```

Step 3 writes `<project>/.continue/mcpServers/world-model.yaml`.
Defaults the `command` field to `sys.executable` (absolute path
to the interpreter running the CLI) — the same absolute-path
posture as the OpenClaw and Hermes adapters.

Reload Continue: reopen the VS Code / JetBrains window, or reload
the extension. Continue watches the `.continue/mcpServers/`
directory for changes and picks up new servers automatically.

Verify: open Continue's agent mode and check the tool picker. All
27 world-model tools should be visible under the `world-model`
server name.

**Flags** — `install-continue` supports:

| Flag | Purpose |
| --- | --- |
| `--project-dir PATH` | Which project to install into (default: current directory) |
| `--python PATH` | Absolute path to the python3 you want Continue to spawn. Default: the interpreter running the CLI. Relative values are rejected. |
| `--db-path PATH` | Value for `WORLD_MODEL_DB_PATH`. Default: `.claude/world-model` |
| `--dry-run` | Print the YAML that would be written without touching disk |
| `--force` | Overwrite the existing `world-model.yaml` file if present |

## Manual install (fallback)

If you prefer to skip the CLI, create
`<project>/.continue/mcpServers/world-model.yaml` yourself with this
content (replace the placeholder with the output of `which python3`):

```yaml
name: world-model-mcp
version: 0.1.0
schema: v1
mcpServers:
  - name: world-model
    type: stdio
    command: /absolute/path/to/python3
    args:
      - -m
      - world_model_server.server
    env:
      WORLD_MODEL_DB_PATH: .claude/world-model
```

Same file is bundled as [`world-model.yaml`](./world-model.yaml)
for copy-paste.

## Where the database lives

The adapter uses `.claude/world-model/` (relative to the project
root) as the database path. If the same project also has the Claude
Code, Cursor, OpenClaw, or Hermes adapters installed, all clients
read and write the same SQLite database — one shared fact graph
across every coding-agent runtime you use in that project.

For user-wide shared memory across multiple projects, override with
an absolute path:

```bash
python -m world_model_server.cli install-continue \
    --db-path /Users/you/.world-model-shared
```

## Overlap with Continue Rules

Continue ships its own rules system at `.continue/rules/*.md`.
world-model-mcp does not replace it — the two sit at different
layers:

- **Continue Rules**: static markdown rules you write. Best for
  project conventions you already know.
- **world-model-mcp**: learned constraints with violation counts,
  hard / defer / warn enforcement tiers, plus a temporal fact
  graph with per-fact provenance, per-evidence-type decay
  half-lives, and confidence-weighted contradiction resolution.
  Best for constraints that should *enforce* and for facts that
  need provenance and history.

If you use both, expect some overlap — that's fine, they don't
conflict.

## Roadmap for this adapter

MCP-only integration ships in v0.10. Two follow-ups on the roadmap:

**v0.10.x — Continue global-config path.** Continue supports a
user-level `~/.continue/config.yaml` with a top-level `mcpServers`
array. `install-continue` currently writes only project-scoped
files. A `--global` flag that merges into `~/.continue/config.yaml`
using `ruamel.yaml` round-trip mode (same as the Hermes adapter)
is on the roadmap.

**v0.10.x — Slash-command variant.** Continue supports custom
slash commands via its rules and skills system. Wiring
`/world-model` slash commands from the existing Claude Code /
Cursor / Codex slash-command surface into Continue is a natural
follow-up.

## Caveats

- **VS Code / JetBrains restart required.** After writing the
  server file, Continue may need a reload to pick it up. Reopening
  the workspace or restarting the extension is the fastest way
  to force it. Continue also watches `.continue/mcpServers/`
  in newer builds — auto-discovery should work but the manual
  restart is the safe path.
- **`python3` MUST be an absolute path.** OpenClaw's process spawn
  is documented to not inherit shell PATH; Hermes' spawn behavior
  is likely similar; Continue's has not been verified end-to-end
  as of 2026-07-01. `install-continue` defaults to `sys.executable`
  and rejects relative `--python` overrides as a hard error, so
  users hit a clear failure at install time rather than a cryptic
  "server did not start" later.
- **Project-scoped only.** Runs from the project you install into.
  For user-wide memory across projects, use `--db-path` with an
  absolute path pointing to a shared location.
- **Agent-mode only.** MCP tools are only available in Continue's
  agent mode (per Continue's own docs). Chat and edit modes will
  not surface world-model tools.
- **HTTP transport.** For `world-model-mcp` deployed over HTTP,
  register the server with Continue's `type: streamable-http`
  or `type: sse` and a `url:` field instead of `command`/`args`/`env`.

## Reporting issues

Open an issue on the main repo with the `adapter:continue` label:
<https://github.com/SaravananJaichandar/world-model-mcp/issues>

Include:
- Continue version (from the VS Code / JetBrains extension list)
- Your `<project>/.continue/mcpServers/world-model.yaml` contents
- Continue's own MCP debug output if available
