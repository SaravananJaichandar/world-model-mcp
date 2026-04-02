"""
Tests for the auto-seeding module.
"""

import pytest
import tempfile
import shutil
from pathlib import Path

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.config import Config
from world_model_server.seeder import ProjectSeeder, SeedResult


@pytest.fixture
async def kg():
    """Create a temporary knowledge graph."""
    temp_dir = tempfile.mkdtemp()
    kg = KnowledgeGraph(temp_dir)
    await kg.initialize()
    yield kg
    shutil.rmtree(temp_dir)


@pytest.fixture
def project_dir():
    """Create a temporary project directory with sample files."""
    temp_dir = tempfile.mkdtemp()
    base = Path(temp_dir)

    # Python file with class and functions
    (base / "src").mkdir()
    (base / "src" / "auth.py").write_text(
        'import os\n'
        'from pathlib import Path\n'
        '\n'
        'class AuthService:\n'
        '    def authenticate(self, token: str) -> bool:\n'
        '        return True\n'
        '\n'
        '    def refresh_token(self, user_id: str) -> str:\n'
        '        return "new_token"\n'
    )

    # TypeScript file
    (base / "src" / "api.ts").write_text(
        'import { Router } from "express";\n'
        'import { AuthService } from "./auth";\n'
        '\n'
        'export function createRouter(): Router {\n'
        '    return new Router();\n'
        '}\n'
        '\n'
        'export class ApiController {\n'
        '    async getUsers(): Promise<User[]> {\n'
        '        return [];\n'
        '    }\n'
        '}\n'
    )

    # JavaScript file
    (base / "src" / "utils.js").write_text(
        'const helper = (x) => x + 1;\n'
        'function formatDate(d) { return d.toString(); }\n'
    )

    # File that should be skipped (too large won't apply here, but wrong extension)
    (base / "src" / "data.json").write_text('{"key": "value"}')

    # node_modules should be skipped
    (base / "node_modules").mkdir()
    (base / "node_modules" / "pkg.js").write_text('module.exports = {}')

    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def config():
    return Config()


@pytest.mark.asyncio
async def test_collect_files_filters_extensions(kg, project_dir, config):
    """Only .py, .ts, .tsx, .js, .jsx files should be collected."""
    seeder = ProjectSeeder(project_dir, kg, config)
    files = seeder._collect_files()
    extensions = {f.suffix for f in files}
    assert extensions.issubset({".py", ".ts", ".tsx", ".js", ".jsx"})
    assert ".json" not in extensions


@pytest.mark.asyncio
async def test_collect_files_skips_node_modules(kg, project_dir, config):
    """node_modules directory should be skipped."""
    seeder = ProjectSeeder(project_dir, kg, config)
    files = seeder._collect_files()
    paths = [str(f) for f in files]
    assert not any("node_modules" in p for p in paths)


@pytest.mark.asyncio
async def test_collect_files_skips_large_files(kg, config):
    """Files larger than 100KB should be skipped."""
    temp_dir = tempfile.mkdtemp()
    try:
        large_file = Path(temp_dir) / "big.py"
        large_file.write_text("x = 1\n" * 20000)  # ~120KB
        assert large_file.stat().st_size > 100 * 1024

        seeder = ProjectSeeder(temp_dir, kg, config)
        files = seeder._collect_files()
        assert large_file not in files
    finally:
        shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_seed_file_creates_entities(kg, project_dir, config):
    """Seeding a Python file should create entities for functions and classes."""
    seeder = ProjectSeeder(project_dir, kg, config)
    result = SeedResult()
    auth_path = Path(project_dir) / "src" / "auth.py"

    entity_count, rel_count = await seeder._seed_file(auth_path, False, result)

    # Should create: 1 file + 1 class + 2 functions = 4 entities
    assert entity_count >= 4
    assert result.files_seeded == 1


@pytest.mark.asyncio
async def test_seed_file_creates_import_relationships(kg, project_dir, config):
    """Seeding should create import relationships."""
    seeder = ProjectSeeder(project_dir, kg, config)
    result = SeedResult()
    auth_path = Path(project_dir) / "src" / "auth.py"

    _, rel_count = await seeder._seed_file(auth_path, False, result)

    # auth.py has: import os, from pathlib import Path = 2 imports
    assert rel_count == 2


@pytest.mark.asyncio
async def test_seed_skips_already_seeded(kg, project_dir, config):
    """Files already in the KG should be skipped without --force."""
    seeder = ProjectSeeder(project_dir, kg, config)
    result1 = SeedResult()
    auth_path = Path(project_dir) / "src" / "auth.py"

    # First seed
    await seeder._seed_file(auth_path, False, result1)
    assert result1.files_seeded == 1

    # Second seed without force
    result2 = SeedResult()
    entity_count, _ = await seeder._seed_file(auth_path, False, result2)
    assert entity_count == 0
    assert result2.skipped_files == 1


@pytest.mark.asyncio
async def test_seed_force_reprocesses(kg, project_dir, config):
    """With force=True, already-seeded files should be reprocessed."""
    seeder = ProjectSeeder(project_dir, kg, config)
    result1 = SeedResult()
    auth_path = Path(project_dir) / "src" / "auth.py"

    await seeder._seed_file(auth_path, False, result1)

    result2 = SeedResult()
    entity_count, _ = await seeder._seed_file(auth_path, True, result2)
    assert entity_count >= 4
    assert result2.files_seeded == 1


@pytest.mark.asyncio
async def test_seed_creates_seeded_fact(kg, project_dir, config):
    """A fact should be recorded for each seeded file."""
    seeder = ProjectSeeder(project_dir, kg, config)
    result = SeedResult()
    auth_path = Path(project_dir) / "src" / "auth.py"

    await seeder._seed_file(auth_path, False, result)

    facts = await kg.query_facts("seeded")
    assert facts.exists
    assert len(facts.facts) >= 1
    assert "auth.py" in facts.facts[0].fact_text


@pytest.mark.asyncio
async def test_full_seed_integration(kg, project_dir, config):
    """Full seeding of a project with multiple files."""
    seeder = ProjectSeeder(project_dir, kg, config)
    result = await seeder.seed()

    # 3 supported files: auth.py, api.ts, utils.js
    assert result.files_seeded == 3
    assert result.entities_created > 0
    assert result.duration_seconds >= 0
    assert result.skipped_files == 0


@pytest.mark.asyncio
async def test_seed_skips_binary_files(kg, config):
    """Binary files should be skipped."""
    temp_dir = tempfile.mkdtemp()
    try:
        bin_file = Path(temp_dir) / "data.py"
        bin_file.write_bytes(b"\x00\x01\x02\x03" + b"def foo(): pass\n")

        seeder = ProjectSeeder(temp_dir, kg, config)
        result = SeedResult()
        entity_count, _ = await seeder._seed_file(bin_file, False, result)
        assert entity_count == 0
        assert result.skipped_files == 1
    finally:
        shutil.rmtree(temp_dir)
