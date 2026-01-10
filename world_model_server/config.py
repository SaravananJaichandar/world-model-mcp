"""
Configuration management for the World Model MCP server.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


class Config(BaseModel):
    """Configuration for the World Model MCP server."""

    version: str = "0.1.0"
    db_path: str = Field(
        default_factory=lambda: os.getenv(
            "WORLD_MODEL_DB_PATH", str(Path.cwd() / ".claude" / "world-model")
        )
    )
    anthropic_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY")
    )
    extraction_model: str = Field(
        default="claude-3-haiku-20240307", description="Model for entity extraction"
    )
    reasoning_model: str = Field(
        default="claude-3-5-sonnet-20241022", description="Model for complex reasoning"
    )
    max_facts_per_query: int = 10
    confidence_threshold: float = 0.6
    debug: bool = Field(default_factory=lambda: os.getenv("WORLD_MODEL_DEBUG") == "1")

    @classmethod
    def from_env(cls) -> "Config":
        """Create config from environment variables."""
        return cls()

    class Config:
        env_prefix = "WORLD_MODEL_"
