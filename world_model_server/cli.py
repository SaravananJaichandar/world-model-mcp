"""
Command-line interface for world model operations.
"""

import argparse
import os
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
        # $CLAUDE_PROJECT_DIR is expanded at shell time. It MUST be double-
        # quoted or the value gets split on whitespace when the project
        # path contains spaces (e.g. macOS ~/Documents/... or any repo
        # cloned under a folder with a space in the name). Before v0.11.0
        # this was unquoted; any user whose project path had a space
        # silently got zero hook captures. See v0.11.2 dogfooding case
        # study for the diagnosis trail.
        settings = {
            "hooks": {
                "PostToolUse": [{
                    "matcher": "Edit|Write|Bash|Read",
                    "hooks": [{
                        "type": "command",
                        "command": 'node "$CLAUDE_PROJECT_DIR/.claude/hooks/world-model-capture.js"',
                        "timeout": 10,
                    }],
                }],
                "PreToolUse": [{
                    "matcher": "Edit|Write",
                    "hooks": [{
                        "type": "command",
                        "command": 'node "$CLAUDE_PROJECT_DIR/.claude/hooks/world-model-validate.js"',
                        "timeout": 8,
                    }],
                }],
                "SessionStart": [{
                    "matcher": "*",
                    "hooks": [{
                        "type": "command",
                        "command": 'node "$CLAUDE_PROJECT_DIR/.claude/hooks/world-model-session.js" start',
                        "timeout": 5,
                    }],
                }],
                "SessionEnd": [{
                    "matcher": "*",
                    "hooks": [{
                        "type": "command",
                        "command": 'node "$CLAUDE_PROJECT_DIR/.claude/hooks/world-model-session.js" end',
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

    # v0.7.3: prompt for opt-in telemetry once
    _maybe_prompt_for_telemetry(args)

    console.print("\nSetup complete. Restart Claude Code to activate hooks.")
    console.print("Try [bold]world-model demo[/bold] for a guided tour of the primitives.")

    # Telemetry: setup completed (no-op if user opted out / never asked)
    try:
        from . import telemetry as _telemetry
        _telemetry.record("setup_completed", {"version_at_setup": True})
    except Exception:
        pass


def _maybe_prompt_for_telemetry(args) -> None:
    """Ask the user once whether to enable opt-in telemetry.

    Skipped if --no-prompt is set, if WORLD_MODEL_NO_PROMPT=1, or if consent
    has already been recorded (either way).
    """
    from . import telemetry as _telemetry

    if getattr(args, "no_prompt", False):
        return
    if os.getenv("WORLD_MODEL_NO_PROMPT"):
        return
    if _telemetry.consent_status() != "unset":
        return

    if not sys.stdin.isatty():
        # Non-interactive (CI, scripts). Don't make an assumption; leave unset
        # so the user can run `world-model telemetry --enable` later.
        return

    console.print(
        "\n[bold]Anonymous telemetry (opt-in)[/bold]"
    )
    console.print(
        "world-model-mcp can send anonymous usage events (no file paths, no\n"
        "code, no identifiers) to help shape what we ship next. You can\n"
        "inspect the exact payload at any time with `world-model telemetry --status`\n"
        "and disable it with `world-model telemetry --disable`.\n"
    )
    try:
        answer = input("Enable telemetry? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    enabled = answer in ("y", "yes")
    _telemetry.set_consent(enabled)
    console.print(
        f"[green]Telemetry enabled.[/green] Thanks." if enabled
        else "[yellow]Telemetry disabled.[/yellow] You can change this any time with `world-model telemetry --enable`."
    )


def telemetry_command(args):
    """Show / enable / disable opt-in telemetry."""
    from . import telemetry as _telemetry

    if args.enable:
        _telemetry.set_consent(True)
        console.print("[green]Telemetry enabled.[/green]")
        return
    if args.disable:
        _telemetry.set_consent(False)
        console.print("[yellow]Telemetry disabled.[/yellow]")
        return

    # Default: --status
    status = _telemetry.consent_status()
    console.print(f"[bold]Telemetry status:[/bold] {status}")
    console.print(f"Install ID:        {_telemetry.get_install_id()}")
    console.print(f"Repo:              {_telemetry._resolve_repo()}")
    console.print()
    console.print("Sample payload that would be sent for setup_completed:")
    import json as _json
    sample = _telemetry.preview_payload("setup_completed", {"version_at_setup": True})
    console.print(_json.dumps(sample, indent=2))


def demo_command(args):
    """Run a guided tour of world-model-mcp primitives on the current project.

    Initializes the KG (if needed), seeds reproducible demo data, then runs
    a sequence of queries and prints the actual JSON outputs so a new user
    can see the primitives working without writing any code.
    """
    import asyncio
    import json
    import shutil
    import subprocess

    from .config import Config
    from .knowledge_graph import KnowledgeGraph
    from .tools import WorldModelTools

    project_dir = Path(args.project_dir).resolve()
    world_model_dir = project_dir / ".claude" / "world-model"

    console.print("[bold]world-model-mcp guided demo[/bold]")
    console.print(f"Project: {project_dir}\n")

    # 1. Initialize the KG if needed
    if not world_model_dir.exists():
        console.print("Initializing world-model in this project...")
        world_model_dir.mkdir(parents=True, exist_ok=True)

    async def init_kg():
        kg = KnowledgeGraph(str(world_model_dir))
        await kg.initialize()
        return kg

    kg = asyncio.run(init_kg())
    console.print(f"[green]ok[/green] knowledge graph initialized at {world_model_dir}")

    # 2. Seed demo data via the same script users can re-run
    repo_root = Path(__file__).resolve().parent.parent
    seed_script = repo_root / "scripts" / "demo_seed.py"
    if seed_script.exists():
        console.print(f"\nRunning demo seed script (you can re-run this at any time):")
        console.print(f"  python {seed_script.relative_to(repo_root)} --reset --seed-after-reset")
        result = subprocess.run(
            [sys.executable, str(seed_script), "--reset", "--seed-after-reset", "--project-dir", str(project_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            console.print(f"[yellow]seed script failed:[/yellow] {result.stderr.strip()}")
        else:
            for line in result.stdout.strip().splitlines():
                console.print(f"  {line}")
    else:
        console.print("[yellow]scripts/demo_seed.py not found; skipping seed[/yellow]")
        console.print("(This is normal if you installed via pip and not a clone.)")

    # 3. Exercise each primitive
    async def exercise():
        kg = KnowledgeGraph(str(world_model_dir))
        await kg.initialize()
        tools = WorldModelTools(kg, Config.from_env())

        console.print("\n[bold]1. PreToolUse enforcement[/bold]")
        from .hook_helper import classify as _classify
        out = _classify({
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/example.ts", "new_string": "console.log('debug')"},
            "project_dir": str(project_dir),
            "supports_defer": True,
        })
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision", "(no constraints loaded yet)")
        console.print(f"  Proposed: console.log in *.ts -> [bold]{decision}[/bold]")

        console.print("\n[bold]2. Contradiction detection[/bold]")
        pairs = await kg.find_contradictions(query="HTTP transport listen port", limit=5)
        console.print(f"  {len(pairs)} contradicting pair(s) found")
        if pairs:
            p = pairs[0]
            console.print(f"  a: {p['fact_a_text']}")
            console.print(f"  b: {p['fact_b_text']}")
            console.print(f"  confidence: a={p['confidence_a']} b={p['confidence_b']}; "
                          f"sources: a={p['source_count_a']} b={p['source_count_b']}")

        console.print("\n[bold]3. PostCompact injection bundle[/bold]")
        inj_json = await tools.get_injection_context(event_type="PostCompact", max_constraints=5, max_facts=5)
        inj = json.loads(inj_json)
        console.print(f"  Would inject {inj['constraints_count']} constraints + {inj['facts_count']} facts.")

        console.print("\n[bold]4. Compaction audit log[/bold]")
        audit_json = await tools.get_compaction_audit(limit=3)
        audit = json.loads(audit_json)
        console.print(f"  {audit['count']} audit rows on file.")

    asyncio.run(exercise())

    console.print("\n[bold]Next:[/bold]")
    console.print("  - Restart Claude Code; hooks are wired and will start capturing corrections.")
    console.print("  - Run `world-model audit-compactions` after a session to see what was remembered.")
    console.print("  - Run `world-model health` for a memory health report.")

    # Telemetry: demo completed
    try:
        from . import telemetry as _telemetry
        _telemetry.record("demo_run", {"seeded": seed_script.exists()})
    except Exception:
        pass


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


def install_openclaw_command(args):
    """Register world-model-mcp as an MCP server in OpenClaw's config.

    Merges an entry into ~/.openclaw/openclaw.json under mcp.servers.world-model.
    Preserves all other keys in the config file. Defaults --python to
    sys.executable (absolute path) because OpenClaw's process spawn does
    NOT inherit the shell PATH; a bare `python3` command fails probe
    with "MCP error -32000: Connection closed". Verified against
    OpenClaw 2026.6.11 (e085fa1) on macOS on 2026-07-01.
    """
    import json

    config_path = Path(args.config_path).expanduser().resolve() if args.config_path else (
        Path.home() / ".openclaw" / "openclaw.json"
    )

    python_bin = args.python or sys.executable
    if not Path(python_bin).is_absolute():
        console.print(
            f"[red]--python must be an absolute path (got: {python_bin!r}).[/red]\n"
            "OpenClaw's process spawn does not inherit shell PATH, so a bare "
            "interpreter name will fail probe."
        )
        sys.exit(1)

    db_path = args.db_path or ".claude/world-model"

    server_entry = {
        "command": python_bin,
        "args": ["-m", "world_model_server.server"],
        "env": {
            "WORLD_MODEL_DB_PATH": db_path,
        },
    }

    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except json.JSONDecodeError as exc:
            console.print(
                f"[red]Failed to parse {config_path} as JSON: {exc}[/red]\n"
                "Fix the config file by hand or delete it and rerun."
            )
            sys.exit(1)
    else:
        existing = {}

    if not isinstance(existing, dict):
        console.print(f"[red]{config_path} did not parse as a JSON object; refusing to write.[/red]")
        sys.exit(1)

    mcp_block = existing.setdefault("mcp", {})
    servers = mcp_block.setdefault("servers", {})

    if "world-model" in servers and not args.force:
        console.print(
            f"[yellow]OpenClaw adapter already present at {config_path} "
            "(mcp.servers.world-model)[/yellow]\n"
            "Use --force to overwrite the existing entry."
        )
        return

    if args.dry_run:
        console.print(f"[bold]Would write to:[/bold] {config_path}")
        console.print("\n--- proposed mcp.servers.world-model entry ---\n")
        console.print(json.dumps(server_entry, indent=2))
        return

    servers["world-model"] = server_entry

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, indent=2) + "\n")

    console.print("[green]OpenClaw adapter installed[/green]")
    console.print(f"  Wrote mcp.servers.world-model to {config_path}")
    console.print(f"  command: {python_bin}")
    console.print(f"  WORLD_MODEL_DB_PATH: {db_path}")
    console.print(
        "\nNext: verify with:\n  openclaw mcp probe world-model\n"
        "You should see \"world-model: 27 tools\" (or however many the current "
        "world-model-mcp version exposes)."
    )


def install_hermes_provider_command(args):
    """Install the world-model MemoryProvider plugin into Hermes' plugin dir.

    Copies the bundled ``hermes_memory_provider`` package files into
    ``<hermes_home>/plugins/memory/world-model/``. The plugin then imports
    ``world_model_server`` from site-packages at Hermes runtime, so the
    fact graph, decay function, and contradiction-resolution logic are
    the same code as the v0.10 MCP adapter — no divergence between
    the two integration paths.
    """
    import shutil

    hermes_home = Path(args.hermes_home).expanduser().resolve() if args.hermes_home else (
        Path.home() / ".hermes"
    )
    target_dir = hermes_home / "plugins" / "memory" / "world-model"

    pkg_root = Path(__file__).parent
    src_dir = pkg_root / "hermes_memory_provider"
    src_files = ("__init__.py", "plugin.yaml", "README.md")

    for filename in src_files:
        src = src_dir / filename
        if not src.exists():
            console.print(f"[red]Missing bundled plugin file: {src}[/red]")
            sys.exit(1)

    if target_dir.exists() and not args.force:
        console.print(
            f"[yellow]Hermes MemoryProvider plugin already present at {target_dir}[/yellow]\n"
            "Use --force to overwrite the existing plugin directory."
        )
        return

    if args.dry_run:
        console.print(f"[bold]Would copy plugin files to:[/bold] {target_dir}")
        for filename in src_files:
            console.print(f"  - {filename}  ({(src_dir / filename).stat().st_size} bytes)")
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    for filename in src_files:
        shutil.copyfile(src_dir / filename, target_dir / filename)

    console.print("[green]Hermes MemoryProvider plugin installed[/green]")
    console.print(f"  Copied plugin files to {target_dir}")
    console.print(
        "\nNext: restart Hermes (or reload plugins from an active session)\n"
        "and verify with:\n  hermes plugin list\n"
        "The 'world-model' provider should appear under the memory plugins."
    )


def install_hermes_command(args):
    """Register world-model-mcp as an MCP server in Hermes Agent's config.

    Merges an entry into ~/.hermes/config.yaml under mcp_servers.world-model.
    Uses ruamel.yaml round-trip mode so existing comments, blank lines, and
    key ordering in the user's config are preserved — Hermes ships a heavily
    commented reference config and a naive YAML rewrite would strip 1000+
    lines of documentation. Defaults --python to sys.executable (absolute
    path) as a precaution against process-spawn PATH issues observed with
    sibling adapters. Requires the [hermes] optional extra so ruamel.yaml
    is available for the round-trip merge.
    """
    try:
        from ruamel.yaml import YAML
        from ruamel.yaml.error import YAMLError
        from ruamel.yaml.comments import CommentedMap
    except ImportError:
        console.print(
            "[red]install-hermes requires ruamel.yaml.[/red]\n"
            'Install with: pip install "world-model-mcp[hermes]"'
        )
        sys.exit(1)

    config_path = Path(args.config_path).expanduser().resolve() if args.config_path else (
        Path.home() / ".hermes" / "config.yaml"
    )

    python_bin = args.python or sys.executable
    if not Path(python_bin).is_absolute():
        console.print(
            f"[red]--python must be an absolute path (got: {python_bin!r}).[/red]\n"
            "Hermes' process spawn may not inherit shell PATH; a relative "
            "interpreter name is not safe."
        )
        sys.exit(1)

    db_path = args.db_path or ".claude/world-model"

    server_entry = {
        "command": python_bin,
        "args": ["-m", "world_model_server.server"],
        "env": {
            "WORLD_MODEL_DB_PATH": db_path,
        },
        "enabled": True,
        "timeout": 30,
    }

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)

    if config_path.exists():
        try:
            existing = yaml_rt.load(config_path.read_text())
        except YAMLError as exc:
            console.print(
                f"[red]Failed to parse {config_path} as YAML: {exc}[/red]\n"
                "Fix the config file by hand or delete it and rerun."
            )
            sys.exit(1)
        if existing is None:
            existing = CommentedMap()
    else:
        existing = CommentedMap()

    if not isinstance(existing, dict):
        console.print(f"[red]{config_path} did not parse as a YAML mapping; refusing to write.[/red]")
        sys.exit(1)

    if "mcp_servers" not in existing:
        existing["mcp_servers"] = CommentedMap()
    mcp_servers = existing["mcp_servers"]
    if not isinstance(mcp_servers, dict):
        console.print(
            f"[red]mcp_servers in {config_path} is not a YAML mapping; refusing to write.[/red]"
        )
        sys.exit(1)

    if "world-model" in mcp_servers and not args.force:
        console.print(
            f"[yellow]Hermes adapter already present at {config_path} "
            "(mcp_servers.world-model)[/yellow]\n"
            "Use --force to overwrite the existing entry."
        )
        return

    if args.dry_run:
        import io
        console.print(f"[bold]Would write to:[/bold] {config_path}")
        console.print("\n--- proposed mcp_servers.world-model entry ---\n")
        buf = io.StringIO()
        yaml_rt.dump({"world-model": server_entry}, buf)
        console.print(buf.getvalue())
        return

    mcp_servers["world-model"] = server_entry

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w") as f:
        yaml_rt.dump(existing, f)

    console.print("[green]Hermes adapter installed[/green]")
    console.print(f"  Wrote mcp_servers.world-model to {config_path}")
    console.print(f"  command: {python_bin}")
    console.print(f"  WORLD_MODEL_DB_PATH: {db_path}")
    console.print(
        "\nNext: from inside a Hermes session run:\n  /reload-mcp\n"
        "to load the new server without restarting Hermes."
    )


def install_continue_command(args):
    """Register world-model-mcp as an MCP server in Continue.

    Writes a standalone YAML file at
    <project-dir>/.continue/mcpServers/world-model.yaml, matching
    Continue's documented per-server-file pattern. No config merge is
    needed because Continue expects one YAML per MCP server in that
    directory — we own the whole file.

    Defaults --python to sys.executable (absolute path). Rejects relative
    --python overrides as a hard error, matching the OpenClaw and Hermes
    adapters. No ruamel.yaml dependency needed because we author the
    file end-to-end (no comment preservation problem to solve).
    """
    project_dir = Path(args.project_dir).resolve()
    mcp_servers_dir = project_dir / ".continue" / "mcpServers"
    target = mcp_servers_dir / "world-model.yaml"

    python_bin = args.python or sys.executable
    if not Path(python_bin).is_absolute():
        console.print(
            f"[red]--python must be an absolute path (got: {python_bin!r}).[/red]\n"
            "Continue may not inherit shell PATH when spawning stdio MCP "
            "servers; a relative interpreter name is not safe."
        )
        sys.exit(1)

    db_path = args.db_path or ".claude/world-model"

    yaml_body = (
        "name: world-model-mcp\n"
        "version: 0.1.0\n"
        "schema: v1\n"
        "mcpServers:\n"
        "  - name: world-model\n"
        "    type: stdio\n"
        f"    command: {python_bin}\n"
        "    args:\n"
        "      - -m\n"
        "      - world_model_server.server\n"
        "    env:\n"
        f"      WORLD_MODEL_DB_PATH: {db_path}\n"
    )

    if target.exists() and not args.force:
        console.print(
            f"[yellow]Continue adapter already present at {target}[/yellow]\n"
            "Use --force to overwrite the existing file."
        )
        return

    if args.dry_run:
        console.print(f"[bold]Would write to:[/bold] {target}")
        console.print("\n--- proposed world-model.yaml ---\n")
        console.print(yaml_body)
        return

    mcp_servers_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml_body)

    console.print("[green]Continue adapter installed[/green]")
    console.print(f"  Wrote {target}")
    console.print(f"  command: {python_bin}")
    console.print(f"  WORLD_MODEL_DB_PATH: {db_path}")
    console.print(
        "\nNext: reload the Continue extension (reopen the VS Code / JetBrains\n"
        "window, or reload the extension) so it picks up the new server.\n"
        "Then open agent mode and confirm world-model tools are visible."
    )


def install_codex_command(args):
    """Wire world-model-mcp into Codex CLI by appending to ~/.codex/config.toml.

    The bundled adapter files at world_model_server/adapters/codex/
    define an [mcp_servers.world_model] block plus PreToolUse,
    PostToolUse, PostCompact, and SessionStart hooks. This command
    appends both snippets to the user's Codex config in an idempotent
    way: if a marker indicating world-model-mcp has already been
    installed is present, the command refuses to write unless --force
    is passed.

    The Codex MCP server is registered with the underscore name
    `world_model` to avoid Codex's tool-name hyphen-strip on the model-
    visible tool surface (see adapters/codex/README.md).
    """
    config_path = Path(args.config_path).expanduser().resolve() if args.config_path else (
        Path.home() / ".codex" / "config.toml"
    )
    pkg_root = Path(__file__).parent
    adapter_src = pkg_root / "adapters" / "codex"

    config_file = adapter_src / "config.toml"
    hooks_file = adapter_src / "hooks_snippet.toml"

    for src in (config_file, hooks_file):
        if not src.exists():
            console.print(f"[red]Missing bundled adapter file: {src}[/red]")
            sys.exit(1)

    marker = "# world-model-mcp adapter for OpenAI Codex CLI"
    existing = config_path.read_text() if config_path.exists() else ""

    if marker in existing and not args.force:
        console.print(
            f"[yellow]Codex adapter already present in {config_path}[/yellow]\n"
            "Use --force to re-append (will produce duplicate sections; you "
            "may want to manually remove the old one first)."
        )
        return

    if args.dry_run:
        console.print(f"[bold]Would append to:[/bold] {config_path}")
        console.print("\n--- config.toml ---\n")
        console.print(config_file.read_text())
        console.print("\n--- hooks_snippet.toml ---\n")
        console.print(hooks_file.read_text())
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text("# Codex CLI configuration\n")

    with config_path.open("a") as f:
        f.write("\n")
        f.write(config_file.read_text())
        f.write("\n")
        f.write(hooks_file.read_text())
        f.write("\n")

    console.print("[green]Codex adapter installed[/green]")
    console.print(f"  Appended {config_file.name} + {hooks_file.name} to {config_path}")
    console.print(
        "\nNext: restart `codex` and verify with:\n  codex mcp list\n"
        "`world_model` should appear in the list."
    )


def install_pi_command(args):
    """Copy the pi adapter (index.ts, package.json) into ./adapters/world-model-pi/.

    For pi users with `pi install local:<path>` -- writes the adapter as a
    self-contained directory the user can install via pi.
    """
    import shutil

    target_dir = Path(args.target_dir).resolve() if args.target_dir else (
        Path(args.project_dir).resolve() / "adapters" / "world-model-pi"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    pkg_root = Path(__file__).parent
    adapter_src = pkg_root / "adapters" / "pi"

    copied = []
    for filename in ("index.ts", "package.json"):
        src = adapter_src / filename
        if not src.exists():
            console.print(f"[red]Missing bundled adapter file: {src}[/red]")
            sys.exit(1)
        dst = target_dir / filename
        if dst.exists() and not args.force:
            console.print(f"[yellow]skip[/yellow] {dst} (already exists; --force to overwrite)")
            continue
        shutil.copyfile(src, dst)
        copied.append(str(dst))

    if not copied:
        console.print("[yellow]Nothing copied. Use --force to overwrite existing files.[/yellow]")
        return

    console.print("[green]Pi adapter installed[/green]")
    for path in copied:
        console.print(f"  + {path}")
    console.print(
        f"\nNext: install as a pi package with:\n  pi install local:{target_dir}"
    )


def install_cursor_command(args):
    """Copy the Cursor adapter (mcp.json, hooks.json, hook wrappers) into .cursor/."""
    import shutil

    project_dir = Path(args.project_dir).resolve()
    cursor_dir = project_dir / ".cursor"
    cursor_hooks_dir = cursor_dir / "hooks"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    cursor_hooks_dir.mkdir(parents=True, exist_ok=True)

    # Source: bundled adapter files inside the installed package
    pkg_root = Path(__file__).parent
    adapter_src = pkg_root / "adapters" / "cursor"
    hook_js_src = pkg_root / "hooks"

    copied = []

    for filename in ("mcp.json", "hooks.json"):
        src = adapter_src / filename
        if not src.exists():
            console.print(f"[red]Missing adapter file: {src}[/red]")
            sys.exit(1)
        dst = cursor_dir / filename
        if dst.exists() and not args.force:
            console.print(f"[yellow]skip[/yellow] {dst} (already exists; --force to overwrite)")
            continue
        shutil.copyfile(src, dst)
        copied.append(str(dst.relative_to(project_dir)))

    # The PreToolUse path uses the validate hook; the inject hook does both
    # beforeSubmitPrompt and preCompact, so two JS files are enough.
    for filename in ("world-model-validate.js", "world-model-inject.js"):
        src = hook_js_src / filename
        if not src.exists():
            console.print(f"[yellow]warn[/yellow] missing hook wrapper: {src}; skipping")
            continue
        dst = cursor_hooks_dir / filename
        if dst.exists() and not args.force:
            console.print(f"[yellow]skip[/yellow] {dst} (already exists; --force to overwrite)")
            continue
        shutil.copyfile(src, dst)
        copied.append(str(dst.relative_to(project_dir)))

    if not copied:
        console.print("[yellow]Nothing copied. Use --force to overwrite existing files.[/yellow]")
        return

    console.print("[green]Cursor adapter installed[/green]")
    for path in copied:
        console.print(f"  + {path}")
    console.print(
        "\nNext: restart Cursor and accept the one-click MCP install prompt for 'world-model'."
    )


def audit_compactions_command(args):
    """List or export compaction audit entries."""
    import asyncio
    from .audit import export_jsonl, list_compactions
    from .knowledge_graph import KnowledgeGraph

    project_dir = Path(args.project_dir).resolve()
    db_path = str(project_dir / ".claude" / "world-model")

    async def run():
        kg = KnowledgeGraph(db_path)
        await kg.initialize()
        if args.export:
            out = Path(args.export).resolve()
            count = await export_jsonl(
                kg, out, session_id=args.session, limit=args.limit
            )
            console.print(f"Wrote {count} audit rows to {out}")
            return
        entries = await list_compactions(kg, session_id=args.session, limit=args.limit)
        if not entries:
            console.print("[yellow]No compaction audit rows found[/yellow]")
            return
        console.print(f"[bold]{len(entries)} compaction audit rows[/bold]")
        for e in entries:
            console.print(
                f"  {e.compacted_at.isoformat()}  session={e.session_id or '-'}  "
                f"pre={e.pre_compact_tokens}  post={e.post_compact_tokens}  "
                f"facts_injected={e.facts_injected}  constraints_injected={e.constraints_injected}  "
                f"event={e.injection_event or '-'}"
            )

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


def status_watch_command(args):
    """Run the v0.7.6 live TUI status widget.

    Reads the project's ``.claude/world-model/`` directory and shows a
    refreshing panel with constraint counts, contradiction counts, fact
    counts, and the last compaction time. Stops on Ctrl-C.
    """
    from .status_widget import run_watch
    run_watch(args.project_dir, interval=float(args.interval))


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
    setup_parser.add_argument(
        "--no-prompt", action="store_true",
        help="Skip the opt-in telemetry prompt (useful for CI / scripted setup)",
    )
    setup_parser.set_defaults(func=setup_command)

    # Demo command (v0.7.3)
    demo_parser = subparsers.add_parser(
        "demo", help="Guided tour: seed reproducible data and exercise each primitive",
    )
    demo_parser.add_argument("--project-dir", type=str, default=".")
    demo_parser.set_defaults(func=demo_command)

    # Telemetry command (v0.7.3)
    tel_parser = subparsers.add_parser(
        "telemetry", help="Show, enable, or disable opt-in anonymous telemetry",
    )
    tel_group = tel_parser.add_mutually_exclusive_group()
    tel_group.add_argument("--enable", action="store_true", help="Enable telemetry")
    tel_group.add_argument("--disable", action="store_true", help="Disable telemetry")
    tel_group.add_argument("--status", action="store_true", help="Show status + sample payload (default)")
    tel_parser.set_defaults(func=telemetry_command)

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

    cursor_parser = subparsers.add_parser(
        "install-cursor", help="Install the Cursor adapter into ./.cursor/"
    )
    cursor_parser.add_argument("--project-dir", type=str, default=".")
    cursor_parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    cursor_parser.set_defaults(func=install_cursor_command)

    pi_parser = subparsers.add_parser(
        "install-pi", help="Install the pi adapter to ./adapters/world-model-pi/"
    )
    pi_parser.add_argument("--project-dir", type=str, default=".")
    pi_parser.add_argument("--target-dir", type=str, default=None,
                           help="Explicit target directory (default: <project-dir>/adapters/world-model-pi)")
    pi_parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    pi_parser.set_defaults(func=install_pi_command)

    codex_parser = subparsers.add_parser(
        "install-codex",
        help="Wire world-model-mcp into Codex CLI by appending to ~/.codex/config.toml",
    )
    codex_parser.add_argument(
        "--config-path", type=str, default=None,
        help="Override the Codex config path (default: ~/.codex/config.toml)",
    )
    codex_parser.add_argument(
        "--force", action="store_true",
        help="Re-append even if the adapter marker is already present",
    )
    codex_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be appended without writing",
    )
    codex_parser.set_defaults(func=install_codex_command)

    openclaw_parser = subparsers.add_parser(
        "install-openclaw",
        help="Wire world-model-mcp into OpenClaw by merging into ~/.openclaw/openclaw.json",
    )
    openclaw_parser.add_argument(
        "--config-path", type=str, default=None,
        help="Override the OpenClaw config path (default: ~/.openclaw/openclaw.json)",
    )
    openclaw_parser.add_argument(
        "--python", type=str, default=None,
        help=(
            "Absolute path to the python3 that has world-model-mcp installed. "
            "Default: sys.executable (the interpreter running this CLI). "
            "OpenClaw's process spawn does not inherit shell PATH, so this MUST "
            "be an absolute path."
        ),
    )
    openclaw_parser.add_argument(
        "--db-path", type=str, default=None,
        help="WORLD_MODEL_DB_PATH env value for the MCP server (default: .claude/world-model)",
    )
    openclaw_parser.add_argument(
        "--force", action="store_true",
        help="Overwrite the mcp.servers.world-model entry if already present",
    )
    openclaw_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be written without modifying the config file",
    )
    openclaw_parser.set_defaults(func=install_openclaw_command)

    hermes_parser = subparsers.add_parser(
        "install-hermes",
        help="Wire world-model-mcp into Hermes Agent by merging into ~/.hermes/config.yaml",
    )
    hermes_parser.add_argument(
        "--config-path", type=str, default=None,
        help="Override the Hermes config path (default: ~/.hermes/config.yaml)",
    )
    hermes_parser.add_argument(
        "--python", type=str, default=None,
        help=(
            "Absolute path to the python3 that has world-model-mcp installed. "
            "Default: sys.executable (the interpreter running this CLI). "
            "Relative values are rejected."
        ),
    )
    hermes_parser.add_argument(
        "--db-path", type=str, default=None,
        help="WORLD_MODEL_DB_PATH env value for the MCP server (default: .claude/world-model)",
    )
    hermes_parser.add_argument(
        "--force", action="store_true",
        help="Overwrite the mcp_servers.world-model entry if already present",
    )
    hermes_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be written without modifying the config file",
    )
    hermes_parser.set_defaults(func=install_hermes_command)

    hermes_provider_parser = subparsers.add_parser(
        "install-hermes-provider",
        help=(
            "Install the world-model MemoryProvider plugin into "
            "~/.hermes/plugins/memory/world-model/"
        ),
    )
    hermes_provider_parser.add_argument(
        "--hermes-home", type=str, default=None,
        help="Override the Hermes state directory (default: ~/.hermes)",
    )
    hermes_provider_parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing world-model plugin directory",
    )
    hermes_provider_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be copied without touching disk",
    )
    hermes_provider_parser.set_defaults(func=install_hermes_provider_command)

    continue_parser = subparsers.add_parser(
        "install-continue",
        help="Wire world-model-mcp into Continue by writing .continue/mcpServers/world-model.yaml",
    )
    continue_parser.add_argument(
        "--project-dir", type=str, default=".",
        help="Project directory to install into (default: current directory)",
    )
    continue_parser.add_argument(
        "--python", type=str, default=None,
        help=(
            "Absolute path to the python3 that has world-model-mcp installed. "
            "Default: sys.executable (the interpreter running this CLI). "
            "Relative values are rejected."
        ),
    )
    continue_parser.add_argument(
        "--db-path", type=str, default=None,
        help="WORLD_MODEL_DB_PATH env value for the MCP server (default: .claude/world-model)",
    )
    continue_parser.add_argument(
        "--force", action="store_true",
        help="Overwrite the existing world-model.yaml file if present",
    )
    continue_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the YAML that would be written without touching disk",
    )
    continue_parser.set_defaults(func=install_continue_command)

    status_watch_parser = subparsers.add_parser(
        "status-watch",
        help="Live TUI status widget (constraints, contradictions, facts). v0.7.6.",
    )
    status_watch_parser.add_argument(
        "--project-dir", type=str, default=".",
        help="Project directory (default: current)",
    )
    status_watch_parser.add_argument(
        "--interval", type=float, default=5.0,
        help="Refresh interval in seconds (default: 5)",
    )
    status_watch_parser.set_defaults(func=status_watch_command)

    audit_parser = subparsers.add_parser(
        "audit-compactions", help="List or export compaction audit entries"
    )
    audit_parser.add_argument("--project-dir", type=str, default=".")
    audit_parser.add_argument("--session", type=str, default=None, help="Filter by session_id")
    audit_parser.add_argument("--limit", type=int, default=50)
    audit_parser.add_argument("--export", type=str, default=None, help="Path to write JSONL")
    audit_parser.set_defaults(func=audit_compactions_command)

    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
