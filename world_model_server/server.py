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

            else:
                error_msg = f"Unknown tool: {name}"
                logger.error(error_msg)
                return [TextContent(type="text", text=error_msg)]

        except Exception as e:
            error_msg = f"Error executing tool {name}: {str(e)}"
            logger.exception(error_msg)
            return [TextContent(type="text", text=error_msg)]

    # Run the server
    logger.info("Starting stdio server...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
