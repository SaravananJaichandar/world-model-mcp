"""
Integration with external linters (ESLint, Pylint, etc.)

Runs linters on proposed code changes to validate against project rules.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class LinterIntegration:
    """Integration with external linting tools."""

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir)

    async def validate_with_eslint(
        self, file_path: str, content: str
    ) -> List[Dict[str, Any]]:
        """
        Validate JavaScript/TypeScript code with ESLint.

        Returns list of violations found.
        """
        # Check if ESLint is available
        eslint_path = self.project_dir / "node_modules" / ".bin" / "eslint"
        if not eslint_path.exists():
            logger.debug("ESLint not found in project")
            return []

        # Write content to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=Path(file_path).suffix, delete=False
        ) as f:
            f.write(content)
            temp_file = f.name

        try:
            # Run ESLint with project config
            proc = await asyncio.create_subprocess_exec(
                str(eslint_path),
                "--format", "json",
                temp_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.project_dir),
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                # No violations
                return []

            # Parse ESLint output
            try:
                results = json.loads(stdout.decode())
                if results and len(results) > 0:
                    messages = results[0].get("messages", [])
                    violations = []
                    for msg in messages:
                        violations.append({
                            "rule": msg.get("ruleId", "unknown"),
                            "severity": "error" if msg.get("severity") == 2 else "warning",
                            "message": msg.get("message"),
                            "line": msg.get("line"),
                            "column": msg.get("column"),
                        })
                    return violations
            except json.JSONDecodeError:
                logger.error("Failed to parse ESLint output")
                return []

        except FileNotFoundError:
            logger.debug("ESLint binary not found")
            return []
        except Exception as e:
            logger.error(f"ESLint validation failed: {e}")
            return []
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_file)
            except:
                pass

        return []

    async def validate_with_pylint(
        self, file_path: str, content: str
    ) -> List[Dict[str, Any]]:
        """
        Validate Python code with Pylint.

        Returns list of violations found.
        """
        # Check if pylint is available
        try:
            proc = await asyncio.create_subprocess_exec(
                "pylint", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except FileNotFoundError:
            logger.debug("Pylint not found")
            return []

        # Write content to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(content)
            temp_file = f.name

        try:
            # Run Pylint
            proc = await asyncio.create_subprocess_exec(
                "pylint",
                "--output-format=json",
                "--disable=all",  # Start with nothing
                "--enable=C,W,E,F",  # Enable common checks
                temp_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.project_dir),
            )

            stdout, stderr = await proc.communicate()

            # Parse Pylint output
            try:
                results = json.loads(stdout.decode())
                violations = []
                for msg in results:
                    violations.append({
                        "rule": msg.get("symbol", msg.get("message-id")),
                        "severity": msg.get("type", "error").lower(),
                        "message": msg.get("message"),
                        "line": msg.get("line"),
                        "column": msg.get("column"),
                    })
                return violations
            except json.JSONDecodeError:
                logger.error("Failed to parse Pylint output")
                return []

        except Exception as e:
            logger.error(f"Pylint validation failed: {e}")
            return []
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_file)
            except:
                pass

        return []

    async def validate_with_ruff(
        self, file_path: str, content: str
    ) -> List[Dict[str, Any]]:
        """
        Validate Python code with Ruff (faster alternative to Pylint).

        Returns list of violations found.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ruff", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except FileNotFoundError:
            logger.debug("Ruff not found")
            return []

        # Write content to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(content)
            temp_file = f.name

        try:
            # Run Ruff
            proc = await asyncio.create_subprocess_exec(
                "ruff",
                "check",
                "--output-format=json",
                temp_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await proc.communicate()

            # Parse Ruff output
            try:
                results = json.loads(stdout.decode())
                violations = []
                for msg in results:
                    violations.append({
                        "rule": msg.get("code"),
                        "severity": "error" if msg.get("severity") == "error" else "warning",
                        "message": msg.get("message"),
                        "line": msg.get("location", {}).get("row"),
                        "column": msg.get("location", {}).get("column"),
                    })
                return violations
            except json.JSONDecodeError:
                logger.error("Failed to parse Ruff output")
                return []

        except Exception as e:
            logger.error(f"Ruff validation failed: {e}")
            return []
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_file)
            except:
                pass

        return []

    async def validate_code(
        self, file_path: str, content: str
    ) -> List[Dict[str, Any]]:
        """
        Validate code with appropriate linter based on file type.

        Returns combined list of violations from all applicable linters.
        """
        violations = []

        # Detect language
        if file_path.endswith((".ts", ".tsx", ".js", ".jsx")):
            eslint_violations = await self.validate_with_eslint(file_path, content)
            violations.extend(eslint_violations)

        elif file_path.endswith(".py"):
            # Try Ruff first (faster), fallback to Pylint
            ruff_violations = await self.validate_with_ruff(file_path, content)
            if ruff_violations:
                violations.extend(ruff_violations)
            else:
                pylint_violations = await self.validate_with_pylint(file_path, content)
                violations.extend(pylint_violations)

        logger.info(f"Linter validation found {len(violations)} violations in {file_path}")
        return violations
