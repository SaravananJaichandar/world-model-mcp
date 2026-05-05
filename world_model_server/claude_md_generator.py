"""
Generate CLAUDE.md from the knowledge graph.

Outputs a markdown document with the most-relevant project context:
top constraints by violation count, recent decisions, known bug regions,
co-edit patterns. Used by the export-claude-md CLI subcommand and the
export_claude_md MCP tool.
"""

import logging
from datetime import datetime
from typing import List

from .knowledge_graph import KnowledgeGraph

logger = logging.getLogger(__name__)


async def generate_claude_md(
    kg: KnowledgeGraph,
    max_constraints: int = 20,
    max_decisions: int = 10,
    max_bugs: int = 10,
    max_co_edits: int = 10,
) -> str:
    """Generate a CLAUDE.md markdown string from the KG."""

    sections: List[str] = []

    # Header
    sections.append("# CLAUDE.md")
    sections.append("")
    sections.append(f"_Auto-generated from world-model-mcp on {datetime.now().strftime('%Y-%m-%d %H:%M')}_")
    sections.append("")
    sections.append("This file captures learned project context from your Claude Code sessions.")
    sections.append("Treat constraints below as binding. Re-generate with `world-model export-claude-md`.")
    sections.append("")

    # Project Constraints
    sections.append("## Project Constraints")
    sections.append("")
    constraints = await kg.get_constraints()
    constraints_sorted = sorted(constraints, key=lambda c: c.violation_count, reverse=True)
    if not constraints_sorted:
        sections.append("_No constraints learned yet. Constraints accumulate as you correct Claude in sessions._")
    else:
        for c in constraints_sorted[:max_constraints]:
            severity_marker = {"error": "**[REQUIRED]**", "warning": "**[RECOMMENDED]**", "info": "**[NOTE]**"}.get(
                c.severity, "**[RULE]**"
            )
            sections.append(f"- {severity_marker} `{c.rule_name}` ({c.constraint_type})")
            sections.append(f"  - {c.description}")
            if c.file_pattern:
                sections.append(f"  - Applies to: `{c.file_pattern}`")
            if c.violation_count > 0:
                sections.append(f"  - Violated {c.violation_count} times")
            if c.examples:
                example = c.examples[0]
                if isinstance(example, dict) and example.get("incorrect") and example.get("correct"):
                    sections.append(f"  - Avoid: `{example['incorrect']}`")
                    sections.append(f"  - Prefer: `{example['correct']}`")
    sections.append("")

    # Recent Decisions
    sections.append("## Recent Decisions")
    sections.append("")
    decisions = await kg.get_decisions(limit=max_decisions)
    if not decisions:
        sections.append("_No decision traces recorded yet._")
    else:
        for d in decisions:
            ts = d.timestamp.strftime("%Y-%m-%d")
            sections.append(f"- **{ts}** [{d.decision_type}]")
            if d.file_path:
                sections.append(f"  - File: `{d.file_path}`")
            if d.reasoning:
                sections.append(f"  - Reasoning: {d.reasoning}")
    sections.append("")

    # Known Bug Regions
    sections.append("## Known Bug Regions")
    sections.append("")
    # Find facts where evidence_type='bug_fix'
    bugs = []
    try:
        bug_facts_result = await kg.query_facts("bug")
        bugs = [f for f in bug_facts_result.facts if f.evidence_type == "bug_fix"][:max_bugs]
    except Exception:
        pass

    if not bugs:
        sections.append("_No bug fixes tracked yet. Tag fixes with evidence_type='bug_fix' to populate this section._")
    else:
        for fact in bugs:
            sections.append(f"- {fact.fact_text}")
            sections.append(f"  - Source: `{fact.evidence_path}`")
    sections.append("")

    # Co-edit Patterns
    sections.append("## Co-edit Patterns")
    sections.append("")
    sections.append("_Files commonly edited together. When changing one, consider the others._")
    sections.append("")

    # Find files with co-edits by sampling top entities
    file_entities = await kg.find_entities(entity_type="file")
    seen_pairs = set()
    co_edit_lines = []
    for fe in file_entities[:30]:
        if not fe.file_path:
            continue
        co_edits = await kg.get_co_edited_files(fe.file_path, limit=3)
        for ce in co_edits:
            other = ce["file_path"]
            pair_key = tuple(sorted([fe.file_path, other]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            co_edit_lines.append(f"- `{fe.file_path}` <-> `{other}` (co-edited {ce['co_edit_count']}x)")
            if len(co_edit_lines) >= max_co_edits:
                break
        if len(co_edit_lines) >= max_co_edits:
            break

    if not co_edit_lines:
        sections.append("_No co-edit patterns yet. Patterns emerge after multiple sessions._")
    else:
        sections.extend(co_edit_lines)
    sections.append("")

    # Footer
    sections.append("---")
    sections.append("")
    sections.append("_To regenerate this file: `world-model export-claude-md --output CLAUDE.md`_")
    sections.append("")

    return "\n".join(sections)
