# ADR-0001: pin_annotation MCP tool for human interventions in the audit chain

- **Status:** Proposed
- **Date:** 2026-07-22
- **Decision drivers:** primitive-layer positioning; v0.15.0 build queue

## Context

The tamper-evident audit log shipped in v0.13 records agent actions (facts, constraints, events, decisions) but does not represent human interventions as first-class citizens of the signed chain. In compliance-adjacent workflows, a reviewer or operator often overrides an agent's tool call mid-run, records a rationale for why an agent decision was accepted or rejected, or attaches a policy note to a span of agent actions. Today, that intervention lands in the general `events` log with no direct link to the specific agent actions it modifies, forcing an auditor to reconstruct the intervention from timestamps.

Etch's primitive-layer positioning says the audit log is the primitive that sits under any agent stack. A first-class primitive for human interventions in the signed chain directly serves that positioning: buyers running compliance-adjacent workflows (regulated AI vendors selling into fintech, healthcare, gov) need to prove *which* human overrode *what* and *when*, with the same cryptographic assurance as agent actions themselves.

Concretely: the current gap is that "the agent tried X, the human overrode with rationale Y" cannot be represented as a single verifiable claim in the chain. It must be reconstructed by correlating separate event rows, which is fragile and not auditor-friendly.

## Decision

Add a `pin_annotation` MCP tool to world-model-mcp OSS (not proprietary hosted-only), and a corresponding `annotations` table + Merkle chain integration in the audit log.

### 1. Data model

New table `annotations`:

| column | type | notes |
|---|---|---|
| `id` | TEXT PRIMARY KEY | UUID; generated at insert |
| `epoch_id` | INTEGER | Merkle epoch containing this annotation |
| `session_id` | TEXT | agent session the annotation attaches to |
| `event_range_start` | TEXT | first event_id in the span this annotation covers |
| `event_range_end` | TEXT | last event_id in the span (may equal `event_range_start` for a single-event annotation) |
| `author` | TEXT | free-form; caller-supplied. For hosted Etch, the author is a KMS-verified identity. For OSS, author is a self-asserted string. |
| `rationale` | TEXT | free-form human rationale; UTF-8, max 8 KB |
| `annotation_type` | TEXT | one of `human_intervention`, `human_note`, `override_justification` |
| `timestamp` | TEXT | ISO-8601 UTC, ms precision |
| `signature` | BLOB | detached hybrid Ed25519 + SLH-DSA-SHA2-128f signature of the canonical annotation record |
| `created_at` | TEXT | ISO-8601 UTC, ms precision |

Indexes on `epoch_id`, `session_id`, and `(event_range_start, event_range_end)`.

### 2. MCP tool signature

```python
async def pin_annotation(
    session_id: str,
    event_range_start: str,
    event_range_end: str,
    author: str,
    rationale: str,
    annotation_type: Literal[
        "human_intervention",
        "human_note",
        "override_justification",
    ],
) -> PinAnnotationResult:
    """Attach a signed human annotation to a span of agent events.

    Returns the annotation_id, the epoch_id the annotation lands in, and
    a bool signed indicating whether the annotation was successfully
    committed to a signed Merkle epoch (True) or still pending in the
    open epoch (False).
    """
```

Response model:

```python
class PinAnnotationResult(BaseModel):
    annotation_id: str
    epoch_id: int
    signed: bool
    signature_hash: Optional[str]  # SHA-256 of the signature blob, if signed
```

### 3. Audit chain integration

Annotations chain into the same Merkle log as agent events. The leaf hash for an annotation uses a distinct domain prefix so annotations and events are never confused:

```
DOMAIN_ANNOTATION = "world-model-mcp/transparency-log/annotation/v1"
```

An annotation contributes to the same 1024-entry epoch closes as regular events. The `annotation_type` is part of the canonical serialization, so the verifier can filter annotations from events without re-parsing.

### 4. Verifier contract

The reference verifier (Python + TypeScript) gains two new checks:

1. **Annotation inclusion proof.** `prove_annotation_inclusion(annotation_id)` returns a Merkle path proving the annotation is in a signed epoch. Same shape as `prove_entry_inclusion` for regular events.
2. **Annotation span consistency.** For an annotation with `event_range_start` and `event_range_end`, the verifier checks that both event_ids exist in the same or an earlier epoch than the annotation. An annotation attached to a future event is rejected.

### 5. OSS vs hosted split

- **OSS (world-model-mcp):** ships `pin_annotation` MCP tool, `annotations` table, chain integration, offline verifier updates. Author is a self-asserted string.
- **Hosted (Etch, BUSL 1.1):** adds KMS-verified author identity (Etch resolves the caller's user_id and stamps the annotation with the KMS-signed identity, not just the free-form string), signed PDF export with a visual timeline that renders human interventions distinctly from agent events, and a compliance-framework mapping (SOC 2 CC6.3 / HIPAA 164.312 / EU AI Act Art. 12) for the annotation types.

The primitive belongs to OSS. Etch hosted adds managed identity, presentation, and framework mapping. This preserves the sit-under positioning: an operator running OSS locally gets the full primitive; buyers who need the managed layer subscribe to Etch.

## Test surface (mandatory per [[engineering-mandate]])

Before v0.15.0 ships, the following test surface must exist and pass:

### Unit tests (`tests/test_pin_annotation.py`)

1. **Schema validation.** Every field in `PinAnnotationResult` and the DB row round-trips through Pydantic without loss.
2. **Annotation type validation.** Passing an unknown `annotation_type` raises a clear error, not silent success.
3. **Range validation.** `event_range_end` before `event_range_start` in the same session is rejected.
4. **Range validation cross-session.** `event_range_start` from session A and `event_range_end` from session B is rejected.
5. **Rationale size limit.** Rationale > 8 KB is rejected with a clear error.
6. **Author non-empty.** Empty-string author is rejected.

### Integration tests (`tests/test_pin_annotation_integration.py`)

1. **End-to-end via MCP.** Invoke the tool via the MCP JSON-RPC interface (not a direct function call). Verify the response shape, the DB write, and the epoch inclusion.
2. **Multiple annotations in one epoch.** Pin 5 annotations to the same session. Verify all land in the same epoch when it closes.
3. **Epoch close semantics.** Pin 1023 events + 1 annotation. Verify the epoch closes after entry 1024 and both the events and the annotation are included in the signed root.
4. **Chain continuity.** Pin an annotation, close the epoch, pin another annotation in the next epoch. Verify `log_prev_leaf_hash` chains correctly across the annotation entries.

### Security tests (`tests/test_pin_annotation_security.py`)

1. **Signature validity.** A pinned annotation's signature must verify against the operator's public keys via the reference verifier.
2. **Tamper detection: rationale.** Modify the `rationale` text in the DB after signing. Reference verifier must reject the annotation.
3. **Tamper detection: author.** Modify the `author` field in the DB after signing. Reference verifier must reject the annotation.
4. **Tamper detection: event range.** Modify `event_range_start` or `event_range_end` in the DB after signing. Verifier must reject.
5. **Domain separation.** A leaf hash computed with the event domain prefix must NOT verify as an annotation, and vice versa. Prevents cross-type replay.
6. **No auth bypass.** The MCP tool must not accept an annotation for a session_id the caller has no session-level permission to touch. (Hosted Etch enforces via KMS identity; OSS enforces via file-system permission on the DB path.)

### End-to-end product-flow test (`tests/test_pin_annotation_e2e.py`)

Simulates the mid-run intervention workflow end-to-end:

1. Start an agent session, log a few tool calls
2. Human "intervenes" mid-run and pins a `human_intervention` annotation to the span [tool_call_2, tool_call_5]
3. Continue the session with more tool calls
4. Close the epoch
5. Export the audit chain to a dump manifest
6. Run the offline `etch-verify` CLI against the dump
7. Verify:
   - The annotation is present in the verified output
   - Its span reference resolves to the actual tool calls
   - The signature verifies
   - The `annotation_type` and `rationale` are readable

The test must run WITHOUT the world-model-mcp process alive when the verifier runs, matching the "offline verifier, no vendor trust" positioning.

### Property-based tests (`tests/test_pin_annotation_properties.py`, using `hypothesis`)

1. **Ordering invariance.** For any two annotations in the same epoch, changing the pin order must not change the signed root.
2. **Span invariance.** For any annotation spanning `[start, end]`, the annotation must land in the earliest epoch that includes `end` (not `start`) — this is the timing-safety property.
3. **Idempotence.** Pinning the same annotation twice returns two distinct annotation_ids; there is no dedup at this layer.

## Consequences

### Positive
- Represents the mid-run human intervention workflow directly, unlocking a category of compliance-adjacent buyers who need to prove human oversight cryptographically.
- Establishes annotations as a first-class primitive at the OSS layer, reinforcing sit-under positioning.
- Test surface exceeds the current baseline for a single feature (5 test files across unit / integration / security / e2e / property-based), setting the pattern for every v0.15.0+ feature per the engineering mandate.
- Etch hosted gets a differentiated feature (KMS-verified author + framework mapping) without owning the primitive.

### Negative
- Adds schema complexity to the audit log (one new table, one new leaf-domain prefix, verifier updates).
- Small performance impact: annotation entries add to per-epoch entry count, slightly reducing time between epoch closes for annotation-heavy sessions. Estimated overhead: negligible for typical workflows (< 10 annotations per session).
- OSS author is a self-asserted string. Offline verifier cannot distinguish a real human from a scripted call. Hosted Etch closes this gap via KMS identity; OSS operators must document their identity-binding process out-of-band.

### Neutral
- The `annotation_type` enum is fixed at 3 values (`human_intervention`, `human_note`, `override_justification`). Adding new types later is a schema migration; adding new types via CLI or config is not planned in v0.15.0.

## Alternatives considered

### Alt A: Add rationale/author fields to the existing `events` table

Rejected. Conflates two concepts (agent events vs human context on agent events), breaks the "leaf domain prefix per entity type" invariant, and would leave downstream consumers unable to filter human interventions from agent events without schema inspection.

### Alt B: Ship annotation as a hosted-only Etch feature

Rejected per [[north-star-2026-07-22]] Rule 5 (primitives belong at OSS layer). Hosted layer adds managed identity + presentation, not the primitive itself.

### Alt C: Use a free-form JSON blob for annotation content

Rejected. Free-form JSON is not verifiable — an auditor cannot know what schema an annotation was signed under. Explicit `annotation_type` + typed fields let the verifier and the presentation layer both reason correctly.

### Alt D: Skip the offline verifier update for v0.15.0

Rejected. The offline verifier is the trust root. Shipping a signed annotation that only Etch hosted can verify would break the "no vendor trust required" positioning that defines the primitive-layer play.

## Related

- [Audit log spec](../AUDIT_LOG.md) — chain of custody spec this ADR extends
- Primitive-layer positioning notes (internal)
- Engineering mandate (internal)
- v0.15.0 build queue (internal)
