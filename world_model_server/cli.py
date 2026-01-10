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
    import subprocess

    project_dir = Path(args.project_dir).resolve()

    console.print("[bold]🌍 World Model MCP Setup[/bold]")
    console.print(f"Project: {project_dir}\n")

    # Run the bash install script
    script_dir = Path(__file__).parent.parent / "scripts"
    install_script = script_dir / "install.sh"

    if not install_script.exists():
        console.print(
            "[bold red]Error: install.sh not found[/bold red]", style="bold red"
        )
        console.print("Please ensure world-model-mcp is properly installed.")
        sys.exit(1)

    try:
        subprocess.run([str(install_script), str(project_dir)], check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]Setup failed: {e}[/bold red]")
        sys.exit(1)


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
