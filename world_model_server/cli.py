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
    from .knowledge_graph import KnowledgeGraph

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

    # Initialize database
    async def init_db():
        kg = KnowledgeGraph(str(world_model_dir))
        await kg.initialize()

    asyncio.run(init_db())
    console.print("Initialized knowledge graph databases")

    console.print("\nSetup complete.")
    console.print("Next steps:")
    console.print("  1. Restart Claude Code")
    console.print("  2. Run: world-model seed")

    # Auto-seed the knowledge graph from existing codebase
    console.print("\nSeeding knowledge graph from existing codebase...")
    seed_args = argparse.Namespace(project_dir=str(project_dir), force=False)
    seed_command(seed_args)


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

        # Search entities
        entities = await kg.find_entities(name=query)
        if entities:
            console.print(f"\n[bold]Entities matching '{query}':[/bold]")
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

    # Status command
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
