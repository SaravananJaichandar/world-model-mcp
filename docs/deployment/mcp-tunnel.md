# Deploying world-model-mcp over MCP tunnels

This guide walks through running `world-model-mcp` as a long-lived HTTP service
inside your own perimeter and exposing it to Claude Managed Agents via MCP
tunnels (research preview as of May 2026).

If you only use Claude Code or Cursor locally, you do not need any of this --
the default stdio transport is the right choice. This guide is for teams
running Claude Managed Agents with self-hosted sandboxes, or any deployment
where the MCP server lives behind a firewall and Claude reaches it from
Anthropic-side infrastructure.

## When to use HTTP transport

| Scenario | Transport |
| --- | --- |
| Claude Code, Cursor, or `.mcpb` install on a developer machine | stdio (default) |
| Self-hosted sandbox in Claude Managed Agents, agent running inside your VPC | HTTP via MCP tunnel |
| Anthropic-managed cloud container, calling a private MCP server | HTTP via MCP tunnel |
| Local container + ngrok / Cloudflare Tunnel for any client supporting remote MCP | HTTP |

## Architecture

```
+-------------------------+         +--------------------+         +-----------------------+
|   Claude (Anthropic)    | --mTLS--> Cloudflare Edge ---> cloudflared inside your VPC ---> world-model-mcp
|   Managed Agents loop   |          (MCP tunnel proxy)             (Dockerfile.http,        :8765/mcp
+-------------------------+                                          /healthz on /healthz)
```

The MCP wire protocol is unchanged from the stdio case. The tunnel terminates
TLS at the customer side; Anthropic only sees an opaque encrypted stream until
your proxy decrypts it.

## Step 1. Run world-model-mcp in HTTP mode

The simplest path is the bundled Docker image:

```bash
# from a clone of github.com/SaravananJaichandar/world-model-mcp
docker compose up -d                       # uses Dockerfile.http
curl -sf http://127.0.0.1:8765/healthz     # {"status":"ok","version":"0.7.2"}
```

Without Docker:

```bash
pip install 'world-model-mcp[http]'

export WORLD_MODEL_TRANSPORT=http
export WORLD_MODEL_HTTP_HOST=127.0.0.1
export WORLD_MODEL_HTTP_PORT=8765
export WORLD_MODEL_HTTP_PATH=/mcp
export WORLD_MODEL_DB_PATH=/var/lib/world-model

python -m world_model_server.server
```

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `WORLD_MODEL_TRANSPORT` | `stdio` | Set to `http` to enable the HTTP path |
| `WORLD_MODEL_HTTP_HOST` | `0.0.0.0` | Bind address |
| `WORLD_MODEL_HTTP_PORT` | `8765` | Bind port |
| `WORLD_MODEL_HTTP_PATH` | `/mcp` | Path prefix for the streamable HTTP MCP endpoint |
| `WORLD_MODEL_DB_PATH` | `./.claude/world-model` | SQLite database location |

The server exposes two routes:

- `GET /healthz` -- liveness probe, returns `{"status": "ok", "version": "..."}`
- `POST/GET <WORLD_MODEL_HTTP_PATH>` -- streamable HTTP MCP endpoint

## Step 2. Wire it into Claude Managed Agents

MCP tunnels are in research preview; you need an approved tunnel via
`claude.com/form/claude-managed-agents` and the `ant` CLI installed.

Create a tunnel and register your MCP server. From inside your private
network (the same network the world-model-mcp container is reachable from):

```bash
# Authenticate
ant login

# Create a tunnel definition (one-time)
ant tunnels create world-model-tunnel \
  --upstream http://world-model-mcp:8765 \
  --hostname world-model.internal.example.com

# Start the cloudflared sidecar that holds the outbound tunnel open
ant tunnels run world-model-tunnel
```

Then register the MCP server with Claude Managed Agents (the tunnel resolves
to the upstream you set above):

```bash
ant mcp-servers create world-model \
  --tunnel world-model-tunnel \
  --path /mcp \
  --transport streamable-http
```

The server now appears in the Console MCP-server dropdown for any Managed
Agents session in the same workspace.

## Step 3. Verify

From the Console, attach `world-model` to an agent and call a query tool, for
example `query_fact`. If you see the MCP request logged by the world-model-mcp
container, the tunnel is working.

Or from a local test machine that does not need to be inside the perimeter:

```bash
# After tunnel is up, the upstream is still only reachable through Anthropic.
# To smoke-test the local container directly:
curl -sf http://127.0.0.1:8765/healthz
```

## Operational notes

- **Memory caveat for self-hosted sandboxes**: at the time of this writing,
  Claude Managed Agents' built-in Memory primitive is not supported in
  self-hosted sandboxes. world-model-mcp over MCP tunnel is one way to bring
  durable, queryable, audit-logged memory into that configuration.
- **Single endpoint, two transports**: the HTTP path and stdio path share the
  same MCP tool surface (25 tools). All hooks, constraints, and audit log
  behavior is identical across transports.
- **State isolation**: the world-model SQLite databases live at
  `WORLD_MODEL_DB_PATH`. Mount that path on a persistent volume so the graph
  survives container restarts. The bundled `docker-compose.yml` does this via
  the `world-model-data` volume.
- **Auth at the tunnel layer**: the MCP tunnel handshake uses Anthropic mTLS
  plus customer TLS plus optional per-server OAuth on top. world-model-mcp
  itself does not require an API key; if you want client auth at the MCP
  server layer, terminate it at your proxy or run the tunnel only inside a
  trusted network.
- **Healthcheck endpoint**: `/healthz` is intentionally cheap. Use it as the
  upstream health probe for `ant tunnels`, Docker, or Kubernetes.
- **Glama / .mcpb / Cursor installs are unaffected**. Those flows continue
  to use the stdio transport.

## Related

- `Dockerfile.http`, `docker-compose.yml` -- the reference HTTP image
- `Dockerfile` (no suffix) -- the stdio image used by Glama
- `world_model_server/server.py` -- both transports live in the same entry
  point, selected by `WORLD_MODEL_TRANSPORT`
