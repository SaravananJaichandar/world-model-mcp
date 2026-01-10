#!/bin/bash
#
# World Model MCP - Installation Script
#
# Sets up world model in a project directory.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${1:-$(pwd)}"

echo "🌍 World Model MCP - Installation"
echo "=================================="
echo ""
echo "Installing to: $PROJECT_DIR"
echo ""

# Check if .claude directory exists
if [ ! -d "$PROJECT_DIR/.claude" ]; then
    echo "Creating .claude directory..."
    mkdir -p "$PROJECT_DIR/.claude"
fi

# Create world-model directory
echo "Creating world-model directory..."
mkdir -p "$PROJECT_DIR/.claude/world-model"

# Copy hooks
echo "Installing hooks..."
mkdir -p "$PROJECT_DIR/.claude/hooks"

# Build hooks if needed
if [ -d "$SCRIPT_DIR/../hooks" ]; then
    echo "Building TypeScript hooks..."
    cd "$SCRIPT_DIR/../hooks"

    # Install dependencies if needed
    if [ ! -d "node_modules" ]; then
        npm install
    fi

    # Build TypeScript
    npm run build

    # Copy compiled hooks
    cp -r dist/* "$PROJECT_DIR/.claude/hooks/"
    echo "✓ Hooks installed"
fi

# Copy MCP config
echo "Installing MCP configuration..."
if [ -f "$SCRIPT_DIR/../examples/.mcp.json" ]; then
    if [ ! -f "$PROJECT_DIR/.mcp.json" ]; then
        cp "$SCRIPT_DIR/../examples/.mcp.json" "$PROJECT_DIR/.mcp.json"
        echo "✓ .mcp.json created"
    else
        echo "⚠️  .mcp.json already exists, skipping"
    fi
fi

# Copy Claude settings
echo "Installing Claude settings..."
if [ -f "$SCRIPT_DIR/../examples/.claude/settings.json" ]; then
    if [ ! -f "$PROJECT_DIR/.claude/settings.json" ]; then
        cp "$SCRIPT_DIR/../examples/.claude/settings.json" "$PROJECT_DIR/.claude/settings.json"
        echo "✓ .claude/settings.json created"
    else
        echo "⚠️  .claude/settings.json already exists, merging hooks..."
        # TODO: Merge hooks instead of overwriting
    fi
fi

# Initialize database
echo "Initializing world model database..."
python -m world_model_server.init --project-dir "$PROJECT_DIR"

echo ""
echo "✅ Installation complete!"
echo ""
echo "Next steps:"
echo "1. Restart Claude Code"
echo "2. The world model will start capturing your coding sessions"
echo "3. Check .claude/world-model/ for the knowledge graph databases"
echo ""
echo "To verify installation:"
echo "  ls -la $PROJECT_DIR/.claude/world-model/"
echo ""
