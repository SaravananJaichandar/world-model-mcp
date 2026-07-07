"""
v0.12.13: doctor Copilot log-signature check tests.

The new check_copilot_hook_signatures scans ~/.copilot/logs/*.log for
the two documented copilot-cli#4001 failure modes:

  (A) PowerShell parses bash-shaped commands ("ParserError")
  (B) $CLAUDE_PROJECT_DIR unset → paths resolve to "/.claude/..."

Tests use monkeypatch to redirect the log dir lookup so we can build
synthetic log fixtures without touching the user's real ~/.copilot/logs/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world_model_server import doctor as doctor_mod
from world_model_server.doctor import (
    ALL_CHECKS,
    FAIL,
    PASS,
    WARN,
    check_copilot_hook_signatures,
)


@pytest.fixture
def fake_copilot_log_dir(tmp_path, monkeypatch):
    """Redirect _copilot_log_dir to a tmp path. Test writes fixtures there."""
    log_dir = tmp_path / ".copilot" / "logs"
    log_dir.mkdir(parents=True)
    monkeypatch.setattr(doctor_mod, "_copilot_log_dir", lambda: log_dir)
    return log_dir


# ---------------------------------------------------------------------------
# Skip-when-Copilot-absent
# ---------------------------------------------------------------------------


def test_skips_when_no_copilot_install(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_mod, "_copilot_log_dir", lambda: None)
    result = check_copilot_hook_signatures(tmp_path)
    assert result.status == PASS
    assert "not installed" in result.detail


def test_pass_when_copilot_present_but_no_logs(fake_copilot_log_dir, tmp_path):
    result = check_copilot_hook_signatures(tmp_path)
    assert result.status == PASS
    assert "no log files" in result.detail.lower()


def test_pass_when_logs_have_no_error_signatures(fake_copilot_log_dir, tmp_path):
    (fake_copilot_log_dir / "process-1.log").write_text(
        "[INFO] hook fired\n[INFO] hook exited 0\n"
    )
    result = check_copilot_hook_signatures(tmp_path)
    assert result.status == PASS
    assert "no hook error signatures" in result.detail.lower()


# ---------------------------------------------------------------------------
# Detects (A) PowerShell parse errors
# ---------------------------------------------------------------------------


def test_warn_on_powershell_parse_error(fake_copilot_log_dir, tmp_path):
    (fake_copilot_log_dir / "process-1.log").write_text(
        "[ERROR] ParserError: Missing '(' after 'if' in expression\n"
    )
    result = check_copilot_hook_signatures(tmp_path)
    assert result.status == WARN
    assert "PowerShell ParserError" in result.detail
    assert result.fix_hint is not None
    assert "copilot-cli/issues/4001" in result.fix_hint


# ---------------------------------------------------------------------------
# Detects (B) missing $CLAUDE_PROJECT_DIR
# ---------------------------------------------------------------------------


def test_warn_on_missing_project_dir_signature(fake_copilot_log_dir, tmp_path):
    (fake_copilot_log_dir / "process-2.log").write_text(
        "[ERROR] Cannot find module '/.claude/hooks/world-model-capture.js'\n"
    )
    result = check_copilot_hook_signatures(tmp_path)
    assert result.status == WARN
    assert "/.claude/..." in result.detail
    assert "$CLAUDE_PROJECT_DIR" in result.detail


# ---------------------------------------------------------------------------
# Detects BOTH signatures — reports each count separately
# ---------------------------------------------------------------------------


def test_warn_reports_both_signatures_separately(fake_copilot_log_dir, tmp_path):
    (fake_copilot_log_dir / "process-1.log").write_text(
        "[ERROR] ParserError: something\n"
    )
    (fake_copilot_log_dir / "process-2.log").write_text(
        "[ERROR] Cannot find module '/.claude/hooks/x.js'\n"
    )
    (fake_copilot_log_dir / "process-3.log").write_text(
        "[ERROR] ParserError: something else\n"
        "[ERROR] Cannot find module '/.claude/hooks/y.js'\n"
    )
    result = check_copilot_hook_signatures(tmp_path)
    assert result.status == WARN
    # Two logs show ParserError (1 + 3)
    assert "2 log(s) show PowerShell ParserError" in result.detail
    # Two logs show /.claude/... (2 + 3)
    assert "2 log(s) show `/.claude/...`" in result.detail
    assert "AND" in result.detail


# ---------------------------------------------------------------------------
# Malformed logs don't crash the scan
# ---------------------------------------------------------------------------


def test_malformed_log_does_not_crash(fake_copilot_log_dir, tmp_path):
    """Binary content or unreadable files must be skipped, not raise."""
    (fake_copilot_log_dir / "process-1.log").write_bytes(b"\x00\xff binary garbage \x80")
    (fake_copilot_log_dir / "process-2.log").write_text("ParserError: legit")
    result = check_copilot_hook_signatures(tmp_path)
    # Should still find the second log's signature
    assert result.status == WARN
    assert "ParserError" in result.detail


# ---------------------------------------------------------------------------
# Fixture doesn't leak: check is registered in ALL_CHECKS
# ---------------------------------------------------------------------------


def test_copilot_check_registered_in_all_checks():
    """Regression: new check must be in the runner's list, not orphaned."""
    assert check_copilot_hook_signatures in ALL_CHECKS
