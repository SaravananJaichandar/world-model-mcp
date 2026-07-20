"""
Opt-in anonymous telemetry for world-model-mcp.

Design tenets:
- Off by default. Users must explicitly opt in via
  `world-model telemetry --enable`.
- No identifiers tied to a person. One opaque random `install_id` per
  install (UUID stored at ~/.world-model/install_id).
- Never collects file paths, file contents, rule names, hostnames, IPs,
  usernames, emails, or anything that could re-identify a machine or a
  user.
- Fail-open: any error is silent. Telemetry must never break the agent.
- Rate-limited client-side to 1 event per 60s; excess dropped silently.
- Async fire-and-forget via a daemon thread; never blocks the caller.
- All sends are inspectable: `world-model telemetry --status` shows the
  exact payload that would be sent.
- Right to erasure: `world-model telemetry --forget-me` DELETEs every
  row for this install_id on the server AND wipes local state, so a
  user can revoke both consent AND their historical data.

## Backend

Ships to `https://etch.systems/api/telemetry/ingest` — the hosted Etch
service, operated by the same maintainer. Endpoint is unauthenticated
(no PAT dance), rate-limited server-side by install_id, and the source
IP is stripped from the access log so the "no IP retention" promise
holds at every layer.

Previous v0.7.3-v0.13.x sink was a private GitHub Issues repo. That
was a stopgap; every wheel shipped with an empty PAT stub so telemetry
silently no-op'd for every user for the entire v0.7-v0.13 series. v0.14
is the first release where opt-in telemetry actually works.

## Environment variables

- WORLD_MODEL_TELEMETRY_DISABLE=1
    Never send, regardless of opt-in. Overrides consent.
- WORLD_MODEL_TELEMETRY_DEBUG=1
    Log every send/skip decision to stderr.
- WORLD_MODEL_TELEMETRY_ENDPOINT=<url>
    Override the ingest URL. Default: https://etch.systems/api/telemetry/ingest.
    Useful for testing against a local etch.systems checkout.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_ENDPOINT = "https://etch.systems/api/telemetry/ingest"
"""Ingest destination. Etch server, unauthenticated, rate-limited server-side."""

DEFAULT_FORGET_ENDPOINT_FMT = "https://etch.systems/api/telemetry/install/{install_id}"
"""Right-to-erasure endpoint. DELETE removes every row for the install_id."""

_RATE_LIMIT_SECONDS = 60.0
"""Client-side rate limit — at most 1 event per 60s from any one install."""

_HEARTBEAT_INTERVAL_SECONDS = 24 * 3600  # 24 hours
"""Minimum interval between opportunistic heartbeats."""

# State lives in ~/.world-model/ so install_id + consent survive
# `pip uninstall && pip install` cycles.
_STATE_DIR = Path.home() / ".world-model"
_INSTALL_ID_PATH = _STATE_DIR / "install_id"
_CONSENT_PATH = _STATE_DIR / "telemetry_consent"
_LAST_HEARTBEAT_PATH = _STATE_DIR / "telemetry_last_heartbeat"


# Globals for rate-limiting. One process = one bucket.
_last_send_lock = threading.Lock()
_last_send_at: float = 0.0


def _debug(msg: str) -> None:
    if os.getenv("WORLD_MODEL_TELEMETRY_DEBUG"):
        sys.stderr.write(f"[telemetry] {msg}\n")


# ---------------------------------------------------------------------------
# Install ID
# ---------------------------------------------------------------------------


def get_install_id() -> str:
    """
    Return the opaque random install_id, creating it on first call.

    UUID4 stored at ~/.world-model/install_id. NOT tied to any identifier
    from the user's machine. Deleting the file creates a new id on the
    next call — from a privacy standpoint, that's exactly right.
    """
    try:
        if _INSTALL_ID_PATH.exists():
            existing = _INSTALL_ID_PATH.read_text().strip()
            if existing:
                return existing
        new_id = str(uuid.uuid4())
        _write_state_secure(_INSTALL_ID_PATH, new_id)
        return new_id
    except OSError:
        # If we can't read/write state, return an ephemeral id. Session
        # is fine; we just can't correlate events across runs.
        return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Consent state
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """True iff user has explicitly opted in AND the global kill switch is off."""
    if os.getenv("WORLD_MODEL_TELEMETRY_DISABLE"):
        return False
    try:
        if not _CONSENT_PATH.exists():
            return False
        return _CONSENT_PATH.read_text().strip() == "enabled"
    except OSError:
        return False


def set_consent(enabled: bool) -> None:
    """Persist consent decision. Idempotent."""
    _write_state_secure(_CONSENT_PATH, "enabled" if enabled else "disabled")


def consent_status() -> str:
    """'enabled', 'disabled', or 'unset' (never asked)."""
    if not _CONSENT_PATH.exists():
        return "unset"
    raw = _CONSENT_PATH.read_text().strip()
    if raw == "enabled":
        return "enabled"
    return "disabled"


# ---------------------------------------------------------------------------
# Endpoint resolution (env overrides for testing)
# ---------------------------------------------------------------------------


def _resolve_endpoint() -> str:
    return os.getenv("WORLD_MODEL_TELEMETRY_ENDPOINT", DEFAULT_ENDPOINT)


def _resolve_forget_endpoint(install_id: str) -> str:
    base = os.getenv("WORLD_MODEL_TELEMETRY_ENDPOINT", DEFAULT_ENDPOINT)
    # Derive the forget URL from the ingest URL by swapping the path
    # segment. Works for both prod and any override target.
    if base.endswith("/ingest"):
        forget_prefix = base[: -len("/ingest")] + "/install"
    else:
        forget_prefix = base.rsplit("/", 1)[0] + "/install"
    return f"{forget_prefix}/{install_id}"


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------


_ALLOWED_OS_FAMILIES = {"darwin", "linux", "windows", "freebsd"}


def _os_family() -> Optional[str]:
    """Return one of the whitelisted OS families or None."""
    p = sys.platform
    if p.startswith("darwin"):
        return "darwin"
    if p.startswith("linux"):
        return "linux"
    if p.startswith("win"):
        return "windows"
    if p.startswith("freebsd"):
        return "freebsd"
    return None


def _python_version() -> str:
    v = sys.version_info
    return f"{v.major}.{v.minor}"


def _installed_adapters() -> list[str]:
    """
    Detect which agent adapters this user has actually wired up on this
    machine. Purely a heuristic — reads well-known config paths and
    returns adapter names. NEVER opens config contents.
    """
    adapters: list[str] = []
    home = Path.home()

    # Claude Code: ~/.claude/settings.json OR .claude/settings.json in cwd
    if (home / ".claude" / "settings.json").exists():
        adapters.append("claude-code")

    # Cursor: MCP config in Cursor settings
    for cursor_path in [
        home / ".cursor" / "mcp.json",
        home / "Library" / "Application Support" / "Cursor" / "User" / "mcp.json",
    ]:
        if cursor_path.exists():
            adapters.append("cursor")
            break

    # Cline: ~/.cline/mcp.json
    if (home / ".cline" / "mcp.json").exists():
        adapters.append("cline")

    # Codex: ~/.codex/mcp.json OR ~/.codex/config.toml
    if (home / ".codex" / "mcp.json").exists() or (home / ".codex" / "config.toml").exists():
        adapters.append("codex")

    # Continue: ~/.continue/config.yaml
    if (home / ".continue" / "config.yaml").exists():
        adapters.append("continue")

    # GitHub Copilot: .vscode/mcp.json in current cwd (best-effort;
    # no per-machine global config to inspect)
    if (Path.cwd() / ".vscode" / "mcp.json").exists():
        adapters.append("copilot")

    return adapters


def _build_payload(event: str, fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Assemble the JSON payload. Server rejects any key not on this
    whitelist, so we only include exactly what's expected.
    """
    from . import __version__

    body: Dict[str, Any] = {
        "event": event,
        "install_id": get_install_id(),
        "version": __version__,
        "ts": time.time(),
        "os_family": _os_family(),
        "python_version": _python_version(),
    }

    # Heartbeat carries the current adapter set; action events don't.
    if event == "heartbeat":
        body["adapters"] = _installed_adapters()

    if fields:
        # Defensive: strip bytes, only pass flat primitives
        safe_fields = {
            k: v for k, v in fields.items()
            if isinstance(v, (str, int, float, bool)) or v is None
        }
        if safe_fields:
            body["fields"] = safe_fields

    return body


def preview_payload(event: str, fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Return the exact payload that WOULD be sent. Used by
    `world-model telemetry --status` so users can inspect what happens
    on opt-in before flipping the switch.
    """
    return _build_payload(event, fields)


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


def _post(endpoint: str, payload: Dict[str, Any]) -> bool:
    """POST JSON payload. Returns True on 2xx. Never raises."""
    from . import __version__

    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"world-model-mcp/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, OSError):
        return False


def _rate_limit_ok() -> bool:
    """True if at least _RATE_LIMIT_SECONDS have elapsed since last send."""
    global _last_send_at
    with _last_send_lock:
        now = time.monotonic()
        if now - _last_send_at < _RATE_LIMIT_SECONDS:
            return False
        _last_send_at = now
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record(event: str, fields: Optional[Dict[str, Any]] = None) -> None:
    """
    Fire a telemetry event asynchronously. No-op if:
      - user has not opted in
      - kill switch is set
      - rate limit hit

    Sends in a daemon thread; never blocks the caller; never raises.
    """
    if not is_enabled():
        _debug(f"skip {event!r}: telemetry disabled")
        return
    if not _rate_limit_ok():
        _debug(f"skip {event!r}: rate-limited")
        return

    endpoint = _resolve_endpoint()
    payload = _build_payload(event, fields)

    def _send():
        try:
            ok = _post(endpoint, payload)
            _debug(f"send {event!r}: {'ok' if ok else 'failed'}")
        except Exception as exc:  # pragma: no cover - fail-open
            _debug(f"send {event!r} raised: {exc!r}")

    threading.Thread(target=_send, daemon=True).start()


def record_sync(event: str, fields: Optional[Dict[str, Any]] = None) -> bool:
    """Synchronous variant for tests. Returns True on success."""
    if not is_enabled():
        return False
    if not _rate_limit_ok():
        return False
    payload = _build_payload(event, fields)
    return _post(_resolve_endpoint(), payload)


def maybe_heartbeat() -> None:
    """
    Fire a heartbeat if:
      - user has opted in
      - kill switch is not set
      - at least 24h since the last heartbeat

    Called at CLI start-up. Fails silently on any error.
    """
    if not is_enabled():
        return
    try:
        last = 0.0
        if _LAST_HEARTBEAT_PATH.exists():
            try:
                last = float(_LAST_HEARTBEAT_PATH.read_text().strip())
            except (ValueError, OSError):
                last = 0.0
        if time.time() - last < _HEARTBEAT_INTERVAL_SECONDS:
            return
        # Record BEFORE writing the timestamp so a failed record leaves
        # the timestamp stale and gets retried on next invocation.
        record("heartbeat")
        _write_state_secure(_LAST_HEARTBEAT_PATH, str(time.time()))
    except OSError:
        pass


def forget_me() -> tuple[bool, int]:
    """
    Right-to-erasure. Delete every server-side row for this install_id,
    then wipe local telemetry state (install_id, consent, last_heartbeat).

    Returns (server_ok, deleted_count). Failing to reach the server —
    OR the server returning any unexpected response shape — does not
    stop local wipe. Consent revocation is guaranteed offline.
    """
    install_id = get_install_id()
    deleted = 0
    server_ok = False
    try:
        endpoint = _resolve_forget_endpoint(install_id)
        req = urllib.request.Request(
            endpoint,
            method="DELETE",
            headers={
                "User-Agent": "world-model-mcp/forget-me",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            server_ok = 200 <= resp.status < 300
            if server_ok:
                try:
                    body = json.loads(resp.read().decode("utf-8"))
                    if isinstance(body, dict):
                        deleted = int(body.get("deleted", 0) or 0)
                except (ValueError, json.JSONDecodeError,
                        TypeError, AttributeError):
                    pass
    except Exception:
        # Audit fix 2026-07-20: broad except so ANY error path (bad URL
        # in env override, response with wrong shape, TLS error, …)
        # cannot skip the local-wipe block below. Consent revocation
        # must survive every server-side surprise.
        pass
    finally:
        # Wipe local state regardless of server outcome. This is the
        # critical piece: `--forget-me` MUST leave the local machine
        # in an opted-out state with no install_id.
        for path in (_INSTALL_ID_PATH, _CONSENT_PATH, _LAST_HEARTBEAT_PATH):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    return server_ok, deleted


def _write_state_secure(path: Path, content: str) -> None:
    """
    Write a state file at umask-agnostic 0o600 so a co-tenant on the
    same machine can't read the install_id or consent flag and use
    them to spoof erasure requests. Audit fix 2026-07-20.
    """
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_STATE_DIR, 0o700)
    except OSError:
        pass
    path.write_text(content)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
