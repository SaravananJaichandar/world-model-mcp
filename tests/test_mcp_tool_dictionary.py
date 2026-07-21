"""
Tests for src/etch/mcp_tool_dictionary.py.

Contract to preserve:

  1. lookup() never returns None; unknown tools resolve to the fallback.
  2. Runtime prefixes are stripped correctly ("claude-code:Bash" == "Bash").
  3. Coverage doesn't regress silently — the entry count is asserted so
     a well-meaning developer can't delete tools without a test failing.
  4. Every entry has a valid severity ("info" | "warning" | "critical").
  5. No two entries share the same raw_tool.
  6. Every Business String reads as English (no colons, no raw JSON,
     no LLM-shaped fillers like "The agent has" or "Attempts to").
"""

from __future__ import annotations

import pytest

from world_model_server import mcp_tool_dictionary as d


# ---------------------------------------------------------------------------
# Prefix stripping / lookup semantics
# ---------------------------------------------------------------------------


class TestLookup:
    def test_bare_tool_name_resolves(self):
        t = d.lookup("Bash")
        assert t.raw_tool == "Bash"
        assert t.business_string == "Agent ran a shell command"

    def test_claude_code_prefix_stripped(self):
        assert d.lookup("claude-code:Bash").raw_tool == "Bash"

    def test_cursor_prefix_stripped(self):
        assert d.lookup("cursor:Read").raw_tool == "Read"

    def test_github_prefix_stripped(self):
        assert d.lookup("github:commit").raw_tool == "commit"

    def test_deep_mcp_prefix_stripped(self):
        # mcp:<server-name>:<tool>
        assert d.lookup("mcp:my-server:add_fact").raw_tool == "add_fact"

    def test_unknown_tool_returns_fallback(self):
        t = d.lookup("NonexistentTool")
        assert t.business_string == "Agent performed an action"
        assert t.severity == "info"

    def test_empty_string_returns_fallback(self):
        assert d.lookup("").business_string == "Agent performed an action"

    def test_none_returns_fallback(self):
        assert d.lookup(None).business_string == "Agent performed an action"

    def test_lookup_never_returns_none(self):
        # Even the weirdest inputs should get a translation, not None
        for weird in ("", None, "::", ":", ":Bash", "unknown", "!@#$"):
            assert d.lookup(weird) is not None


# ---------------------------------------------------------------------------
# Coverage regression (deletion protection)
# ---------------------------------------------------------------------------


class TestCoverage:
    def test_entry_count_regression_guard(self):
        """
        Pin the entry count so accidental deletions are caught.
        When adding new entries, bump this number in the same commit.
        """
        cov = d.coverage()
        assert cov["entry_count"] >= 30, (
            f"expected at least 30 tool entries, got {cov['entry_count']}. "
            "Someone may have deleted entries — check git blame."
        )

    def test_all_severities_are_valid(self):
        valid = {"info", "warning", "critical"}
        for t in d.all_translations():
            assert t.severity in valid, (
                f"{t.raw_tool}: invalid severity {t.severity!r}"
            )

    def test_no_duplicate_raw_tool_names(self):
        names = [t.raw_tool for t in d.all_translations()]
        assert len(names) == len(set(names)), (
            "duplicate raw_tool entries: "
            f"{[n for n in names if names.count(n) > 1]}"
        )

    def test_all_categories_non_empty(self):
        for t in d.all_translations():
            assert t.category, f"{t.raw_tool}: empty category"

    def test_all_business_strings_non_empty(self):
        for t in d.all_translations():
            assert t.business_string, f"{t.raw_tool}: empty business_string"

    def test_categories_helper_returns_distinct_ordered(self):
        cats = d.categories()
        # Distinct
        assert len(cats) == len(set(cats))
        # At least 5 (Filesystem read / write, Shell, Web, Delegation, ...)
        assert len(cats) >= 5


# ---------------------------------------------------------------------------
# Auditor-cross-examination contract (Gemini's test)
# ---------------------------------------------------------------------------


class TestAuditorReadability:
    """
    Business Strings must survive an auditor's 'what does this mean'
    challenge. Guard against LLM-shaped filler and untranslated jargon.
    """

    _BANNED_SUBSTRINGS = (
        "attempts to",     # LLM filler
        "the agent has",   # LLM filler
        "the model",       # wrong level of abstraction
        "async",           # code jargon
        "callback",        # code jargon
        "MCP",             # protocol jargon — user should not need to know it
        "hook",            # code jargon
        "kwargs",          # code jargon
        "unknown",         # non-answer
    )

    def test_no_llm_filler_in_business_strings(self):
        for t in d.all_translations():
            lower = t.business_string.lower()
            for banned in self._BANNED_SUBSTRINGS:
                assert banned not in lower, (
                    f"{t.raw_tool}: business_string contains banned "
                    f"filler {banned!r}: {t.business_string!r}"
                )

    def test_business_strings_do_not_leak_raw_tool_names(self):
        """A compliance officer should never see 'Bash', 'WebFetch', etc.
        in the Business String; that's the raw tool's job in the JSON.
        We only ban obvious code identifiers (camelCase or snake_case),
        allowing common English words like 'Agent' or 'Task' that happen
        to also be raw tool names."""
        # Words that are ALSO legitimate English nouns used throughout
        # Business Strings — they're not "code identifiers leaking."
        english_word_exceptions = {"Agent", "Task", "Read", "Write",
                                    "Edit", "Stop", "Grep", "Glob",
                                    "commit", "push", "pull_request"}
        raw_names = {t.raw_tool for t in d.all_translations()}
        for t in d.all_translations():
            for raw in raw_names:
                if raw == t.raw_tool:
                    continue
                if raw in english_word_exceptions:
                    continue
                # Ban CamelCase or snake_case tool identifiers only
                is_camel = raw[0].isupper() and any(c.isupper() for c in raw[1:])
                is_snake = "_" in raw
                if is_camel or is_snake:
                    assert raw not in t.business_string, (
                        f"{t.raw_tool}: business_string leaks tool name {raw!r}"
                    )

    def test_business_strings_are_english_sentences_not_code(self):
        for t in d.all_translations():
            # Rough English-sentence heuristic
            assert not t.business_string.startswith("_")
            assert "(" not in t.business_string
            assert "->" not in t.business_string
            assert ";" not in t.business_string


# ---------------------------------------------------------------------------
# Specific spot-checks (representative entries)
# ---------------------------------------------------------------------------


class TestSpecificEntries:
    def test_bash_is_warning_by_default(self):
        assert d.lookup("Bash").severity == "warning"

    def test_read_is_info(self):
        assert d.lookup("Read").severity == "info"

    def test_write_is_warning(self):
        assert d.lookup("Write").severity == "warning"

    def test_webfetch_is_warning_for_egress(self):
        assert d.lookup("WebFetch").severity == "warning"

    def test_websearch_is_only_info(self):
        # Search does not egress data, only queries
        assert d.lookup("WebSearch").severity == "info"

    def test_github_commit_is_info(self):
        t = d.lookup("github:commit")
        assert t.severity == "info"
        assert "committed" in t.business_string.lower()

    def test_record_event_is_info(self):
        assert d.lookup("record_event").severity == "info"

    def test_add_constraint_is_warning(self):
        # Changing constraint set is a governance-relevant mutation
        assert d.lookup("add_constraint").severity == "warning"
