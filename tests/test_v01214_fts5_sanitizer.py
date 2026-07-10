"""
v0.12.14 — FTS5 sanitizer.

Found via TS SDK dogfooding on 2026-07-10: `query_fact` crashed with
`sqlite3.OperationalError: fts5: syntax error near "?"` for queries
containing FTS5 metacharacters. Also fixes the long-standing
"file paths with dots/slashes" workaround in tools.get_context_for_action.
"""

import pytest
import tempfile

from world_model_server.knowledge_graph import KnowledgeGraph, sanitize_fts5_query


class TestSanitizerUnit:
    def test_empty_query_returns_empty_phrase(self):
        assert sanitize_fts5_query("") == '""'
        assert sanitize_fts5_query("   ") == '""'

    def test_plain_words_pass_through_quoted(self):
        assert sanitize_fts5_query("linter TypeScript") == '"linter" "TypeScript"'

    def test_question_mark_stripped(self):
        result = sanitize_fts5_query("which linter do we use?")
        assert '?' not in result
        assert '"which" "linter" "do" "we" "use"' == result

    def test_file_path_with_slashes_and_dots(self):
        result = sanitize_fts5_query("src/example.ts")
        assert '/' in result or '.' in result  # slashes/dots aren't FTS5 metachars
        assert '?' not in result and '"' in result

    def test_all_fts5_metacharacters_stripped(self):
        result = sanitize_fts5_query('foo? bar* baz"qux (a+b) c-d e:f g^h')
        for meta in ['?', '*', '(', ')', '+', '-', ':', '^']:
            # Metachars removed from token content — they only appear as
            # literal chars stripped or replaced by whitespace before quoting.
            assert meta not in result.replace('"', '')

    def test_reserved_words_lowercased(self):
        # AND / OR / NOT / NEAR are FTS5 operators when uppercase and unquoted.
        # Quoting alone is enough to defang them, but we also lowercase for
        # visual clarity.
        result = sanitize_fts5_query("foo AND bar OR NOT baz")
        assert '"and"' in result
        assert '"or"' in result
        assert '"not"' in result


class TestSanitizerIntegration:
    @pytest.mark.asyncio
    async def test_query_with_question_mark_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            kg = KnowledgeGraph(tmp)
            await kg.initialize()
            # Before v0.12.14 this raised sqlite3.OperationalError.
            result = await kg.query_facts("which linter do we use?")
            assert result.exists is False
            assert result.facts == []

    @pytest.mark.asyncio
    async def test_query_with_file_path_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            kg = KnowledgeGraph(tmp)
            await kg.initialize()
            result = await kg.query_facts("src/example.ts")
            assert result.exists is False

    @pytest.mark.asyncio
    async def test_find_contradictions_with_metacharacter_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            kg = KnowledgeGraph(tmp)
            await kg.initialize()
            # Should not crash even with metacharacters in the query.
            result = await kg.find_contradictions(query="what changed?", limit=5)
            assert result == []
