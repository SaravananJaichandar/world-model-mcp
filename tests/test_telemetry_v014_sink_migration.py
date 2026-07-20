"""
v0.14 telemetry sink migration tests.

Contract to preserve:
  1. record() and record_sync() never raise, even when the endpoint
     is unreachable, returns 4xx/5xx, or hangs.
  2. Payload contains ONLY the whitelisted fields — never file paths,
     content, hostnames, IPs, usernames, emails.
  3. Heartbeat fires at most once per _HEARTBEAT_INTERVAL_SECONDS
     per install.
  4. forget_me() wipes local state even if the server is unreachable.
  5. The endpoint URL comes from env override when set (for testing
     against a local etch.systems checkout).
"""

from __future__ import annotations

import importlib
import json
import time
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Reload the telemetry module so its state paths pick up the patched HOME
    from world_model_server import telemetry as t
    importlib.reload(t)
    return tmp_path


# --------------------------------------------------------------------------
# Payload shape
# --------------------------------------------------------------------------


class TestPayloadShape:
    def test_heartbeat_payload_contains_expected_keys(self, home):
        from world_model_server import telemetry as t
        p = t.preview_payload("heartbeat")
        assert p["event"] == "heartbeat"
        assert "install_id" in p
        assert "version" in p
        assert "ts" in p
        assert "os_family" in p
        assert "python_version" in p
        assert "adapters" in p  # heartbeat carries adapter list

    def test_action_event_omits_adapters(self, home):
        from world_model_server import telemetry as t
        p = t.preview_payload("setup_completed")
        assert p["event"] == "setup_completed"
        assert "adapters" not in p

    def test_payload_never_contains_pii_keys(self, home):
        from world_model_server import telemetry as t
        p = t.preview_payload("heartbeat")
        forbidden = {"path", "file", "content", "hostname", "user",
                     "ip", "email", "cwd", "prompt", "response"}
        for k in p:
            assert k.lower() not in forbidden, f"leak: {k}"

    def test_payload_fields_only_flat_primitives(self, home):
        from world_model_server import telemetry as t
        p = t.preview_payload("test_event", {
            "ok": True, "nested": {"a": 1}, "bytes_val": b"hi",
        })
        # nested dict + bytes should have been dropped
        fields = p.get("fields", {})
        assert "ok" in fields
        assert "nested" not in fields
        assert "bytes_val" not in fields

    def test_os_family_whitelisted(self, home):
        from world_model_server import telemetry as t
        # Function should never return anything outside the whitelist
        assert t._os_family() in {"darwin", "linux", "windows", "freebsd", None}

    def test_python_version_shape(self, home):
        from world_model_server import telemetry as t
        v = t._python_version()
        assert v.startswith("3.")
        assert len(v.split(".")) == 2


# --------------------------------------------------------------------------
# Fail-open contract
# --------------------------------------------------------------------------


class TestFailOpen:
    def test_record_never_raises_on_bad_endpoint(self, home, monkeypatch):
        monkeypatch.setenv(
            "WORLD_MODEL_TELEMETRY_ENDPOINT",
            "http://127.0.0.1:1/api/telemetry/ingest",  # non-routable
        )
        from world_model_server import telemetry as t
        importlib.reload(t)
        t.set_consent(True)
        # Should return None, not raise
        t.record("heartbeat")

    def test_record_sync_returns_false_when_disabled(self, home):
        from world_model_server import telemetry as t
        # Consent unset by default
        assert t.record_sync("heartbeat") is False

    def test_record_sync_returns_false_when_kill_switch_set(self, home, monkeypatch):
        from world_model_server import telemetry as t
        t.set_consent(True)
        monkeypatch.setenv("WORLD_MODEL_TELEMETRY_DISABLE", "1")
        assert t.record_sync("heartbeat") is False


# --------------------------------------------------------------------------
# Heartbeat cadence
# --------------------------------------------------------------------------


class TestHeartbeat:
    def test_heartbeat_noop_when_not_opted_in(self, home):
        from world_model_server import telemetry as t
        # Not opted in → should not touch the last-heartbeat file
        t.maybe_heartbeat()
        assert not t._LAST_HEARTBEAT_PATH.exists()

    def test_heartbeat_writes_timestamp_first_time(self, home, monkeypatch):
        from world_model_server import telemetry as t
        t.set_consent(True)
        # Stub _post so we don't hit the network
        monkeypatch.setattr(t, "_post", lambda url, payload: True)
        t.maybe_heartbeat()
        assert t._LAST_HEARTBEAT_PATH.exists()

    def test_heartbeat_skips_if_recent(self, home, monkeypatch):
        from world_model_server import telemetry as t
        t.set_consent(True)
        # Recent heartbeat 1 hour ago
        t._STATE_DIR.mkdir(parents=True, exist_ok=True)
        t._LAST_HEARTBEAT_PATH.write_text(str(time.time() - 3600))
        called = []
        monkeypatch.setattr(t, "record", lambda *a, **kw: called.append(a))
        t.maybe_heartbeat()
        assert not called, "heartbeat fired too soon"

    def test_heartbeat_fires_after_interval(self, home, monkeypatch):
        from world_model_server import telemetry as t
        t.set_consent(True)
        # Last heartbeat older than 24h
        t._STATE_DIR.mkdir(parents=True, exist_ok=True)
        t._LAST_HEARTBEAT_PATH.write_text(
            str(time.time() - t._HEARTBEAT_INTERVAL_SECONDS - 60),
        )
        called = []
        monkeypatch.setattr(t, "record", lambda *a, **kw: called.append(a))
        t.maybe_heartbeat()
        assert called == [("heartbeat",)]


# --------------------------------------------------------------------------
# Right-to-erasure
# --------------------------------------------------------------------------


class TestForgetMe:
    def test_forget_wipes_local_state_even_when_offline(self, home, monkeypatch):
        from world_model_server import telemetry as t
        t.set_consent(True)
        _ = t.get_install_id()
        assert t._INSTALL_ID_PATH.exists()
        assert t._CONSENT_PATH.exists()

        # Point at a non-routable endpoint so the DELETE fails
        monkeypatch.setenv(
            "WORLD_MODEL_TELEMETRY_ENDPOINT",
            "http://127.0.0.1:1/api/telemetry/ingest",
        )
        server_ok, deleted = t.forget_me()
        assert server_ok is False
        assert deleted == 0
        # Local state must be wiped regardless
        assert not t._INSTALL_ID_PATH.exists()
        assert not t._CONSENT_PATH.exists()

    def test_forget_reports_server_success(self, home, monkeypatch):
        from world_model_server import telemetry as t
        t.set_consent(True)

        # Stub urllib.request.urlopen to simulate 200 + {"deleted": 3}
        class _MockResp:
            status = 200
            def read(self):
                return b'{"deleted": 3}'
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **kw: _MockResp())
        server_ok, deleted = t.forget_me()
        assert server_ok is True
        assert deleted == 3


# --------------------------------------------------------------------------
# Endpoint resolution
# --------------------------------------------------------------------------


class TestSecurityAuditFixes:
    """
    Client-side regression tests for the 2026-07-20 security audit.
    """

    def test_forget_wipes_local_when_server_returns_null(self, home, monkeypatch):
        """[MEDIUM] forget_me used to raise AttributeError on
        body.get('deleted', 0) if body was null. Local wipe would be
        skipped. Fix: broad except + finally block guarantees wipe."""
        from world_model_server import telemetry as t
        t.set_consent(True)
        _ = t.get_install_id()

        class _NullResp:
            status = 200
            def read(self):
                return b"null"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **kw: _NullResp())
        # Must not raise; local wipe must happen
        server_ok, deleted = t.forget_me()
        assert not t._INSTALL_ID_PATH.exists()
        assert not t._CONSENT_PATH.exists()

    def test_forget_wipes_local_when_env_url_is_malformed(self, home, monkeypatch):
        """[MEDIUM] Malformed WORLD_MODEL_TELEMETRY_ENDPOINT raised
        ValueError before the wipe loop ran. Fix: broad except."""
        import importlib
        monkeypatch.setenv("WORLD_MODEL_TELEMETRY_ENDPOINT",
                           "://not-a-url")
        from world_model_server import telemetry as t
        importlib.reload(t)
        t.set_consent(True)
        _ = t.get_install_id()
        server_ok, deleted = t.forget_me()
        assert server_ok is False
        assert not t._INSTALL_ID_PATH.exists()
        assert not t._CONSENT_PATH.exists()

    def test_state_files_have_owner_only_perms(self, home):
        """[LOW] Client state files were world-readable at umask 0644.
        Fix: _write_state_secure sets 0600 on the file and 0700 on
        _STATE_DIR. Skip on Windows where POSIX perms don't apply."""
        import os
        import sys
        if sys.platform.startswith("win"):
            pytest.skip("POSIX permissions don't apply on Windows")
        from world_model_server import telemetry as t
        t.set_consent(True)
        _ = t.get_install_id()
        # Both files should be 0600
        install_mode = t._INSTALL_ID_PATH.stat().st_mode & 0o777
        consent_mode = t._CONSENT_PATH.stat().st_mode & 0o777
        assert install_mode == 0o600, oct(install_mode)
        assert consent_mode == 0o600, oct(consent_mode)
        # Directory should be 0700
        dir_mode = t._STATE_DIR.stat().st_mode & 0o777
        assert dir_mode == 0o700, oct(dir_mode)


class TestEndpointResolution:
    def test_default_endpoint_is_etch_systems(self, home, monkeypatch):
        monkeypatch.delenv("WORLD_MODEL_TELEMETRY_ENDPOINT", raising=False)
        from world_model_server import telemetry as t
        importlib.reload(t)
        assert t._resolve_endpoint() == "https://etch.systems/api/telemetry/ingest"

    def test_env_override_used_when_set(self, home, monkeypatch):
        monkeypatch.setenv("WORLD_MODEL_TELEMETRY_ENDPOINT",
                           "http://localhost:8000/api/telemetry/ingest")
        from world_model_server import telemetry as t
        importlib.reload(t)
        assert t._resolve_endpoint() == "http://localhost:8000/api/telemetry/ingest"

    def test_forget_endpoint_derived_from_ingest_url(self, home, monkeypatch):
        monkeypatch.setenv("WORLD_MODEL_TELEMETRY_ENDPOINT",
                           "http://localhost:8000/api/telemetry/ingest")
        from world_model_server import telemetry as t
        importlib.reload(t)
        url = t._resolve_forget_endpoint("abc-123")
        assert url == "http://localhost:8000/api/telemetry/install/abc-123"


# --------------------------------------------------------------------------
# _post behavior (mocked)
# --------------------------------------------------------------------------


class TestPostSuccess:
    def test_post_returns_true_on_2xx(self, home, monkeypatch):
        from world_model_server import telemetry as t
        class _MockResp:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **kw: _MockResp())
        assert t._post("http://x", {"event": "x"}) is True

    def test_post_returns_false_on_5xx(self, home, monkeypatch):
        from world_model_server import telemetry as t
        class _MockResp:
            status = 500
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **kw: _MockResp())
        # 500 is treated as failure but never raises
        assert t._post("http://x", {"event": "x"}) is False
