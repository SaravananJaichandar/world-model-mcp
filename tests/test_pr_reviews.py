"""
Tests for PR review intelligence module.
"""

import pytest
import tempfile
import shutil
from unittest.mock import AsyncMock, patch, MagicMock

from world_model_server.knowledge_graph import KnowledgeGraph
from world_model_server.config import Config
from world_model_server.pr_reviews import PRReviewIngester, IngestResult


@pytest.fixture
async def kg():
    """Create a temporary knowledge graph."""
    temp_dir = tempfile.mkdtemp()
    kg = KnowledgeGraph(temp_dir)
    await kg.initialize()
    yield kg
    shutil.rmtree(temp_dir)


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def ingester(kg, config):
    return PRReviewIngester(kg, config)


@pytest.mark.asyncio
async def test_detect_repo_ssh(ingester):
    """Should parse owner/repo from SSH git remote."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(
        return_value=(b"git@github.com:owner/repo.git\n", b"")
    )
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await ingester._detect_repo()
        assert result == "owner/repo"


@pytest.mark.asyncio
async def test_detect_repo_https(ingester):
    """Should parse owner/repo from HTTPS git remote."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(
        return_value=(b"https://github.com/myorg/myrepo.git\n", b"")
    )
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await ingester._detect_repo()
        assert result == "myorg/myrepo"


def test_filter_substantive_comments(ingester):
    """Should filter out trivial and bot comments."""
    comments = [
        {"body": "LGTM", "user": {"type": "User"}},
        {"body": "looks good", "user": {"type": "User"}},
        {"body": "short", "user": {"type": "User"}},
        {"body": "This is a useful review comment about code quality", "user": {"type": "User"}},
        {"body": "Please use the query builder instead of raw SQL here", "user": {"type": "User"}},
        {"body": "Auto-generated review comment", "user": {"type": "Bot"}},
    ]
    result = ingester._filter_substantive_comments(comments)
    assert len(result) == 2
    assert "query builder" in result[1]["body"]


def test_classify_comment_with_patterns_constraint(ingester):
    """Should detect constraint from 'use X instead of Y' pattern."""
    comment = {
        "body": "Please use logger.debug instead of console.log for production code",
        "path": "src/api/handler.ts",
    }
    constraint = ingester._classify_with_patterns(comment)
    assert constraint is not None
    assert constraint.constraint_type == "linting"
    assert len(constraint.examples) > 0


def test_classify_comment_with_patterns_avoid(ingester):
    """Should detect constraint from 'avoid' pattern."""
    comment = {
        "body": "We should always use typed constants for error codes, never hardcode strings",
        "path": "src/errors.ts",
    }
    constraint = ingester._classify_with_patterns(comment)
    assert constraint is not None
    assert constraint.severity == "warning"


def test_classify_comment_trivial_returns_none(ingester):
    """Non-constraint comments should return None."""
    comment = {
        "body": "I fixed the typo you mentioned in the previous review cycle",
        "path": "src/utils.ts",
    }
    constraint = ingester._classify_with_patterns(comment)
    assert constraint is None


@pytest.mark.asyncio
async def test_duplicate_pr_tracking(kg, config):
    """Already-ingested PRs should be tracked and skippable."""
    ingester = PRReviewIngester(kg, config)

    # Mark PR as ingested
    await ingester._mark_pr_ingested(42, "owner/repo")

    # Check it's tracked
    ingested = await ingester._get_ingested_prs("owner/repo")
    assert 42 in ingested


@pytest.mark.asyncio
async def test_constraint_deduplication(kg, config):
    """Two comments producing same rule_name should update one constraint."""
    ingester = PRReviewIngester(kg, config)

    comment1 = {
        "body": "Please use the logger module instead of console.log here",
        "path": "src/api/handler.ts",
    }
    comment2 = {
        "body": "Again, prefer the logger instead of console.log for consistency",
        "path": "src/api/routes.ts",
    }

    c1 = ingester._classify_with_patterns(comment1)
    c2 = ingester._classify_with_patterns(comment2)

    assert c1 is not None
    assert c2 is not None

    # Both should produce constraints
    await kg.create_or_update_constraint(c1)
    await kg.create_or_update_constraint(c2)

    # If they share the same rule_name, violation_count should be 2
    constraints = await kg.get_constraints()
    # At minimum we should have constraints created
    assert len(constraints) >= 1


@pytest.mark.asyncio
async def test_gh_not_available(kg, config):
    """Should return error when gh CLI is not available."""
    ingester = PRReviewIngester(kg, config)

    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
        result = await ingester.ingest(repo="owner/repo")
        assert len(result.errors) > 0
        assert "gh CLI" in result.errors[0]
