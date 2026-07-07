#!/usr/bin/env python3
"""
Version consistency check across ship-critical manifests.

Prevents the 400 trap where `mcp-publisher publish` fails because
server.json still points at the previously-shipped version. Ran twice
during v0.12.0 and v0.12.12 ships; this hook makes it the last time.

Compares:
  pyproject.toml              [project].version
  world_model_server/__init__ __version__
  server.json                 top-level .version
  server.json                 .packages[0].version

All four must match. Exits nonzero on mismatch with a clear diff.
Usable standalone (`python scripts/check_version_consistency.py`) or as
a git pre-commit hook.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        print("check_version_consistency: could not parse pyproject.toml version", file=sys.stderr)
        sys.exit(2)
    return m.group(1)


def _init_version() -> str:
    text = (ROOT / "world_model_server" / "__init__.py").read_text()
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        print("check_version_consistency: could not parse __init__.py __version__", file=sys.stderr)
        sys.exit(2)
    return m.group(1)


def _server_json_versions() -> tuple[str, str]:
    data = json.loads((ROOT / "server.json").read_text())
    top = data.get("version")
    pkg = data["packages"][0]["version"] if data.get("packages") else None
    if not top or not pkg:
        print("check_version_consistency: server.json missing .version or .packages[0].version", file=sys.stderr)
        sys.exit(2)
    return top, pkg


def main() -> int:
    pyproject = _pyproject_version()
    init = _init_version()
    server_top, server_pkg = _server_json_versions()

    versions = {
        "pyproject.toml": pyproject,
        "world_model_server/__init__.py": init,
        "server.json .version": server_top,
        "server.json .packages[0].version": server_pkg,
    }
    unique = set(versions.values())
    if len(unique) == 1:
        return 0

    print("check_version_consistency: VERSION MISMATCH", file=sys.stderr)
    for label, v in versions.items():
        marker = "  " if v == pyproject else "!!"
        print(f"  {marker} {label:<38}  {v}", file=sys.stderr)
    print(
        "\nAll four locations must match before shipping. Bump the outliers "
        "to the intended release version and re-commit.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
