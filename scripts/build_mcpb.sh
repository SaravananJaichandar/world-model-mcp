#!/usr/bin/env bash
#
# Build a .mcpb (Anthropic Desktop Extension) archive from the current source.
#
# Usage:
#   bash scripts/build_mcpb.sh [version]
#
# Output: dist/world-model-mcp-<version>.mcpb
#

set -euo pipefail

VERSION="${1:-0.6.0}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT/dist/mcpb-build"
OUTPUT="$ROOT/dist/world-model-mcp-${VERSION}.mcpb"

echo "Building .mcpb v${VERSION}"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/hooks"
mkdir -p "$ROOT/dist"

# Copy manifest
cp "$ROOT/manifest.json" "$BUILD_DIR/"

# Copy hooks (compiled JS)
cp "$ROOT/world_model_server/hooks/"*.js "$BUILD_DIR/hooks/"

# Copy server source
cp -r "$ROOT/world_model_server" "$BUILD_DIR/"

# Copy example .mcp.json if present
if [ -f "$ROOT/examples/.mcp.json" ]; then
    cp "$ROOT/examples/.mcp.json" "$BUILD_DIR/" 2>/dev/null || true
fi

# Build the archive
cd "$BUILD_DIR"
rm -f "$OUTPUT"
zip -r "$OUTPUT" . -x "*/__pycache__/*" "*.pyc" >/dev/null

echo "Built: $OUTPUT"
ls -lh "$OUTPUT"
