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
        default_factory=lambda: os.getenv(
            "WORLD_MODEL_EXTRACTION_MODEL", "claude-3-haiku-20240307"
        ),
        description="Model for entity extraction",
    )
    reasoning_model: str = Field(
        default_factory=lambda: os.getenv(
            "WORLD_MODEL_REASONING_MODEL", "claude-3-5-sonnet-20241022"
        ),
        description="Model for complex reasoning",
    )
    verification_model: str = Field(
        default_factory=lambda: os.getenv(
            "WORLD_MODEL_VERIFICATION_MODEL", "claude-haiku-4-5-20251001"
        ),
        description=(
            "Model for the Coach adversarial verification pass in "
            "verify_retrieval (v0.12.12). Defaults to Haiku 4.5 — fast + "
            "cheap; verification is a per-answer overhead call and shouldn't "
            "share the reasoning-model budget."
        ),
    )
    verification_backend: str = Field(
        default_factory=lambda: os.getenv("WORLD_MODEL_VERIFICATION_BACKEND", "anthropic"),
        description=(
            "Coach backend (v0.12.13). 'anthropic' uses AsyncAnthropic against "
            "api.anthropic.com (unchanged from v0.12.12). 'openai-compatible' "
            "uses AsyncOpenAI against WORLD_MODEL_VERIFICATION_BASE_URL — works "
            "with OpenRouter, Ollama, vLLM, LiteLLM, and any OpenAI-shape endpoint. "
            "Skips the LiteLLM-proxy dance for OpenRouter users."
        ),
    )
    verification_base_url: Optional[str] = Field(
        default_factory=lambda: os.getenv("WORLD_MODEL_VERIFICATION_BASE_URL"),
        description=(
            "Base URL for the openai-compatible verification backend. "
            "Ignored when verification_backend='anthropic'. Common values:\n"
            "  OpenRouter:  https://openrouter.ai/api/v1\n"
            "  Ollama:      http://localhost:11434/v1\n"
            "  vLLM:        http://localhost:8000/v1"
        ),
    )
    verification_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("WORLD_MODEL_VERIFICATION_API_KEY"),
        description=(
            "API key for the openai-compatible verification backend. Falls back "
            "to OPENROUTER_API_KEY, then OPENAI_API_KEY, then a placeholder for "
            "local endpoints that don't authenticate. Ignored when "
            "verification_backend='anthropic'."
        ),
    )
    max_facts_per_query: int = Field(
        default_factory=lambda: int(os.getenv("WORLD_MODEL_MAX_FACTS_PER_QUERY", "10"))
    )
    confidence_threshold: float = Field(
        default_factory=lambda: float(os.getenv("WORLD_MODEL_CONFIDENCE_THRESHOLD", "0.6"))
    )
    debug: bool = Field(default_factory=lambda: os.getenv("WORLD_MODEL_DEBUG") == "1")

    @classmethod
    def from_env(cls) -> "Config":
        """Create config from environment variables."""
        return cls()
