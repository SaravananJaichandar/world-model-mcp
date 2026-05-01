"""
World Model MCP Server

An MCP server that builds a world model for codebases, learning from
Claude Code sessions to prevent hallucinations, repeated mistakes,
and regressions.
"""

__version__ = "0.5.0"
__author__ = "World Model Team"

from .models import Entity, Fact, Constraint, Session, Event, Decision, TestOutcome

__all__ = [
    "Entity",
    "Fact",
    "Constraint",
    "Session",
    "Event",
    "Decision",
    "TestOutcome",
]
