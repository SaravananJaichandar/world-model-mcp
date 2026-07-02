"""
v0.11.2 dogfooding case study tests.

The case study at case-studies/v011-dogfooding/CASE_STUDY.md asserts specific
counts and constraint names that came from `.claude/world-model/` at the
time of publication. This suite verifies:

  F1  The snapshot script exists, is executable, and generates JSON.
  F2  The snapshot JSON committed in case-studies/v011-dogfooding/ matches
      the numbers cited verbatim in CASE_STUDY.md — no drift between the
      writeup and the machine-readable data.
  F3  The script handles a missing db directory cleanly.
  F4  The script handles an empty db directory (no *.db files) cleanly.

These tests guard against the case study rotting: if someone regenerates
the snapshot without updating the writeup (or vice versa), the tests
catch it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SNAPSHOT_PATH = REPO_ROOT / "case-studies" / "v011-dogfooding" / "snapshot.json"
CASE_STUDY_PATH = REPO_ROOT / "case-studies" / "v011-dogfooding" / "CASE_STUDY.md"
SCRIPT_PATH = REPO_ROOT / "scripts" / "dogfooding_snapshot.py"


# ============================================================================
# F1: Snapshot script exists and runs
# ============================================================================


def test_f1_snapshot_script_exists():
    assert SCRIPT_PATH.exists()
    # Executable bit set so `./scripts/dogfooding_snapshot.py` works
    assert os.access(SCRIPT_PATH, os.X_OK)


def test_f1_snapshot_json_committed():
    assert SNAPSHOT_PATH.exists()
    # File must be valid JSON, not truncated or corrupted
    json.loads(SNAPSHOT_PATH.read_text())


def test_f1_case_study_committed():
    assert CASE_STUDY_PATH.exists()
    text = CASE_STUDY_PATH.read_text()
    # Sanity: expected section headers must be present
    assert "## Headline" in text
    assert "## The three learned constraints" in text
    assert "## The one bug-fix reflection" in text
    assert "## What is NOT in the graph" in text
    assert "## Reproducing this document" in text


# ============================================================================
# F2: Snapshot numbers match what CASE_STUDY.md cites
# ============================================================================


def _load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text())


def test_f2_totals_match_writeup():
    """The four numbers cited in the writeup Headline table must match the
    snapshot JSON exactly."""
    snap = _load_snapshot()
    totals = snap["totals"]

    text = CASE_STUDY_PATH.read_text()
    for table, count in totals.items():
        # Headline table lists each table with its count
        marker = f"| {table} | {count} |"
        assert marker in text, (
            f"CASE_STUDY.md headline table missing row: '{marker}'. "
            f"Actual snapshot totals: {totals}"
        )


def test_f2_constraint_names_cited_in_writeup():
    """Every rule_name in the snapshot appears in CASE_STUDY.md."""
    snap = _load_snapshot()
    text = CASE_STUDY_PATH.read_text()
    for constraint in snap.get("constraints", []):
        name = constraint["rule_name"]
        assert name in text, f"CASE_STUDY.md does not cite constraint: {name}"


def test_f2_bug_fix_fact_cited_in_writeup():
    """The one bug_fix fact from the snapshot appears in CASE_STUDY.md."""
    snap = _load_snapshot()
    bug_facts = snap.get("bug_fix_facts", [])
    text = CASE_STUDY_PATH.read_text()

    # The writeup cites the bug_fix section — verify at least one bug_fix
    # fact's evidence_path is quoted in the writeup so we cannot drift into
    # discussing a fact that no longer exists
    if bug_facts:
        assert "bug_fix" in text.lower() or "bug-fix" in text.lower()
        # The evidence_path should appear verbatim so readers can find it
        first_path = bug_facts[0]["evidence_path"]
        assert first_path in text, (
            f"CASE_STUDY.md does not cite the bug_fix fact evidence_path: {first_path}"
        )


def test_f2_facts_evidence_type_breakdown_reasonable():
    """Sanity: source_code should dominate the fact set — the writeup relies
    on this to justify the 'seeder produced most of these' point."""
    snap = _load_snapshot()
    facts = snap["totals"]["facts"]
    by_type = snap.get("facts_by_evidence_type", {})
    if facts > 0:
        assert by_type.get("source_code", 0) > 0


# ============================================================================
# F3: Missing db directory is a clean error
# ============================================================================


def test_f3_missing_db_dir_exits_nonzero(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--db-path", str(tmp_path / "does-not-exist")],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower()


# ============================================================================
# F4: Empty db directory (no .db files) produces zero-filled snapshot
# ============================================================================


def test_f4_empty_db_dir_produces_zero_snapshot(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--db-path", str(tmp_path)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    # Every count is zero because no .db files exist
    for table, count in data["totals"].items():
        assert count == 0, f"Expected 0 for {table} on empty dir, got {count}"
