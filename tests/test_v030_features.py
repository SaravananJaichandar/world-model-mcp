"""
Comprehensive tests for v0.3.0 features.
Covers: module-level matching, incremental re-seeding, fuzzy matching,
query caching, Java/Solidity extraction.

Test levels:
- Unit: individual function behavior
- Integration: feature interactions with KG
- E2E: full seed -> query workflows
- Smoke: basic sanity checks
"""

import os
import pytest
import tempfile
import shutil
import time
from pathlib import Path
from datetime import datetime, timedelta

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.config import Config
from world_model_server.extraction import EntityExtractor
from world_model_server.seeder import ProjectSeeder, SeedResult
from world_model_server.models import Entity, Fact


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
async def kg():
    temp_dir = tempfile.mkdtemp()
    kg = KnowledgeGraph(temp_dir)
    await kg.initialize()
    yield kg
    shutil.rmtree(temp_dir)


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def extractor(config):
    return EntityExtractor(config)


# ============================================================================
# Unit Tests: Module-level matching
# ============================================================================

@pytest.mark.asyncio
async def test_find_entities_matches_file_path(kg):
    """find_entities should match on file_path, not just name."""
    entity = Entity(
        entity_type="file", name="trading.py", file_path="services/paper_trading.py"
    )
    await kg.create_entity(entity)

    results = await kg.find_entities(name="paper_trading")
    assert len(results) >= 1
    assert any(e.file_path == "services/paper_trading.py" for e in results)


@pytest.mark.asyncio
async def test_find_entities_matches_module_name_in_path(kg):
    """Searching a module name should find files containing that name in their path."""
    await kg.create_entity(Entity(
        entity_type="function", name="run_strategy", file_path="src/paper_trading/runner.py"
    ))

    results = await kg.find_entities(name="paper_trading")
    assert len(results) >= 1


# ============================================================================
# Unit Tests: Fuzzy matching
# ============================================================================

@pytest.mark.asyncio
async def test_fuzzy_match_typo(kg):
    """Fuzzy search should find entities with small typos."""
    await kg.create_entity(Entity(
        entity_type="class", name="StrategyManager", file_path="src/strategy.py"
    ))

    results = await kg.find_entities_fuzzy("StrategyMgr", threshold=0.5)
    assert len(results) >= 1
    assert results[0].name == "StrategyManager"


@pytest.mark.asyncio
async def test_fuzzy_match_partial(kg):
    """Fuzzy search should find entities with partial names."""
    await kg.create_entity(Entity(
        entity_type="class", name="AuthenticationService", file_path="src/auth.py"
    ))

    results = await kg.find_entities_fuzzy("AuthService", threshold=0.5)
    assert len(results) >= 1
    assert results[0].name == "AuthenticationService"


@pytest.mark.asyncio
async def test_fuzzy_match_substring(kg):
    """Substring matches should get high score."""
    await kg.create_entity(Entity(
        entity_type="function", name="getUserById", file_path="src/users.ts"
    ))

    results = await kg.find_entities_fuzzy("getUser", threshold=0.5)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_fuzzy_match_no_false_positives(kg):
    """Completely different names should not match."""
    await kg.create_entity(Entity(
        entity_type="class", name="DatabaseConnection", file_path="src/db.py"
    ))

    results = await kg.find_entities_fuzzy("UserProfile", threshold=0.6)
    assert len(results) == 0


@pytest.mark.asyncio
async def test_fuzzy_match_ranking(kg):
    """Better matches should rank higher."""
    await kg.create_entity(Entity(entity_type="class", name="StrategyManager", file_path="a.py"))
    await kg.create_entity(Entity(entity_type="class", name="StrategyRunner", file_path="b.py"))
    await kg.create_entity(Entity(entity_type="class", name="DatabaseManager", file_path="c.py"))

    results = await kg.find_entities_fuzzy("StrategyManager", threshold=0.5)
    assert len(results) >= 2
    assert results[0].name == "StrategyManager"  # Exact match first


# ============================================================================
# Unit Tests: Query caching
# ============================================================================

@pytest.mark.asyncio
async def test_cache_returns_same_result(kg):
    """Repeated queries should return cached results."""
    fact = Fact(
        fact_text="class Foo exists in bar.py",
        evidence_type="source_code", evidence_path="bar.py",
        confidence=1.0, status="canonical",
    )
    await kg.create_fact(fact)

    result1 = await kg.query_facts("Foo")
    result2 = await kg.query_facts("Foo")
    assert result1.exists == result2.exists
    assert len(result1.facts) == len(result2.facts)


@pytest.mark.asyncio
async def test_cache_invalidated_on_new_fact(kg):
    """Cache should be invalidated when new facts are created."""
    result1 = await kg.query_facts("NonExistent")
    assert not result1.exists

    await kg.create_fact(Fact(
        fact_text="class NonExistent exists in test.py",
        evidence_type="source_code", evidence_path="test.py",
        confidence=1.0, status="canonical",
    ))

    result2 = await kg.query_facts("NonExistent")
    assert result2.exists


# ============================================================================
# Unit Tests: Incremental re-seeding
# ============================================================================

@pytest.mark.asyncio
async def test_incremental_seed_skips_unchanged(kg, config):
    """Files unchanged since last seed should be skipped."""
    temp_dir = tempfile.mkdtemp()
    try:
        py_file = Path(temp_dir) / "test.py"
        py_file.write_text("class Foo:\n    pass\n")

        seeder = ProjectSeeder(temp_dir, kg, config)

        # First seed
        result1 = SeedResult()
        await seeder._seed_file(py_file, False, result1)
        assert result1.files_seeded == 1

        # Second seed without changes
        result2 = SeedResult()
        await seeder._seed_file(py_file, False, result2)
        assert result2.skipped_files == 1
        assert result2.files_seeded == 0
    finally:
        shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_incremental_seed_reprocesses_modified(kg, config):
    """Files modified since last seed should be reprocessed."""
    temp_dir = tempfile.mkdtemp()
    try:
        py_file = Path(temp_dir) / "test.py"
        py_file.write_text("class Foo:\n    pass\n")

        seeder = ProjectSeeder(temp_dir, kg, config)

        # First seed
        result1 = SeedResult()
        await seeder._seed_file(py_file, False, result1)
        assert result1.files_seeded == 1

        # Modify the file (touch with future mtime)
        time.sleep(0.1)
        py_file.write_text("class Foo:\n    pass\n\nclass Bar:\n    pass\n")

        # Second seed should reprocess
        result2 = SeedResult()
        await seeder._seed_file(py_file, False, result2)
        assert result2.files_seeded == 1
    finally:
        shutil.rmtree(temp_dir)


# ============================================================================
# Unit Tests: Java extraction
# ============================================================================

def test_java_class_extraction(extractor):
    """Should extract Java classes and interfaces."""
    content = """
public class UserService {
    private final UserRepository repo;
}

public interface UserRepository {
    User findById(String id);
}

public enum UserRole {
    ADMIN, USER, GUEST
}
"""
    entities, _ = extractor.extract_entities_from_file("UserService.java", content)
    names = {e.name for e in entities}
    assert "UserService" in names
    assert "UserRepository" in names
    assert "UserRole" in names


def test_java_method_extraction(extractor):
    """Should extract Java methods."""
    content = """
public class AuthController {
    public ResponseEntity<User> login(String username, String password) {
        return null;
    }

    private void validateToken(String token) {}

    protected static List<Role> getRoles(String userId) {
        return null;
    }
}
"""
    entities, _ = extractor.extract_entities_from_file("AuthController.java", content)
    names = {e.name for e in entities}
    assert "AuthController" in names
    assert "login" in names
    assert "validateToken" in names
    assert "getRoles" in names


def test_java_spring_endpoints(extractor):
    """Should extract Spring API endpoints."""
    content = """
@RestController
public class UserController {
    @GetMapping("/api/users")
    public List<User> getUsers() { return null; }

    @PostMapping("/api/users")
    public User createUser() { return null; }
}
"""
    entities, _ = extractor.extract_entities_from_file("UserController.java", content)
    names = {e.name for e in entities}
    assert "/api/users" in names


# ============================================================================
# Unit Tests: Solidity extraction
# ============================================================================

def test_solidity_contract_extraction(extractor):
    """Should extract Solidity contracts, interfaces, and libraries."""
    content = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract AgentNFT is ERC721 {
    function mint(address to) public {}
    function burn(uint256 tokenId) public {}
    event Transfer(address from, address to, uint256 tokenId);
}

interface IMarketplace {
    function listItem(uint256 tokenId, uint256 price) external;
}

library MathUtils {
    function add(uint256 a, uint256 b) internal pure returns (uint256) {}
}
"""
    entities, _ = extractor.extract_entities_from_file("AgentNFT.sol", content)
    names = {e.name for e in entities}
    assert "AgentNFT" in names
    assert "IMarketplace" in names
    assert "MathUtils" in names
    assert "mint" in names
    assert "burn" in names
    assert "Transfer" in names
    assert "listItem" in names


# ============================================================================
# Unit Tests: Multiline Python patterns
# ============================================================================

def test_multiline_function_signature(extractor):
    """Should extract functions with multiline parameter lists."""
    content = """
def create_order(
    user_id: str,
    items: List[Item],
    discount: float = 0.0,
    shipping_address: Optional[Address] = None,
) -> Order:
    pass
"""
    entities, _ = extractor.extract_entities_from_file("orders.py", content)
    names = {e.name for e in entities}
    assert "create_order" in names


def test_decorated_class(extractor):
    """Should extract class name, not decorator."""
    content = """
@dataclass
@frozen
class OrderItem:
    product_id: str
    quantity: int
"""
    entities, _ = extractor.extract_entities_from_file("models.py", content)
    names = {e.name for e in entities}
    assert "OrderItem" in names
    assert "dataclass" not in names
    assert "frozen" not in names


def test_async_function(extractor):
    """Should extract async functions."""
    content = """
async def fetch_market_data(
    symbol: str,
    timeframe: str = "1h",
) -> MarketData:
    pass
"""
    entities, _ = extractor.extract_entities_from_file("market.py", content)
    names = {e.name for e in entities}
    assert "fetch_market_data" in names


# ============================================================================
# Integration Tests
# ============================================================================

@pytest.mark.asyncio
async def test_seed_then_query_fact_finds_entity(kg, config):
    """Seeded entities should be findable via query_fact tool."""
    from world_model_server.tools import WorldModelTools

    temp_dir = tempfile.mkdtemp()
    try:
        (Path(temp_dir) / "strategy.py").write_text(
            "class StrategyManager:\n    def execute(self): pass\n"
        )

        seeder = ProjectSeeder(temp_dir, kg, config)
        await seeder.seed()

        tools = WorldModelTools(kg, config)
        result = await tools.query_fact("StrategyManager")
        assert result.exists
        assert result.confidence >= 0.8
    finally:
        shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_seed_then_fuzzy_query(kg, config):
    """Fuzzy queries should work on seeded entities."""
    from world_model_server.tools import WorldModelTools

    temp_dir = tempfile.mkdtemp()
    try:
        (Path(temp_dir) / "auth.py").write_text(
            "class AuthenticationService:\n    pass\n"
        )

        seeder = ProjectSeeder(temp_dir, kg, config)
        await seeder.seed()

        tools = WorldModelTools(kg, config)
        result = await tools.query_fact("AuthService")
        assert result.exists
    finally:
        shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_seed_then_module_query(kg, config):
    """Module name queries should find the file and its entities."""
    from world_model_server.tools import WorldModelTools

    temp_dir = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(temp_dir, "services"))
        (Path(temp_dir) / "services" / "paper_trading.py").write_text(
            "class PaperTrader:\n    def trade(self): pass\n"
        )

        seeder = ProjectSeeder(temp_dir, kg, config)
        await seeder.seed()

        tools = WorldModelTools(kg, config)
        result = await tools.query_fact("paper_trading")
        assert result.exists
    finally:
        shutil.rmtree(temp_dir)


# ============================================================================
# E2E Tests
# ============================================================================

@pytest.mark.asyncio
async def test_e2e_multilang_project(kg, config):
    """Seed a project with Python, TypeScript, Solidity, and Java files."""
    temp_dir = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(temp_dir, "src"))
        os.makedirs(os.path.join(temp_dir, "contracts"))

        (Path(temp_dir) / "src" / "auth.py").write_text(
            "class AuthService:\n    async def login(self, token: str): pass\n"
        )
        (Path(temp_dir) / "src" / "api.ts").write_text(
            "export class ApiController {\n    async getUsers(): Promise<User[]> { return []; }\n}\n"
        )
        (Path(temp_dir) / "contracts" / "Token.sol").write_text(
            "contract Token {\n    function mint(address to) public {}\n    event Mint(address to);\n}\n"
        )
        (Path(temp_dir) / "src" / "UserService.java").write_text(
            "public class UserService {\n    public User findById(String id) { return null; }\n}\n"
        )

        seeder = ProjectSeeder(temp_dir, kg, config)
        result = await seeder.seed()

        assert result.files_seeded == 4
        assert result.entities_created > 10

        # Verify each language extracted correctly
        entities = await kg.find_entities(name="AuthService")
        assert len(entities) >= 1

        entities = await kg.find_entities(name="ApiController")
        assert len(entities) >= 1

        entities = await kg.find_entities(name="Token")
        assert len(entities) >= 1

        entities = await kg.find_entities(name="UserService")
        assert len(entities) >= 1
    finally:
        shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_e2e_incremental_workflow(kg, config):
    """Full workflow: seed, modify file, re-seed incrementally."""
    temp_dir = tempfile.mkdtemp()
    try:
        py_file = Path(temp_dir) / "app.py"
        py_file.write_text("class App:\n    pass\n")

        seeder = ProjectSeeder(temp_dir, kg, config)

        # Initial seed
        result1 = await seeder.seed()
        assert result1.files_seeded == 1
        initial_entities = await kg.get_entity_count()

        # Re-seed without changes (should skip)
        result2 = await seeder.seed()
        assert result2.files_seeded == 0
        assert result2.skipped_files == 1

        # Modify file
        time.sleep(0.1)
        py_file.write_text("class App:\n    pass\n\nclass Router:\n    pass\n")

        # Re-seed (should pick up changes)
        result3 = await seeder.seed()
        assert result3.files_seeded == 1
        new_entities = await kg.get_entity_count()
        assert new_entities >= initial_entities
    finally:
        shutil.rmtree(temp_dir)


# ============================================================================
# Smoke Tests
# ============================================================================

@pytest.mark.asyncio
async def test_smoke_kg_initialize(kg):
    """Knowledge graph should initialize without errors."""
    count = await kg.get_entity_count()
    assert count == 0


@pytest.mark.asyncio
async def test_smoke_empty_query(kg):
    """Querying empty KG should return exists=False."""
    result = await kg.query_facts("anything")
    assert not result.exists


@pytest.mark.asyncio
async def test_smoke_fuzzy_empty_kg(kg):
    """Fuzzy search on empty KG should return empty list."""
    results = await kg.find_entities_fuzzy("test", threshold=0.5)
    assert results == []


def test_smoke_extractor_unknown_language(extractor):
    """Unknown file types should return empty entities."""
    entities, imports = extractor.extract_entities_from_file("data.csv", "col1,col2\n1,2\n")
    assert entities == []
    assert imports == []


def test_smoke_extractor_empty_file(extractor):
    """Empty files should return empty entities."""
    entities, imports = extractor.extract_entities_from_file("empty.py", "")
    assert entities == []
    assert imports == []


@pytest.mark.asyncio
async def test_smoke_cli_help():
    """CLI should show help without errors."""
    import subprocess
    result = subprocess.run(
        ["python3", "-m", "world_model_server.cli", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "setup" in result.stdout
    assert "seed" in result.stdout
    assert "query" in result.stdout
    assert "status" in result.stdout
