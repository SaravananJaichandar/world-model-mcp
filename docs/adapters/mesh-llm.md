# Mesh-LLM adapter

**Status: verified against world-model-mcp v0.14.0.** No code changes on either side; the OpenAI-compatible Coach backend added in v0.12.13 already speaks the wire format Mesh-LLM exposes.

## What Mesh-LLM is

[Mesh-LLM](https://github.com/Mesh-LLM/mesh-llm) is a decentralized peer-to-peer inference network. It pools spare GPU compute across machines so you can run models too large for a single device. Uses Nostr for node discovery, built on `llama.cpp`, MIT-licensed. Exposes an OpenAI-compatible API at `http://localhost:9337/v1`.

## What integration enables

- Coach-Player adversarial verification (`verify_retrieval` tool) runs on your local Mesh-LLM cluster instead of a hosted API
- No verification inference data leaves your machine or trusted mesh
- Opt-in audit log (v0.13, `WORLD_MODEL_AUDIT_LOG=on`) records every verification decision alongside the memory writes with a hybrid Ed25519 + SLH-DSA-SHA2-128f (FIPS 205) Merkle signature per epoch
- Composes with the sovereign stack: agent runs in Goose or BUZZ → inference on Mesh-LLM → memory + Coach verification via world-model-mcp → audit chain optionally exported to Etch

## Prerequisites

- world-model-mcp installed with the `[openai]` extra
- Mesh-LLM running locally on port 9337 with an OpenAI-compatible model loaded

```bash
pip install "world-model-mcp[openai]"
```

## Configuration

Verification backend is set via env vars. Config field names are declared in [`world_model_server/config.py`](../../world_model_server/config.py); the defaults documented here match that file.

```bash
# Route the Coach verifier at Mesh-LLM
export WORLD_MODEL_VERIFICATION_BACKEND=openai-compatible
export WORLD_MODEL_VERIFICATION_BASE_URL=http://localhost:9337/v1

# Pick whichever model your Mesh-LLM cluster is serving
export WORLD_MODEL_VERIFICATION_MODEL=llama-3.3-70b-instruct

# API key: optional for local Mesh-LLM. If omitted, the client falls back to
# OPENROUTER_API_KEY, then OPENAI_API_KEY, then an internal placeholder for
# endpoints that don't authenticate. Setting a dummy value works fine locally.
export WORLD_MODEL_VERIFICATION_API_KEY=local-mesh
```

Then start world-model-mcp:

```bash
python3 -m world_model_server.server
```

## Verification steps

1. With Mesh-LLM running locally, invoke the `verify_retrieval` MCP tool (from any MCP client connected to world-model-mcp) against a sample fact
2. Mesh-LLM's local log should show a matching `POST /v1/chat/completions` request from `127.0.0.1`
3. `world-model-mcp` server stderr will show the backend + base URL chosen at startup

If the request never reaches Mesh-LLM, check:
- Is Mesh-LLM actually serving on port 9337? (`curl http://localhost:9337/v1/models`)
- Is the `[openai]` extra installed? (`pip show openai`)
- Is `WORLD_MODEL_VERIFICATION_BACKEND` set to `openai-compatible` in the shell that started `world_model_server.server`?

## Signed evidence chain

Enable the opt-in audit log:

```bash
export WORLD_MODEL_AUDIT_LOG=on
```

Every memory write and every Coach verification is chained into a hybrid Ed25519 + SLH-DSA-SHA2-128f signed Merkle log. The signed epochs are verifiable offline via the reference verifier — no dependency on world-model-mcp or Etch servers. See [AUDIT_LOG.md](../AUDIT_LOG.md) for the full spec.

## Known limits

- **Model support.** The Coach verifier expects a chat-completions model with reasonable instruction-following. Small quantized models (7B and below) may produce noisy verdicts. The v0.12.13 default (`claude-haiku-4-5-20251001`) is Anthropic-specific; when routing through Mesh-LLM, override `WORLD_MODEL_VERIFICATION_MODEL` to a served open-weights model.
- **Streaming.** Coach calls are non-streaming by design. Mesh-LLM's streaming path is unused.
- **Nostr identity binding.** world-model-mcp does not yet accept Nostr keys as agent identity. On roadmap.

## Related

- [Coach-Player verification benchmark](../../benchmarks/coach-player/) — the verification mode this adapter routes
- [Audit log spec](../AUDIT_LOG.md) — chain of custody for memory + verification decisions
- [BUZZ adapter](./buzz.md) — plug world-model-mcp into buzz-agent alongside Mesh-LLM for the sovereign stack
- [Mesh-LLM upstream](https://github.com/Mesh-LLM/mesh-llm)
