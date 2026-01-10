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
