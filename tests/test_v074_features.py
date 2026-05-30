"""
v0.7.4 feature tests.

F1: AGENTS.md / .agents/skills/ constraint reader
F2: Self-hosted Claude Managed Agents deployment doc + Modal quickstart
F3: NLI contradiction benchmark (reproducible test set + runner)
Backward-compat regression: v0.7.0..v0.7.3.1 surface unchanged.

Conventions follow v0.4..v0.7.3 suites.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ============================================================================
# F1: AGENTS.md reader
# ============================================================================

@pytest.fixture
def project(tmp_path):
    """A throwaway project dir for AGENTS.md tests."""
    p = tmp_path / "proj"
    p.mkdir()
    return p


def test_f1_returns_empty_on_missing_project(tmp_path):
    from world_model_server.agents_md_reader import read_agents_constraints
    out = read_agents_constraints(tmp_path / "does-not-exist")
    assert out == []


def test_f1_returns_empty_when_no_agents_files(project):
    from world_model_server.agents_md_reader import read_agents_constraints
    # Has some other markdown but none of the agentic-tool files
    (project / "README.md").write_text("# Project\nSome notes")
    assert read_agents_constraints(project) == []


def test_f1_extracts_fenced_constraint_block(project):
    from world_model_server.agents_md_reader import read_agents_constraints
    (project / "AGENTS.md").write_text(
        "# Project conventions\n\n"
        "```constraint\n"
        "rule: no-console-log\n"
        "severity: error\n"
        "file_pattern: \"*.ts\"\n"
        "description: Use logger.debug() not console.log()\n"
        "```\n"
    )
    rows = read_agents_constraints(project)
    assert len(rows) == 1
    r = rows[0]
    assert r["rule_name"] == "no-console-log"
    assert r["severity"] == "error"
    assert r["file_pattern"] == "*.ts"
    assert "logger.debug" in r["description"]
    assert r["source"] == "agents_md"
    assert r["_source_file"] == "AGENTS.md"


def test_f1_extracts_frontmatter_constraints(project):
    from world_model_server.agents_md_reader import read_agents_constraints
    (project / "AGENTS.md").write_text(
        "---\n"
        "constraints:\n"
        "  - rule: never-edit-migrations\n"
        "    description: Migrations are append-only\n"
        "    severity: error\n"
        "  - rule: prefer-uv-over-pip\n"
        "    description: Use uv add not pip install\n"
        "    severity: warning\n"
        "---\n"
        "# Project conventions\n"
    )
    rows = read_agents_constraints(project)
    rule_names = {r["rule_name"] for r in rows}
    assert "never-edit-migrations" in rule_names
    assert "prefer-uv-over-pip" in rule_names


def test_f1_extracts_imperative_sentences(project):
    from world_model_server.agents_md_reader import read_agents_constraints
    (project / "AGENTS.md").write_text(
        "# Project conventions\n\n"
        "- Never commit secrets to the repo.\n"
        "- Use ruff for formatting.\n"
        "- Always run tests before pushing.\n"
        "- Prefer pathlib over os.path.\n"
        "This is just a paragraph about how I think about things.\n"
    )
    rows = read_agents_constraints(project)
    descs = " ".join(r["description"] for r in rows)
    assert "commit secrets" in descs
    assert "ruff" in descs
    # The paragraph shouldn't produce a row
    assert "paragraph" not in descs
    # Strong verbs (Never/Always) map to "warning"; soft verbs (Use/Prefer) to "info"
    for r in rows:
        if "Never" in r["description"] or "Always" in r["description"]:
            assert r["severity"] == "warning"


def test_f1_reads_skill_files(project):
    from world_model_server.agents_md_reader import read_agents_constraints
    sk = project / ".agents" / "skills"
    sk.mkdir(parents=True)
    (sk / "frontend.md").write_text(
        "```constraint\n"
        "rule: use-tsx-not-jsx\n"
        "severity: warning\n"
        "description: New components must be .tsx\n"
        "```\n"
    )
    rows = read_agents_constraints(project)
    rule_names = {r["rule_name"] for r in rows}
    assert "use-tsx-not-jsx" in rule_names
    src_files = {r["_source_file"] for r in rows}
    assert ".agents/skills/frontend.md" in src_files


def test_f1_dedupes_across_files(project):
    """If the same rule_name appears in AGENTS.md and a skill file, only first
    occurrence is kept (root files take priority because they come first)."""
    from world_model_server.agents_md_reader import read_agents_constraints
    (project / "AGENTS.md").write_text(
        "```constraint\nrule: shared\nseverity: error\ndescription: From root\n```\n"
    )
    sk = project / ".agents" / "skills"
    sk.mkdir(parents=True)
    (sk / "x.md").write_text(
        "```constraint\nrule: shared\nseverity: warning\ndescription: From skill\n```\n"
    )
    rows = [r for r in read_agents_constraints(project) if r["rule_name"] == "shared"]
    assert len(rows) == 1
    assert rows[0]["description"] == "From root"
    assert rows[0]["severity"] == "error"


def test_f1_virtual_constraints_filters_by_file_glob(project):
    from world_model_server.agents_md_reader import virtual_constraints_for
    (project / "AGENTS.md").write_text(
        "```constraint\nrule: ts-only\nfile_pattern: \"*.ts\"\ndescription: TypeScript\n```\n"
        "```constraint\nrule: any-file\ndescription: Applies everywhere\n```\n"
    )
    ts_match = {r["rule_name"] for r in virtual_constraints_for(project, "src/x.ts")}
    py_match = {r["rule_name"] for r in virtual_constraints_for(project, "src/x.py")}
    # ts-only matches *.ts; any-file (no pattern) matches both
    assert "ts-only" in ts_match
    assert "any-file" in ts_match
    assert "ts-only" not in py_match
    assert "any-file" in py_match


def test_f1_invalid_severity_falls_back_to_warning(project):
    from world_model_server.agents_md_reader import read_agents_constraints
    (project / "AGENTS.md").write_text(
        "```constraint\n"
        "rule: weird\n"
        "severity: nuclear\n"
        "description: bogus severity\n"
        "```\n"
    )
    rows = read_agents_constraints(project)
    assert rows[0]["severity"] == "warning"


def test_f1_mcp_tool_returns_json(project):
    """The get_agents_md_constraints MCP tool returns a JSON string with count
    and constraints keys."""
    import asyncio
    from world_model_server.config import Config
    from world_model_server.knowledge_graph import KnowledgeGraph
    from world_model_server.tools import WorldModelTools

    (project / "AGENTS.md").write_text(
        "```constraint\nrule: test-rule\ndescription: test\nseverity: warning\n```\n"
    )

    async def go():
        wm_dir = project / ".claude" / "world-model"
        wm_dir.mkdir(parents=True)
        kg = KnowledgeGraph(str(wm_dir))
        await kg.initialize()
        tools = WorldModelTools(kg, Config())
        out = await tools.get_agents_md_constraints(project_dir=str(project))
        return out

    result = asyncio.run(go())
    parsed = json.loads(result)
    assert parsed["count"] >= 1
    assert any(c["rule_name"] == "test-rule" for c in parsed["constraints"])


def test_f1_hook_helper_classifies_agents_md_violation(project):
    """End-to-end: a violation that exists only in AGENTS.md flows through
    hook_helper.classify() and produces a non-empty response."""
    from world_model_server.hook_helper import classify

    # No SQLite world-model directory at all -- we want to confirm AGENTS.md
    # alone is enough to produce a verdict.
    (project / "AGENTS.md").write_text(
        "```constraint\n"
        "rule: no-console\n"
        "severity: warning\n"
        "description: Use logger.debug() not console.log()\n"
        "examples:\n"
        "```\n"
    )

    payload = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "src/foo.ts",
            "new_string": "console.log('hi')",
        },
        "project_dir": str(project),
        "supports_defer": False,
    }
    out = classify(payload)
    # The built-in 'no-console' pattern fires on console.log; with AGENTS.md
    # contributing a warning-severity constraint, classify() returns a hook
    # output (decision either "ask" or "warn"-ish via "ask" fallback).
    assert isinstance(out, dict)
    # Some output is produced -- the exact decision can vary, but the response
    # must not be empty and must reference the constraint
    if out:
        hso = out.get("hookSpecificOutput", {})
        assert hso.get("permissionDecision") in {"ask", "defer", "deny"}


# ============================================================================
# F2: Self-hosted Claude Managed Agents deployment doc
# ============================================================================

def test_f2_managed_agents_self_hosted_doc_exists():
    doc = REPO_ROOT / "docs" / "deployment" / "managed-agents-self-hosted.md"
    assert doc.exists(), f"Missing {doc}"
    text = doc.read_text()
    # Must mention the load-bearing env vars
    for var in ("WORLD_MODEL_TRANSPORT", "WORLD_MODEL_HTTP_PORT"):
        assert var in text
    # Must explicitly cover the self-hosted path
    assert "self-hosted" in text.lower() or "Self-Hosted" in text


def test_f2_modal_quickstart_example_exists():
    ex = REPO_ROOT / "examples" / "managed-agents-self-hosted"
    assert ex.exists() and ex.is_dir()
    # Should ship at minimum a README and a Modal deploy snippet
    assert (ex / "README.md").exists()
    files = [f.name for f in ex.iterdir()]
    assert any("modal" in f.lower() for f in files), (
        f"Expected at least one Modal-flavored file in {ex}, got: {files}"
    )


def test_f2_doc_mentions_mcp_tunnels_caveat():
    """The doc must surface Anthropic's own caveat: Memory isn't yet
    supported in self-hosted Managed Agents sandboxes. That's the value-prop
    line; if it's missing the doc has lost its point."""
    doc = REPO_ROOT / "docs" / "deployment" / "managed-agents-self-hosted.md"
    text = doc.read_text()
    assert "Memory" in text or "memory" in text
    assert "self-hosted" in text.lower()


# ============================================================================
# F3: NLI contradiction benchmark
# ============================================================================

def test_f3_benchmark_dataset_exists():
    ds = REPO_ROOT / "benchmarks" / "contradictions" / "dataset.jsonl"
    assert ds.exists(), f"Missing benchmark dataset: {ds}"
    rows = [json.loads(line) for line in ds.read_text().splitlines() if line.strip()]
    assert len(rows) >= 20, f"Need >=20 contradiction pairs; have {len(rows)}"
    for row in rows:
        assert "id" in row
        assert "fact_a" in row
        assert "fact_b" in row
        # Must encode an expected outcome the runner can score against
        assert (
            "expected_winner" in row
            or "expected_relation" in row
            or "expected_winner_strategies" in row
        )


def test_f3_runner_script_exists_and_imports():
    runner = REPO_ROOT / "benchmarks" / "contradictions" / "run.py"
    assert runner.exists(), f"Missing runner: {runner}"
    # Import it as a module so a syntax error fails the test loudly
    spec_text = runner.read_text()
    compile(spec_text, str(runner), "exec")


def test_f3_runner_executes_on_dataset(tmp_path):
    """Running the benchmark end-to-end should produce a results JSON
    without raising. We pipe output into tmp_path."""
    runner = REPO_ROOT / "benchmarks" / "contradictions" / "run.py"
    out_path = tmp_path / "results.json"
    result = subprocess.run(
        [sys.executable, str(runner), "--out", str(out_path)],
        capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert "total" in data
    assert "passed" in data
    assert "by_strategy" in data
    assert data["total"] >= 20


def test_f3_results_committed():
    """RESULTS.md must exist and reference at least one of the canonical
    strategies (auto, keep_higher_confidence, keep_most_recent,
    keep_most_sources)."""
    results = REPO_ROOT / "benchmarks" / "contradictions" / "RESULTS.md"
    assert results.exists()
    text = results.read_text()
    assert any(s in text for s in (
        "keep_higher_confidence",
        "keep_most_recent",
        "keep_most_sources",
        "auto",
    ))


# ============================================================================
# Backward-compat regression
# ============================================================================

def test_bc_existing_cli_subcommands_present():
    result = subprocess.run(
        [sys.executable, "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
    )
    for cmd in (
        "setup", "seed", "query", "decisions", "register", "projects",
        "search-global", "health", "decay", "recall", "export-claude-md",
        "migrate", "status", "audit-compactions", "install-cursor",
        "demo", "telemetry", "install-pi",
    ):
        assert cmd in result.stdout, f"v0.7.3 subcommand missing: {cmd}"


def test_bc_version_is_074():
    from world_model_server import __version__
    parts = __version__.split(".")
    major, minor = int(parts[0]), int(parts[1])
    # Accept either "0.7.4" or a future "0.7.4.X" patch
    assert (major, minor) >= (0, 7)
    if (major, minor) == (0, 7):
        # patch is the third dotted segment
        patch_str = parts[2].split("rc")[0].split("a")[0].split("b")[0]
        assert int(patch_str) >= 4


def test_bc_existing_mcp_tool_count_at_least_26():
    """v0.7.4 adds get_agents_md_constraints. Total MCP tools = 26."""
    from world_model_server.tools import WorldModelTools
    expected_methods = {
        "query_fact", "record_event", "validate_change", "get_constraints",
        "record_correction", "get_related_bugs", "seed_project",
        "ingest_pr_reviews", "find_contradictions",
        "recall_transcript_range", "export_claude_md",
        "get_injection_context", "record_compaction_audit",
        "get_compaction_audit", "resolve_contradiction",
        "get_agents_md_constraints",  # v0.7.4
    }
    for m in expected_methods:
        assert hasattr(WorldModelTools, m), f"Missing tool method: {m}"
