"""
v0.15.6: hosted-notary nudge on the human-facing CLI commands.

`world-model setup` and `world-model demo` end with a one-line pointer to the
hosted Etch notary — the OSS-install -> hosted-signup funnel. The pointer must:
  - name etch.systems so a fresh installer can find the hosted option;
  - be suppressible via WORLD_MODEL_NO_NUDGE=1 (scripted / CI installs);
  - stay wired into both human-facing commands (regression guard);
  - never be emitted from the MCP stdio server path, where stdout is JSON-RPC.
"""
import inspect
import io

from rich.console import Console

from world_model_server import cli


def _capture_nudge(monkeypatch, env_value=None):
    """Run the nudge with a captured console and return what it wrote."""
    buf = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buf, force_terminal=False, width=100))
    if env_value is None:
        monkeypatch.delenv("WORLD_MODEL_NO_NUDGE", raising=False)
    else:
        monkeypatch.setenv("WORLD_MODEL_NO_NUDGE", env_value)
    cli._print_hosted_notary_nudge()
    return buf.getvalue()


def test_nudge_points_to_etch(monkeypatch):
    out = _capture_nudge(monkeypatch)
    assert "etch.systems" in out
    assert "hosted notary" in out.lower()


def test_nudge_suppressed_by_env(monkeypatch):
    assert _capture_nudge(monkeypatch, env_value="1") == ""


def test_nudge_wired_into_setup_and_demo():
    """Regression guard: both human-facing commands must call the nudge."""
    for fn in (cli.setup_command, cli.demo_command):
        src = inspect.getsource(fn)
        assert "_print_hosted_notary_nudge()" in src, (
            f"{fn.__name__} must call _print_hosted_notary_nudge()"
        )


def test_nudge_not_wired_into_stdio_server():
    """The MCP server speaks JSON-RPC on stdout; it must not print the nudge."""
    from world_model_server import server

    assert "_print_hosted_notary_nudge" not in inspect.getsource(server)
