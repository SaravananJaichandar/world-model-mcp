"""
v0.8.0 F1: domain-aware confidence decay with per-evidence-type TTL.

The math is deterministic: given a fact's ``evidence_type``, its
``confidence`` at the last confirmation point, and the elapsed wall time
since that confirmation, compute the decayed confidence as an
exponential half-life curve where the half-life is set per
``evidence_type``.

The per-evidence-type half-lives encode the architectural claim that
different kinds of evidence rot at different rates. A
``source_code``-evidenced claim survives longer than a
``session``-evidenced one because the source-of-truth is checkable; a
``user_correction`` is the strongest possible signal and decays the
slowest of all. The constants below are the v0.8.0 defaults; they are
not yet configurable (v0.8.1 may add a config knob if real users need
it).

Auto-status transitions complement the decay: when ``confidence`` drops
below a tier-dependent threshold, ``status`` moves toward ``superseded``
to surface the rot to the read path. Settled facts
(``confirmer != NULL`` or ``status == "canonical"``) never auto-decay
their status, because the human or test runner already settled them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

# Per-evidence-type half-life in days. Values reflect the agreed
# v0.8 plan and the architectural claim in the working group spec
# sketch on anthropics/claude-code#47023.
EVIDENCE_TTL_DAYS: dict[str, int] = {
    "source_code":     365,
    "test":            180,
    "session":          14,
    "user_correction": 730,
    "bug_fix":         365,
}

# Fallback when evidence_type is missing or unknown. Conservative
# default that does not aggressively expire facts the system did not
# attribute properly.
DEFAULT_TTL_DAYS: int = 90

# Status transition thresholds.
#
# Below these confidence values, the read path treats the fact as rotted
# and moves its status toward ``superseded``. Canonical and
# user-confirmed (confirmer != NULL) facts NEVER auto-transition because
# settled is settled.
SYNTHESIZED_ROT_THRESHOLD: float = 0.20
CORROBORATED_ROT_THRESHOLD: float = 0.10


def _parse_ts(ts):
    """Parse a SQLite-stored timestamp into a tz-aware datetime.

    Returns None if ``ts`` is None or unparseable. SQLite stores
    timestamps either as ISO 8601 strings or already as datetimes (when
    Python-driver-converted). Treat naive datetimes as UTC because the
    schema's ``CURRENT_TIMESTAMP`` is UTC.
    """
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    try:
        cleaned = str(ts).replace("T", " ").split(".")[0]
        dt = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def compute_decayed_confidence(
    confidence: float,
    evidence_type: Optional[str],
    reference_ts,
    now: Optional[datetime] = None,
) -> float:
    """Return the decayed confidence for a fact.

    Args:
        confidence: the fact's confidence at the reference timestamp
            (typically the value stored in the DB).
        evidence_type: one of ``EVIDENCE_TTL_DAYS`` keys, or None / unknown
            for the fallback half-life.
        reference_ts: the timestamp the confidence was last anchored to.
            Use ``last_confirmed_at`` if present, otherwise ``created_at``.
        now: override for testability; defaults to ``datetime.now(UTC)``.

    Returns:
        The decayed confidence as a float in ``[0.0, 1.0]``. Returns the
        input confidence unchanged when ``reference_ts`` cannot be parsed
        (fail-open).
    """
    if confidence is None:
        return 0.0
    if confidence <= 0.0:
        return 0.0
    ref = _parse_ts(reference_ts)
    if ref is None:
        return float(confidence)

    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    elapsed_seconds = (now_dt - ref).total_seconds()
    if elapsed_seconds <= 0:
        return float(confidence)

    age_days = elapsed_seconds / 86400.0
    ttl_days = EVIDENCE_TTL_DAYS.get(evidence_type or "", DEFAULT_TTL_DAYS)
    if ttl_days <= 0:
        return 0.0

    half_lives = age_days / ttl_days
    decayed = confidence * (0.5 ** half_lives)
    if decayed < 0.0:
        return 0.0
    if decayed > 1.0:
        return 1.0
    return decayed


def should_auto_supersede(
    status: str,
    confidence: float,
    confirmer: Optional[str],
) -> bool:
    """Return True if a fact should auto-transition to ``superseded``.

    Settled facts (``canonical`` status, or any fact with ``confirmer``
    set) never auto-transition: a human or test runner already closed
    the loop on the fact and decay alone should not unsettle it.

    Synthesized facts that decay below ``SYNTHESIZED_ROT_THRESHOLD``
    transition; the inference was never confirmed and has now rotted.

    Corroborated facts that decay below ``CORROBORATED_ROT_THRESHOLD``
    transition; even multi-source corroboration is not load-bearing
    forever.
    """
    if confirmer is not None:
        return False
    if status == "canonical":
        return False
    if status == "synthesized" and confidence < SYNTHESIZED_ROT_THRESHOLD:
        return True
    if status == "corroborated" and confidence < CORROBORATED_ROT_THRESHOLD:
        return True
    return False


def apply_decay_to_row(row: dict, now: Optional[datetime] = None) -> dict:
    """Return a copy of ``row`` with decay applied to ``confidence`` and
    ``status`` if needed.

    The input row is expected to be a dict-like from a sqlite3.Row,
    containing at minimum: ``confidence``, ``evidence_type``,
    ``last_confirmed_at`` (or ``created_at``), ``status``, ``confirmer``.

    The output is a new dict (does not mutate the input). The caller
    decides whether to write back the decayed values to the DB; the
    decay module is intentionally pure so the read path can amortize
    writes (e.g., only update ``last_decay_at`` when status changes).
    """
    new_row = dict(row)

    reference_ts = (
        row.get("last_confirmed_at")
        or row.get("created_at")
    )
    decayed = compute_decayed_confidence(
        confidence=float(row.get("confidence") or 0.0),
        evidence_type=row.get("evidence_type"),
        reference_ts=reference_ts,
        now=now,
    )
    new_row["confidence"] = decayed

    if should_auto_supersede(
        status=row.get("status") or "",
        confidence=decayed,
        confirmer=row.get("confirmer"),
    ):
        new_row["status"] = "superseded"

    return new_row
