"""
Cross-platform determinism regression tests for compute_decayed_confidence.

Origin: 2026-07-22 CI failure. `tests/test_v030_features.py::
test_seed_then_query_fact_finds_entity` asserted `result.confidence >= 0.8`
against a value that had been decayed by a fraction of a second of wall
clock. macOS produced 0.8 exactly; Linux CI produced 0.7999999869939778 —
a difference of ~1.3e-8, well below any semantic meaning at 365-day TTL.

Root cause: `confidence * (0.5 ** half_lives)` where `half_lives` is on
the order of 1e-11 produces platform-dependent last-bit drift because
IEEE 754 double multiplication rounds differently on different CPUs and
math libraries.

Fix: quantize the decay output to 6 decimal places at the function
boundary in world_model_server/decay.py. This is a stable numerical
contract, not a test workaround: no downstream consumer of decay output
uses more than a few decimals of precision (thresholds like 0.8, 0.6 are
exact literals).

These tests lock in that contract so the fix cannot silently regress.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from world_model_server.decay import compute_decayed_confidence


class TestDecayQuantizationContract:
    """The quantization contract: decay output must have at most 6 decimals."""

    def test_output_has_at_most_6_decimals(self) -> None:
        """For a case that triggers many-decimal FP drift, the returned
        value must equal itself rounded to 6 decimals. If a maintainer
        removes the `round(decayed, 6)` in decay.py, this fails."""
        ref = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
        now = ref + timedelta(milliseconds=500)
        result = compute_decayed_confidence(
            confidence=0.8,
            evidence_type="source_code",
            reference_ts=ref,
            now=now,
        )
        assert result == round(result, 6), (
            f"decay output {result!r} carries more than 6 decimals of "
            f"precision; the quantization contract in decay.py is broken."
        )

    def test_quantization_holds_for_multiple_evidence_types(self) -> None:
        """The 6-decimal contract must hold regardless of evidence_type;
        each TTL produces a different half_lives value and each can
        surface FP drift differently."""
        ref = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
        now = ref + timedelta(seconds=1)
        for evidence_type in (
            "source_code",
            "test",
            "session",
            "user_correction",
            "bug_fix",
            None,  # falls back to DEFAULT_TTL_DAYS
        ):
            result = compute_decayed_confidence(
                confidence=0.8,
                evidence_type=evidence_type,
                reference_ts=ref,
                now=now,
            )
            assert result == round(result, 6), (
                f"quantization contract broken for evidence_type="
                f"{evidence_type!r}: {result!r}"
            )


class TestDecayPlatformStableRegression:
    """Regression tests for the exact CI failure pattern from 2026-07-22.

    On a 365-day TTL and sub-second elapsed time, decay is scientifically
    negligible. Before the quantization fix, macOS returned 0.8 and Linux
    returned 0.7999999869939778 — a platform-dependent ~1.3e-8 drift.
    After the fix, both platforms return 0.8 exactly.
    """

    def test_subsecond_source_code_decay_returns_input(self) -> None:
        """The exact case that caused CI failure fdee7f4: source_code
        evidence, confidence=0.8, ~500ms elapsed. Must return 0.8 on
        every platform."""
        ref = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
        now = ref + timedelta(milliseconds=500)
        result = compute_decayed_confidence(
            confidence=0.8,
            evidence_type="source_code",
            reference_ts=ref,
            now=now,
        )
        assert result == 0.8, (
            f"expected 0.8 (sub-second decay at 365-day TTL is negligible), "
            f"got {result!r}. Cross-platform determinism regressed."
        )

    def test_zero_elapsed_returns_input_confidence(self) -> None:
        """The elapsed_seconds <= 0 fast path preserves input confidence."""
        now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
        result = compute_decayed_confidence(
            confidence=0.87342,
            evidence_type="source_code",
            reference_ts=now,
            now=now,
        )
        assert result == 0.87342

    def test_deterministic_across_subsecond_variation(self) -> None:
        """Multiple sub-second timings against the same reference must
        yield identical quantized outputs. This is the direct property
        that would have caught the CI failure on any platform."""
        ref = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)
        results = set()
        for millisecond_offset in (10, 100, 250, 500, 750, 999):
            now = ref + timedelta(milliseconds=millisecond_offset)
            result = compute_decayed_confidence(
                confidence=0.8,
                evidence_type="source_code",
                reference_ts=ref,
                now=now,
            )
            results.add(result)
        assert len(results) == 1, (
            f"decay results diverged across sub-second timing offsets: "
            f"{sorted(results)}. Platform-dependent FP drift has "
            f"re-emerged."
        )


class TestDecaySemanticsPreserved:
    """The quantization contract must not distort real, semantically
    meaningful decay (e.g., across days or weeks)."""

    def test_decay_after_one_half_life_is_half_input(self) -> None:
        """After exactly one half-life of elapsed time, confidence must
        halve (within 6-decimal precision). Verifies the physical
        semantics of the decay formula survived the quantization fix."""
        ref = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        # source_code TTL is 365 days
        now = ref + timedelta(days=365)
        result = compute_decayed_confidence(
            confidence=0.8,
            evidence_type="source_code",
            reference_ts=ref,
            now=now,
        )
        assert result == 0.4

    def test_decay_after_two_half_lives_is_quarter_input(self) -> None:
        """After two half-lives, confidence must be 1/4 of input."""
        ref = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        now = ref + timedelta(days=730)  # 2 x 365
        result = compute_decayed_confidence(
            confidence=0.8,
            evidence_type="source_code",
            reference_ts=ref,
            now=now,
        )
        assert result == 0.2

    def test_negative_confidence_clamps_to_zero(self) -> None:
        """Guarded clamp to 0.0 must still apply after the round."""
        # Force a case where the decay math would produce something
        # arithmetically absurd; verify the clamps hold.
        result = compute_decayed_confidence(
            confidence=-0.1,  # invalid input
            evidence_type="source_code",
            reference_ts=datetime(2026, 1, 1, tzinfo=UTC),
            now=datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert result == 0.0
