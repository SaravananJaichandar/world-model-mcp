# Memory for Claude Managed Agents (self-hosted sandboxes)

This guide walks through running `world-model-mcp` as a memory layer for
**Anthropic Claude Managed Agents in the self-hosted sandbox configuration** --
the configuration where the platform's built-in Memory primitive is not yet
supported. From the official [Claude Managed Agents updates](https://claude.com/blog/claude-managed-agents-updates):

> Memory is not yet supported in self-hosted sessions, which constrains the
> use cases where persistent context matters.

`world-model-mcp` fills that gap. You run the server inside your own
perimeter, expose it over streamable HTTP, and Anthropic reaches it via an
MCP tunnel.

If you only use Claude Code, Cursor, or `.mcpb` installs, you do not need
this -- the default stdio transport is correct for those flows. This page is
for teams running self-hosted Managed Agents and wanting durable, queryable,
audit-logged memory across sessions.

## When to use this configuration

| Scenario | Setup |
| --- | --- |
| Claude Code / Cursor on a developer laptop | stdio (default) |
| Claude Managed Agents, Anthropic-hosted sandbox | Anthropic Memory tool (built-in) |
| Claude Managed Agents, **self-hosted sandbox** | **This guide.** stdio Anthropic Memory tool is unavailable; world-model-mcp over MCP tunnel fills the gap |
| Local container reachable over ngrok/Cloudflare Tunnel | HTTP transport, same as below |

## Architecture

```
+-----------------------+    mTLS     +-------------------+    customer TLS     +-------------------+
|  Claude (Anthropic)   | ----------> | Cloudflare Edge / | ------------------> | world-model-mcp   |
|  Managed Agents loop  |             | MCP tunnel proxy  |                     | Streamable HTTP   |
+-----------------------+             +-------------------+                     | :8765 /mcp        |
                                                                                | /healthz          |
                                                                                +-------------------+
                                                                                       inside
                                                                                       customer
                                                                                       perimeter
```

The MCP wire protocol is unchanged. The tunnel terminates customer TLS at the
proxy you control; Anthropic only sees encrypted bytes until your proxy
decrypts them.

## Step 1. Run world-model-mcp in HTTP mode

The fastest path is the bundled Docker image:

```bash
git clone https://github.com/SaravananJaichandar/world-model-mcp
cd world-model-mcp
docker compose up -d                       # uses Dockerfile.http
curl -sf http://127.0.0.1:8765/healthz     # {"status":"ok","version":"0.7.4"}
```

Or without Docker:

```bash
pip install 'world-model-mcp[http]'

export WORLD_MODEL_TRANSPORT=http
export WORLD_MODEL_HTTP_HOST=0.0.0.0
export WORLD_MODEL_HTTP_PORT=8765
export WORLD_MODEL_HTTP_PATH=/mcp
export WORLD_MODEL_DB_PATH=/var/lib/world-model

python -m world_model_server.server
```

## Step 2. Wire it into Anthropic via an MCP tunnel

MCP tunnels are research preview as of May 2026; you need an approved
tunnel and the `ant` CLI.

```bash
ant login

ant tunnels create world-model-tunnel \
  --upstream http://world-model-mcp:8765 \
  --hostname world-model.internal.example.com

ant tunnels run world-model-tunnel   # holds the outbound connection open
```

Then register the MCP server with Claude Managed Agents:

```bash
ant mcp-servers create world-model \
  --tunnel world-model-tunnel \
  --path /mcp \
  --transport streamable-http
```

The server now appears in the Console's MCP-server dropdown for any
Managed Agents session in the same workspace.

## Step 3. Verify the loop

From the Console, attach `world-model` to an agent. In a session, the agent
can call `query_fact`, `validate_change`, `find_contradictions`, etc., and
the requests reach your container via the tunnel.

Locally:

```bash
curl -sf http://127.0.0.1:8765/healthz
```

## Modal deployment (alternative to Docker)

If you do not want to run Docker yourself, [Modal](https://modal.com) ships a
Python-only path that integrates cleanly with `ant tunnels`. See the
end-to-end example at
[`examples/managed-agents-self-hosted/`](../../examples/managed-agents-self-hosted/).

The short version:

```python
# deploy_modal.py
import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("world-model-mcp[http]")
)

app = modal.App("world-model-mcp")

@app.function(image=image, timeout=600, allow_concurrent_inputs=20)
@modal.web_server(8765, startup_timeout=60)
def server():
    import os, subprocess
    os.environ["WORLD_MODEL_TRANSPORT"] = "http"
    os.environ["WORLD_MODEL_HTTP_PORT"] = "8765"
    os.environ["WORLD_MODEL_HTTP_HOST"] = "0.0.0.0"
    subprocess.Popen(["python", "-m", "world_model_server.server"])
```

```bash
pip install modal
modal deploy deploy_modal.py
# Modal prints the public URL, e.g. https://your-org--world-model-mcp.modal.run
ant tunnels create wm --upstream https://your-org--world-model-mcp.modal.run
ant mcp-servers create world-model --tunnel wm --path /mcp --transport streamable-http
```

## Operational notes

- **Memory caveat (again)**: Anthropic's official line is that the
  built-in Memory primitive is not yet supported in self-hosted sandboxes.
  `world-model-mcp` is the alternative -- not a replacement for the
  managed Memory tool when that becomes available; a stop-gap and a more
  feature-rich option for regulated environments.
- **State location**: the SQLite databases live at `WORLD_MODEL_DB_PATH`.
  Mount that on a persistent volume so the graph survives container
  restarts. The bundled `docker-compose.yml` uses a named volume by default.
- **Auth at the tunnel layer**: MCP tunnels combine Anthropic mTLS,
  customer TLS, and optional per-server OAuth. `world-model-mcp` does not
  add a separate auth layer; terminate at your proxy if needed.
- **Healthcheck**: `/healthz` returns `{"status":"ok","version":"..."}`. Use
  it as the upstream probe for `ant tunnels`, Docker, or Kubernetes.

## Related

- [`Dockerfile.http`](../../Dockerfile.http) and
  [`docker-compose.yml`](../../docker-compose.yml) -- reference HTTP image
- [`docs/deployment/mcp-tunnel.md`](mcp-tunnel.md) -- the original MCP
  tunnel deployment doc (general-purpose; this guide is the self-hosted
  Managed Agents specialization)
- [Claude Managed Agents updates](https://claude.com/blog/claude-managed-agents-updates)
- [Anthropic Memory tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
