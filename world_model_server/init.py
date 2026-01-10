"""
Initialize world model database for a project.
"""

import argparse
import asyncio
from pathlib import Path

from .knowledge_graph import KnowledgeGraph
from rich.console import Console

console = Console()


async def initialize_database(project_dir: str) -> None:
    """Initialize the world model database."""
    db_path = Path(project_dir) / ".claude" / "world-model"
    db_path.mkdir(parents=True, exist_ok=True)

    console.print(f"[bold green]Initializing world model database...[/bold green]")
    console.print(f"Location: {db_path}")

    kg = KnowledgeGraph(str(db_path))
    await kg.initialize()

    console.print("[bold green]✓ Database initialized successfully![/bold green]")
    console.print("\nDatabases created:")
    for db_file in db_path.glob("*.db"):
        size = db_file.stat().st_size
        console.print(f"  - {db_file.name} ({size} bytes)")


def main():
    parser = argparse.ArgumentParser(description="Initialize world model database")
    parser.add_argument(
        "--project-dir",
        type=str,
        default=".",
        help="Project directory (default: current directory)",
    )

    args = parser.parse_args()

    asyncio.run(initialize_database(args.project_dir))


if __name__ == "__main__":
    main()
