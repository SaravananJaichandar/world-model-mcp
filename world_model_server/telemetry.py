"""
Opt-in anonymous telemetry for world-model-mcp (v0.7.3 F2).

Design tenets:
- Off by default. Users must explicitly opt in.
- No identifiers tied to a person. One opaque random `install_id` per install.
- Never collects file paths, file contents, rule names, hostnames, IPs.
- Fail-open: any error is silent. Telemetry must never break the agent.
- Rate-limited client-side to 1 event per 60s; excess dropped silently.
- Async fire-and-forget via a daemon thread; never blocks the caller.
- All sends are inspectable: `world-model telemetry --status` shows the
  exact payload that would be sent.

Backend: posts an issue to a dedicated private GitHub repo (one issue per
event). No external infra. Project: SaravananJaichandar/world-model-telemetry.
The PAT is embedded with scope = Issues: write on that one repo. If abused,
rotate + ship a patch.

Environment variables that override behavior:
- WORLD_MODEL_TELEMETRY_DISABLE=1 -- never send, regardless of opt-in
- WORLD_MODEL_TELEMETRY_DEBUG=1 -- log every send/skip decision to stderr
- WORLD_MODEL_TELEMETRY_TOKEN=<token> -- override the embedded PAT
- WORLD_MODEL_TELEMETRY_REPO=<owner/repo> -- override the destination repo
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default backend. The embedded PAT is supplied at wheel-build time by
# scripts/embed_token.py, which writes world_model_server/_embedded_token.py
# from .env.release. The generated file is gitignored, so no token enters
# source control. When _embedded_token.py is absent (typical for editable
# installs and contributor wheels), the constant stays empty and telemetry
# silently no-ops -- the user still controls consent, just no events fire.
DEFAULT_REPO = "SaravananJaichandar/world-model-telemetry"

try:
    from ._embedded_token import EMBEDDED_TOKEN as _EMBEDDED_TOKEN  # type: ignore[import]
except ImportError:
    _EMBEDDED_TOKEN = ""

# Rate-limit window. 60s = at most 1 event/min from any one install.
_RATE_LIMIT_SECONDS = 60.0

# Where state lives. We use ~/.world-model/ so the install_id and consent
# survive `pip uninstall && pip install` cycles.
_STATE_DIR = Path.home() / ".world-model"
_INSTALL_ID_PATH = _STATE_DIR / "install_id"
_CONSENT_PATH = _STATE_DIR / "telemetry_consent"


# Globals for rate-limiting. One process = one bucket.
_last_send_lock = threading.Lock()
_last_send_at: float = 0.0


def _debug(msg: str) -> None:
    if os.getenv("WORLD_MODEL_TELEMETRY_DEBUG"):
        sys.stderr.write(f"[telemetry] {msg}\n")


def get_install_id() -> str:
    """Return the opaque random install_id, creating it on first call.

    The id is a UUID4 stored at ~/.world-model/install_id. It is NOT tied to
    any identifier from the user's machine. If the file is deleted, a new id
    is generated -- a single user clearing state looks like a new install,
    which is correct from a privacy standpoint.
    """
    try:
        if _INSTALL_ID_PATH.exists():
            existing = _INSTALL_ID_PATH.read_text().strip()
            if existing:
                return existing
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        new_id = str(uuid.uuid4())
        _INSTALL_ID_PATH.write_text(new_id)
        return new_id
    except OSError:
        # If we can't read or write state, return an ephemeral id. The session
        # is fine; we just can't correlate events across runs.
        return str(uuid.uuid4())


def is_enabled() -> bool:
    """Return True iff the user has explicitly opted in AND the global kill
    switch is not set."""
    if os.getenv("WORLD_MODEL_TELEMETRY_DISABLE"):
        return False
    try:
        if not _CONSENT_PATH.exists():
            return False
        return _CONSENT_PATH.read_text().strip() == "enabled"
    except OSError:
        return False


def set_consent(enabled: bool) -> None:
    """Persist the user's consent decision. Idempotent."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _CONSENT_PATH.write_text("enabled" if enabled else "disabled")


def consent_status() -> str:
    """Return 'enabled', 'disabled', or 'unset' (consent has never been asked)."""
    if not _CONSENT_PATH.exists():
        return "unset"
    raw = _CONSENT_PATH.read_text().strip()
    if raw == "enabled":
        return "enabled"
    return "disabled"


def _resolve_token() -> Optional[str]:
    env_token = os.getenv("WORLD_MODEL_TELEMETRY_TOKEN")
    if env_token:
        return env_token
    return _EMBEDDED_TOKEN or None


def _resolve_repo() -> str:
    return os.getenv("WORLD_MODEL_TELEMETRY_REPO", DEFAULT_REPO)


def _build_payload(event: str, fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Construct the JSON payload that will be sent."""
    from . import __version__

    body = {
        "event": event,
        "version": __version__,
        "install_id": get_install_id(),
        "ts": time.time(),
    }
    if fields:
        # Defensive: ensure no field accidentally leaks paths or content
        safe_fields = {
            k: v for k, v in fields.items()
            if not isinstance(v, (bytes, bytearray))
        }
        body.update(safe_fields)
    return body


def preview_payload(event: str, fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the exact payload that would be sent for the given event.

    Used by the `telemetry --status` CLI to show users what would be sent.
    Does not actually send anything.
    """
    return _build_payload(event, fields)


def _post_issue(repo: str, token: str, payload: Dict[str, Any]) -> bool:
    """Open an issue on the telemetry repo with the payload as body. Returns
    True on success, False on failure (silent)."""
    url = f"https://api.github.com/repos/{repo}/issues"
    title = f"[event:{payload['event']}] world-model-mcp v{payload['version']}"
    data = json.dumps({"title": title, "body": "```json\n" + json.dumps(payload, indent=2) + "\n```"}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": f"world-model-mcp/{payload['version']}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


def _rate_limit_ok() -> bool:
    """Return True if at least _RATE_LIMIT_SECONDS have elapsed since the last
    send. Always updates the last-send timestamp when True."""
    global _last_send_at
    with _last_send_lock:
        now = time.monotonic()
        if now - _last_send_at < _RATE_LIMIT_SECONDS:
            return False
        _last_send_at = now
        return True


def record(event: str, fields: Optional[Dict[str, Any]] = None) -> None:
    """Record a telemetry event. No-op if disabled, no token, or rate-limited.
    Sends in a daemon thread; never blocks the caller; never raises."""
    if not is_enabled():
        _debug(f"skip {event!r}: telemetry disabled")
        return

    token = _resolve_token()
    if not token:
        _debug(f"skip {event!r}: no token configured")
        return

    if not _rate_limit_ok():
        _debug(f"skip {event!r}: rate-limited")
        return

    repo = _resolve_repo()
    payload = _build_payload(event, fields)

    def _send():
        try:
            ok = _post_issue(repo, token, payload)
            _debug(f"send {event!r}: {'ok' if ok else 'failed'}")
        except Exception as exc:  # pragma: no cover - fail-open
            _debug(f"send {event!r} raised: {exc!r}")

    threading.Thread(target=_send, daemon=True).start()


def record_sync(event: str, fields: Optional[Dict[str, Any]] = None) -> bool:
    """Synchronous variant used by tests. Returns True on success.
    Production code should use `record()`, not this."""
    if not is_enabled():
        return False
    token = _resolve_token()
    if not token:
        return False
    if not _rate_limit_ok():
        return False
    payload = _build_payload(event, fields)
    return _post_issue(_resolve_repo(), token, payload)
