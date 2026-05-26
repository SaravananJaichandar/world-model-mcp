"""
Empty stub for the release-time embedded telemetry token.

The release process (`scripts/embed_token.py`) overwrites this file locally
with the real PAT before `python3 -m build` runs. The token itself never
enters git -- only this empty stub does.

When EMBEDDED_TOKEN is "", telemetry.py treats it as "no token configured"
and `record()` silently no-ops. That's the safe default for contributor
builds, editable installs, and CI builds without a release secret.
"""

EMBEDDED_TOKEN = ""
