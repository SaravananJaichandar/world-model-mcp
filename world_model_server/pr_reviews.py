"""
PR Review Intelligence: ingests GitHub PR review comments as constraints.

Pulls review comments via `gh api`, classifies them as reusable coding
rules using LLM or keyword patterns, and stores them in the knowledge graph.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .config import Config
from .knowledge_graph import KnowledgeGraph
from .models import Constraint, Fact

logger = logging.getLogger(__name__)

TRIVIAL_PATTERNS = re.compile(
    r"^(lgtm|looks good|approved|nice|thanks|\+1|ship it|nit\s*$|great|done|fixed)",
    re.IGNORECASE,
)

CONSTRAINT_KEYWORDS = re.compile(
    r"(instead of|rather than|prefer|should use|don't use|avoid|always|never|must|convention|pattern)",
    re.IGNORECASE,
)

CONSTRAINT_TYPE_MAP = {
    "import": "architecture",
    "require": "architecture",
    "module": "architecture",
    "naming": "style",
    "case": "style",
    "convention": "style",
    "format": "style",
    "indent": "style",
    "test": "testing",
    "coverage": "testing",
    "assert": "testing",
    "spec": "testing",
    "console": "linting",
    "log": "linting",
    "debug": "linting",
    "lint": "linting",
    "type": "style",
    "error": "linting",
    "exception": "linting",
}


@dataclass
class IngestResult:
    """Result of PR review ingestion."""
    prs_scanned: int = 0
    prs_skipped: int = 0
    comments_analyzed: int = 0
    constraints_created: int = 0
    constraints_updated: int = 0
    duration_seconds: float = 0.0
    errors: List[str] = field(default_factory=list)


class PRReviewIngester:
    """Ingests GitHub PR review comments into the knowledge graph as constraints."""

    def __init__(self, kg: KnowledgeGraph, config: Config):
        self.kg = kg
        self.config = config
        self.client = None
        if config.anthropic_api_key:
            from anthropic import AsyncAnthropic
            self.client = AsyncAnthropic(api_key=config.anthropic_api_key)

    async def ingest(
        self, repo: Optional[str] = None, count: int = 10
    ) -> IngestResult:
        """
        Ingest PR review comments and create constraints.

        Args:
            repo: GitHub repo (owner/repo). Auto-detected if None.
            count: Number of recent PRs to scan.

        Returns:
            IngestResult with statistics.
        """
        start = time.time()
        result = IngestResult()

        # Check gh CLI availability
        if not await self._check_gh():
            result.errors.append("gh CLI not found or not authenticated. Install: https://cli.github.com")
            return result

        # Detect repo if not provided
        if repo is None:
            repo = await self._detect_repo()
            if repo is None:
                result.errors.append("Could not detect repo from git remote. Provide repo parameter.")
                return result

        logger.info(f"Ingesting PR reviews from {repo} (count={count})")

        # Get already-ingested PR numbers
        ingested = await self._get_ingested_prs(repo)

        # Fetch recent PRs
        prs = await self._fetch_recent_prs(repo, count)
        result.prs_scanned = len(prs)

        for pr in prs:
            pr_number = pr.get("number")
            if pr_number in ingested:
                result.prs_skipped += 1
                continue

            comments = await self._fetch_pr_comments(repo, pr_number)
            substantive = self._filter_substantive_comments(comments)

            for comment in substantive:
                result.comments_analyzed += 1
                constraint = await self._classify_comment(comment)
                if constraint:
                    cid = await self.kg.create_or_update_constraint(constraint)
                    # Check if it was a new or updated constraint
                    existing = await self.kg.get_constraints()
                    matched = [c for c in existing if c.id == cid]
                    if matched and matched[0].violation_count > 1:
                        result.constraints_updated += 1
                    else:
                        result.constraints_created += 1

            await self._mark_pr_ingested(pr_number, repo)

        result.duration_seconds = round(time.time() - start, 2)
        logger.info(
            f"Ingestion complete: {result.prs_scanned} PRs, "
            f"{result.comments_analyzed} comments, "
            f"{result.constraints_created} new constraints"
        )
        return result

    async def _check_gh(self) -> bool:
        """Check if gh CLI is available."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return proc.returncode == 0
        except FileNotFoundError:
            return False

    async def _detect_repo(self) -> Optional[str]:
        """Detect owner/repo from git remote."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "remote", "get-url", "origin",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None

            url = stdout.decode().strip()

            # SSH: git@github.com:owner/repo.git
            ssh_match = re.match(r"git@github\.com:(.+?)(?:\.git)?$", url)
            if ssh_match:
                return ssh_match.group(1)

            # HTTPS: https://github.com/owner/repo.git
            https_match = re.match(r"https://github\.com/(.+?)(?:\.git)?$", url)
            if https_match:
                return https_match.group(1)

            return None
        except FileNotFoundError:
            return None

    async def _fetch_recent_prs(self, repo: str, count: int) -> List[Dict]:
        """Fetch recent closed/merged PRs."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "api", f"repos/{repo}/pulls",
                "-f", "state=closed",
                "-f", "sort=updated",
                "-f", "direction=desc",
                "-f", f"per_page={count}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(f"gh api failed: {stderr.decode()}")
                return []

            return json.loads(stdout.decode())
        except Exception as e:
            logger.error(f"Failed to fetch PRs: {e}")
            return []

    async def _fetch_pr_comments(self, repo: str, pr_number: int) -> List[Dict]:
        """Fetch inline review comments for a PR."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "api", f"repos/{repo}/pulls/{pr_number}/comments",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"Failed to fetch comments for PR #{pr_number}")
                return []

            return json.loads(stdout.decode())
        except Exception as e:
            logger.error(f"Failed to fetch PR comments: {e}")
            return []

    def _filter_substantive_comments(self, comments: List[Dict]) -> List[Dict]:
        """Filter out trivial and bot comments."""
        result = []
        for comment in comments:
            body = comment.get("body", "").strip()

            # Skip short comments
            if len(body) < 20:
                continue

            # Skip trivial patterns
            if TRIVIAL_PATTERNS.match(body):
                continue

            # Skip bot comments
            user = comment.get("user", {})
            if user.get("type") == "Bot":
                continue

            result.append(comment)
        return result

    async def _classify_comment(self, comment: Dict) -> Optional[Constraint]:
        """Classify a review comment as a constraint using LLM or patterns."""
        if self.client:
            return await self._classify_with_llm(comment)
        return self._classify_with_patterns(comment)

    async def _classify_with_llm(self, comment: Dict) -> Optional[Constraint]:
        """Use LLM to classify a review comment."""
        body = comment.get("body", "")
        path = comment.get("path", "")
        diff_hunk = comment.get("diff_hunk", "")

        prompt = f"""Analyze this PR review comment and determine if it expresses a reusable coding rule.

File: {path}
Comment: {body[:500]}
Diff context: {diff_hunk[:300]}

If this IS a reusable rule, respond in JSON:
{{"is_constraint": true, "constraint_type": "linting|architecture|style|testing|api_contract", "rule_name": "kebab-case-identifier", "description": "clear description", "severity": "error|warning|info", "avoid": "what to avoid", "prefer": "what to prefer"}}

If this is NOT a reusable rule (just a one-off fix), respond:
{{"is_constraint": false}}"""

        try:
            response = await self.client.messages.create(
                model=self.config.extraction_model,
                max_tokens=512,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            data = json.loads(text)

            if not data.get("is_constraint"):
                return None

            file_pattern = self._infer_file_pattern(path)
            return Constraint(
                constraint_type=data.get("constraint_type", "style"),
                rule_name=data.get("rule_name", "pr-review-rule"),
                file_pattern=file_pattern,
                description=data.get("description", body[:200]),
                violation_count=1,
                examples=[{
                    "incorrect": data.get("avoid", ""),
                    "correct": data.get("prefer", ""),
                }],
                severity=data.get("severity", "warning"),
            )
        except Exception as e:
            logger.warning(f"LLM classification failed: {e}, falling back to patterns")
            return self._classify_with_patterns(comment)

    def _classify_with_patterns(self, comment: Dict) -> Optional[Constraint]:
        """Classify a review comment using keyword patterns."""
        body = comment.get("body", "")
        path = comment.get("path", "")

        # Strip markdown code blocks before matching
        clean_body = re.sub(r"```[\s\S]*?```", "", body)

        # Check if it matches constraint keywords
        if not CONSTRAINT_KEYWORDS.search(clean_body):
            return None

        # Determine constraint type from keywords
        constraint_type = "style"  # default
        body_lower = clean_body.lower()
        for keyword, ctype in CONSTRAINT_TYPE_MAP.items():
            if keyword in body_lower:
                constraint_type = ctype
                break

        # Generate a rule name from the comment
        rule_name = self._generate_rule_name(clean_body)

        # Try to extract avoid/prefer patterns
        avoid, prefer = self._extract_patterns(clean_body)

        file_pattern = self._infer_file_pattern(path)

        return Constraint(
            constraint_type=constraint_type,
            rule_name=rule_name,
            file_pattern=file_pattern,
            description=clean_body[:200].strip(),
            violation_count=1,
            examples=[{"incorrect": avoid, "correct": prefer}] if avoid or prefer else [],
            severity="warning",
        )

    def _generate_rule_name(self, body: str) -> str:
        """Generate a kebab-case rule name from comment text."""
        # Take first meaningful phrase (up to 5 words)
        words = re.findall(r"\b[a-z]+\b", body.lower())[:5]
        if not words:
            return "pr-review-rule"
        return "-".join(words)

    def _extract_patterns(self, body: str) -> tuple:
        """Extract avoid/prefer patterns from comment text."""
        avoid = ""
        prefer = ""

        # "use X instead of Y" pattern
        match = re.search(r"use\s+(.+?)\s+instead\s+of\s+(.+?)(?:\.|$)", body, re.IGNORECASE)
        if match:
            prefer = match.group(1).strip()
            avoid = match.group(2).strip()
            return avoid, prefer

        # "prefer X over Y" pattern
        match = re.search(r"prefer\s+(.+?)\s+over\s+(.+?)(?:\.|$)", body, re.IGNORECASE)
        if match:
            prefer = match.group(1).strip()
            avoid = match.group(2).strip()
            return avoid, prefer

        # "don't use X" / "avoid X" pattern
        match = re.search(r"(?:don't use|avoid)\s+(.+?)(?:\.|,|$)", body, re.IGNORECASE)
        if match:
            avoid = match.group(1).strip()

        # "should use X" / "always use X" pattern
        match = re.search(r"(?:should use|always use)\s+(.+?)(?:\.|,|$)", body, re.IGNORECASE)
        if match:
            prefer = match.group(1).strip()

        return avoid, prefer

    def _infer_file_pattern(self, file_path: str) -> Optional[str]:
        """Infer a glob pattern from a file path."""
        import os
        if not file_path:
            return None

        dir_path = os.path.dirname(file_path)
        _, ext = os.path.splitext(file_path)

        if not ext:
            return None

        if dir_path.startswith("src/"):
            return f"src/**/*{ext}"
        elif dir_path.startswith("tests/") or dir_path.startswith("test/"):
            return f"tests/**/*{ext}"
        else:
            return f"**/*{ext}"

    async def _get_ingested_prs(self, repo: str) -> Set[int]:
        """Get set of already-ingested PR numbers."""
        ingested = set()
        try:
            facts = await self.kg.query_facts("review comments ingested", current_only=True)
            for fact in facts.facts:
                # Parse PR number from fact text: "PR #123 review comments ingested from owner/repo"
                match = re.search(r"PR #(\d+)", fact.fact_text)
                if match and repo in fact.fact_text:
                    ingested.add(int(match.group(1)))
        except Exception:
            pass  # If query fails, re-ingest is acceptable
        return ingested

    async def _mark_pr_ingested(self, pr_number: int, repo: str):
        """Record that a PR has been ingested."""
        fact = Fact(
            fact_text=f"PR #{pr_number} review comments ingested from {repo}",
            evidence_type="session",
            evidence_path=f"github:{repo}/pull/{pr_number}",
            confidence=1.0,
            status="canonical",
        )
        await self.kg.create_fact(fact)
