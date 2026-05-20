"""
World Model MCP Server

Main entry point for the MCP server that exposes tools for querying
and updating the knowledge graph.
"""

import asyncio
import os
import sys
from typing import Any, Dict
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .knowledge_graph import KnowledgeGraph
from .tools import WorldModelTools
from .config import Config
from .ingest import ingest_queued_events, ingest_session_files

# Set up logging
logging.basicConfig(
    level=logging.DEBUG if os.getenv("WORLD_MODEL_DEBUG") else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Main entry point for the MCP server."""
    # Load configuration
    config = Config.from_env()
    logger.info(f"Starting World Model MCP Server v{config.version}")
    logger.info(f"Database path: {config.db_path}")

    # Initialize knowledge graph
    kg = KnowledgeGraph(config.db_path)
    await kg.initialize()
    logger.info("Knowledge graph initialized")

    # Ingest any queued events/sessions from hooks
    events_ingested = await ingest_queued_events(kg, config.db_path)
    sessions_ingested = await ingest_session_files(kg, config.db_path)
    if events_ingested or sessions_ingested:
        logger.info(f"Ingested {events_ingested} queued events, {sessions_ingested} sessions from hooks")

    # Create tools instance
    tools = WorldModelTools(kg, config)

    # Create MCP server
    server = Server("world-model")

    # ============================================================================
    # Tool 1: query_fact
    # ============================================================================
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List all available tools."""
        return [
            Tool(
                name="query_fact",
                description="Query the knowledge graph for facts about entities (APIs, functions, classes, etc.)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query (e.g., 'User.findByEmail', 'JWT authentication')",
                        },
                        "entity_type": {
                            "type": "string",
                            "enum": ["api", "function", "class", "constraint", "file", "package"],
                            "description": "Optional filter by entity type",
                        },
                        "context": {
                            "type": "object",
                            "description": "Additional context for the query",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="record_event",
                description="Record a development event (file edit, test run, etc.)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "event_type": {
                            "type": "string",
                            "enum": [
                                "file_edit",
                                "file_create",
                                "file_delete",
                                "test_run",
                                "lint_run",
                                "user_correction",
                                "tool_call",
                            ],
                        },
                        "session_id": {"type": "string"},
                        "entities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Entity names/paths involved",
                        },
                        "description": {"type": "string"},
                        "reasoning": {"type": "string"},
                        "evidence": {
                            "type": "object",
                            "description": "Tool inputs/outputs, file contents, etc.",
                        },
                        "success": {"type": "boolean", "default": True},
                    },
                    "required": ["event_type", "session_id", "description"],
                },
            ),
            Tool(
                name="validate_change",
                description="Validate a proposed code change against known constraints",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "change_type": {
                            "type": "string",
                            "enum": ["edit", "create", "delete"],
                        },
                        "file_path": {"type": "string"},
                        "proposed_content": {
                            "type": "string",
                            "description": "The new content to validate",
                        },
                    },
                    "required": ["change_type", "file_path", "proposed_content"],
                },
            ),
            Tool(
                name="get_constraints",
                description="Get constraints (linting rules, patterns, conventions) for a file",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "constraint_types": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["linting", "architecture", "testing", "api_contract", "style"],
                            },
                        },
                    },
                    "required": ["file_path"],
                },
            ),
            Tool(
                name="record_correction",
                description="Record a user correction to Claude's output (high-priority learning signal)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "claude_action": {
                            "type": "object",
                            "description": "What Claude did (tool, file, content)",
                        },
                        "user_correction": {
                            "type": "object",
                            "description": "How the user corrected it",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Inferred reason for the correction",
                        },
                    },
                    "required": ["session_id", "claude_action", "user_correction"],
                },
            ),
            Tool(
                name="get_related_bugs",
                description="Get bugs fixed in a file and assess regression risk",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "change_description": {
                            "type": "string",
                            "description": "Brief description of proposed change",
                        },
                    },
                    "required": ["file_path"],
                },
            ),
            Tool(
                name="seed_project",
                description="Scan the project codebase and populate the knowledge graph with entities and relationships from existing code",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project_dir": {
                            "type": "string",
                            "description": "Project directory path (defaults to current)",
                        },
                        "force": {
                            "type": "boolean",
                            "description": "Re-seed already processed files",
                        },
                    },
                },
            ),
            Tool(
                name="ingest_pr_reviews",
                description="Pull GitHub PR review comments and convert them into learned constraints in the knowledge graph",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "GitHub repo (owner/repo). Auto-detected from git remote if omitted.",
                        },
                        "count": {
                            "type": "integer",
                            "description": "Number of recent PRs to scan (default 10, max 50)",
                        },
                    },
                },
            ),
            Tool(
                name="record_decision",
                description="Record a decision trace: what the agent proposed and how the human responded",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "tool_name": {"type": "string"},
                        "agent_proposal": {"type": "object"},
                        "human_correction": {"type": "object"},
                        "file_path": {"type": "string"},
                        "reasoning": {"type": "string"},
                        "decision_type": {"type": "string", "enum": ["correction", "approval", "rejection"]},
                    },
                    "required": ["session_id", "decision_type"],
                },
            ),
            Tool(
                name="get_decision_log",
                description="Get decision traces showing agent proposals and human corrections",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "file_path": {"type": "string"},
                        "decision_type": {"type": "string", "enum": ["correction", "approval", "rejection"]},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            Tool(
                name="record_test_outcome",
                description="Record test results and link failures to recent code changes",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "test_results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "file": {"type": "string"},
                                    "passed": {"type": "boolean"},
                                    "error": {"type": "string"},
                                },
                                "required": ["name", "passed"],
                            },
                        },
                    },
                    "required": ["session_id", "test_results"],
                },
            ),
            Tool(
                name="get_co_edit_suggestions",
                description="Get files commonly edited alongside the given file based on historical patterns",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["file_path"],
                },
            ),
            Tool(
                name="search_global",
                description="Search entities across all registered world-model projects",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="predict_regression",
                description="Score regression risk for a proposed change to a file based on past bugs, test failures, and constraint violations",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "change_description": {"type": "string"},
                    },
                    "required": ["file_path"],
                },
            ),
            Tool(
                name="simulate_change",
                description="Project blast radius and historical outcomes for a proposed change",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "change_description": {"type": "string"},
                    },
                    "required": ["file_path", "change_description"],
                },
            ),
            Tool(
                name="predict_test_failures",
                description="Surface tests likely to fail given a set of edited files",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["file_paths"],
                },
            ),
            Tool(
                name="promote_constraint",
                description="Promote a constraint from this project to all other registered projects",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "constraint_id": {"type": "string"},
                        "target_projects": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["constraint_id"],
                },
            ),
            Tool(
                name="get_health_report",
                description="Memory health diagnostics: orphans, stale facts, contradictions, decay candidates, DB sizes",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_context_for_action",
                description="Pre-action context bundle: constraints, decisions, bugs, co-edits, related facts, and risk score for a file before editing",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "action_type": {
                            "type": "string",
                            "enum": ["edit", "create", "delete", "refactor"],
                        },
                    },
                    "required": ["file_path", "action_type"],
                },
            ),
            Tool(
                name="find_contradictions",
                description="Find pairs of facts that contradict each other based on similarity and status differences",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            Tool(
                name="recall_transcript_range",
                description="Hydrate a Claude Code session transcript by line range. Lets agents trace a fact back to the exact conversation that produced it.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "line_start": {"type": "integer"},
                        "line_end": {"type": "integer"},
                    },
                    "required": ["session_id"],
                },
            ),
            Tool(
                name="export_claude_md",
                description="Generate a CLAUDE.md document from the knowledge graph (top constraints, recent decisions, known bug regions, co-edit patterns).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "max_constraints": {"type": "integer"},
                    },
                },
            ),
            Tool(
                name="get_injection_context",
                description="Return a compact constraint+fact bundle for PostCompact / UserPromptSubmit hooks to re-inject after context loss.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "event_type": {"type": "string", "enum": ["PostCompact", "UserPromptSubmit", "SessionStart"]},
                        "project_hint": {"type": "string"},
                        "max_constraints": {"type": "integer"},
                        "max_facts": {"type": "integer"},
                    },
                    "required": ["event_type"],
                },
            ),
            Tool(
                name="record_compaction_audit",
                description="Record a context-compaction event with token counts and what was re-injected. Lets developers audit what was remembered across compaction boundaries.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "pre_compact_tokens": {"type": "integer"},
                        "post_compact_tokens": {"type": "integer"},
                        "facts_injected": {"type": "integer"},
                        "constraints_injected": {"type": "integer"},
                        "injection_event": {"type": "string"},
                        "raw_summary": {"type": "string"},
                    },
                },
            ),
            Tool(
                name="get_compaction_audit",
                description="List recent compaction audit entries, most-recent first. Filter by session_id or limit count.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            Tool(
                name="resolve_contradiction",
                description="Pick a winner between two contradicting facts using a confidence-weighted strategy (auto, keep_higher_confidence, keep_most_recent, keep_most_sources, supersede_a, supersede_b, manual).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fact_a_id": {"type": "string"},
                        "fact_b_id": {"type": "string"},
                        "strategy": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["fact_a_id", "fact_b_id"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Any) -> list[TextContent]:
        """Handle tool calls."""
        try:
            logger.info(f"Tool called: {name} with args: {arguments}")

            if name == "query_fact":
                result = await tools.query_fact(
                    query=arguments["query"],
                    entity_type=arguments.get("entity_type"),
                    context=arguments.get("context", {}),
                )
                return [TextContent(type="text", text=result.model_dump_json(indent=2))]

            elif name == "record_event":
                result = await tools.record_event(
                    event_type=arguments["event_type"],
                    session_id=arguments["session_id"],
                    entities=arguments.get("entities", []),
                    description=arguments["description"],
                    reasoning=arguments.get("reasoning"),
                    evidence=arguments.get("evidence", {}),
                    success=arguments.get("success", True),
                )
                return [TextContent(type="text", text=result)]

            elif name == "validate_change":
                result = await tools.validate_change(
                    change_type=arguments["change_type"],
                    file_path=arguments["file_path"],
                    proposed_content=arguments["proposed_content"],
                )
                return [TextContent(type="text", text=result.model_dump_json(indent=2))]

            elif name == "get_constraints":
                result = await tools.get_constraints(
                    file_path=arguments["file_path"],
                    constraint_types=arguments.get("constraint_types"),
                )
                return [TextContent(type="text", text=result)]

            elif name == "record_correction":
                result = await tools.record_correction(
                    session_id=arguments["session_id"],
                    claude_action=arguments["claude_action"],
                    user_correction=arguments["user_correction"],
                    reasoning=arguments.get("reasoning", ""),
                )
                return [TextContent(type="text", text=result)]

            elif name == "get_related_bugs":
                result = await tools.get_related_bugs(
                    file_path=arguments["file_path"],
                    change_description=arguments.get("change_description", ""),
                )
                return [TextContent(type="text", text=result)]

            elif name == "seed_project":
                result = await tools.seed_project(
                    project_dir=arguments.get("project_dir"),
                    force=arguments.get("force", False),
                )
                return [TextContent(type="text", text=result)]

            elif name == "ingest_pr_reviews":
                result = await tools.ingest_pr_reviews(
                    repo=arguments.get("repo"),
                    count=arguments.get("count", 10),
                )
                return [TextContent(type="text", text=result)]

            elif name == "record_decision":
                result = await tools.record_decision(
                    session_id=arguments["session_id"],
                    tool_name=arguments.get("tool_name"),
                    agent_proposal=arguments.get("agent_proposal"),
                    human_correction=arguments.get("human_correction"),
                    file_path=arguments.get("file_path"),
                    reasoning=arguments.get("reasoning"),
                    decision_type=arguments.get("decision_type", "correction"),
                )
                return [TextContent(type="text", text=result)]

            elif name == "get_decision_log":
                result = await tools.get_decision_log(
                    session_id=arguments.get("session_id"),
                    file_path=arguments.get("file_path"),
                    decision_type=arguments.get("decision_type"),
                    limit=arguments.get("limit", 50),
                )
                return [TextContent(type="text", text=result)]

            elif name == "record_test_outcome":
                result = await tools.record_test_outcome(
                    session_id=arguments["session_id"],
                    test_results=arguments["test_results"],
                )
                return [TextContent(type="text", text=result)]

            elif name == "get_co_edit_suggestions":
                result = await tools.get_co_edit_suggestions(
                    file_path=arguments["file_path"],
                    limit=arguments.get("limit", 5),
                )
                return [TextContent(type="text", text=result)]

            elif name == "search_global":
                result = await tools.search_global(
                    query=arguments["query"],
                    limit=arguments.get("limit", 20),
                )
                return [TextContent(type="text", text=result)]

            elif name == "predict_regression":
                result = await tools.predict_regression(
                    file_path=arguments["file_path"],
                    change_description=arguments.get("change_description"),
                )
                return [TextContent(type="text", text=result)]

            elif name == "simulate_change":
                result = await tools.simulate_change(
                    file_path=arguments["file_path"],
                    change_description=arguments["change_description"],
                )
                return [TextContent(type="text", text=result)]

            elif name == "predict_test_failures":
                result = await tools.predict_test_failures(
                    file_paths=arguments["file_paths"],
                )
                return [TextContent(type="text", text=result)]

            elif name == "promote_constraint":
                result = await tools.promote_constraint(
                    constraint_id=arguments["constraint_id"],
                    target_projects=arguments.get("target_projects"),
                )
                return [TextContent(type="text", text=result)]

            elif name == "get_health_report":
                result = await tools.get_health_report()
                return [TextContent(type="text", text=result)]

            elif name == "get_context_for_action":
                result = await tools.get_context_for_action(
                    file_path=arguments["file_path"],
                    action_type=arguments["action_type"],
                )
                return [TextContent(type="text", text=result)]

            elif name == "find_contradictions":
                result = await tools.find_contradictions(
                    query=arguments.get("query"),
                    limit=arguments.get("limit", 20),
                )
                return [TextContent(type="text", text=result)]

            elif name == "recall_transcript_range":
                result = await tools.recall_transcript_range(
                    session_id=arguments["session_id"],
                    line_start=arguments.get("line_start"),
                    line_end=arguments.get("line_end"),
                )
                return [TextContent(type="text", text=result)]

            elif name == "export_claude_md":
                result = await tools.export_claude_md(
                    max_constraints=arguments.get("max_constraints", 20),
                )
                return [TextContent(type="text", text=result)]

            elif name == "get_injection_context":
                result = await tools.get_injection_context(
                    event_type=arguments["event_type"],
                    project_hint=arguments.get("project_hint"),
                    max_constraints=arguments.get("max_constraints", 10),
                    max_facts=arguments.get("max_facts", 10),
                )
                return [TextContent(type="text", text=result)]

            elif name == "record_compaction_audit":
                result = await tools.record_compaction_audit(
                    session_id=arguments.get("session_id"),
                    pre_compact_tokens=arguments.get("pre_compact_tokens"),
                    post_compact_tokens=arguments.get("post_compact_tokens"),
                    facts_injected=arguments.get("facts_injected", 0),
                    constraints_injected=arguments.get("constraints_injected", 0),
                    injection_event=arguments.get("injection_event"),
                    raw_summary=arguments.get("raw_summary"),
                )
                return [TextContent(type="text", text=result)]

            elif name == "get_compaction_audit":
                result = await tools.get_compaction_audit(
                    session_id=arguments.get("session_id"),
                    limit=arguments.get("limit", 50),
                )
                return [TextContent(type="text", text=result)]

            elif name == "resolve_contradiction":
                result = await tools.resolve_contradiction(
                    fact_a_id=arguments["fact_a_id"],
                    fact_b_id=arguments["fact_b_id"],
                    strategy=arguments.get("strategy", "auto"),
                    notes=arguments.get("notes"),
                )
                return [TextContent(type="text", text=result)]

            else:
                error_msg = f"Unknown tool: {name}"
                logger.error(error_msg)
                return [TextContent(type="text", text=error_msg)]

        except Exception as e:
            error_msg = f"Error executing tool {name}: {str(e)}"
            logger.exception(error_msg)
            return [TextContent(type="text", text=error_msg)]

    # Transport selection. Default: stdio (Claude Code / Cursor / .mcpb).
    # Set WORLD_MODEL_TRANSPORT=http to expose streamable HTTP for remote/tunnel deployment.
    transport = os.getenv("WORLD_MODEL_TRANSPORT", "stdio").lower()

    if transport == "stdio":
        logger.info("Starting stdio server...")
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    elif transport == "http":
        await _run_http(server)
    else:
        raise ValueError(
            f"Unknown WORLD_MODEL_TRANSPORT={transport!r}; expected 'stdio' or 'http'"
        )


async def _run_http(server) -> None:
    """Run the server over streamable HTTP for remote / MCP-tunnel deployment.

    Listens on WORLD_MODEL_HTTP_HOST:WORLD_MODEL_HTTP_PORT (default 0.0.0.0:8765).
    Exposes:
      - the MCP endpoint at WORLD_MODEL_HTTP_PATH (default /mcp)
      - a /healthz endpoint returning {"status": "ok", "version": "..."}
    """
    # Imports are deferred so the stdio path has zero new dependencies at runtime
    # for environments that never enable HTTP.
    import contextlib

    try:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route
        from starlette.types import Receive, Scope, Send
    except ImportError as exc:
        raise SystemExit(
            "HTTP transport requested but uvicorn/starlette are not installed.\n"
            "Install the optional 'http' extras:\n"
            "  pip install 'world-model-mcp[http]'"
        ) from exc

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    from . import __version__

    host = os.getenv("WORLD_MODEL_HTTP_HOST", "0.0.0.0")
    port = int(os.getenv("WORLD_MODEL_HTTP_PORT", "8765"))
    mount_path = os.getenv("WORLD_MODEL_HTTP_PATH", "/mcp")
    if not mount_path.startswith("/"):
        mount_path = "/" + mount_path

    session_manager = StreamableHTTPSessionManager(
        app=server,
        event_store=None,  # in-memory; tunnel front-end is responsible for durability
        stateless=False,
    )

    async def mcp_asgi(scope: Scope, receive: Receive, send: Send) -> None:
        await session_manager.handle_request(scope, receive, send)

    async def healthz(_request):
        return JSONResponse({"status": "ok", "version": __version__})

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Mount(mount_path, app=mcp_asgi),
        ],
        lifespan=lifespan,
    )

    logger.info(f"Starting HTTP server on {host}:{port} (mcp at {mount_path}, healthz at /healthz)")
    config = uvicorn.Config(app, host=host, port=port, log_level="info", lifespan="on")
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    asyncio.run(main())
