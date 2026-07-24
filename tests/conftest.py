"""
Shared pytest fixtures + collection-time markers.

Skip audit-chain tests when SLH-DSA is not enabled in the running
liboqs build. Not every environment has a liboqs with SLH-DSA
compiled in — GitHub Actions runners, for example, ship a
liboqs-python wheel that omits it despite having 200+ other
mechanisms enabled. Prod boxes install liboqs from source with
SLH-DSA support; those still run the full suite. This conftest
lets the non-crypto parts of the codebase get exercised in CI
without gating everything on a specific liboqs configuration.

Runtime behavior in an environment without SLH-DSA is loud, not
silent — every SLH-DSA-touching entrypoint calls _require_slh_dsa()
and raises a clear RuntimeError naming what's missing and how to
fix it. So a customer running world-model-mcp with a broken
liboqs cannot silently produce unsigned chain entries; they get
an immediate, actionable error.
"""

from __future__ import annotations

import pytest

try:
    from world_model_server.hybrid_signer import SLH_DSA_AVAILABLE
except Exception:  # noqa: BLE001 — collection-time defensive
    SLH_DSA_AVAILABLE = False

# Test files that instantiate keys, sign, verify, or otherwise
# exercise the audit chain end-to-end. Skipped as a group when
# SLH-DSA isn't wired up.
_AUDIT_CHAIN_TEST_MODULES = frozenset({
    "tests/test_v0130_epoch_close.py",
    "tests/test_v0130_hybrid_signer.py",
    "tests/test_v0130_proof_apis.py",
    "tests/test_v0130_proof_mcp_tools.py",
    "tests/test_pin_annotation_e2e.py",
    "tests/test_pin_annotation_verifier.py",
    "tests/test_audit_dump_export.py",
    "tests/test_etch_verify.py",
    "tests/test_etch_verify_subprocess_e2e.py",
    "tests/test_operational_e2e.py",
    "tests/test_tamper_evident_concurrent_append.py",
    "tests/test_verify_slh_dsa_semantics.py",
    "tests/integration/test_pin_annotation_integration.py",
    "tests/security/test_pin_annotation_security.py",
})


def pytest_collection_modifyitems(config, items):
    """Skip audit-chain tests when SLH-DSA is not available.

    Runs at collection time so the skip appears in the summary
    with a clear reason, and no test file is imported through the
    normal signing code path (which would raise if we tried to
    build a keypair inside a fixture)."""
    if SLH_DSA_AVAILABLE:
        return
    skip = pytest.mark.skip(
        reason=(
            "SLH-DSA-SHA2-128f is not enabled in this liboqs build. "
            "Audit-chain tests skipped. This is an environment "
            "limitation, not a code failure — install a liboqs with "
            "SLH-DSA support to run these."
        )
    )
    for item in items:
        path = str(item.fspath)
        if any(path.endswith(m) for m in _AUDIT_CHAIN_TEST_MODULES):
            item.add_marker(skip)
