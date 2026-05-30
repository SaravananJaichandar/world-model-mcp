"""
Deploy world-model-mcp on Modal as a streamable-HTTP MCP server, for use
behind an Anthropic MCP tunnel in the Claude Managed Agents self-hosted
sandbox configuration.

Usage:
    pip install modal
    modal deploy deploy_modal.py

Modal prints the public URL after the deploy completes. Wire it into
Anthropic with `bash ant-setup.sh <printed-url>`.
"""

import modal

# Pin to a specific image so cold-starts are deterministic. Bump
# `world-model-mcp` here to upgrade the deployed memory layer.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("world-model-mcp[http]>=0.7.4")
)

app = modal.App("world-model-mcp")


@app.function(
    image=image,
    timeout=3600,
    allow_concurrent_inputs=20,
    # For real workloads, attach a Volume so the SQLite databases survive
    # container restarts:
    #   volume = modal.Volume.from_name("world-model-data", create_if_missing=True)
    #   volumes={"/data/world-model": volume}
)
@modal.web_server(8765, startup_timeout=90)
def server() -> None:
    """Run world-model-mcp's streamable-HTTP transport on Modal.

    Modal owns the public TLS endpoint; we just bind locally on 8765 and
    let Modal route requests to it.
    """
    import os
    import subprocess

    os.environ.setdefault("WORLD_MODEL_TRANSPORT", "http")
    os.environ.setdefault("WORLD_MODEL_HTTP_HOST", "0.0.0.0")
    os.environ.setdefault("WORLD_MODEL_HTTP_PORT", "8765")
    os.environ.setdefault("WORLD_MODEL_HTTP_PATH", "/mcp")
    os.environ.setdefault("WORLD_MODEL_DB_PATH", "/data/world-model")

    # The Modal @web_server decorator manages the lifetime; spawn the
    # actual server as a child process so this function returns control
    # back to Modal immediately.
    subprocess.Popen(["python", "-m", "world_model_server.server"])
