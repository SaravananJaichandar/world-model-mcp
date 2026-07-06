"""
v0.12.3: universal content-type routing consumers.

The v0.11.1 patch added a nullable content_type field on Fact + facts
table but did not wire any consumer. Worse, create_fact did not persist
the field — a value set on the Fact model was silently dropped by the
INSERT. This suite covers:

  W1 create_fact now persists content_type (plus the v0.12.2 governance
     fields, which had the same gap).
  R1 query_facts + tools.query_fact accept a content_type filter and
     return only rows with that type. NULL rows are excluded when the
     filter is set.
  I1 get_injection_context splits its bundle into a dedicated Rules
     section (drawn first) and a Recent facts section (fills remaining
     slots).
  I2 Procedures are never auto-injected. They only surface when
     query_fact is called with content_type='procedure'.
  I3 The rules/facts budget is respected: max_facts caps the combined
     total across both sections.
  B1 Legacy rows (content_type NULL) continue to appear in the Recent
     facts section — they are treated as 'fact' for auto-inject.
  S1 MCP query_fact tool schema and Hermes surfaced tool schema both
     expose the content_type param.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from world_model_server.config import Config
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.models import Fact
from world_model_server.tools import WorldModelTools


# ----------------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------------


@pytest.fixture
async def tools(tmp_path):
    """A tools instance backed by a fresh temp KG."""
    kg = KnowledgeGraph(str(tmp_path / "wm"))
    await kg.initialize()
    config = Config(db_path=str(tmp_path / "wm"))
    return WorldModelTools(kg, config), kg


async def _seed_typed_facts(kg):
    """Seed one fact of each content_type plus a NULL legacy row."""
    now = datetime.now()
    await kg.create_fact(Fact(
        id="rule-1",
        fact_text="Always await async database calls",
        evidence_path="rules/async.md",
        status="canonical",
        content_type="rule",
        valid_at=now,
    ))
    await kg.create_fact(Fact(
        id="fact-1",
        fact_text="Endpoint POST /users returns 201 on success",
        evidence_path="src/api/users.ts:42",
        status="canonical",
        content_type="fact",
        valid_at=now,
    ))
    await kg.create_fact(Fact(
        id="proc-1",
        fact_text="Deploy runbook: bump version, tag, push, gh release",
        evidence_path="docs/deploy.md",
        status="canonical",
        content_type="procedure",
        valid_at=now,
    ))
    await kg.create_fact(Fact(
        id="legacy-1",
        fact_text="Legacy row with no content_type",
        evidence_path="legacy/x.py",
        status="canonical",
        content_type=None,
        valid_at=now,
    ))


# ----------------------------------------------------------------------------
# W1: create_fact persists content_type + governance fields
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_w1_create_fact_persists_content_type(tools):
    """The load-bearing regression: prior to v0.12.3, create_fact silently
    dropped content_type. This must never happen again."""
    _, kg = tools
    await kg.create_fact(Fact(
        id="rule-persist",
        fact_text="A rule",
        evidence_path="p",
        content_type="rule",
    ))
    row = await kg.get_fact_by_id("rule-persist")
    assert row is not None
    assert row.get("content_type") == "rule"


@pytest.mark.asyncio
async def test_w1_create_fact_persists_influence_state(tools):
    _, kg = tools
    await kg.create_fact(Fact(
        id="obs-1",
        fact_text="Observed but not yet trusted",
        evidence_path="p",
        influence_state="observed",
    ))
    row = await kg.get_fact_by_id("obs-1")
    assert row.get("influence_state") == "observed"


@pytest.mark.asyncio
async def test_w1_create_fact_persists_expires_at(tools):
    _, kg = tools
    expiry = datetime(2027, 1, 1, 12, 0, 0)
    await kg.create_fact(Fact(
        id="exp-1",
        fact_text="Expires 2027",
        evidence_path="p",
        expires_at=expiry,
    ))
    row = await kg.get_fact_by_id("exp-1")
    # Stored as ISO string; compare on parse
    assert row.get("expires_at") is not None
    assert datetime.fromisoformat(row["expires_at"]) == expiry


@pytest.mark.asyncio
async def test_w1_create_fact_null_defaults_preserved(tools):
    """A fact without any of the new fields set writes NULL for all three."""
    _, kg = tools
    await kg.create_fact(Fact(
        id="plain-1",
        fact_text="Plain fact, no new fields",
        evidence_path="p",
    ))
    row = await kg.get_fact_by_id("plain-1")
    assert row.get("content_type") is None
    assert row.get("influence_state") is None
    assert row.get("expires_at") is None


# ----------------------------------------------------------------------------
# R1: content_type filter on query
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r1_query_facts_filters_by_content_type(tools):
    _, kg = tools
    await _seed_typed_facts(kg)

    rule_hits = await kg.query_facts(query="async", content_type="rule")
    assert len(rule_hits.facts) == 1
    assert rule_hits.facts[0].id == "rule-1"

    # A term that would match the fact but not the rule
    fact_hits = await kg.query_facts(query="Endpoint", content_type="fact")
    assert len(fact_hits.facts) == 1
    assert fact_hits.facts[0].id == "fact-1"


@pytest.mark.asyncio
async def test_r1_query_facts_content_type_excludes_null(tools):
    _, kg = tools
    await _seed_typed_facts(kg)
    # Search a term that matches the legacy NULL row; a typed filter must
    # exclude it (NULL cannot answer a typed query).
    hits = await kg.query_facts(query="Legacy", content_type="rule")
    assert len(hits.facts) == 0


@pytest.mark.asyncio
async def test_r1_query_facts_no_content_type_includes_everything(tools):
    _, kg = tools
    await _seed_typed_facts(kg)
    # With no content_type filter, a search that matches multiple types
    # must return all of them (backward-compat).
    hits = await kg.query_facts(query="Legacy OR async OR Endpoint OR Deploy")
    ids = {f.id for f in hits.facts}
    assert {"rule-1", "fact-1", "proc-1", "legacy-1"}.issubset(ids)


@pytest.mark.asyncio
async def test_r1_tools_query_fact_passes_content_type_through(tools):
    t, kg = tools
    await _seed_typed_facts(kg)
    result = await t.query_fact(query="Deploy", content_type="procedure")
    assert len(result.facts) == 1
    assert result.facts[0].id == "proc-1"


@pytest.mark.asyncio
async def test_r1_query_facts_hydrates_content_type_field(tools):
    _, kg = tools
    await _seed_typed_facts(kg)
    hits = await kg.query_facts(query="async")
    assert hits.facts
    assert hits.facts[0].content_type == "rule"


# ----------------------------------------------------------------------------
# I1: get_injection_context routes rules into a dedicated section
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i1_injection_context_contains_rules_section(tools):
    t, kg = tools
    await _seed_typed_facts(kg)
    payload = json.loads(await t.get_injection_context(event_type="PostCompact"))
    assert "## Rules (always active)" in payload["injection"]
    assert "Always await async database calls" in payload["injection"]
    assert payload["rules_count"] == 1


@pytest.mark.asyncio
async def test_i1_injection_context_reports_fact_and_rule_counts_separately(tools):
    t, kg = tools
    await _seed_typed_facts(kg)
    payload = json.loads(await t.get_injection_context(event_type="PostCompact"))
    assert payload["rules_count"] == 1
    # fact-1 and legacy-1 both count as fact-pool for auto-inject; proc-1 does not.
    assert payload["facts_count"] == 2


# ----------------------------------------------------------------------------
# I2: procedures are never auto-injected
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i2_procedure_excluded_from_injection(tools):
    t, kg = tools
    await _seed_typed_facts(kg)
    payload = json.loads(await t.get_injection_context(event_type="PostCompact"))
    assert "Deploy runbook" not in payload["injection"]


@pytest.mark.asyncio
async def test_i2_procedure_reachable_via_explicit_query(tools):
    t, kg = tools
    await _seed_typed_facts(kg)
    result = await t.query_fact(query="Deploy", content_type="procedure")
    assert len(result.facts) == 1
    assert result.facts[0].id == "proc-1"


# ----------------------------------------------------------------------------
# I3: rules-first budget
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_i3_rules_get_first_shot_at_max_facts_budget(tools):
    """When max_facts=1, and both a rule and a fact match, the rule is
    picked over the fact."""
    t, kg = tools
    await _seed_typed_facts(kg)
    payload = json.loads(await t.get_injection_context(
        event_type="PostCompact",
        max_facts=1,
    ))
    assert payload["rules_count"] == 1
    assert payload["facts_count"] == 0


@pytest.mark.asyncio
async def test_i3_combined_budget_respected(tools):
    """max_facts=2 with 1 rule + 2 fact-pool rows: 1 rule + 1 fact = 2 total."""
    t, kg = tools
    await _seed_typed_facts(kg)
    payload = json.loads(await t.get_injection_context(
        event_type="PostCompact",
        max_facts=2,
    ))
    assert payload["rules_count"] + payload["facts_count"] == 2
    assert payload["rules_count"] == 1


# ----------------------------------------------------------------------------
# B1: legacy NULL rows behave as 'fact' for injection routing
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b1_null_content_type_row_appears_in_facts_section(tools):
    t, kg = tools
    await _seed_typed_facts(kg)
    payload = json.loads(await t.get_injection_context(event_type="PostCompact"))
    assert "Legacy row with no content_type" in payload["injection"]


# ----------------------------------------------------------------------------
# S1: schema surfaces expose content_type
# ----------------------------------------------------------------------------


def test_s1_hermes_surfaced_query_fact_schema_exposes_content_type():
    from world_model_server.hermes_memory_provider import _surfaced_tool_schemas
    schemas = list(_surfaced_tool_schemas())
    qf = next(s for s in schemas if s["name"] == "query_fact")
    props = qf["inputSchema"]["properties"]
    assert "content_type" in props
    assert set(props["content_type"]["enum"]) == {"rule", "fact", "procedure"}


def test_s1_hermes_surfaced_query_fact_schema_still_requires_only_query():
    """Adding an optional field must not silently change the required list."""
    from world_model_server.hermes_memory_provider import _surfaced_tool_schemas
    schemas = list(_surfaced_tool_schemas())
    qf = next(s for s in schemas if s["name"] == "query_fact")
    assert qf["inputSchema"]["required"] == ["query"]
