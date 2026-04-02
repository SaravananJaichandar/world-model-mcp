"""
Auto-seeder for populating the knowledge graph from an existing codebase.

Scans project files, extracts entities (functions, classes, APIs) and
import relationships using regex patterns. No LLM required.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .config import Config
from .extraction import EntityExtractor
from .knowledge_graph import KnowledgeGraph
from .models import Entity, Fact, Relationship

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".sol", ".go", ".rs"}

SKIP_DIRS = {
    "node_modules", "dist", "build", "__pycache__", ".git", ".claude",
    "venv", ".venv", "env", ".env", ".tox", "coverage", ".mypy_cache",
    ".pytest_cache", ".next", ".nuxt", ".eggs", "egg-info", ".ruff_cache",
    "htmlcov", ".coverage", ".hypothesis",
}

MAX_FILE_SIZE = 100 * 1024  # 100KB
MAX_FILES_WALK = 5000  # Cap for manual walk fallback only
# No cap for git ls-files (returns only tracked files)


@dataclass
class SeedResult:
    """Result of a seeding operation."""
    files_scanned: int = 0
    files_seeded: int = 0
    entities_created: int = 0
    relationships_created: int = 0
    duration_seconds: float = 0.0
    skipped_files: int = 0
    errors: List[str] = field(default_factory=list)


class ProjectSeeder:
    """Scans a project codebase and populates the knowledge graph."""

    def __init__(self, project_dir: str, kg: KnowledgeGraph, config: Config):
        self.project_dir = Path(project_dir).resolve()
        self.kg = kg
        self.config = config
        self.extractor = EntityExtractor(config)

    async def seed(self, force: bool = False) -> SeedResult:
        """
        Scan the project and populate the knowledge graph.

        Args:
            force: Re-seed files that are already in the graph.

        Returns:
            SeedResult with statistics.
        """
        start = time.time()
        result = SeedResult()

        files, used_git = self._collect_files()
        result.files_scanned = len(files)

        # Only cap when using walk fallback (git ls-files already returns only tracked files)
        if not used_git and len(files) > MAX_FILES_WALK:
            logger.warning(
                f"Project has {len(files)} supported files, processing first {MAX_FILES_WALK} "
                f"(source directories prioritized). Use git to remove the cap."
            )
            files = files[:MAX_FILES_WALK]

        logger.info(f"Collected {len(files)} files to seed (git={used_git})")

        sem = asyncio.Semaphore(20)

        async def process(fp: Path) -> Tuple[int, int]:
            async with sem:
                return await self._seed_file(fp, force, result)

        tasks = [process(f) for f in files]
        counts = await asyncio.gather(*tasks)

        for entity_count, rel_count in counts:
            result.entities_created += entity_count
            result.relationships_created += rel_count

        result.duration_seconds = round(time.time() - start, 2)

        logger.info(
            f"Seeding complete: {result.files_seeded} files, "
            f"{result.entities_created} entities, "
            f"{result.relationships_created} relationships "
            f"in {result.duration_seconds}s"
        )
        return result

    def _collect_files(self) -> Tuple[List[Path], bool]:
        """Collect project files, respecting .gitignore. Returns (files, used_git)."""
        git_dir = self.project_dir / ".git"

        if git_dir.exists():
            files = self._collect_via_git()
            if files is not None:
                return files, True

        return self._collect_via_walk(), False

    def _collect_via_git(self) -> Optional[List[Path]]:
        """Use git ls-files for precise .gitignore handling. Returns None on failure."""
        import subprocess

        try:
            proc = subprocess.run(
                ["git", "ls-files", "--cached"],
                capture_output=True, text=True, cwd=str(self.project_dir),
                timeout=30,
            )
            if proc.returncode != 0:
                logger.warning("git ls-files failed, falling back to walk")
                return None

            files = []
            for line in proc.stdout.strip().split("\n"):
                if not line:
                    continue
                path = self.project_dir / line
                if self._is_supported(path):
                    files.append(path)

            return self._prioritize_files(files)

        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("git not available, falling back to walk")
            return None

    def _collect_via_walk(self) -> List[Path]:
        """Manual directory walk with hardcoded skip patterns."""
        files = []
        for root, dirs, filenames in os.walk(self.project_dir):
            # Prune skip directories in-place
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            for fname in filenames:
                path = Path(root) / fname
                if self._is_supported(path):
                    files.append(path)
        return self._prioritize_files(files)

    def _prioritize_files(self, files: List[Path]) -> List[Path]:
        """Sort files so source code directories come before dependencies."""
        priority_prefixes = ("src/", "app/", "lib/", "services/", "packages/", "core/", "api/")

        def sort_key(path: Path) -> tuple:
            rel = str(path.relative_to(self.project_dir))
            # Priority 0: source directories
            for prefix in priority_prefixes:
                if rel.startswith(prefix):
                    return (0, rel)
            # Priority 1: root-level files
            if "/" not in rel:
                return (1, rel)
            # Priority 2: everything else
            return (2, rel)

        return sorted(files, key=sort_key)

    def _is_supported(self, path: Path) -> bool:
        """Check if a file should be processed."""
        if path.suffix not in SUPPORTED_EXTENSIONS:
            return False
        try:
            if path.stat().st_size > MAX_FILE_SIZE:
                return False
        except OSError:
            return False
        return True

    def _is_binary(self, content: bytes) -> bool:
        """Check if content appears to be binary."""
        return b"\x00" in content[:512]

    async def _seed_file(
        self, file_path: Path, force: bool, result: SeedResult
    ) -> Tuple[int, int]:
        """Process a single file. Returns (entity_count, relationship_count)."""
        rel_path = str(file_path.resolve().relative_to(self.project_dir))

        # Skip already-seeded files unless force
        if not force and await self.kg.entity_exists_for_file(rel_path):
            result.skipped_files += 1
            return (0, 0)

        try:
            raw = file_path.read_bytes()
            if self._is_binary(raw):
                result.skipped_files += 1
                return (0, 0)

            content = raw.decode("utf-8", errors="ignore")
        except OSError as e:
            result.errors.append(f"Failed to read {rel_path}: {e}")
            result.skipped_files += 1
            return (0, 0)

        # Create file entity
        file_entity = Entity(
            entity_type="file",
            name=file_path.name,
            file_path=rel_path,
        )
        await self.kg.create_entity(file_entity)

        # Extract entities and imports
        entities, import_data = self.extractor.extract_entities_from_file(rel_path, content)

        entity_count = 1  # file entity
        for entity in entities:
            await self.kg.create_entity(entity)
            entity_count += 1

            # Create a searchable fact for each entity so query_fact can find it
            entity_fact = Fact(
                fact_text=f"{entity.entity_type} {entity.name} exists in {rel_path}",
                evidence_type="source_code",
                evidence_path=rel_path,
                confidence=0.8,
                status="canonical",
                entity_ids=[entity.id],
            )
            await self.kg.create_fact(entity_fact)

        # Create import relationships
        rel_count = 0
        for imp in import_data:
            rel = Relationship(
                source_entity_id=file_entity.id,
                target_entity_id=imp["imported_module"],
                relationship_type="imports",
            )
            await self.kg.create_relationship(rel)
            rel_count += 1

        # Create seeded summary fact
        fact = Fact(
            fact_text=f"File {rel_path} seeded with {entity_count} entities",
            evidence_type="source_code",
            evidence_path=rel_path,
            confidence=0.7,
            status="canonical",
        )
        await self.kg.create_fact(fact)

        result.files_seeded += 1
        return (entity_count, rel_count)
