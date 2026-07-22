# Integration tests

Tests in this directory require external artifacts that are not shipped
with world-model-mcp and are not always present in CI. They live outside
`tests/` proper so the default pytest run does not import them, but
each one is a real end-to-end verification and MUST NOT be replaced by
a mock in place of the external dependency (per the project engineering
mandate: no simulations).

Run any integration test explicitly:

```bash
pytest tests/integration/test_buzz_acp_handshake.py -v
```

## test_buzz_acp_handshake.py

Verifies world-model-mcp works end-to-end as an MCP server spawned by a
real `buzz-agent` binary via the ACP protocol.

### Prerequisites

1. Rust toolchain (stable, tested against 1.93.0)
2. Local clone of https://github.com/block/buzz
3. `buzz-agent` binary built:
   ```bash
   cd /path/to/buzz
   cargo build --release -p buzz-agent
   # produces target/release/buzz-agent
   ```
4. Point the test at the binary via env var:
   ```bash
   export BUZZ_AGENT_BIN=$HOME/starters/buzz/target/release/buzz-agent
   pytest tests/integration/test_buzz_acp_handshake.py -v
   ```

If `BUZZ_AGENT_BIN` is unset or points to a non-existent file, the test
is skipped with a clear message. This is the ONE class of skip
explicitly documented as legitimate in the engineering mandate:
environment-conditional skip for a real external binary that is not
present in every test host.

### What it verifies

- buzz-agent starts and speaks ACP over stdio
- buzz-agent accepts a `session/new` with world-model-mcp listed in
  `mcpServers`
- buzz-agent spawns world-model-mcp as a stdio MCP subprocess
- world-model-mcp responds to the MCP `initialize` + `list_tools` calls
  buzz-agent makes during MCP handshake
- `session/prompt` triggers an LLM turn; a canned fake-LLM response
  emits one tool call to `world_model__query_fact`
- buzz-agent forwards the tool call to world-model-mcp
- world-model-mcp returns a valid `QueryFactResult` JSON
- buzz-agent's tool_call session/update reaches `status=completed`
- The session ends cleanly with `stopReason=end_turn`

### What it does NOT do (documented gaps)

- Does not exercise the BUZZ desktop app UI — only the underlying
  ACP-compliant agent (buzz-agent) which is what the app orchestrates
- Does not test Nostr identity binding (world-model-mcp does not yet
  accept Nostr keys as agent identity)
- Does not test the buzz-relay's channel-level integration (out of
  scope; this is agent-to-MCP-server verification)
