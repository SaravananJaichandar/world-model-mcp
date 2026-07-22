# BUZZ adapter

**Status: partially verified as of 2026-07-22.**

Verified via source inspection:
- `python3 -m world_model_server.server` is the stdio MCP entry (matches [`world_model_server/adapters/cursor/mcp.json`](../../world_model_server/adapters/cursor/mcp.json) and other shipped adapter configs)
- `WORLD_MODEL_DB_PATH` env var is real (`world_model_server/config.py:15`)
- `WORLD_MODEL_AUDIT_LOG=on` is real (used elsewhere in this repo's docs)
- buzz-agent spawns MCP servers via ACP `session/new` (per [BUZZ VISION_AGENT.md](https://github.com/block/buzz/blob/main/VISION_AGENT.md): *"MCP is the only interface to the tools. No runtime coupling."*)

Not yet verified end-to-end:
- Live handshake between the BUZZ desktop app / buzz-acp orchestrator and `world_model_server.server` under `session/new`
- Behavior when `buzz-dev-mcp` and `world-model-mcp` are spawned in the same session
- Whether the BUZZ desktop app exposes a UI for adding custom MCP servers, or whether the ACP client config must be edited directly

**File an issue at `github.com/SaravananJaichandar/world-model-mcp` if you hit a gap.**

## What BUZZ is

[BUZZ](https://github.com/block/buzz) is a self-hostable workspace where humans and AI agents share the same rooms, built on the Nostr protocol. Every action (chat, workflow step, git event, canvas update, huddle event) is a signed Nostr event on a single relay. Agents are first-class members with their own keys, channel memberships, and audit trail. Apache 2.0, Rust monorepo, maintained by Block.

## What integration enables

- BUZZ agents (running under `buzz-agent`) get persistent memory across sessions via world-model-mcp
- Multi-turn conversations in a BUZZ channel accumulate learned constraints, facts, and decisions
- Context re-injection after compaction so an agent picks up prior conversation state after long channel gaps
- Coach-Player adversarial verification of memory retrievals guards against hallucinated recall
- Opt-in signed audit chain (v0.13, `WORLD_MODEL_AUDIT_LOG=on`) records every memory write with hybrid Ed25519 + SLH-DSA-SHA2-128f (FIPS 205) Merkle epoch signatures

## Complementary to `buzz-audit`, not competitive

BUZZ ships its own [`buzz-audit`](https://github.com/block/buzz/tree/main/crates/buzz-audit) crate — hash-chain audit at the relay layer for internal tamper detection. world-model-mcp's audit log is a different job:

| Concern | `buzz-audit` | world-model-mcp audit log |
|---|---|---|
| Purpose | Internal tamper detection for BUZZ operators | Third-party auditor-verifiable evidence |
| Verification trust root | The BUZZ relay | Public keys + offline CLI, no vendor trust required |
| Signature | Hash chain | Hybrid Ed25519 + SLH-DSA-SHA2-128f (FIPS 205 post-quantum) |
| Framework mapping | None built in | Compliance frameworks (see Etch hosted) |
| Multi-runtime | BUZZ only | Any MCP-native runtime (Claude Code, Cursor, Codex, Goose, BUZZ) |

Both can be enabled at once. Different consumers.

## Prerequisites

- BUZZ desktop app or self-hosted relay running (see [BUZZ Quick Start](https://github.com/block/buzz#stuff-you-do-in-buzz))
- `buzz-agent` binary running (see BUZZ Quick Start)
- world-model-mcp installed:

```bash
pip install world-model-mcp
```

## Configuration

`buzz-agent` receives its MCP server list from the ACP client (BUZZ desktop app or an equivalent orchestrator) via the ACP `session/new` request. Add world-model-mcp to that list.

Example `session/new` payload with `buzz-dev-mcp` and `world-model-mcp` both spawned:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "session/new",
  "params": {
    "cwd": "/path/to/your/workspace",
    "mcpServers": [
      {
        "name": "buzz-dev-mcp",
        "command": "buzz-dev-mcp",
        "args": [],
        "env": {}
      },
      {
        "name": "world-model",
        "command": "python3",
        "args": ["-m", "world_model_server.server"],
        "env": {
          "WORLD_MODEL_DB_PATH": ".claude/world-model",
          "WORLD_MODEL_AUDIT_LOG": "on"
        }
      }
    ]
  }
}
```

Notes:
- The `mcpServers` array shape follows the ACP spec, not a BUZZ-specific extension
- The `world-model` MCP server invocation matches every other shipped adapter in this repo (see [Cursor mcp.json](../../world_model_server/adapters/cursor/mcp.json) for the reference pattern)
- If BUZZ ships a UI for adding MCP servers, use that instead of editing the raw ACP payload; the underlying `command`/`args` shape will be the same

## Verification steps

1. Start a BUZZ channel with an agent added
2. Send a message asking the agent to remember something specific ("note that our staging deploys freeze at 4 PM UTC Fridays")
3. Confirm world-model-mcp received it by checking the local DB:

```bash
sqlite3 .claude/world-model/facts.db "SELECT fact_text FROM facts ORDER BY id DESC LIMIT 5;"
```

4. In a new session (or after clearing the agent's context), ask "when do staging deploys freeze?"
5. The agent should retrieve the fact via world-model-mcp's `query_fact` tool

If the fact does not appear in the DB, check:
- Did the ACP client actually pass world-model-mcp in `session/new`?
- Does the BUZZ agent have permission to call arbitrary MCP tools?
- Is `WORLD_MODEL_DB_PATH` writable from where `buzz-agent` runs?

## Positioning for BUZZ operators shipping to regulated buyers

Teams running BUZZ in fintech, healthcare, or gov contexts often need auditor-verifiable evidence separate from internal tamper detection. Enable the opt-in audit log:

```bash
export WORLD_MODEL_AUDIT_LOG=on
```

Every memory write and Coach verification is chained into a signed Merkle log verifiable offline via `etch-verify` (or the OSS reference verifier). Auditors accept this as third-party evidence without trusting the BUZZ operator's relay.

## Known limits

- **End-to-end handshake is unverified as of this writing.** See status marker at top of doc.
- **BUZZ ACP session/new schema may drift.** Always check against your buzz-acp version.
- **Multiple MCP servers per session** — supported per BUZZ architecture; world-model-mcp itself is lightweight (Python stdio, one process per session).
- **Nostr identity binding** — world-model-mcp does not yet accept Nostr keys as agent identity. On roadmap.

## Related

- [Mesh-LLM adapter](./mesh-llm.md) — sovereign inference layer that composes with BUZZ + world-model-mcp
- [Audit log spec](../AUDIT_LOG.md) — chain of custody for memory + verification decisions
- [BUZZ ARCHITECTURE.md](https://github.com/block/buzz/blob/main/ARCHITECTURE.md)
- [buzz-agent VISION](https://github.com/block/buzz/blob/main/VISION_AGENT.md)
