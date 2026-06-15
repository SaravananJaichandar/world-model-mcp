"""
v0.8.0 feature tests.

F1: domain-aware confidence decay with per-evidence-type TTL
F2: source_tool + confirmer provenance fields on facts
F3: slash command write operations (/world-model resolve, /world-model forget)
F4: resolve_contradiction accepts confirmer to stamp the winning fact

Plus regression coverage that v0.7.0 through v0.7.6 surface still works.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ============================================================================
# F1: Decay math (pure functions, no DB needed)
# ============================================================================

def test_f1_decay_returns_input_when_no_reference_ts():
    """compute_decayed_confidence with None reference returns input unchanged
    (fail-open behavior)."""
    from world_model_server.decay import compute_decayed_confidence
    assert compute_decayed_confidence(0.9, "source_code", None) == 0.9


def test_f1_decay_returns_zero_for_zero_confidence():
    from world_model_server.decay import compute_decayed_confidence
    assert compute_decayed_confidence(0.0, "source_code", "2026-01-01 00:00:00") == 0.0


def test_f1_decay_returns_zero_for_negative_confidence():
    from world_model_server.decay import compute_decayed_confidence
    assert compute_decayed_confidence(-0.5, "source_code", "2026-01-01 00:00:00") == 0.0


def test_f1_decay_half_life_at_one_period():
    """After exactly one TTL period of elapsed time, confidence should be
    half of the original."""
    from world_model_server.decay import compute_decayed_confidence
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    # source_code: 365 days. one year ago + 1.0 confidence => 0.5
    ref = (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
    decayed = compute_decayed_confidence(1.0, "source_code", ref, now=now)
    assert 0.49 < decayed < 0.51


def test_f1_decay_half_life_at_two_periods():
    """After two TTLs, confidence should be 1/4."""
    from world_model_server.decay import compute_decayed_confidence
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    # session: 14 days. 28 days ago + 1.0 confidence => 0.25
    ref = (now - timedelta(days=28)).strftime("%Y-%m-%d %H:%M:%S")
    decayed = compute_decayed_confidence(1.0, "session", ref, now=now)
    assert 0.24 < decayed < 0.26


def test_f1_decay_evidence_types_have_distinct_ttls():
    """A 90-day-old fact should decay differently based on evidence_type.
    user_correction (730d TTL) should still be near 1.0; session (14d TTL)
    should be near 0."""
    from world_model_server.decay import compute_decayed_confidence
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    ref = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")

    user_corr = compute_decayed_confidence(1.0, "user_correction", ref, now=now)
    session = compute_decayed_confidence(1.0, "session", ref, now=now)
    source = compute_decayed_confidence(1.0, "source_code", ref, now=now)

    # user_correction: 90 / 730 = 0.123 half-lives => ~0.918
    assert user_corr > 0.91
    # session: 90 / 14 = 6.43 half-lives => extremely small
    assert session < 0.02
    # source_code: 90 / 365 = 0.247 half-lives => ~0.84
    assert 0.83 < source < 0.85


def test_f1_decay_default_ttl_for_unknown_evidence_type():
    """An evidence_type not in EVIDENCE_TTL_DAYS falls back to DEFAULT_TTL_DAYS."""
    from world_model_server.decay import compute_decayed_confidence, DEFAULT_TTL_DAYS
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    ref = (now - timedelta(days=DEFAULT_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    decayed = compute_decayed_confidence(1.0, "fictional_type", ref, now=now)
    assert 0.49 < decayed < 0.51


def test_f1_decay_none_evidence_type_uses_default():
    from world_model_server.decay import compute_decayed_confidence, DEFAULT_TTL_DAYS
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    ref = (now - timedelta(days=DEFAULT_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    decayed = compute_decayed_confidence(1.0, None, ref, now=now)
    assert 0.49 < decayed < 0.51


def test_f1_decay_future_reference_is_no_op():
    """A reference timestamp in the future means clock skew or a test
    setup quirk. Return input unchanged."""
    from world_model_server.decay import compute_decayed_confidence
    now = datetime(2026, 6, 15, tzinfo=timezone.utc)
    future_ref = (now + timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    assert compute_decayed_confidence(0.9, "session", future_ref, now=now) == 0.9


def test_f1_decay_unparseable_reference_is_fail_open():
    from world_model_server.decay import compute_decayed_confidence
    assert compute_decayed_confidence(0.7, "test", "not-a-timestamp") == 0.7


def test_f1_decay_constant_ttls_match_spec():
    """Lock the TTL constants so accidental changes break tests, not
    production rows. Values per the agreed v0.8.0 plan."""
    from world_model_server.decay import EVIDENCE_TTL_DAYS
    assert EVIDENCE_TTL_DAYS["source_code"] == 365
    assert EVIDENCE_TTL_DAYS["test"] == 180
    assert EVIDENCE_TTL_DAYS["session"] == 14
    assert EVIDENCE_TTL_DAYS["user_correction"] == 730
    assert EVIDENCE_TTL_DAYS["bug_fix"] == 365


# ============================================================================
# F1: Auto-status transitions
# ============================================================================

def test_f1_should_auto_supersede_canonical_never():
    """Canonical facts are settled and never auto-transition under decay."""
    from world_model_server.decay import should_auto_supersede
    assert should_auto_supersede("canonical", 0.01, confirmer=None) is False
    assert should_auto_supersede("canonical", 0.001, confirmer=None) is False


def test_f1_should_auto_supersede_with_confirmer_never():
    """Any fact with confirmer set is settled."""
    from world_model_server.decay import should_auto_supersede
    assert should_auto_supersede("synthesized", 0.01, confirmer="user") is False
    assert should_auto_supersede("corroborated", 0.001, confirmer="test_runner") is False


def test_f1_should_auto_supersede_synthesized_above_threshold():
    from world_model_server.decay import should_auto_supersede, SYNTHESIZED_ROT_THRESHOLD
    assert should_auto_supersede("synthesized", SYNTHESIZED_ROT_THRESHOLD + 0.05, confirmer=None) is False


def test_f1_should_auto_supersede_synthesized_below_threshold():
    from world_model_server.decay import should_auto_supersede, SYNTHESIZED_ROT_THRESHOLD
    assert should_auto_supersede("synthesized", SYNTHESIZED_ROT_THRESHOLD - 0.01, confirmer=None) is True


def test_f1_should_auto_supersede_corroborated_uses_lower_threshold():
    from world_model_server.decay import (
        should_auto_supersede,
        SYNTHESIZED_ROT_THRESHOLD,
        CORROBORATED_ROT_THRESHOLD,
    )
    assert CORROBORATED_ROT_THRESHOLD < SYNTHESIZED_ROT_THRESHOLD
    # A confidence value between the two thresholds: synthesized would
    # supersede, corroborated would not.
    between = (CORROBORATED_ROT_THRESHOLD + SYNTHESIZED_ROT_THRESHOLD) / 2
    assert should_auto_supersede("synthesized", between, confirmer=None) is True
    assert should_auto_supersede("corroborated", between, confirmer=None) is False


def test_f1_apply_decay_to_row_does_not_mutate_input():
    """The row dict returned must be a new dict; the input is not mutated."""
    from world_model_server.decay import apply_decay_to_row
    row = {
        "confidence": 1.0,
        "evidence_type": "session",
        "created_at": "2026-01-01 00:00:00",
        "last_confirmed_at": None,
        "status": "synthesized",
        "confirmer": None,
    }
    snapshot = dict(row)
    result = apply_decay_to_row(row, now=datetime(2026, 6, 15, tzinfo=timezone.utc))
    assert row == snapshot
    assert result is not row


def test_f1_apply_decay_to_row_handles_missing_fields():
    """Rows from older schemas may not have all the new columns. Should
    not crash."""
    from world_model_server.decay import apply_decay_to_row
    result = apply_decay_to_row(
        {"confidence": 1.0, "status": "canonical", "created_at": "2026-01-01 00:00:00"},
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )
    assert "confidence" in result
    assert "status" in result


# ============================================================================
# F2: source_tool + confirmer field availability
# ============================================================================

def test_f2_fact_model_accepts_source_tool():
    from world_model_server.models import Fact
    fact = Fact(
        fact_text="test",
        evidence_path="x.py:1",
        source_tool="codex",
    )
    assert fact.source_tool == "codex"


def test_f2_fact_model_accepts_confirmer():
    from world_model_server.models import Fact
    fact = Fact(
        fact_text="test",
        evidence_path="x.py:1",
        confirmer="user",
    )
    assert fact.confirmer == "user"


def test_f2_fact_model_defaults_provenance_to_none():
    """Backward compat: existing code creating Fact without provenance
    fields must still work."""
    from world_model_server.models import Fact
    fact = Fact(fact_text="test", evidence_path="x.py:1")
    assert fact.source_tool is None
    assert fact.confirmer is None
    assert fact.last_decay_at is None


# ============================================================================
# F2: Schema migration (3 new columns, all NULL-defaulted)
# ============================================================================

@pytest.mark.asyncio
async def test_f2_migration_adds_provenance_columns_idempotently(tmp_path):
    """Apply migration twice. Second pass must be a no-op."""
    from world_model_server.knowledge_graph import KnowledgeGraph
    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()

    # The columns must exist after the first init
    conn = sqlite3.connect(str(tmp_path / "facts.db"))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(facts)").fetchall()}
    conn.close()
    for col in ("source_tool", "confirmer", "last_decay_at"):
        assert col in cols, f"Migration did not add {col}"

    # Re-initialize a second KG against the same dir; the migration must
    # not crash on already-present columns.
    kg2 = KnowledgeGraph(str(tmp_path))
    await kg2.initialize()


@pytest.mark.asyncio
async def test_f2_migration_existing_facts_get_null_provenance(tmp_path):
    """Existing rows from v0.7 schemas should not be modified; new fields
    should be NULL."""
    from world_model_server.knowledge_graph import KnowledgeGraph
    from world_model_server.models import Fact

    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()

    # Insert a fact (this will use the v0.8 INSERT but with NULL provenance)
    fact = Fact(
        fact_text="test fact",
        evidence_type="source_code",
        evidence_path="test.py:1-5",
        confidence=0.9,
        status="canonical",
    )
    await kg.create_fact(fact)

    conn = sqlite3.connect(str(tmp_path / "facts.db"))
    row = conn.execute(
        "SELECT source_tool, confirmer, last_decay_at FROM facts"
    ).fetchone()
    conn.close()
    assert row[0] is None  # source_tool
    assert row[1] is None  # confirmer
    assert row[2] is None  # last_decay_at


@pytest.mark.asyncio
async def test_f2_create_fact_persists_source_tool_and_confirmer(tmp_path):
    from world_model_server.knowledge_graph import KnowledgeGraph
    from world_model_server.models import Fact

    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()

    fact = Fact(
        fact_text="test fact",
        evidence_type="user_correction",
        evidence_path="conversation:1",
        confidence=0.95,
        status="canonical",
        source_tool="claude_code",
        confirmer="user",
    )
    await kg.create_fact(fact)

    conn = sqlite3.connect(str(tmp_path / "facts.db"))
    row = conn.execute(
        "SELECT source_tool, confirmer FROM facts"
    ).fetchone()
    conn.close()
    assert row[0] == "claude_code"
    assert row[1] == "user"


# ============================================================================
# F3: Slash command write operations
# ============================================================================

@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def _make_wm_dir(project: Path) -> Path:
    db_dir = project / ".claude" / "world-model"
    db_dir.mkdir(parents=True)
    return db_dir


def _make_facts_db(db_dir: Path, fact_rows, contradiction_rows=None) -> None:
    conn = sqlite3.connect(str(db_dir / "facts.db"))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            fact_text TEXT, confidence REAL, status TEXT,
            invalid_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    for row in fact_rows:
        conn.execute(
            "INSERT INTO facts (id, fact_text, confidence, status) VALUES (?, ?, ?, ?)",
            row,
        )
    if contradiction_rows is not None:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS contradictions (
                id TEXT PRIMARY KEY,
                fact_a TEXT, fact_b TEXT, status TEXT,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        for row in contradiction_rows:
            conn.execute(
                "INSERT INTO contradictions (id, fact_a, fact_b, status) "
                "VALUES (?, ?, ?, ?)",
                row,
            )
    conn.commit()
    conn.close()


def test_f3_slash_command_parses_argument():
    from world_model_server.slash_command import parse_argument
    assert parse_argument("/world-model resolve ct123") == "ct123"
    assert parse_argument("/world-model forget fact_abc") == "fact_abc"
    assert parse_argument("/world-model status") is None
    assert parse_argument("/world-model") is None


def test_f3_slash_command_recognizes_write_subcommands():
    from world_model_server.slash_command import parse_subcommand
    assert parse_subcommand("/world-model resolve ct1") == "resolve"
    assert parse_subcommand("/world-model forget f1") == "forget"


def test_f3_resolve_without_argument_returns_usage_hint(project):
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model resolve", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "Missing contradiction id" in body
    assert "Usage" in body


def test_f3_forget_without_argument_returns_usage_hint(project):
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model forget", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "Missing fact id" in body
    assert "Usage" in body


def test_f3_resolve_unknown_id_returns_not_found(project):
    db_dir = _make_wm_dir(project)
    _make_facts_db(
        db_dir,
        fact_rows=[],
        contradiction_rows=[
            ("ct1", "A", "B", "unresolved"),
        ],
    )
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model resolve ct_unknown", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "No contradiction found" in body
    assert "ct_unknown" in body


def test_f3_resolve_actually_updates_status(project):
    db_dir = _make_wm_dir(project)
    _make_facts_db(
        db_dir,
        fact_rows=[],
        contradiction_rows=[
            ("ct1", "Use uv", "Use pip", "unresolved"),
        ],
    )
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model resolve ct1", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "marked resolved" in body

    # Verify the DB state
    conn = sqlite3.connect(str(db_dir / "facts.db"))
    row = conn.execute(
        "SELECT status FROM contradictions WHERE id = ?", ("ct1",)
    ).fetchone()
    conn.close()
    assert row[0] == "resolved"


def test_f3_resolve_idempotent_on_already_resolved(project):
    db_dir = _make_wm_dir(project)
    _make_facts_db(
        db_dir,
        fact_rows=[],
        contradiction_rows=[
            ("ct1", "A", "B", "resolved"),
        ],
    )
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model resolve ct1", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "already resolved" in body


def test_f3_forget_unknown_id_returns_not_found(project):
    db_dir = _make_wm_dir(project)
    _make_facts_db(
        db_dir,
        fact_rows=[("f1", "fact A", 0.9, "canonical")],
    )
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model forget f_unknown", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "No fact found" in body
    assert "f_unknown" in body


def test_f3_forget_sets_invalid_at(project):
    db_dir = _make_wm_dir(project)
    _make_facts_db(
        db_dir,
        fact_rows=[("f1", "Use logger.debug", 0.9, "canonical")],
    )
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model forget f1", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "marked invalid" in body

    conn = sqlite3.connect(str(db_dir / "facts.db"))
    row = conn.execute(
        "SELECT invalid_at FROM facts WHERE id = ?", ("f1",)
    ).fetchone()
    conn.close()
    assert row[0] is not None
    # Row is not deleted
    conn = sqlite3.connect(str(db_dir / "facts.db"))
    count = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    conn.close()
    assert count == 1


def test_f3_forget_idempotent_on_already_invalid(project):
    db_dir = _make_wm_dir(project)
    conn = sqlite3.connect(str(db_dir / "facts.db"))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            fact_text TEXT, confidence REAL, status TEXT,
            invalid_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "INSERT INTO facts (id, fact_text, confidence, status, invalid_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("f1", "old fact", 0.5, "superseded", "2026-01-01"),
    )
    conn.commit()
    conn.close()

    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model forget f1", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "already invalidated" in body


def test_f3_help_lists_write_subcommands(project):
    from world_model_server.slash_command import handle_slash_command
    out = handle_slash_command("/world-model help", str(project))
    body = out["hookSpecificOutput"]["additionalContext"]
    assert "resolve" in body
    assert "forget" in body
    assert "Write operations" in body


# ============================================================================
# F4: resolve_contradiction accepts confirmer
# ============================================================================

@pytest.mark.asyncio
async def test_f4_resolve_contradiction_without_confirmer_no_op(tmp_path):
    """If confirmer is None (default), the winning fact's confirmer column
    stays NULL."""
    from world_model_server.knowledge_graph import KnowledgeGraph
    from world_model_server.models import Fact
    from world_model_server.contradictions import resolve

    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()

    f_a = Fact(
        fact_text="Use uv", evidence_type="source_code",
        evidence_path="pyproject.toml", confidence=0.9, status="canonical",
    )
    f_b = Fact(
        fact_text="Use pip", evidence_type="session",
        evidence_path="session:abc", confidence=0.6, status="canonical",
    )
    await kg.create_fact(f_a)
    await kg.create_fact(f_b)

    await resolve(kg, f_a.id, f_b.id, strategy="keep_higher_confidence")

    conn = sqlite3.connect(str(tmp_path / "facts.db"))
    row = conn.execute(
        "SELECT confirmer FROM facts WHERE id = ?", (f_a.id,)
    ).fetchone()
    conn.close()
    assert row[0] is None  # No confirmer when none passed


@pytest.mark.asyncio
async def test_f4_resolve_contradiction_with_confirmer_stamps_winner(tmp_path):
    """When confirmer is set, the winning fact gets its confirmer column
    set to that value."""
    from world_model_server.knowledge_graph import KnowledgeGraph
    from world_model_server.models import Fact
    from world_model_server.contradictions import resolve

    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()

    f_a = Fact(
        fact_text="Tests use pytest", evidence_type="source_code",
        evidence_path="pyproject.toml", confidence=0.9, status="canonical",
    )
    f_b = Fact(
        fact_text="Tests use unittest", evidence_type="session",
        evidence_path="session:xyz", confidence=0.6, status="canonical",
    )
    await kg.create_fact(f_a)
    await kg.create_fact(f_b)

    await resolve(
        kg, f_a.id, f_b.id,
        strategy="keep_higher_confidence", confirmer="user",
    )

    conn = sqlite3.connect(str(tmp_path / "facts.db"))
    row = conn.execute(
        "SELECT confirmer FROM facts WHERE id = ?", (f_a.id,)
    ).fetchone()
    conn.close()
    assert row[0] == "user"


# ============================================================================
# Backward-compat regression
# ============================================================================

def test_bc_version_is_080():
    from world_model_server import __version__
    parts = __version__.split(".")
    major, minor = int(parts[0]), int(parts[1])
    assert (major, minor) >= (0, 8)


def test_bc_all_v076_cli_subcommands_still_registered():
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    for cmd in (
        "setup", "seed", "query", "decisions", "register", "projects",
        "search-global", "health", "decay", "recall", "export-claude-md",
        "migrate", "status", "audit-compactions", "install-cursor",
        "install-pi", "install-codex", "demo", "telemetry",
        "status-watch",  # v0.7.6
    ):
        assert cmd in result.stdout, f"Missing CLI subcommand: {cmd}"


def test_bc_v076_read_subcommands_unchanged(project):
    """Read-only subcommands shipped in v0.7.6 must keep working unchanged."""
    from world_model_server.slash_command import handle_slash_command
    for cmd in ("status", "contradictions", "recent", "help"):
        out = handle_slash_command(f"/world-model {cmd}", str(project))
        assert out is not None
        assert "hookSpecificOutput" in out
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_bc_slash_command_unknown_falls_through_to_help(project):
    """Unknown subcommands still fall through to help, including names
    that collide with old write attempts."""
    from world_model_server.slash_command import parse_subcommand
    assert parse_subcommand("/world-model deletefact") == "help"


@pytest.mark.asyncio
async def test_bc_existing_v07_facts_decay_doesnt_break_query(tmp_path):
    """A facts.db that has the new columns but with NULL provenance on
    existing rows must still query successfully. The decay applies and
    the result is a valid Fact object."""
    from world_model_server.knowledge_graph import KnowledgeGraph
    from world_model_server.models import Fact

    kg = KnowledgeGraph(str(tmp_path))
    await kg.initialize()
    fact = Fact(
        fact_text="JWT auth required on /api/users",
        evidence_type="source_code",
        evidence_path="src/auth.ts:42",
        confidence=1.0,
        status="canonical",
    )
    await kg.create_fact(fact)

    result = await kg.query_facts("JWT")
    assert result.exists
    assert len(result.facts) >= 1
    # Canonical facts do not auto-supersede on decay
    assert result.facts[0].status == "canonical"
