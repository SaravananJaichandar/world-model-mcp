# World Model MCP - Quick Start Guide

## 🚀 Installation (5 minutes)

### Prerequisites
- Python 3.11+
- Node.js 18+
- Claude Code (VSCode extension)

### Step 1: Install the package

```bash
# Clone the repository
git clone https://github.com/yourorg/world-model-mcp.git
cd world-model-mcp

# Install Python dependencies
pip install -e .

# Build TypeScript hooks
cd hooks
npm install
npm run build
cd ..
```

### Step 2: Set up in your project

```bash
# Navigate to your project
cd /path/to/your/project

# Run setup
python -m world_model_server.cli setup

# Or use the bash script directly
bash /path/to/world-model-mcp/scripts/install.sh .
```

This will create:
```
your-project/
├── .mcp.json                    # MCP server configuration
├── .claude/
│   ├── settings.json           # Hook configuration
│   ├── hooks/                  # Compiled hook scripts
│   └── world-model/            # SQLite databases
│       ├── entities.db
│       ├── facts.db
│       ├── relationships.db
│       ├── constraints.db
│       ├── sessions.db
│       └── events.db
```

### Step 3: Restart Claude Code

Close and reopen VSCode (or reload window: `Cmd/Ctrl + Shift + P` → "Reload Window")

## ✅ Verify Installation

```bash
# Check status
python -m world_model_server.cli status

# Should show:
# ✓ Databases initialized
# ✓ Hooks installed
# ✓ MCP configuration ready
```

## 🎯 Usage Examples

### Example 1: Preventing Hallucinations

**Before World Model:**
```
User: "Add user authentication"
Claude: "I'll use the User.findByEmail() method..."
Result: ❌ Method doesn't exist, code breaks
```

**With World Model:**
```
User: "Add user authentication"
Claude internally:
  → query_fact("User.findByEmail")
  → Result: exists=false, confidence=0.9
  → Alternative: User.findOne({email: "..."})

Claude: "I'll use User.findOne({email}) to find users by email,
        based on the existing API in src/models/User.ts:42"
Result: ✅ Correct API, code works
```

### Example 2: Learning from Corrections

**Session 1:**
```typescript
// Claude writes:
console.log('Rate limit applied');

// You correct to:
logger.info('Rate limit applied');

// World model records:
Constraint learned: {
  rule: "no-console",
  type: "linting",
  pattern: "Use logger.info() instead of console.log()",
  violation_count: 1
}
```

**Session 2 (next day):**
```typescript
// Claude automatically writes:
logger.info('Request received');  // ✅ Learned from correction!

// No user correction needed
```

### Example 3: Regression Prevention

```typescript
// Week 1: Bug fixed
// src/api/auth.ts:42-45
if (!refreshToken) {
  return null;  // Fixed: prevents null pointer
}

// World model records:
Bug fix: "Null check for refreshToken prevents session expiry bug"
Critical region: Lines 42-45

// Week 2: Refactoring
User: "Refactor the authentication middleware"

// World model warns:
⚠️  This file has 1 known bug fix (refreshToken null check)
    Critical region: Lines 42-45 must preserve null check

// Claude's refactored code preserves the fix:
export const authenticateUser = (req, res, next) => {
  const { refreshToken } = req.cookies;

  // Preserve fix for bug #123 (null check)
  if (!refreshToken) {
    return null;
  }

  // ... rest of refactored code
}
```

## 🔧 Configuration

### Environment Variables

```bash
# Database path (default: ./.claude/world-model/)
export WORLD_MODEL_DB_PATH="/path/to/custom/location"

# Anthropic API key (optional - for LLM-powered entity extraction)
# IMPORTANT: Never commit this! Use .env file (see .env.example)
export ANTHROPIC_API_KEY="your-api-key-here"

# Debug mode
export WORLD_MODEL_DEBUG=1
```

### Customizing Hooks

Edit `.claude/settings.json` to customize which tools trigger hooks:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|Bash",  // Add/remove tools here
        "hooks": [...]
      }
    ]
  }
}
```

## 📊 Monitoring

### View captured events

```bash
# Check session files
ls -la .claude/world-model/*.json

# View latest session
cat .claude/world-model/session-<id>.json | jq
```

### Query the knowledge graph

```python
import asyncio
from world_model_server.knowledge_graph import KnowledgeGraph

async def query():
    kg = KnowledgeGraph(".claude/world-model")

    # Query facts
    result = await kg.query_facts("JWT authentication")
    print(f"Found {len(result.facts)} facts")
    for fact in result.facts:
        print(f"  - {fact.fact_text}")

    # Get constraints
    constraints = await kg.get_constraints("src/**/*.ts")
    print(f"\nConstraints: {len(constraints)}")
    for c in constraints:
        print(f"  - {c.rule_name}: {c.description}")

asyncio.run(query())
```

## 🐛 Troubleshooting

### Hooks not firing

1. Check Claude Code console: `Cmd/Ctrl + Shift + P` → "Claude Code: Show Output Channel"
2. Verify hooks are executable: `ls -la .claude/hooks/`
3. Check hook output: Look for errors in Claude Code output

### MCP server not connecting

1. Test MCP server manually:
```bash
echo '{"method": "initialize"}' | python -m world_model_server.server
```

2. Check `.mcp.json` paths are correct
3. Verify `WORLD_MODEL_DB_PATH` is set correctly

### Database not initializing

```bash
# Re-initialize
python -m world_model_server.init --project-dir .

# Check permissions
ls -la .claude/world-model/
```

## 📚 Next Steps

- **Advanced Usage**: See [README.md](README.md) for full documentation
- **API Reference**: See [docs/API.md](docs/API.md) for MCP tool details
- **Contributing**: See [CONTRIBUTING.md](CONTRIBUTING.md) to help improve the project

## 🆘 Getting Help

- **Issues**: https://github.com/yourorg/world-model-mcp/issues
- **Discussions**: https://github.com/yourorg/world-model-mcp/discussions
- **Discord**: https://discord.gg/world-model-mcp

---

**Happy coding with your new world model! 🌍🤖**
