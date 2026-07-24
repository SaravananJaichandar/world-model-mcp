"""
Regression guard for the v0.15.3 semantic fix.

v0.15.2 refactored hybrid_signer so it imports cleanly without
SLH-DSA in the running liboqs build (CI runners that ship a
SLH-DSA-less liboqs-python wheel). That refactor initially made
`verify_slh_dsa` return False when SLH-DSA was unavailable, on the
theory that "cannot verify" and "signature verified as invalid"
were the same result for the caller.

They are not. A signature that cannot be verified in the local
environment is not evidence that the signature is invalid. An
auditor running etch-verify on a laptop with a broken liboqs
would see chain_integrity FAIL and reasonably conclude tampering
— but the actual chain is fine and their tool is broken.

v0.15.3 changes verify_slh_dsa to raise RuntimeError when
SLH-DSA is unavailable. Callers must distinguish the two
outcomes. etch_verify surfaces the exception as a distinct
"epoch_signatures_unverifiable" check with the environment
error verbatim in the detail, so operators can tell tool
problems apart from real chain integrity failures.

This test file locks the fix. If verify_slh_dsa ever regresses
to returning False in the unavailable case — for CI convenience
or any other reason — this test fails loud with the same
compliance-story reasoning in the assertion message.
"""

from __future__ import annotations

from unittest import mock

import pytest

from world_model_server import hybrid_signer as hs


class TestVerifySlhDsaSemantics:
    """v0.15.3 contract: verify_slh_dsa distinguishes 'cannot
    verify in this environment' from 'signature verified as
    invalid.'"""

    def test_raises_when_slh_dsa_unavailable(self):
        """Simulate the CI environment where SLH_DSA_AVAILABLE is
        False. Any verify call must raise RuntimeError with a
        message that names the missing mechanism and points at the
        fix. Returning False here would silently reclassify an
        environment problem as a tamper detection."""
        with mock.patch.object(hs, "SLH_DSA_AVAILABLE", False):
            with pytest.raises(RuntimeError) as excinfo:
                hs.verify_slh_dsa(
                    public_key_bytes=b"\x00" * 32,
                    message=b"anything",
                    signature=b"\x00" * 17088,
                )
        msg = str(excinfo.value)
        assert "SLH-DSA is not available" in msg, (
            f"error message should name the missing primitive so "
            f"operators know it's an environment problem; got: {msg}"
        )
        assert "liboqs" in msg, (
            "error message should point at the actual dep so the "
            "operator has an actionable fix"
        )

    def test_returns_false_when_signature_actually_invalid(self):
        """A signature with the wrong bytes but correct shape must
        return False, not raise. This is the "verified as invalid"
        outcome that's semantically distinct from "unverifiable."
        Locks the fact that the two outcomes stay distinct even
        after the v0.15.3 fix."""
        if not hs.SLH_DSA_AVAILABLE:
            pytest.skip("liboqs build lacks SLH-DSA; can't run real verify")
        signer = hs.SlhDsaSigner.generate()
        # A valid signature over one message doesn't verify against a
        # DIFFERENT message. Real verify path, real False result.
        sig = signer.sign(b"correct message")
        result = hs.verify_slh_dsa(
            public_key_bytes=signer.public_key_bytes(),
            message=b"tampered message",
            signature=sig,
        )
        assert result is False

    def test_returns_false_on_wrong_signature_length(self):
        """Shape rejection stays a False return — it's a caller
        error, not an environment error. Only the 'unavailable'
        case escalates to raise."""
        if not hs.SLH_DSA_AVAILABLE:
            pytest.skip("liboqs build lacks SLH-DSA")
        signer = hs.SlhDsaSigner.generate()
        result = hs.verify_slh_dsa(
            public_key_bytes=signer.public_key_bytes(),
            message=b"msg",
            signature=b"too short",
        )
        assert result is False


class TestVerifyHybridPropagatesUnavailable:
    """verify_hybrid wraps verify_slh_dsa. The RuntimeError must
    propagate — it MUST NOT be swallowed and turned into a False
    return, because that would restore the exact silent-drift
    problem v0.15.3 exists to close."""

    def test_verify_hybrid_raises_when_slh_dsa_unavailable(self):
        """verify_hybrid short-circuits on Ed25519 failure before
        reaching the SLH-DSA step, so we need a valid Ed25519
        signature to reach the RuntimeError path. Sign a real
        Ed25519 sig, forge the SLH-DSA half, mock SLH_DSA_AVAILABLE
        off, assert the runtime propagates instead of silent False."""
        if not hs.SLH_DSA_AVAILABLE:
            pytest.skip("need SLH-DSA to build a real Ed25519 keypair path")
        message = b"hybrid-verify-message"
        ed = hs.Ed25519Signer.generate()
        ed_sig = ed.sign(message)
        envelope = {
            "version": 1,
            "ed25519": ed_sig.hex(),
            "slh_dsa": ("00" * hs.SLH_DSA_SIGNATURE_BYTES),
        }
        with mock.patch.object(hs, "SLH_DSA_AVAILABLE", False):
            with pytest.raises(RuntimeError) as excinfo:
                hs.verify_hybrid(
                    envelope=envelope,
                    message=message,
                    ed25519_public_key=ed.public_key_bytes(),
                    slh_dsa_public_key=b"\x00" * 32,
                )
        assert "SLH-DSA is not available" in str(excinfo.value)


class TestEtchVerifySurfacesEnvironmentError:
    """etch_verify's epoch-signature pass must catch the
    RuntimeError and record a distinct 'unverifiable' check name
    so an operator can tell 'cannot verify' apart from 'verified
    as tampered' in the report output.

    This is what protects a real customer from an auditor running
    etch-verify on a broken laptop and reporting tamper when the
    chain is fine.
    """

    def test_report_names_unverifiable_check_when_slh_dsa_missing(
        self, tmp_path, monkeypatch,
    ):
        """Build a real dump with SLH-DSA available, then verify it
        under a mock where SLH_DSA_AVAILABLE is False. Assert the
        report FAILs on epoch_signatures_unverifiable specifically,
        NOT on epoch_signatures (which would read as "tampered").

        Uses monkeypatch for env vars so nothing leaks into other
        test modules — previous version set os.environ directly and
        contaminated test_etch_verify.py runs downstream."""
        if not hs.SLH_DSA_AVAILABLE:
            pytest.skip("liboqs build lacks SLH-DSA; can't build a real dump")
        import asyncio
        from world_model_server import audit_dump, etch_verify
        from world_model_server.knowledge_graph import KnowledgeGraph
        from world_model_server.models import Event

        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG", "on")
        # Auto-close threshold set to exactly the number of events we
        # seed; the second create_event closes the epoch inline.
        monkeypatch.setenv("WORLD_MODEL_AUDIT_LOG_EPOCH_SIZE", "2")

        async def _build_dump():
            kg = KnowledgeGraph(str(tmp_path))
            await kg.initialize()
            for i in range(2):
                await kg.create_event(Event(
                    session_id="sess-1",
                    event_type="tool_call",
                    tool_name=f"seed_{i}",
                    success=True,
                ))
            return await audit_dump.export_audit_dump(kg)

        manifest = asyncio.run(_build_dump())

        # Verify with SLH_DSA suddenly "unavailable"
        with mock.patch.object(hs, "SLH_DSA_AVAILABLE", False):
            report = etch_verify.verify_manifest(manifest)

        assert not report.ok, "report should FAIL when unverifiable"
        check_names = [c.get("name") for c in report.checks]
        assert "epoch_signatures_unverifiable" in check_names, (
            f"expected a distinct 'epoch_signatures_unverifiable' "
            f"check so operators know it's an environment problem, "
            f"not tampering. Got check names: {check_names}"
        )
        assert "epoch_signatures" not in [
            c.get("name") for c in report.checks
            if c.get("ok") is False
        ], (
            "must NOT report epoch_signatures as failed — that reads "
            "to an auditor as 'chain tampered' when it's actually "
            "'my tool cannot verify'"
        )
        # And the detail names the underlying problem so the operator
        # has an actionable fix (install proper liboqs).
        unverif = next(
            c for c in report.checks
            if c.get("name") == "epoch_signatures_unverifiable"
        )
        assert "SLH-DSA is not available" in (unverif.get("detail") or "")
