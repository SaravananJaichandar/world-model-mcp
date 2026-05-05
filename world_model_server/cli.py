"""
Command-line interface for world model operations.
"""

import argparse
import sys
from pathlib import Path

from rich.console import Console

console = Console()


def setup_command(args):
    """Run the full setup process."""
    import asyncio
    import json
    import shutil
    from .knowledge_graph import KnowledgeGraph
    from .project_identity import get_or_create_project_id

    project_dir = Path(args.project_dir).resolve()

    console.print("[bold]World Model MCP Setup[/bold]")
    console.print(f"Project: {project_dir}\n")

    # Create directories
    claude_dir = project_dir / ".claude"
    world_model_dir = claude_dir / "world-model"
    hooks_dir = claude_dir / "hooks"

    world_model_dir.mkdir(parents=True, exist_ok=True)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    console.print("Created .claude/world-model/ and .claude/hooks/")

    # v0.6.0: Create or update project identity
    metadata = get_or_create_project_id(project_dir)
    console.print(f"Project ID: {metadata['project_id']}")

    # Copy bundled hooks
    bundled_hooks_dir = Path(__file__).parent / "hooks"
    if bundled_hooks_dir.exists():
        hooks_copied = 0
        for js_file in bundled_hooks_dir.glob("*.js"):
            shutil.copy2(js_file, hooks_dir / js_file.name)
            hooks_copied += 1
        console.print(f"Installed {hooks_copied} hook scripts")
    else:
        console.print("[yellow]Warning: bundled hooks not found, hooks will not fire[/yellow]")

    # Generate settings.json with correct hook paths
    settings_path = claude_dir / "settings.json"
    if not settings_path.exists():
        settings = {
            "hooks": {
                "PostToolUse": [{
                    "matcher": "Edit|Write|Bash|Read",
                    "hooks": [{
                        "type": "command",
                        "command": "node $CLAUDE_PROJECT_DIR/.claude/hooks/world-model-capture.js",
                        "timeout": 10,
                    }],
                }],
                "PreToolUse": [{
                    "matcher": "Edit|Write",
                    "hooks": [{
                        "type": "command",
                        "command": "node $CLAUDE_PROJECT_DIR/.claude/hooks/world-model-validate.js",
                        "timeout": 8,
                    }],
                }],
                "SessionStart": [{
                    "matcher": "*",
                    "hooks": [{
                        "type": "command",
                        "command": "node $CLAUDE_PROJECT_DIR/.claude/hooks/world-model-session.js start",
                        "timeout": 5,
                    }],
                }],
                "SessionEnd": [{
                    "matcher": "*",
                    "hooks": [{
                        "type": "command",
                        "command": "node $CLAUDE_PROJECT_DIR/.claude/hooks/world-model-session.js end",
                        "timeout": 10,
                    }],
                }],
            }
        }
        settings_path.write_text(json.dumps(settings, indent=2))
        console.print("Generated .claude/settings.json with hook configuration")
    else:
        console.print("Existing .claude/settings.json preserved")

    # Initialize database
    async def init_db():
        kg = KnowledgeGraph(str(world_model_dir))
        await kg.initialize()

    asyncio.run(init_db())
    console.print("Initialized knowledge graph databases")

    # Auto-seed the knowledge graph from existing codebase
    console.print("\nSeeding knowledge graph from existing codebase...")
    seed_args = argparse.Namespace(project_dir=str(project_dir), force=False)
    seed_command(seed_args)

    console.print("\nSetup complete. Restart Claude Code to activate hooks.")


def seed_command(args):
    """Seed the knowledge graph from existing codebase."""
    import asyncio
    from .seeder import ProjectSeeder
    from .knowledge_graph import KnowledgeGraph
    from .config import Config

    project_dir = Path(args.project_dir).resolve()
    config = Config.from_env()
    db_path = str(project_dir / ".claude" / "world-model")

    async def run():
        kg = KnowledgeGraph(db_path)
        await kg.initialize()
        seeder = ProjectSeeder(str(project_dir), kg, config)
        result = await seeder.seed(force=args.force)

        console.print(f"\n[bold]Seeding complete:[/bold]")
        console.print(f"  Files scanned: {result.files_scanned}")
        console.print(f"  Files seeded:  {result.files_seeded}")
        console.print(f"  Entities:      {result.entities_created}")
        console.print(f"  Relationships: {result.relationships_created}")
        console.print(f"  Skipped:       {result.skipped_files}")
        console.print(f"  Duration:      {result.duration_seconds}s")

        if result.errors:
            console.print(f"\n[yellow]Errors ({len(result.errors)}):[/yellow]")
            for err in result.errors[:5]:
                console.print(f"  {err}")

    asyncio.run(run())


def query_command(args):
    """Query the knowledge graph for entities and facts."""
    import asyncio
    from .knowledge_graph import KnowledgeGraph

    project_dir = Path(args.project_dir).resolve()
    db_path = str(project_dir / ".claude" / "world-model")

    async def run():
        kg = KnowledgeGraph(db_path)
        await kg.initialize()

        query = args.query

        # Search entities by name and file path
        entities = await kg.find_entities(name=query)
        fuzzy_used = False

        if not entities:
            # Fuzzy fallback
            entities = await kg.find_entities_fuzzy(name=query, threshold=0.6, limit=10)
            if entities:
                fuzzy_used = True

        if entities:
            label = "Fuzzy matches" if fuzzy_used else "Entities matching"
            console.print(f"\n[bold]{label} for '{query}':[/bold]")
            for e in entities:
                sig = f" ({e.signature})" if e.signature else ""
                console.print(f"  [{e.entity_type}] {e.name}{sig}")
                if e.file_path:
                    console.print(f"         {e.file_path}")
        else:
            console.print(f"\n[dim]No entities matching '{query}'[/dim]")

        # Search facts
        try:
            facts = await kg.query_facts(query)
            if facts.facts:
                console.print(f"\n[bold]Facts matching '{query}':[/bold]")
                for f in facts.facts:
                    console.print(f"  {f.fact_text}")
                    console.print(f"         evidence: {f.evidence_path} (confidence: {f.confidence})")
            else:
                console.print(f"[dim]No facts matching '{query}'[/dim]")
        except Exception:
            console.print(f"[dim]No facts matching '{query}'[/dim]")

        # Summary
        count = await kg.get_entity_count()
        console.print(f"\n[dim]Total entities in graph: {count}[/dim]")

    asyncio.run(run())


def decisions_command(args):
    """Browse decision traces."""
    import asyncio
    from .knowledge_graph import KnowledgeGraph

    project_dir = Path(args.project_dir).resolve()
    db_path = str(project_dir / ".claude" / "world-model")

    async def run():
        kg = KnowledgeGraph(db_path)
        await kg.initialize()

        decisions = await kg.get_decisions(
            session_id=args.session,
            file_path=args.file,
            decision_type=args.type,
            limit=args.limit,
        )

        if not decisions:
            console.print("[dim]No decisions recorded yet[/dim]")
            return

        console.print(f"[bold]Decision Log ({len(decisions)} entries):[/bold]\n")
        for d in decisions:
            console.print(f"  [{d.decision_type}] {d.timestamp.strftime('%Y-%m-%d %H:%M')}")
            if d.file_path:
                console.print(f"    File: {d.file_path}")
            if d.reasoning:
                console.print(f"    Reason: {d.reasoning}")
            if d.constraint_learned_id:
                console.print(f"    Constraint: {d.constraint_learned_id}")
            console.print()

    asyncio.run(run())


def register_command(args):
    """Register current project for cross-project search."""
    from .registry import ProjectRegistry
    from .project_identity import read_project_metadata

    project_dir = Path(args.project_dir).resolve()
    db_path = str(project_dir / ".claude" / "world-model")
    project_name = project_dir.name

    metadata = read_project_metadata(project_dir)
    project_id = metadata.get("project_id") if metadata else None

    ProjectRegistry.register(project_name, db_path, project_id=project_id)
    console.print(f"Registered project: {project_name} -> {db_path}")
    if project_id:
        console.print(f"  Project ID: {project_id}")


def migrate_command(args):
    """Detect and merge duplicate KGs across path variants for the same project_id."""
    import asyncio
    from .knowledge_graph import KnowledgeGraph
    from .registry import ProjectRegistry
    from .project_identity import read_project_metadata

    project_dir = Path(args.project_dir).resolve()
    metadata = read_project_metadata(project_dir)

    if not metadata:
        console.print("[yellow]No project identity found. Run: world-model setup[/yellow]")
        return

    project_id = metadata["project_id"]
    matches = ProjectRegistry.find_by_project_id(project_id)

    console.print(f"[bold]Migrating project_id={project_id}[/bold]")
    console.print(f"Found {len(matches)} registry entries with matching project_id\n")

    if len(matches) <= 1:
        console.print("[dim]Nothing to merge.[/dim]")
        return

    if args.dry_run:
        console.print("[yellow]DRY RUN - no changes will be made[/yellow]")
        for m in matches:
            console.print(f"  {m['name']}: {m['db_path']}")
        return

    # Pick the most recently modified DB as primary
    primary_path = matches[0]["db_path"]
    others = matches[1:]

    async def run():
        primary = KnowledgeGraph(primary_path)
        await primary.initialize()

        total = {"facts_merged": 0, "facts_skipped": 0, "constraints_merged": 0, "constraints_skipped": 0}
        for other_meta in others:
            other = KnowledgeGraph(other_meta["db_path"])
            await other.initialize()
            stats = await primary.merge_from(other)
            console.print(f"  Merged from {other_meta['name']}: {stats}")
            for k, v in stats.items():
                total[k] = total.get(k, 0) + v

        console.print(f"\n[bold]Total: {total}[/bold]")

    asyncio.run(run())


def projects_command(args):
    """List registered projects."""
    from .registry import ProjectRegistry

    projects = ProjectRegistry.list_projects()
    if not projects:
        console.print("[dim]No projects registered. Run: world-model register[/dim]")
        return

    console.print(f"[bold]Registered Projects ({len(projects)}):[/bold]")
    for p in projects:
        console.print(f"  {p['name']} -> {p['db_path']}")


def search_global_command(args):
    """Search across all registered projects."""
    import asyncio
    from .registry import search_global

    async def run():
        results = await search_global(args.query, limit=args.limit)
        if not results:
            console.print(f"[dim]No results for '{args.query}' across registered projects[/dim]")
            return

        console.print(f"[bold]Global search for '{args.query}' ({len(results)} results):[/bold]")
        for r in results:
            console.print(f"  [{r['entity_type']}] {r['name']} ({r['project']})")
            if r.get("file_path"):
                console.print(f"         {r['file_path']}")

    asyncio.run(run())


def health_command(args):
    """Print memory health report."""
    import asyncio
    import json
    from .knowledge_graph import KnowledgeGraph
    from .health import build_health_report

    project_dir = Path(args.project_dir).resolve()
    db_path = str(project_dir / ".claude" / "world-model")

    async def run():
        kg = KnowledgeGraph(db_path)
        await kg.initialize()
        report = await build_health_report(kg)

        if args.json:
            print(report.model_dump_json(indent=2))
            return

        console.print("[bold]Memory Health Report[/bold]\n")
        s = report.summary
        console.print(f"  Orphan entities: {s['orphan_count']}")
        console.print(f"  Stale facts: {s['stale_fact_count']}")
        console.print(f"  Contradictions: {s['contradiction_count']}")
        console.print(f"  Constraint decay candidates: {s['decay_candidate_count']}")
        console.print(f"  Total DB size: {s['total_db_bytes']:,} bytes\n")

        if report.orphaned_entities:
            console.print("[bold]Orphan entities (top 5):[/bold]")
            for e in report.orphaned_entities[:5]:
                console.print(f"  [{e['entity_type']}] {e['name']} ({e['file_path']})")
        if report.stale_facts:
            console.print("\n[bold]Stale facts (top 5):[/bold]")
            for f in report.stale_facts[:5]:
                console.print(f"  {f['fact_text'][:100]}")
        if report.conflicting_facts:
            console.print("\n[bold]Contradictions (top 5):[/bold]")
            for c in report.conflicting_facts[:5]:
                console.print(f"  similarity={c['similarity_score']}: {c['reason']}")
        if report.constraint_decay_candidates:
            console.print("\n[bold]Constraint decay candidates:[/bold]")
            for c in report.constraint_decay_candidates[:5]:
                console.print(f"  {c['rule_name']} (violations: {c['violation_count']})")

    asyncio.run(run())


def decay_command(args):
    """Apply fact TTL decay - mark unobserved facts as invalid."""
    import asyncio
    from .knowledge_graph import KnowledgeGraph

    project_dir = Path(args.project_dir).resolve()
    db_path = str(project_dir / ".claude" / "world-model")

    if not args.yes:
        console.print(f"This will mark facts older than {args.days} days as invalid (no re-observation).")
        answer = input("Continue? [y/N]: ")
        if answer.lower() != "y":
            console.print("Aborted.")
            return

    async def run():
        kg = KnowledgeGraph(db_path)
        await kg.initialize()
        count = await kg.apply_fact_decay(days=args.days)
        console.print(f"Marked {count} facts as invalid (no re-observation in {args.days} days)")

    asyncio.run(run())


def recall_command(args):
    """Recall a session transcript by ID."""
    import json
    from .transcript import read_range

    line_start = args.line_start
    line_end = args.line_end

    if args.lines:
        # Format: START:END
        try:
            parts = args.lines.split(":")
            line_start = int(parts[0]) if parts[0] else None
            line_end = int(parts[1]) if len(parts) > 1 and parts[1] else None
        except (ValueError, IndexError):
            console.print(f"[red]Invalid --lines format. Use START:END[/red]")
            return

    result = read_range(args.session_id, line_start=line_start, line_end=line_end)

    if "error" in result:
        console.print(f"[red]{result['error']}[/red]")
        return

    if args.json:
        print(json.dumps(result, indent=2))
        return

    console.print(f"[bold]Session: {result['session_id']}[/bold]")
    console.print(f"[dim]Path: {result['path']}[/dim]")
    console.print(f"[dim]Total lines: {result['total_lines']}[/dim]\n")

    for entry in result["lines"]:
        line_num = entry.get("_line", "?")
        if "_error" in entry:
            console.print(f"[dim]{line_num}:[/dim] [red]{entry.get('_raw', '')[:100]}[/red]")
        else:
            entry_type = entry.get("type", "?")
            console.print(f"[dim]{line_num}:[/dim] [{entry_type}]")


def export_claude_md_command(args):
    """Export a CLAUDE.md from the knowledge graph."""
    import asyncio
    from .knowledge_graph import KnowledgeGraph
    from .claude_md_generator import generate_claude_md

    project_dir = Path(args.project_dir).resolve()
    db_path = str(project_dir / ".claude" / "world-model")

    async def run():
        kg = KnowledgeGraph(db_path)
        await kg.initialize()
        md = await generate_claude_md(kg, max_constraints=args.max_constraints)

        if args.output and args.output != "-":
            output_path = Path(args.output)
            output_path.write_text(md)
            console.print(f"Wrote {len(md)} chars to {output_path}")
        else:
            print(md)

    asyncio.run(run())


def status_command(args):
    """Show world model status for a project."""
    project_dir = Path(args.project_dir).resolve()
    world_model_dir = project_dir / ".claude" / "world-model"

    console.print("[bold]World Model Status[/bold]")
    console.print(f"Project: {project_dir}\n")

    if not world_model_dir.exists():
        console.print("[yellow]World model not initialized[/yellow]")
        console.print("\nRun: world-model setup")
        return

    # Check databases
    console.print("[bold]Databases:[/bold]")
    for db_file in world_model_dir.glob("*.db"):
        size = db_file.stat().st_size / 1024  # KB
        console.print(f"  ✓ {db_file.name} ({size:.1f} KB)")

    # Check hooks
    hooks_dir = project_dir / ".claude" / "hooks"
    if hooks_dir.exists():
        console.print("\n[bold]Hooks:[/bold]")
        console.print(f"  ✓ Hooks directory exists")
    else:
        console.print("\n[yellow]⚠️  Hooks not found[/yellow]")

    # Check MCP config
    mcp_config = project_dir / ".mcp.json"
    if mcp_config.exists():
        console.print("\n[bold]MCP Configuration:[/bold]")
        console.print(f"  ✓ .mcp.json configured")
    else:
        console.print("\n[yellow]⚠️  .mcp.json not found[/yellow]")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="World Model MCP - Knowledge graph for codebases"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Setup command
    setup_parser = subparsers.add_parser("setup", help="Set up world model in a project")
    setup_parser.add_argument(
        "--project-dir", type=str, default=".", help="Project directory"
    )
    setup_parser.set_defaults(func=setup_command)

    # Seed command
    seed_parser = subparsers.add_parser("seed", help="Seed knowledge graph from codebase")
    seed_parser.add_argument(
        "--project-dir", type=str, default=".", help="Project directory"
    )
    seed_parser.add_argument(
        "--force", action="store_true", help="Re-seed already processed files"
    )
    seed_parser.set_defaults(func=seed_command)

    # Query command
    query_parser = subparsers.add_parser("query", help="Query the knowledge graph")
    query_parser.add_argument("query", type=str, help="Search term (entity name, function, class)")
    query_parser.add_argument(
        "--project-dir", type=str, default=".", help="Project directory"
    )
    query_parser.set_defaults(func=query_command)

    # Decisions command
    decisions_parser = subparsers.add_parser("decisions", help="Browse decision traces")
    decisions_parser.add_argument("--project-dir", type=str, default=".")
    decisions_parser.add_argument("--session", type=str, help="Filter by session ID")
    decisions_parser.add_argument("--file", type=str, help="Filter by file path")
    decisions_parser.add_argument("--type", type=str, choices=["correction", "approval", "rejection"])
    decisions_parser.add_argument("--limit", type=int, default=20)
    decisions_parser.set_defaults(func=decisions_command)

    # Register command
    register_parser = subparsers.add_parser("register", help="Register project for cross-project search")
    register_parser.add_argument("--project-dir", type=str, default=".")
    register_parser.set_defaults(func=register_command)

    # Projects command
    projects_parser = subparsers.add_parser("projects", help="List registered projects")
    projects_parser.set_defaults(func=projects_command)

    # Search global command
    search_global_parser = subparsers.add_parser("search-global", help="Search across all projects")
    search_global_parser.add_argument("query", type=str, help="Search term")
    search_global_parser.add_argument("--limit", type=int, default=20)
    search_global_parser.set_defaults(func=search_global_command)

    # Health command
    health_parser = subparsers.add_parser("health", help="Print memory health report")
    health_parser.add_argument("--project-dir", type=str, default=".")
    health_parser.add_argument("--json", action="store_true", help="Output JSON")
    health_parser.set_defaults(func=health_command)

    # Decay command
    decay_parser = subparsers.add_parser("decay", help="Apply fact TTL decay")
    decay_parser.add_argument("--project-dir", type=str, default=".")
    decay_parser.add_argument("--days", type=int, default=90)
    decay_parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    decay_parser.set_defaults(func=decay_command)

    # Status command
    # Recall command (v0.6.0)
    recall_parser = subparsers.add_parser("recall", help="Recall a Claude Code session transcript")
    recall_parser.add_argument("session_id", type=str, help="Session UUID")
    recall_parser.add_argument("--lines", type=str, help="Line range as START:END")
    recall_parser.add_argument("--line-start", type=int, help="Start line (1-indexed)")
    recall_parser.add_argument("--line-end", type=int, help="End line (inclusive)")
    recall_parser.add_argument("--json", action="store_true", help="Output JSON")
    recall_parser.set_defaults(func=recall_command)

    # Export CLAUDE.md command (v0.6.0)
    export_md_parser = subparsers.add_parser("export-claude-md", help="Export a CLAUDE.md from the knowledge graph")
    export_md_parser.add_argument("--project-dir", type=str, default=".")
    export_md_parser.add_argument("--output", type=str, default="-", help="Output path (- for stdout)")
    export_md_parser.add_argument("--max-constraints", type=int, default=20)
    export_md_parser.set_defaults(func=export_claude_md_command)

    # Migrate command (v0.6.0)
    migrate_parser = subparsers.add_parser("migrate", help="Merge KGs across path variants for same project_id")
    migrate_parser.add_argument("--project-dir", type=str, default=".")
    migrate_parser.add_argument("--dry-run", action="store_true", help="Show what would be merged without changes")
    migrate_parser.set_defaults(func=migrate_command)

    status_parser = subparsers.add_parser("status", help="Show world model status")
    status_parser.add_argument(
        "--project-dir", type=str, default=".", help="Project directory"
    )
    status_parser.set_defaults(func=status_command)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
