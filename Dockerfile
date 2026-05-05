# world-model-mcp - Temporal knowledge graph MCP server for codebases
#
# Builds a minimal image that exposes the MCP server over stdio.
# Used by Glama (https://glama.ai/mcp/servers) for introspection checks
# and by users who want to run the server in a container.

FROM python:3.11-slim

# Install git (required by seeder for git ls-files) and gh CLI optional dep
# (PR review ingestion requires gh CLI; install only the lightweight git here).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package source
COPY pyproject.toml README.md LICENSE ./
COPY world_model_server ./world_model_server

# Install the package
RUN pip install --no-cache-dir .

# Default DB path is the project's .claude/world-model directory.
# Override via the WORLD_MODEL_DB_PATH environment variable.
ENV WORLD_MODEL_DB_PATH=/data/world-model

VOLUME ["/data"]

# MCP servers communicate over stdio - no port exposed
ENTRYPOINT ["python3", "-m", "world_model_server.server"]
