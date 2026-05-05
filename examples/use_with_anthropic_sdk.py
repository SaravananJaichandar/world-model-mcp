"""
Demo: use world-model-mcp as a memory backend for the Anthropic SDK.

If your installed `anthropic` SDK provides BetaAbstractMemoryTool, the
WorldModelMemoryBackend below is a drop-in subclass. Otherwise it works as
a standalone async memory store.

Usage:
    python examples/use_with_anthropic_sdk.py
"""

import asyncio
from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.memory_backend import WorldModelMemoryBackend


async def main():
    kg = KnowledgeGraph("/tmp/wm-memory-demo")
    await kg.initialize()

    backend = WorldModelMemoryBackend(kg, session_id="demo-session")

    print(f"SDK base class available: {WorldModelMemoryBackend.has_sdk_base()}")

    # Create a memory entry
    await backend.create("/memories/notes.md", "First version of notes.")
    print("Created.")

    # Read it back
    content = await backend.view("/memories/notes.md")
    print(f"View: {content}")

    # Update via str_replace
    await backend.str_replace("/memories/notes.md", "First version", "Second version")
    print("Replaced.")

    # Read updated
    content = await backend.view("/memories/notes.md")
    print(f"View: {content}")

    # Append a line via insert
    await backend.insert("/memories/notes.md", 2, "Appended line.\n")
    content = await backend.view("/memories/notes.md")
    print(f"View: {content}")

    # Delete
    await backend.delete("/memories/notes.md")
    content = await backend.view("/memories/notes.md")
    print(f"After delete (empty): {repr(content)}")


if __name__ == "__main__":
    asyncio.run(main())
