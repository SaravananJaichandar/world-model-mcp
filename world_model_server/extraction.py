"""
LLM-powered entity and fact extraction from code changes.

Uses Claude Haiku for fast, cost-effective extraction of entities (files, APIs,
functions, classes) and facts (assertions about the codebase) from file edits.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic
from .models import Entity, Fact, Constraint, Relationship
from .config import Config

logger = logging.getLogger(__name__)


class EntityExtractor:
    """Extract entities and facts from code changes using LLM."""

    def __init__(self, config: Config):
        self.config = config
        if config.anthropic_api_key:
            self.client = AsyncAnthropic(api_key=config.anthropic_api_key)
        else:
            logger.warning("No Anthropic API key found - extraction will use fallback patterns")
            self.client = None

    async def extract_from_file_edit(
        self,
        file_path: str,
        old_content: str,
        new_content: str,
        reasoning: Optional[str] = None,
    ) -> Tuple[List[Entity], List[Fact]]:
        """
        Extract entities and facts from a file edit.

        Args:
            file_path: Path to the file
            old_content: Content before edit
            new_content: Content after edit
            reasoning: Why the edit was made (if available)

        Returns:
            Tuple of (entities, facts)
        """
        # Compute diff
        diff = self._compute_diff(old_content, new_content)

        if self.client:
            return await self._extract_with_llm(file_path, diff, reasoning)
        else:
            return self._extract_with_patterns(file_path, new_content, diff)

    def _compute_diff(self, old: str, new: str) -> str:
        """Compute a simple unified diff."""
        old_lines = old.split('\n')
        new_lines = new.split('\n')

        diff_lines = []
        for i, (old_line, new_line) in enumerate(zip(old_lines, new_lines), 1):
            if old_line != new_line:
                diff_lines.append(f"- {old_line}")
                diff_lines.append(f"+ {new_line}")

        # Handle added lines
        if len(new_lines) > len(old_lines):
            for line in new_lines[len(old_lines):]:
                diff_lines.append(f"+ {line}")

        return '\n'.join(diff_lines)

    async def _extract_with_llm(
        self, file_path: str, diff: str, reasoning: Optional[str]
    ) -> Tuple[List[Entity], List[Fact]]:
        """Extract using Claude Haiku."""
        prompt = f"""Analyze this code change and extract:

1. **Entities** (things that exist): APIs, functions, classes, constants
2. **Facts** (assertions about the codebase): what's true about the code

File: {file_path}
Reasoning: {reasoning or 'Not specified'}

Diff:
```
{diff[:2000]}  # Limit to 2000 chars
```

Respond in JSON format:
{{
  "entities": [
    {{"type": "api|function|class|constant", "name": "...", "signature": "..."}}
  ],
  "facts": [
    {{"assertion": "...", "evidence": "line numbers or description"}}
  ]
}}

Focus on:
- New APIs or functions added
- Changed function signatures
- New constraints or patterns (e.g., "always use X instead of Y")
- Architectural decisions
"""

        try:
            response = await self.client.messages.create(
                model=self.config.extraction_model,
                max_tokens=1024,
                temperature=0.0,  # Deterministic
                messages=[{"role": "user", "content": prompt}],
            )

            # Parse response
            import json

            result_text = response.content[0].text
            # Extract JSON from markdown code blocks if present
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0]

            result = json.loads(result_text)

            # Convert to our models
            entities = []
            for e in result.get("entities", []):
                entity = Entity(
                    entity_type=e["type"],
                    name=e["name"],
                    file_path=file_path,
                    signature=e.get("signature"),
                    metadata={"extracted_from": "llm", "reasoning": reasoning},
                )
                entities.append(entity)

            facts = []
            for f in result.get("facts", []):
                fact = Fact(
                    fact_text=f["assertion"],
                    valid_at=datetime.now(),
                    status="canonical",
                    evidence_type="source_code",
                    evidence_path=f"{file_path}:{f.get('evidence', 'unknown')}",
                    confidence=0.9,  # LLM-extracted facts have 0.9 confidence
                )
                facts.append(fact)

            logger.info(
                f"LLM extracted {len(entities)} entities and {len(facts)} facts from {file_path}"
            )
            return entities, facts

        except Exception as e:
            logger.error(f"LLM extraction failed: {e}, falling back to patterns")
            return self._extract_with_patterns(file_path, "", diff)

    def _extract_with_patterns(
        self, file_path: str, content: str, diff: str
    ) -> Tuple[List[Entity], List[Fact]]:
        """Fallback: Extract using regex patterns."""
        entities = []
        facts = []

        # Detect language
        lang = self._detect_language(file_path)

        if lang == "typescript" or lang == "javascript":
            entities.extend(self._extract_typescript_entities(file_path, content, diff))
        elif lang == "python":
            entities.extend(self._extract_python_entities(file_path, content, diff))

        # Extract generic facts from diff
        if "logger" in diff and "console.log" in diff:
            facts.append(
                Fact(
                    fact_text="Project uses logger instead of console.log",
                    valid_at=datetime.now(),
                    status="corroborated",
                    evidence_type="source_code",
                    evidence_path=file_path,
                )
            )

        logger.info(
            f"Pattern-based extraction found {len(entities)} entities and {len(facts)} facts"
        )
        return entities, facts

    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension."""
        if file_path.endswith((".ts", ".tsx")):
            return "typescript"
        elif file_path.endswith((".js", ".jsx")):
            return "javascript"
        elif file_path.endswith(".py"):
            return "python"
        elif file_path.endswith(".go"):
            return "go"
        elif file_path.endswith((".rs")):
            return "rust"
        elif file_path.endswith(".sol"):
            return "solidity"
        return "unknown"

    def _extract_typescript_entities(
        self, file_path: str, content: str, diff: str
    ) -> List[Entity]:
        """Extract entities from TypeScript/JavaScript code."""
        entities = []

        # Function declarations
        func_pattern = r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\((.*?)\)"
        for match in re.finditer(func_pattern, diff):
            entities.append(
                Entity(
                    entity_type="function",
                    name=match.group(1),
                    file_path=file_path,
                    signature=f"function {match.group(1)}({match.group(2)})",
                )
            )

        # Arrow functions
        arrow_pattern = r"(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\((.*?)\)\s*=>"
        for match in re.finditer(arrow_pattern, diff):
            entities.append(
                Entity(
                    entity_type="function",
                    name=match.group(1),
                    file_path=file_path,
                    signature=f"const {match.group(1)} = ({match.group(2)}) =>",
                )
            )

        # API endpoints (Express-style)
        api_pattern = r"app\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]"
        for match in re.finditer(api_pattern, diff):
            method = match.group(1).upper()
            path = match.group(2)
            entities.append(
                Entity(
                    entity_type="api",
                    name=f"{method} {path}",
                    file_path=file_path,
                    signature=f"app.{match.group(1)}('{path}')",
                )
            )

        # Classes
        class_pattern = r"(?:export\s+)?class\s+(\w+)"
        for match in re.finditer(class_pattern, diff):
            name = match.group(1)
            if name not in [e.name for e in entities]:
                entities.append(
                    Entity(
                        entity_type="class", name=name, file_path=file_path
                    )
                )

        return entities

    def _extract_python_entities(self, file_path: str, content: str, diff: str) -> List[Entity]:
        """Extract entities from Python code."""
        entities = []
        seen_names = set()

        # Function definitions (supports multiline signatures)
        func_pattern = r"(?:async\s+)?def\s+(\w+)\s*\("
        for match in re.finditer(func_pattern, diff):
            name = match.group(1)
            if name not in seen_names:
                seen_names.add(name)
                entities.append(
                    Entity(
                        entity_type="function",
                        name=name,
                        file_path=file_path,
                        signature=f"def {name}(...)",
                    )
                )

        # Class definitions (handles decorators above the class line)
        class_pattern = r"^class\s+(\w+)"
        for match in re.finditer(class_pattern, diff, re.MULTILINE):
            name = match.group(1)
            if name not in seen_names:
                seen_names.add(name)
                entities.append(
                    Entity(
                        entity_type="class",
                        name=name,
                        file_path=file_path,
                        signature=f"class {name}",
                    )
                )

        # API routes (Flask/FastAPI)
        route_pattern = r"@(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]"
        for match in re.finditer(route_pattern, diff):
            method = match.group(1).upper()
            path = match.group(2)
            entities.append(
                Entity(
                    entity_type="api",
                    name=f"{method} {path}",
                    file_path=file_path,
                    signature=f"@app.{match.group(1)}('{path}')",
                )
            )

        return entities

    def extract_entities_from_file(
        self, file_path: str, content: str
    ) -> Tuple[List[Entity], List["Relationship"]]:
        """
        Extract entities and import relationships from a full file.

        Used by the auto-seeder to populate the knowledge graph from existing code.
        Pattern-based only (no LLM) to work without an API key.

        Returns:
            Tuple of (entities, relationships as dicts with source/target info)
        """
        entities = []
        import_data = []

        lang = self._detect_language(file_path)

        # Extract entities by passing content as the search text
        if lang in ("typescript", "javascript"):
            entities.extend(self._extract_typescript_entities(file_path, content, content))
            import_data.extend(self._extract_typescript_imports(file_path, content))
        elif lang == "python":
            entities.extend(self._extract_python_entities(file_path, content, content))
            import_data.extend(self._extract_python_imports(file_path, content))
        elif lang == "solidity":
            entities.extend(self._extract_solidity_entities(file_path, content))

        return entities, import_data

    def _extract_solidity_entities(self, file_path: str, content: str) -> List[Entity]:
        """Extract entities from Solidity code."""
        entities = []
        seen_names = set()

        # Contracts, interfaces, libraries
        contract_pattern = r"^(?:abstract\s+)?(?:contract|interface|library)\s+(\w+)"
        for match in re.finditer(contract_pattern, content, re.MULTILINE):
            name = match.group(1)
            if name not in seen_names:
                seen_names.add(name)
                entities.append(
                    Entity(entity_type="class", name=name, file_path=file_path, signature=match.group(0).strip())
                )

        # Functions
        func_pattern = r"^\s+function\s+(\w+)\s*\("
        for match in re.finditer(func_pattern, content, re.MULTILINE):
            name = match.group(1)
            if name not in seen_names:
                seen_names.add(name)
                entities.append(
                    Entity(entity_type="function", name=name, file_path=file_path, signature=f"function {name}(...)")
                )

        # Events
        event_pattern = r"^\s+event\s+(\w+)\s*\("
        for match in re.finditer(event_pattern, content, re.MULTILINE):
            name = match.group(1)
            if name not in seen_names:
                seen_names.add(name)
                entities.append(
                    Entity(entity_type="api", name=name, file_path=file_path, signature=f"event {name}(...)")
                )

        return entities

    def _extract_python_imports(self, file_path: str, content: str) -> List[Dict]:
        """Extract import statements from Python code."""
        imports = []

        # import os, import json
        for match in re.finditer(r"^import\s+([\w.]+)", content, re.MULTILINE):
            imports.append({
                "source_file": file_path,
                "imported_module": match.group(1),
            })

        # from pathlib import Path
        for match in re.finditer(r"^from\s+([\w.]+)\s+import", content, re.MULTILINE):
            module = match.group(1)
            if not module.startswith("."):
                imports.append({
                    "source_file": file_path,
                    "imported_module": module,
                })

        return imports

    def _extract_typescript_imports(self, file_path: str, content: str) -> List[Dict]:
        """Extract import statements from TypeScript/JavaScript code."""
        imports = []

        # ES6: import { foo } from './bar'
        for match in re.finditer(r"import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]", content):
            imports.append({
                "source_file": file_path,
                "imported_module": match.group(1),
            })

        # CommonJS: require('./bar')
        for match in re.finditer(r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", content):
            imports.append({
                "source_file": file_path,
                "imported_module": match.group(1),
            })

        return imports

    async def infer_constraint_from_correction(
        self, claude_content: str, user_content: str, file_path: str
    ) -> Optional[Constraint]:
        """
        Infer a constraint from a user correction.

        Uses LLM to understand the pattern and generate a reusable constraint.
        """
        if not self.client:
            return self._infer_constraint_with_patterns(claude_content, user_content, file_path)

        prompt = f"""A user corrected Claude's code. Infer a constraint/pattern from this correction.

File: {file_path}

Claude wrote:
```
{claude_content[:500]}
```

User corrected to:
```
{user_content[:500]}
```

Analyze the correction and respond in JSON:
{{
  "constraint_type": "linting|architecture|style|testing",
  "rule_name": "short-identifier",
  "description": "clear description of the rule",
  "pattern": {{
    "avoid": "what to avoid",
    "prefer": "what to prefer instead"
  }}
}}

Common patterns:
- Logging: console.log → logger.debug
- Imports: relative → absolute paths
- Naming: camelCase → snake_case
- Error handling: throw → return error
"""

        try:
            response = await self.client.messages.create(
                model=self.config.extraction_model,
                max_tokens=512,
                temperature=0.0,
                messages=[{"role": "user", "content": prompt}],
            )

            import json

            result_text = response.content[0].text
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0]

            result = json.loads(result_text)

            constraint = Constraint(
                constraint_type=result["constraint_type"],
                rule_name=result["rule_name"],
                file_pattern=self._infer_file_pattern(file_path),
                description=result["description"],
                violation_count=1,
                last_violated=datetime.now(),
                examples=[
                    {
                        "incorrect": result["pattern"]["avoid"],
                        "correct": result["pattern"]["prefer"],
                    }
                ],
                severity="error",
            )

            logger.info(f"LLM inferred constraint: {constraint.rule_name}")
            return constraint

        except Exception as e:
            logger.error(f"LLM constraint inference failed: {e}")
            return self._infer_constraint_with_patterns(claude_content, user_content, file_path)

    def _infer_constraint_with_patterns(
        self, claude_content: str, user_content: str, file_path: str
    ) -> Optional[Constraint]:
        """Fallback: Infer constraints using pattern matching."""
        # console.log → logger.*
        if "console.log" in claude_content and "logger." in user_content:
            return Constraint(
                constraint_type="linting",
                rule_name="no-console",
                file_pattern=self._infer_file_pattern(file_path),
                description="Use logger instead of console.log",
                violation_count=1,
                examples=[{"incorrect": "console.log(...)", "correct": "logger.debug(...)"}],
                severity="error",
            )

        # var → const/let
        if "var " in claude_content and ("const " in user_content or "let " in user_content):
            return Constraint(
                constraint_type="linting",
                rule_name="no-var",
                file_pattern=self._infer_file_pattern(file_path),
                description="Use const or let instead of var",
                violation_count=1,
                examples=[{"incorrect": "var x = 1", "correct": "const x = 1"}],
                severity="error",
            )

        return None

    def _infer_file_pattern(self, file_path: str) -> str:
        """Infer a file pattern from a specific file path."""
        # Extract directory and extension
        import os

        dir_path = os.path.dirname(file_path)
        _, ext = os.path.splitext(file_path)

        if dir_path.startswith("src/"):
            return f"src/**/*{ext}"
        elif dir_path.startswith("tests/"):
            return f"tests/**/*{ext}"
        else:
            return f"**/*{ext}"
