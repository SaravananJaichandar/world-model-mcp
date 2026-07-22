# Goose adapter

**Status: verified against Goose extension config format.** No code changes on either side; Goose's built-in MCP extension system spawns world-model-mcp as a stdio MCP server via `~/.config/goose/config.yaml`.

## What Goose is

[Goose](https://github.com/aaif-goose/goose) is an open-source AI agent framework — desktop app, CLI, and API. Apache 2.0, Rust, 51k+ stars, part of the Linux Foundation's Agentic AI Foundation. Connects to 70+ extensions via the Model Context Protocol.

## What integration enables

- Goose agents get persistent memory across sessions via world-model-mcp
- Constraint learning + `get_injection_context` for restoring state after context compaction
- Coach-Player adversarial verification (`verify_retrieval`) guards against hallucinated recall
- Opt-in signed audit chain (`WORLD_MODEL_AUDIT_LOG=on`) with hybrid Ed25519 + SLH-DSA-SHA2-128f (FIPS 205 post-quantum) Merkle epoch signatures
- Composes with [Mesh-LLM](./mesh-llm.md) for sovereign inference (Goose runs inference on Mesh-LLM; world-model-mcp records + verifies decisions)

## Prerequisites

- Goose installed ([Goose Quickstart](https://goose-docs.ai/docs/quickstart))
- world-model-mcp installed:

```bash
pip install world-model-mcp
```

## Configuration

Add world-model-mcp to Goose's extension config at `~/.config/goose/config.yaml`:

```yaml
extensions:
  world-model:
    name: World Model MCP
    cmd: python
    args: [-m, world_model_server.server]
    envs:
      WORLD_MODEL_DB_PATH: .claude/world-model
      WORLD_MODEL_AUDIT_LOG: "on"
    type: stdio
    timeout: 300
    enabled: true
```

Or add it interactively via the CLI:

```bash
goose configure
# In the menu:
#   1. Add extension
#   2. Type: stdio
#   3. name: world-model
#   4. cmd: python
#   5. args: -m world_model_server.server
```

Restart Goose after config changes.

## Verification

1. Start a Goose session (`goose session` in CLI, or launch the desktop app)
2. Ask Goose to remember something specific: "note that our deploy freeze starts every Friday at 4 PM UTC"
3. Confirm world-model-mcp received it:

```bash
sqlite3 .claude/world-model/facts.db "SELECT fact_text FROM facts ORDER BY id DESC LIMIT 5;"
```

4. Start a new session; ask "when does our deploy freeze start?"
5. Goose should retrieve via the `query_fact` tool and answer correctly

If facts are not stored, check:
- Does `goose extensions list` show `world-model` as enabled?
- Is `python -m world_model_server.server` runnable in the shell Goose spawns? (`which python; python -m world_model_server.server --help`)
- Is `WORLD_MODEL_DB_PATH` writable from Goose's process?

## Signed audit chain for regulated Goose deployments

Enable the opt-in audit log (already in the config above via `WORLD_MODEL_AUDIT_LOG: "on"`). Every memory write and Coach verification chains into a hybrid Ed25519 + SLH-DSA-SHA2-128f signed Merkle log, verifiable offline via `etch-verify` or the OSS reference verifier. Auditors accept this as third-party evidence without trusting the Goose runtime. See [AUDIT_LOG.md](../AUDIT_LOG.md) for the full spec.

## Composing with Mesh-LLM

Point world-model-mcp's Coach at your Mesh-LLM cluster's OpenAI-compatible endpoint so that both agent inference (Goose → Mesh-LLM) and adversarial verification (world-model-mcp Coach → Mesh-LLM) stay on the sovereign stack:

```bash
export WORLD_MODEL_VERIFICATION_BACKEND=openai-compatible
export WORLD_MODEL_VERIFICATION_BASE_URL=http://localhost:9337/v1
export WORLD_MODEL_VERIFICATION_MODEL=llama-3.3-70b-instruct
```

See [Mesh-LLM adapter](./mesh-llm.md).

## Known limits

- **Auto-injection lifecycle hooks.** Goose does not currently expose PostCompact / UserPromptSubmit lifecycle hooks in the way Claude Code does, so context re-injection happens at query time (via `get_injection_context` MCP tool) rather than automatically after compaction. Practically this means the agent must be prompted to consult memory; automatic re-hydration on compaction is not available yet.
- **Extension toggle requires session restart.** Enabling / disabling an extension in `config.yaml` takes effect on the next Goose session, not the current one.
- **Nostr identity binding.** world-model-mcp does not yet accept Nostr keys as agent identity. On roadmap.

## Related

- [Mesh-LLM adapter](./mesh-llm.md) — sovereign inference layer that composes with Goose + world-model-mcp
- [BUZZ adapter](./buzz.md) — Nostr-relay-based multi-agent workspace where Goose runs as an ACP-compliant agent
- [Audit log spec](../AUDIT_LOG.md) — chain of custody for memory + verification decisions
- [Goose upstream](https://github.com/aaif-goose/goose)
- [Goose extension docs](https://goose-docs.ai/docs/getting-started/using-extensions)
