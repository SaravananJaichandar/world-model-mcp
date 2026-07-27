"""Registry hardening: fail-soft on any file-system error.

Regression lock for the fix applied 2026-07-27 after a BUZZ desktop
agent flagged that `_raw_load` should catch PermissionError and return
empty (it did via OSError, but write paths didn't). We now fail soft
on the write side too, so a locked-down HOME or read-only mount
doesn't crash the caller.

Chain event surfacing this: cee91a28-ba04-42ee-814c-8c75d6b65a9f.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import patch

import pytest


def _isolated_registry(monkeypatch, tmp_path: Path) -> Path:
    """Point the registry at a temp dir so tests don't touch $HOME."""
    from world_model_server import registry as reg
    root = tmp_path / ".world-model"
    monkeypatch.setattr(reg, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg, "REGISTRY_FILE", root / "projects.json")
    return root / "projects.json"


class TestRawLoadFailSoft:
    def test_missing_file_returns_empty(self, monkeypatch, tmp_path):
        from world_model_server.registry import ProjectRegistry
        _isolated_registry(monkeypatch, tmp_path)
        assert ProjectRegistry._raw_load() == {}
        assert ProjectRegistry.load() == {}

    def test_malformed_json_returns_empty(self, monkeypatch, tmp_path):
        from world_model_server.registry import ProjectRegistry
        registry_file = _isolated_registry(monkeypatch, tmp_path)
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text("{not: valid json")
        assert ProjectRegistry._raw_load() == {}

    def test_permission_error_returns_empty_no_raise(
        self, monkeypatch, tmp_path,
    ):
        """Simulate PermissionError on read. PermissionError inherits
        from OSError; the fix's OSError catch handles it."""
        from world_model_server.registry import ProjectRegistry
        registry_file = _isolated_registry(monkeypatch, tmp_path)
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text('{"proj": "/path"}')

        original_read = Path.read_text

        def readtext_denies(self, *a, **kw):
            if self == registry_file:
                raise PermissionError(13, "denied", str(self))
            return original_read(self, *a, **kw)

        with patch.object(Path, "read_text", readtext_denies):
            assert ProjectRegistry._raw_load() == {}
            assert ProjectRegistry.load() == {}

    def test_isadirectory_error_returns_empty(
        self, monkeypatch, tmp_path,
    ):
        """A caller replaces the JSON file with a directory. Still
        should not crash."""
        from world_model_server.registry import ProjectRegistry
        registry_file = _isolated_registry(monkeypatch, tmp_path)
        registry_file.mkdir(parents=True, exist_ok=True)
        # Reading a directory as text raises IsADirectoryError (subclass of OSError)
        assert ProjectRegistry._raw_load() == {}


class TestRegisterFailSoft:
    def test_register_succeeds_when_writable(self, monkeypatch, tmp_path):
        from world_model_server.registry import ProjectRegistry
        registry_file = _isolated_registry(monkeypatch, tmp_path)
        ProjectRegistry.register("alpha", "/tmp/alpha", project_id="p_alpha")
        loaded = json.loads(registry_file.read_text())
        assert loaded == {"alpha": {"db_path": "/tmp/alpha", "project_id": "p_alpha"}}

    def test_register_fails_soft_on_readonly_parent(
        self, monkeypatch, tmp_path, caplog,
    ):
        """The registry DIR can't be created (parent is read-only).
        register() should log a warning and return, not raise."""
        from world_model_server.registry import ProjectRegistry
        # Make the tmp_path itself read-only, so REGISTRY_DIR mkdir
        # inside it fails with PermissionError.
        readonly_parent = tmp_path / "ro"
        readonly_parent.mkdir()
        from world_model_server import registry as reg
        monkeypatch.setattr(reg, "REGISTRY_DIR", readonly_parent / "locked" / ".world-model")
        monkeypatch.setattr(
            reg, "REGISTRY_FILE",
            readonly_parent / "locked" / ".world-model" / "projects.json",
        )
        readonly_parent.chmod(0o500)  # r-x, no write
        try:
            # Must not raise
            ProjectRegistry.register("beta", "/tmp/beta")
        finally:
            readonly_parent.chmod(0o700)  # restore for cleanup

    def test_register_fails_soft_on_write_permission_denied(
        self, monkeypatch, tmp_path,
    ):
        """Directory exists but write to the JSON file is denied."""
        from world_model_server.registry import ProjectRegistry
        registry_file = _isolated_registry(monkeypatch, tmp_path)

        def writetext_denies(self, *a, **kw):
            raise PermissionError(13, "write denied", str(self))

        with patch.object(Path, "write_text", writetext_denies):
            # Must not raise; log a warning; leave state unchanged.
            ProjectRegistry.register("gamma", "/tmp/gamma")

        assert not registry_file.exists(), (
            "write was denied so the registry file must NOT have been created"
        )


class TestUnregisterFailSoft:
    def test_unregister_missing_project_is_noop(
        self, monkeypatch, tmp_path,
    ):
        from world_model_server.registry import ProjectRegistry
        _isolated_registry(monkeypatch, tmp_path)
        # Registry is empty. unregister should return without raising.
        ProjectRegistry.unregister("does_not_exist")

    def test_unregister_fails_soft_on_write_denied(
        self, monkeypatch, tmp_path,
    ):
        from world_model_server.registry import ProjectRegistry
        registry_file = _isolated_registry(monkeypatch, tmp_path)
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text(json.dumps({"delta": "/tmp/delta"}))

        def writetext_denies(self, *a, **kw):
            raise PermissionError(13, "write denied", str(self))

        with patch.object(Path, "write_text", writetext_denies):
            # Must not raise.
            ProjectRegistry.unregister("delta")

        # File is unchanged because the write was denied. The row is
        # NOT removed from disk — fail-soft, not silent-success.
        loaded = json.loads(registry_file.read_text())
        assert "delta" in loaded
