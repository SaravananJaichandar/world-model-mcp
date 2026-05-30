# Self-hosted Claude Managed Agents quickstart

End-to-end example for running `world-model-mcp` as a memory layer for
Anthropic Claude Managed Agents in the **self-hosted sandbox** configuration,
deployed on [Modal](https://modal.com).

This pairs with the full walkthrough at
[`docs/deployment/managed-agents-self-hosted.md`](../../docs/deployment/managed-agents-self-hosted.md).

## What's in this directory

| File | Purpose |
| --- | --- |
| `deploy_modal.py` | Modal app definition. `modal deploy deploy_modal.py` ships world-model-mcp's streamable-HTTP server publicly on Modal. |
| `ant-setup.sh` | One-shot script that runs `ant tunnels create` + `ant mcp-servers create` after Modal deploys. |
| `README.md` | This file. |

## Why this configuration

Anthropic's [Claude Managed Agents updates](https://claude.com/blog/claude-managed-agents-updates)
explicitly state:

> Memory is not yet supported in self-hosted sessions, which constrains the
> use cases where persistent context matters.

`world-model-mcp` fills the gap. The flow is:

1. Anthropic runs the agent loop and the model.
2. Tool execution happens in your self-hosted sandbox.
3. Your sandbox reaches `world-model-mcp` (deployed here, on Modal) via an
   MCP tunnel.
4. The agent's memory survives across sessions, with full audit log.

## Run it

```bash
# 1. Install Modal + the ant CLI
pip install modal
# (install the ant CLI per Anthropic docs)

# 2. Deploy world-model-mcp to Modal
modal deploy deploy_modal.py
# Modal prints something like:
#   https://your-org--world-model-mcp.modal.run

# 3. Wire it into Anthropic
bash ant-setup.sh https://your-org--world-model-mcp.modal.run
```

## What you get

After step 3 the `world-model` MCP server appears in your Claude Managed
Agents Console MCP-server dropdown. Any agent in your workspace can attach
it. The 25 MCP tools world-model-mcp ships (query_fact, validate_change,
find_contradictions, resolve_contradiction, get_injection_context, etc.)
become directly callable from the agent.

## Caveats

- This example targets *demonstration* not production. For real workloads
  mount a Modal Volume at `WORLD_MODEL_DB_PATH` so the graph survives
  container restarts.
- MCP tunnels are a research preview as of May 2026. You need approval from
  Anthropic to provision one.
- The Anthropic-hosted (non-self-hosted) configuration uses the built-in
  Memory primitive and does not need this. If that works for you, use it.
