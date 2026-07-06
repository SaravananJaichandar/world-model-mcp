"""
v0.12.11: MCP 2026-07-28 spec readiness scaffolding.

Non-behavior-changing observability + audit. This suite locks:
  - The five READINESS_STATE rows and their expected states
  - extract_meta / log_meta_if_present semantics
  - The single call site in server.py:call_tool is wired to log_meta_if_present
  - The public audit doc exists and its table mentions all five rows
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest

from world_model_server.spec_readiness import (
    KNOWN_META_KEYS,
    READINESS_STATE,
    extract_meta,
    log_meta_if_present,
    readiness_summary,
)


REPO_ROOT = Path(__file__).parent.parent
AUDIT_DOC = REPO_ROOT / "docs" / "MCP_2026_SPEC_READINESS.md"


# ---------------------------------------------------------------------------
# READINESS_STATE
# ---------------------------------------------------------------------------


EXPECTED_KEYS = {
    "stateless_first",
    "_meta_field",
    "mcp_method_mcp_name_headers",
    "input_required_result",
    "server_discover_method",
}

VALID_STATES = {"compatible", "logged", "not_yet", "not_applicable"}


def test_readiness_state_has_expected_rows():
    assert set(READINESS_STATE.keys()) == EXPECTED_KEYS


def test_readiness_state_states_locked():
    """These are the states that landed in v0.12.11. If a later patch
    flips one, update this test with the changelog entry that motivates
    the flip — do not silently drift."""
    summary = readiness_summary()
    assert summary["stateless_first"] == "compatible"
    assert summary["_meta_field"] == "logged"
    assert summary["mcp_method_mcp_name_headers"] == "not_yet"
    assert summary["input_required_result"] == "not_applicable"
    assert summary["server_discover_method"] == "not_yet"


def test_every_row_has_notes():
    for key, entry in READINESS_STATE.items():
        assert entry.get("notes"), f"{key} missing 'notes' body"
        assert entry.get("state") in VALID_STATES, f"{key} has invalid state {entry.get('state')!r}"


# ---------------------------------------------------------------------------
# extract_meta
# ---------------------------------------------------------------------------


def test_extract_meta_returns_none_for_non_dict_arguments():
    assert extract_meta(None) is None
    assert extract_meta("string") is None
    assert extract_meta(42) is None
    assert extract_meta([]) is None


def test_extract_meta_returns_none_when_meta_absent():
    assert extract_meta({"query": "foo"}) is None


def test_extract_meta_returns_the_meta_mapping_when_present():
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1.0"},
    }
    out = extract_meta({"query": "foo", "_meta": meta})
    assert out == meta


def test_extract_meta_returns_none_for_malformed_meta(caplog):
    """A non-dict _meta must not crash — return None + warn."""
    with caplog.at_level(logging.WARNING, logger="world_model_server.spec_readiness"):
        result = extract_meta({"_meta": "not-a-dict"})
    assert result is None
    assert any("Ignoring non-dict _meta" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# log_meta_if_present
# ---------------------------------------------------------------------------


def test_log_meta_if_present_silent_when_absent(caplog):
    with caplog.at_level(logging.INFO, logger="world_model_server.spec_readiness"):
        log_meta_if_present("query_fact", {"query": "foo"})
    assert not any("MCP spec _meta seen" in rec.message for rec in caplog.records)


def test_log_meta_if_present_logs_known_and_unknown_keys(caplog):
    args = {
        "query": "foo",
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "x"},
            "some.other.key": "ignored",
        },
    }
    with caplog.at_level(logging.INFO, logger="world_model_server.spec_readiness"):
        log_meta_if_present("query_fact", args)
    hits = [r for r in caplog.records if "MCP spec _meta seen" in r.message]
    assert hits, "expected at least one log line"
    line = hits[0].message
    assert "query_fact" in line
    assert "2026-07-28" in line
    # unknown keys are surfaced too so operators can see spec drift
    assert "some.other.key" in line


def test_log_meta_if_present_does_not_raise_on_bad_input():
    """The hook is called from the call-tool hot path; it must not raise
    even on garbage input."""
    log_meta_if_present("x", None)
    log_meta_if_present("x", 42)
    log_meta_if_present("x", "not a dict")
    log_meta_if_present("x", [])
    log_meta_if_present("x", {"_meta": "not-a-dict"})


# ---------------------------------------------------------------------------
# Call-site wiring
# ---------------------------------------------------------------------------


def test_server_call_tool_invokes_log_meta_if_present():
    """The single call site in server.py must call log_meta_if_present.
    Regression against silently removing observability."""
    server_py = (REPO_ROOT / "world_model_server" / "server.py").read_text()
    assert "log_meta_if_present" in server_py, (
        "server.py:call_tool must invoke log_meta_if_present per v0.12.11 audit."
    )


# ---------------------------------------------------------------------------
# Known meta keys stay locked
# ---------------------------------------------------------------------------


def test_known_meta_keys_match_2026_spec():
    """These are the three keys the 2026-07-28 RC lists under
    io.modelcontextprotocol/. If the final spec adds keys, extend
    KNOWN_META_KEYS in the same commit that flips a row from not_yet
    to compatible."""
    assert set(KNOWN_META_KEYS) == {
        "io.modelcontextprotocol/protocolVersion",
        "io.modelcontextprotocol/clientInfo",
        "io.modelcontextprotocol/clientCapabilities",
    }


# ---------------------------------------------------------------------------
# Audit doc exists and covers all rows
# ---------------------------------------------------------------------------


def test_audit_doc_exists():
    assert AUDIT_DOC.exists(), f"Audit doc missing: {AUDIT_DOC}"


def test_audit_doc_mentions_every_readiness_row():
    """If a row is in READINESS_STATE, the audit doc must mention it.
    Prevents doc drift from the source-of-truth constant."""
    body = AUDIT_DOC.read_text()
    for key in EXPECTED_KEYS:
        # Doc uses human-readable phrases; check for anchor text that maps
        # to each row in an obvious way.
        anchors = {
            "stateless_first": "Stateless-first",
            "_meta_field": "`_meta` field",
            "mcp_method_mcp_name_headers": "`Mcp-Method`",
            "input_required_result": "`InputRequiredResult`",
            "server_discover_method": "`server/discover`",
        }
        assert anchors[key] in body, f"Audit doc missing anchor for {key}: {anchors[key]!r}"


def test_audit_doc_mentions_target_spec_date():
    body = AUDIT_DOC.read_text()
    assert "2026-07-28" in body
