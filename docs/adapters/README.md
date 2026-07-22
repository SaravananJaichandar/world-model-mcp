# Adapters

world-model-mcp integrates with agent runtimes through the standard MCP protocol. This directory documents the integration path for each runtime. All adapter configurations use the same reference invocation pattern (`python -m world_model_server.server`), so the docs below differ mainly in how each runtime's client passes MCP servers to its agent.

## Available adapters

| Adapter | Runtime | Config surface | Verification status |
|---|---|---|---|
| [Mesh-LLM](./mesh-llm.md) | Decentralized peer-to-peer inference | `WORLD_MODEL_VERIFICATION_*` env vars | Verified via source inspection |
| [BUZZ](./buzz.md) | Nostr-based multi-agent groupchat | ACP `session/new` JSON payload | Partially verified — end-to-end handshake pending local test |
| [Goose](./goose.md) | Linux Foundation OSS agent framework | `~/.config/goose/config.yaml` | Verified against Goose extension schema |

Additional runtime adapters ship as code under [`world_model_server/adapters/`](../../world_model_server/adapters/): Claude Code, Cursor, Codex CLI, Copilot, Cline, Continue, Hermes, OpenClaw, Pi, Windsurf. See the main [QUICKSTART.md](../../QUICKSTART.md) for those.

## Validation contract

Every adapter doc in this directory is validated by [`tests/test_adapters_docs_config.py`](../../tests/test_adapters_docs_config.py). The tests enforce:

- Every `WORLD_MODEL_*` env var referenced in a doc must be one that `world_model_server.config.Config` actually reads (or explicitly whitelisted).
- Every fenced code block (JSON, YAML) must parse cleanly.
- Every world-model MCP server invocation must match the reference config shipped in `world_model_server/adapters/cursor/mcp.json` — command `python3`, args `["-m", "world_model_server.server"]`.
- All adapter docs must reference the same MCP server module path — enforced as a cross-doc invariant.

**Rule:** no new adapter doc lands without matching validation tests. If you add a fourth adapter here, extend `tests/test_adapters_docs_config.py` with the same shape of checks used for the existing three.

## Sovereign-stack composition

The three adapters here compose into a fully self-hosted, verifiable multi-agent stack:

- **Inference:** [Mesh-LLM](./mesh-llm.md) — decentralized peer-to-peer GPU pool
- **Runtime:** [Goose](./goose.md) — MCP-native agent framework
- **Coordination:** [BUZZ](./buzz.md) — Nostr-relay groupchat where humans and agents share the same rooms
- **Memory + audit chain:** world-model-mcp (this repo)

Every layer is Apache-2.0 or MIT OSS. No hyperscaler required at any layer. See each adapter doc for the composition example.

## Signed audit chain

All adapters support the opt-in signed audit chain (`WORLD_MODEL_AUDIT_LOG=on`). Every memory write and Coach-Player verification is chained into a hybrid Ed25519 + SLH-DSA-SHA2-128f (FIPS 205 post-quantum) signed Merkle log, verifiable offline via `etch-verify` or the OSS reference verifier. See [AUDIT_LOG.md](../AUDIT_LOG.md) for the spec.
