"""
MCP 2026-07-28 spec-readiness scaffolding.

The 2026-07-28 MCP spec (currently Release Candidate, final ships 2026-07-28)
moves the protocol to stateless-first: every request carries protocol
metadata in a ``_meta`` field, HTTP responses include ``Mcp-Method`` and
``Mcp-Name`` headers, and mid-call elicitation is expressed as an
``InputRequiredResult`` rather than a held-open session.

This module is intentionally non-behavior-changing. Its purpose is:

  1. Observe & log the ``_meta`` fields we see from incoming requests so
     operators can verify their client's spec version is being detected.
  2. Provide small extraction helpers so, once the SDK exposes ``_meta``
     natively on the call-tool decorator, the call site changes in one
     place, not seven.
  3. Document what world-model-mcp does and does not support against the
     RC via READINESS_STATE — the single source of truth also read by
     tests and the audit doc.

Nothing here alters tool dispatch, argument parsing, or return values.
Backward compatibility with the 2025-03-26 spec is preserved unconditionally.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional


logger = logging.getLogger("world_model_server.spec_readiness")


# Known meta keys under the io.modelcontextprotocol/ prefix in the
# 2026-07-28 spec. These live in the request's _meta field. Servers do
# not need to act on them yet; logging them lets operators confirm what
# their client is sending.
KNOWN_META_KEYS = (
    "io.modelcontextprotocol/protocolVersion",
    "io.modelcontextprotocol/clientInfo",
    "io.modelcontextprotocol/clientCapabilities",
)


# Readiness matrix. Each entry is (change, state, notes). State is one
# of: "compatible", "logged", "not_yet", "not_applicable". The state is
# machine-readable so both the audit doc and the readiness tests can
# check it without duplication.
READINESS_STATE: Dict[str, Dict[str, str]] = {
    "stateless_first": {
        "state": "compatible",
        "notes": (
            "world-model-mcp tool calls are already effectively stateless: "
            "each call opens a fresh aiosqlite connection and returns; no "
            "session state is inferred across requests."
        ),
    },
    "_meta_field": {
        "state": "logged",
        "notes": (
            "Incoming _meta fields (io.modelcontextprotocol/protocolVersion, "
            "clientInfo, clientCapabilities) are logged at INFO when present. "
            "No behavior branches on them yet — the RC may change shape "
            "before 2026-07-28 final ship."
        ),
    },
    "mcp_method_mcp_name_headers": {
        "state": "not_yet",
        "notes": (
            "Streamable HTTP header emission (Mcp-Method, Mcp-Name) will land "
            "after the final spec confirms header names and semantics. "
            "Current HTTP transport does not emit them."
        ),
    },
    "input_required_result": {
        "state": "not_applicable",
        "notes": (
            "No world-model tool currently requires mid-call user input. "
            "InputRequiredResult support is a no-op until we ship such a tool."
        ),
    },
    "server_discover_method": {
        "state": "not_yet",
        "notes": (
            "server/discover returns capabilities on demand. Currently we "
            "serve tools via list_tools per the 2025-03-26 spec. "
            "Add after the final spec locks the response shape."
        ),
    },
}


def extract_meta(arguments: Any) -> Optional[Mapping[str, Any]]:
    """Return the ``_meta`` mapping from a call-tool arguments payload, or
    None if not present.

    Some 2026-spec clients place ``_meta`` inside the arguments dict for
    backward compatibility with servers whose SDK version does not yet
    expose the field on the decorator. This helper is the single place
    that decision lives — once the MCP SDK exposes ``_meta`` natively on
    ``call_tool``, this function moves to consult that source instead
    and the call site (server.py) does not change.
    """
    if not isinstance(arguments, dict):
        return None
    meta = arguments.get("_meta")
    if meta is None:
        return None
    if not isinstance(meta, dict):
        # Malformed — a strict spec-compliant client should not do this,
        # but we do not want a misbehaving client to crash our loop.
        logger.warning("Ignoring non-dict _meta on call-tool: %r", type(meta).__name__)
        return None
    return meta


def log_meta_if_present(tool_name: str, arguments: Any) -> None:
    """One-line observability hook the call-tool dispatcher can invoke.

    No side effects other than a log line. Safe to call from every
    call_tool dispatch — cheap, does not open resources, does not raise.
    """
    meta = extract_meta(arguments)
    if meta is None:
        return
    seen = {k: meta[k] for k in KNOWN_META_KEYS if k in meta}
    unknown = [k for k in meta.keys() if k not in KNOWN_META_KEYS]
    logger.info(
        "MCP spec _meta seen on %s: known=%s unknown_keys=%s",
        tool_name, seen, unknown,
    )


def readiness_summary() -> Dict[str, str]:
    """Compact {change: state} view of READINESS_STATE. Used by tests
    and the audit doc's rendered table."""
    return {k: v["state"] for k, v in READINESS_STATE.items()}
