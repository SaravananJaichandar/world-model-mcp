# MCP 2026-07-28 Spec Readiness

**Audit target:** MCP Specification 2026-07-28 (Release Candidate as of the v0.12.11 ship; final specification lands 2026-07-28).

**Purpose:** State clearly which parts of the RC world-model-mcp already handles, which are logged for observability, which are deferred pending the final spec, and which are not applicable. The machine-readable source of truth is `world_model_server/spec_readiness.READINESS_STATE`; tests assert this doc and that constant do not drift out of sync.

## Readiness matrix

| Change in 2026-07-28 RC | State | Notes |
| --- | --- | --- |
| Stateless-first: no session state assumed across requests | **compatible** | Every world-model tool call opens a fresh aiosqlite connection and returns. We infer nothing across requests today. No change needed. |
| `_meta` field on every request (`io.modelcontextprotocol/protocolVersion`, `/clientInfo`, `/clientCapabilities`) | **logged** | Incoming `_meta` is extracted and logged at INFO when a client sends it under `arguments._meta` (which some clients do for backward compat with servers whose SDK does not yet expose `_meta` natively on the decorator). No behavior branches on `_meta` yet — the RC may still shift key names before final ship. |
| HTTP headers `Mcp-Method`, `Mcp-Name` on Streamable HTTP responses | **not_yet** | Will land after final spec confirms exact header names, semantics, and whether they apply to every response or a subset. Emitting header names guessed from the RC risks breaking gateway routing when the final spec locks a variant. |
| `InputRequiredResult` (mid-call elicitation via `inputRequests` + `requestState`) | **not_applicable** | No world-model tool currently requires mid-call user input. Support is a no-op until we ship such a tool. |
| `server/discover` (client-pulled capabilities on demand) | **not_yet** | Today we serve tools via `list_tools` per the 2025-03-26 spec. Ship `server/discover` after the final spec locks the response shape — it's additive, not breaking. |

## Backward compatibility

Every reader / writer of the pre-2026 spec continues to work unchanged. `list_tools`, `call_tool`, and the tool schemas emitted for `query_fact` and friends are byte-identical to v0.12.10.

The `_meta` observability path is opt-in from the client side: if the client does not send `_meta` in the arguments payload, no code path activates.

## Why not implement more of the RC now?

Concrete reasons the "not_yet" rows exist:

1. **Release Candidate is not final.** The 2026-07-28 RC has already gone through revisions; naming and framing of some fields may change before the July 28 lock. Building against a moving target means potentially throwing code away 22 days from now.

2. **Header emission has gateway consequences.** Load balancers, gateways, and rate-limiters route on `Mcp-Method` / `Mcp-Name`. If we emit a header shape the final spec rejects, downstream infrastructure will silently misroute. Better to wait for lock.

3. **Additive vs breaking distinction.** Compatibility (row 1), observability (row 2), and no-op-until-needed (row 4) can all land now without risk. Header emission and `server/discover` are additive-looking but coupled to spec-final shapes; landing them prematurely is not additive from an operator's perspective.

## What tests lock

`tests/test_v01211_spec_readiness.py`:
- The five rows above exist in `READINESS_STATE` with the states this doc lists.
- `extract_meta` returns the mapping when present, `None` otherwise, `None` (with a warning log) when `_meta` is the wrong shape.
- `log_meta_if_present` is silent when no `_meta` is on the payload; emits a log line otherwise.
- The single call site in `server.py:call_tool` calls `log_meta_if_present` and remains non-behavior-changing (fact dispatch unaffected).

## Post-2026-07-28 follow-up

When the final specification ships:

1. Flip `mcp_method_mcp_name_headers` to `compatible` after implementing header emission in the HTTP transport.
2. Flip `server_discover_method` to `compatible` after implementing the endpoint.
3. Revisit `input_required_result` if any world-model tool grows a mid-call elicitation requirement (e.g., ambiguity resolution when two facts contradict).
4. Extend `KNOWN_META_KEYS` if the final spec adds keys we should surface in logs.

Each of the above is small and additive against a locked spec — no risk of re-work.
