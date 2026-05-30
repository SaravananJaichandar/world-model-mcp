#!/usr/bin/env bash
#
# ant-setup.sh -- wire a deployed world-model-mcp instance into Anthropic
# Claude Managed Agents via an MCP tunnel.
#
# Usage:
#     bash ant-setup.sh <upstream-url>
# Example:
#     bash ant-setup.sh https://your-org--world-model-mcp.modal.run
#
# Pre-reqs:
#     - The `ant` CLI is installed and you've run `ant login`.
#     - You have an approved MCP tunnel (research preview as of May 2026).

set -euo pipefail

UPSTREAM="${1:-}"
if [ -z "$UPSTREAM" ]; then
    echo "Usage: $0 <upstream-url>" >&2
    echo "Example: $0 https://your-org--world-model-mcp.modal.run" >&2
    exit 2
fi

TUNNEL_NAME="${TUNNEL_NAME:-world-model-tunnel}"
SERVER_NAME="${SERVER_NAME:-world-model}"

echo "[1/2] Creating tunnel ${TUNNEL_NAME} -> ${UPSTREAM}"
ant tunnels create "${TUNNEL_NAME}" --upstream "${UPSTREAM}"

echo "[2/2] Registering MCP server ${SERVER_NAME} on tunnel ${TUNNEL_NAME}"
ant mcp-servers create "${SERVER_NAME}" \
    --tunnel "${TUNNEL_NAME}" \
    --path /mcp \
    --transport streamable-http

echo
echo "Done. The MCP server '${SERVER_NAME}' is now available in the Claude"
echo "Managed Agents Console MCP-server dropdown for this workspace."
echo
echo "To run the tunnel from this machine:"
echo "    ant tunnels run ${TUNNEL_NAME}"
