"""
world-model-mcp as a Hermes Agent MemoryProvider plugin (v0.11.0 B).

Hermes surfaces external memory backends through the MemoryProvider ABC
in `agent/memory_provider.py`. Only one MemoryProvider slot is active at
a time. This plugin implements the ABC and dispatches tool calls to the
shipped `WorldModelTools` methods, so the same fact graph, provenance
schema, decay function, and contradiction resolution ships to Hermes
users without a second implementation.

Design:

- **No hard dependency on Hermes.** The ABC import is guarded so the
  plugin file can be imported for tests and copied into the user's
  Hermes install without Hermes being present at world-model-mcp's
  install time.
- **Sync ↔ async bridge.** WorldModelTools methods are async;
  MemoryProvider's ABC is sync. Each `handle_tool_call` opens a fresh
  event loop, runs the target method, and returns. Acceptable for a
  first ship; a persistent loop lands in v0.11.x if the per-call cost
  matters in practice.
- **Tool surface.** Ships the seven highest-value world-model tools
  from the 27-tool set. The trimmed list keeps Hermes' tool namespace
  clean (Hermes agents already have many tools). Users who want the
  full surface can register the world-model MCP server ALSO (v0.10
  install-hermes adapter) — MCP and MemoryProvider are non-exclusive
  from the world-model side.

To install:
    python -m world_model_server.cli install-hermes-provider

To use programmatically (tests, other integrations):
    from world_model_server.hermes_memory_provider import WorldModelMemoryProvider
    provider = WorldModelMemoryProvider()
    provider.initialize("session-id", hermes_home="/path/to/.hermes")
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

# Hermes ABC import is soft: the plugin can be imported for unit tests
# on machines without Hermes installed, and the file that ends up in
# ~/.hermes/plugins/memory/world-model/ can be edited by users who have
# the ABC available. When Hermes is not importable, we fall back to
# `object` — the plugin still satisfies duck-typing.
try:
    from hermes_agent.memory_provider import MemoryProvider  # type: ignore
    _HERMES_ABC_AVAILABLE = True
except ImportError:
    MemoryProvider = object  # type: ignore
    _HERMES_ABC_AVAILABLE = False


logger = logging.getLogger("world_model_server.hermes_memory_provider")


# The seven tools the MemoryProvider surfaces to Hermes agent turns.
# Trimmed from the 27 exposed via MCP to keep Hermes' tool namespace
# focused; users who want the full 27 can additionally register the
# v0.10 MCP adapter (`install-hermes`). Non-exclusive from our side.
SURFACED_TOOL_NAMES = (
    "query_fact",
    "get_constraints",
    "get_injection_context",
    "record_event",
    "record_correction",
    "find_contradictions",
    "resolve_contradiction",
    "verify_retrieval",
)


def _run_async(coro):
    """Run a coroutine to completion using a fresh event loop.

    v0.11 first cut: per-call event loop. Simple, no state, correct
    under Hermes' sync ABC. A persistent loop is a v0.11.x follow-up
    if per-call cost becomes measurable.
    """
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    except Exception:
        logger.exception("world-model handler failed")
        raise


class WorldModelMemoryProvider(MemoryProvider):
    """MemoryProvider implementation backed by world-model-mcp.

    Hermes calls the sync methods on this class; each dispatches to the
    async WorldModelTools methods on the shared KnowledgeGraph.
    """

    def __init__(self, db_path: Optional[str] = None):
        # If db_path is set at construction time, we use it. Otherwise we
        # defer resolution to initialize() when hermes_home arrives.
        self._db_path = db_path
        self._session_id: Optional[str] = None
        self._kg = None
        self._tools = None
        self._config = None

    # ------------------------------------------------------------------
    # Required ABC methods
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "world-model"

    def is_available(self) -> bool:
        """No network calls. Confirms the world_model_server package is
        importable and its dependencies are installed."""
        try:
            from world_model_server.knowledge_graph import KnowledgeGraph  # noqa: F401
            from world_model_server.tools import WorldModelTools  # noqa: F401
            from world_model_server.config import Config  # noqa: F401
        except ImportError:
            return False
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        """Open the knowledge graph and construct the tools instance.

        Priority for resolving the database path:
          1. Explicit ``db_path`` passed at ``__init__`` time (tests)
          2. ``kwargs["hermes_home"]`` from Hermes → ``<hermes_home>/world-model``
          3. Fallback to ``.claude/world-model`` (project-cwd relative)

        Config is constructed directly (not via ``from_env``) so the plugin
        does not pollute ``os.environ`` inside the Hermes process. Other
        plugins and providers keep their own view of the environment.
        """
        from world_model_server.config import Config
        from world_model_server.knowledge_graph import KnowledgeGraph
        from world_model_server.tools import WorldModelTools

        self._session_id = session_id
        hermes_home = kwargs.get("hermes_home")
        if self._db_path is None:
            if hermes_home:
                self._db_path = str(Path(hermes_home) / "world-model")
            else:
                self._db_path = ".claude/world-model"

        self._config = Config(db_path=self._db_path)
        self._kg = KnowledgeGraph(self._db_path)
        _run_async(self._kg.initialize())
        self._tools = WorldModelTools(self._kg, self._config)
        logger.info(
            "world-model MemoryProvider initialized (session=%s, db=%s)",
            session_id,
            self._db_path,
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return JSON-schema descriptors for the seven surfaced tools.

        Format matches Hermes' tool-schema expectation: a list of dicts
        with keys ``name``, ``description``, and ``inputSchema``.
        """
        return list(_surfaced_tool_schemas())

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> Any:
        """Dispatch to the WorldModelTools method by name."""
        if self._tools is None:
            raise RuntimeError("MemoryProvider.handle_tool_call called before initialize()")

        if tool_name not in SURFACED_TOOL_NAMES:
            return json.dumps({
                "error": f"tool {tool_name!r} not surfaced by world-model provider",
                "surfaced": list(SURFACED_TOOL_NAMES),
            })

        method = getattr(self._tools, tool_name, None)
        if method is None or not callable(method):
            return json.dumps({"error": f"world-model tool {tool_name!r} not found"})

        # Args are passed straight through. WorldModelTools methods accept
        # keyword arguments matching the MCP inputSchema.
        return _run_async(method(**args))

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """Config fields for Hermes' setup wizard."""
        return [
            {
                "key": "world_model_db_path",
                "description": (
                    "Filesystem path where world-model-mcp stores its SQLite fact "
                    "graph. Leave blank to use <hermes_home>/world-model/."
                ),
                "secret": False,
                "required": False,
                "env_var": "WORLD_MODEL_DB_PATH",
                "default": "",
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Persist non-secret config alongside Hermes' state."""
        target = Path(hermes_home) / "world-model.config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(values, indent=2))

    # ------------------------------------------------------------------
    # Optional lifecycle hooks (v0.12.9)
    # ------------------------------------------------------------------
    #
    # Hermes calls these at specific lifecycle points if the provider
    # implements them. All are best-effort by convention: an exception
    # inside a hook must never crash the Hermes turn. Each hook catches
    # and logs; returns a safe default; and passes through the sync-async
    # bridge to reuse the shipped WorldModelTools async methods.

    def sync_turn(self, turn_data: Dict[str, Any], **kwargs) -> None:
        """Called after every completed agent turn. Records the turn as a
        world-model event so cross-tool history is preserved. Non-blocking;
        an exception is swallowed to protect the Hermes loop."""
        if self._tools is None:
            return
        try:
            _run_async(self._tools.record_event(
                event_type="tool_call",
                session_id=str(turn_data.get("session_id") or self._session_id or "unknown"),
                entities=list(turn_data.get("entities") or []),
                description=str(turn_data.get("description") or "hermes turn"),
                reasoning=turn_data.get("reasoning"),
                evidence={
                    "tool_name": "hermes:sync_turn",
                    "tool_input": turn_data.get("input", {}),
                    "tool_output": turn_data.get("output", {}),
                },
                success=bool(turn_data.get("success", True)),
            ))
        except Exception:
            logger.exception("sync_turn failed; ignoring")

    def on_pre_compress(self, context: Dict[str, Any], **kwargs) -> str:
        """Called just before Hermes compresses context. Returns a short
        injection bundle (rules + recent canonical facts + top constraints)
        that Hermes can include verbatim in the compressed summary. This is
        the load-bearing Hermes-side complement to the v0.12.3 content-type
        routing: rules always inject at pre-compress; procedures never do."""
        if self._tools is None:
            return ""
        try:
            payload = _run_async(self._tools.get_injection_context(
                event_type="PostCompact",
                project_hint=context.get("project_hint") if isinstance(context, dict) else None,
                max_constraints=int((context or {}).get("max_constraints", 10)),
                max_facts=int((context or {}).get("max_facts", 10)),
            ))
            data = json.loads(payload)
            return data.get("injection", "") or ""
        except Exception:
            logger.exception("on_pre_compress failed; returning empty injection")
            return ""

    def prefetch(self, query_hint: Optional[str], **kwargs) -> List[Dict[str, Any]]:
        """Speculative pre-fetch. Given a short hint (e.g. topic or file
        path), warm the fact cache and return facts Hermes can pre-load
        into context. Returns [] on any failure or when no hint is given."""
        if self._tools is None or not query_hint:
            return []
        try:
            result = _run_async(self._tools.query_fact(query=query_hint))
            return [f.model_dump() for f in result.facts]
        except Exception:
            logger.exception("prefetch failed; returning empty list")
            return []

    def on_session_end(self, session_data: Dict[str, Any], **kwargs) -> None:
        """Called when a Hermes session ends. Records a session-close event
        so the fact graph has a boundary marker for later attribution."""
        if self._tools is None:
            return
        try:
            _run_async(self._tools.record_event(
                event_type="tool_call",
                session_id=str(session_data.get("session_id") or self._session_id or "unknown"),
                entities=[],
                description="hermes session ended",
                reasoning=session_data.get("reason"),
                evidence={
                    "tool_name": "hermes:on_session_end",
                    "tool_input": {
                        "turn_count": session_data.get("turn_count"),
                        "duration_ms": session_data.get("duration_ms"),
                    },
                    "tool_output": {},
                },
                success=True,
            ))
        except Exception:
            logger.exception("on_session_end failed; ignoring")

    def on_memory_write(self, entry: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Called when Hermes writes to memory through this provider. Logs
        the write, echoes the entry (Hermes uses the return value as the
        possibly-mutated entry). This hook does NOT perform content-type
        classification — that is a caller responsibility — but it does
        stamp a routing hint so downstream consumers know which bucket
        the write landed in (rule / fact / procedure / null).

        Returning the entry unchanged is the safe default. A future revision
        can annotate content_type here if the caller has not set it."""
        try:
            content_type = None
            if isinstance(entry, dict):
                content_type = entry.get("content_type")
            logger.info(
                "on_memory_write: content_type=%s session=%s",
                content_type,
                self._session_id,
            )
        except Exception:
            logger.exception("on_memory_write logging failed; ignoring")
        return entry if isinstance(entry, dict) else {}


# --------------------------------------------------------------------------
# Tool-schema helpers
# --------------------------------------------------------------------------


def _surfaced_tool_schemas():
    """Yield JSON-schema dicts for the surfaced tools.

    Kept as a generator so the schemas can be lazily edited without
    breaking the ABC contract. Schemas mirror what server.py already
    declares for the MCP surface; keeping them in one place is a
    v0.11.x refactor (extract into a shared registry).
    """
    yield {
        "name": "query_fact",
        "description": (
            "Query the world-model knowledge graph for facts about entities "
            "(APIs, functions, classes, constraints). Pass content_type='procedure' "
            "to explicitly summon procedures, which are excluded from auto-injection "
            "by design (v0.12.3 content-type routing)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "entity_type": {
                    "type": "string",
                    "enum": ["api", "function", "class", "constraint", "file", "package"],
                },
                "context": {"type": "object"},
                "content_type": {
                    "type": "string",
                    "enum": ["rule", "fact", "procedure"],
                },
            },
            "required": ["query"],
        },
    }
    yield {
        "name": "get_constraints",
        "description": "Return the learned constraints (linting rules, patterns, conventions) that apply to a given file.",
        "inputSchema": {
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
    }
    yield {
        "name": "get_injection_context",
        "description": (
            "Return a compact constraint + fact bundle for injection after context loss. "
            "Complements Hermes' on_pre_compress hook."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "enum": ["PostCompact", "UserPromptSubmit", "SessionStart"],
                },
                "project_hint": {"type": "string"},
                "max_constraints": {"type": "integer"},
                "max_facts": {"type": "integer"},
            },
            "required": ["event_type"],
        },
    }
    yield {
        "name": "record_event",
        "description": "Record a development event (file edit, test run, user correction, tool call).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "enum": [
                        "file_edit", "file_create", "file_delete",
                        "test_run", "lint_run", "user_correction", "tool_call",
                    ],
                },
                "session_id": {"type": "string"},
                "entities": {"type": "array", "items": {"type": "string"}},
                "description": {"type": "string"},
                "reasoning": {"type": "string"},
                "evidence": {"type": "object"},
                "success": {"type": "boolean"},
            },
            "required": ["event_type", "session_id", "description"],
        },
    }
    yield {
        "name": "record_correction",
        "description": "Record a user correction to model output (high-priority learning signal).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "claude_action": {"type": "object"},
                "user_correction": {"type": "object"},
                "reasoning": {"type": "string"},
            },
            "required": ["session_id", "claude_action", "user_correction"],
        },
    }
    yield {
        "name": "find_contradictions",
        "description": "Find pairs of facts that contradict each other based on similarity and status differences.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    }
    yield {
        "name": "verify_retrieval",
        "description": (
            "Adversarially verify that an answer is grounded in a specific "
            "set of facts. An independent Coach LLM call checks each material "
            "claim in the answer against the supplied source facts. Returns "
            "confidence (HIGH / MEDIUM / LOW), verified + unverified claim "
            "lists, and per-claim source_pointers. Never raises; failures "
            "return LOW + `error` populated. v0.12.12."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "answer": {"type": "string"},
                "fact_ids": {"type": "array", "items": {"type": "string"}},
                "verification_model": {"type": "string"},
            },
            "required": ["query", "answer", "fact_ids"],
        },
    }
    yield {
        "name": "resolve_contradiction",
        "description": (
            "Pick a winner between two contradicting facts using a confidence-weighted strategy "
            "(auto, keep_higher_confidence, keep_higher_confidence_decayed, keep_most_recent, "
            "keep_most_sources, supersede_a, supersede_b, manual)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fact_a_id": {"type": "string"},
                "fact_b_id": {"type": "string"},
                "strategy": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["fact_a_id", "fact_b_id"],
        },
    }


# --------------------------------------------------------------------------
# register(ctx) — Hermes plugin discovery entry point
# --------------------------------------------------------------------------


def register(ctx) -> None:
    """Called by Hermes' memory-plugin discovery system.

    The ``ctx`` argument exposes ``register_memory_provider(provider)``.
    """
    ctx.register_memory_provider(WorldModelMemoryProvider())
