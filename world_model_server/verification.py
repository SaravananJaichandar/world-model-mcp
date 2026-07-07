"""
v0.12.12 Coach-Player adversarial verification.

Pattern ported from the maintainer's earlier y=c project. The KG returns
facts; a Player (a caller, or another LLM) synthesizes an answer citing
those facts; the Coach — an independent LLM call — checks each material
claim in the answer against the supplied source facts and produces a
confidence band plus itemized verified / unverified claim lists.

Design principles:

- **Isolation.** The Coach lives in its own module and its own LLM call
  path — it does not share prompt state with extraction or reasoning
  models. That's the "adversarial" part: the Coach doesn't know how the
  answer was produced, only what the facts say.
- **Best-effort.** Any failure — no API key, network error, malformed
  Coach response, empty fact list — returns LOW confidence with `error`
  populated. Never raises. Callers can trust the return shape without a
  try/except.
- **Cheap default.** WORLD_MODEL_VERIFICATION_MODEL defaults to Haiku 4.5.
  Verification is a per-answer overhead call; it shouldn't share the
  reasoning-model budget.
- **Testable.** The Coach call is a single `_run_coach(client, model, ...)`
  function so tests can inject a mock client without touching the wider
  pipeline. Confidence banding is pure (`_confidence_from_counts`).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from .models import Fact, VerificationResult

logger = logging.getLogger("world_model_server.verification")


# Confidence banding thresholds. Encoded as constants (not env-configurable
# yet) — tests lock these values, and shifting them without a benchmark
# update would silently move the shipped calibration.
HIGH_MIN_VERIFIED_FRACTION = 1.0  # every claim must be verified
MEDIUM_MIN_VERIFIED_FRACTION = 0.7  # >=70% verified


def _confidence_from_counts(verified: int, unverified: int) -> str:
    """Pure banding rule. Kept small and locked so tests can pin it."""
    total = verified + unverified
    if total == 0:
        # No claims extracted from the answer. Coach doesn't have enough
        # signal to bless the answer; treat as LOW rather than vacuously HIGH.
        return "LOW"
    fraction = verified / total
    if fraction >= HIGH_MIN_VERIFIED_FRACTION:
        return "HIGH"
    if fraction >= MEDIUM_MIN_VERIFIED_FRACTION:
        return "MEDIUM"
    return "LOW"


COACH_SYSTEM_PROMPT = (
    "You are an adversarial verifier. You are given a query, a candidate "
    "answer, and a list of source facts each with a fact_id. Your job is "
    "to check whether each material claim in the answer is supported by "
    "at least one of the supplied source facts.\n\n"
    "Rules:\n"
    "1. Extract every material claim from the answer. Ignore hedging, "
    "conversational filler, and meta-statements.\n"
    "2. For each claim, decide: does at least one source fact directly "
    "support it? If yes, name the fact_id. If no, mark it unverified.\n"
    "3. Do NOT invent supporting facts. If no source fact clearly supports "
    "a claim, mark it unverified even if the claim seems plausible.\n"
    "4. If the answer contains no material claims (empty, refusal, "
    "meta-only), return empty lists.\n\n"
    "Respond with STRICT JSON only, no prose, no code fences:\n"
    "{\n"
    '  "verified_claims": ["...", "..."],\n'
    '  "unverified_claims": ["...", "..."],\n'
    '  "source_pointers": [{"claim": "...", "fact_id": "..."}],\n'
    '  "reasoning": "one short paragraph"\n'
    "}"
)


def _format_facts_for_coach(facts: List[Fact]) -> str:
    """Serialize source facts into a compact, deterministic block the Coach
    can reference by fact_id."""
    lines = []
    for f in facts:
        # Prefer id / fact_text / evidence_path. Keep short — Coach is Haiku.
        lines.append(f"- fact_id={f.id} :: {f.fact_text}  (evidence: {f.evidence_path})")
    return "\n".join(lines) if lines else "(no facts supplied)"


def _build_coach_user_prompt(query: str, answer: str, facts: List[Fact]) -> str:
    return (
        f"QUERY:\n{query}\n\n"
        f"CANDIDATE ANSWER:\n{answer}\n\n"
        f"SOURCE FACTS:\n{_format_facts_for_coach(facts)}"
    )


def _parse_coach_response(raw_text: str) -> Dict[str, Any]:
    """Robustly extract the Coach's JSON. Coach is asked for STRICT JSON;
    we still tolerate an accidental code fence to survive small drift."""
    text = raw_text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif text.startswith("```"):
        text = text.split("```", 1)[1].split("```", 1)[0]
    return json.loads(text)


def _normalize_coach_output(
    parsed: Dict[str, Any],
) -> Tuple[List[str], List[str], List[Dict[str, str]], Optional[str]]:
    """Shared post-processing: pull verified/unverified/pointers/reasoning
    out of the parsed JSON in a way that's identical across Anthropic and
    OpenAI-compatible backends."""
    verified = list(parsed.get("verified_claims") or [])
    unverified = list(parsed.get("unverified_claims") or [])
    pointers = list(parsed.get("source_pointers") or [])
    clean_pointers = [
        {"claim": str(p.get("claim", "")), "fact_id": str(p.get("fact_id", ""))}
        for p in pointers
        if isinstance(p, dict)
    ]
    reasoning = parsed.get("reasoning") or None
    return verified, unverified, clean_pointers, reasoning


async def _run_coach_anthropic(
    client: Any,
    model: str,
    query: str,
    answer: str,
    facts: List[Fact],
) -> Tuple[List[str], List[str], List[Dict[str, str]], Optional[str]]:
    """Coach call against the Anthropic messages API. v0.12.12 path."""
    user_prompt = _build_coach_user_prompt(query, answer, facts)
    response = await client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=0.0,
        system=COACH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return _normalize_coach_output(_parse_coach_response(response.content[0].text))


async def _run_coach_openai_compatible(
    client: Any,
    model: str,
    query: str,
    answer: str,
    facts: List[Fact],
) -> Tuple[List[str], List[str], List[Dict[str, str]], Optional[str]]:
    """Coach call against an OpenAI-shape chat/completions endpoint. v0.12.13.

    Works against OpenRouter, Ollama, vLLM, LiteLLM, and any endpoint that
    implements POST /v1/chat/completions. The system prompt moves into the
    messages list (OpenAI convention); response text lives at
    response.choices[0].message.content instead of response.content[0].text.
    Everything else — deterministic temperature, JSON parsing, output
    shape — matches the Anthropic path.
    """
    user_prompt = _build_coach_user_prompt(query, answer, facts)
    response = await client.chat.completions.create(
        model=model,
        max_tokens=1024,
        temperature=0.0,
        messages=[
            {"role": "system", "content": COACH_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    raw = response.choices[0].message.content
    return _normalize_coach_output(_parse_coach_response(raw))


# Public alias for backward compat with v0.12.12 tests that patched
# _run_coach directly. Kept as a shim over the Anthropic-backend path since
# the v0.12.12 default was Anthropic.
_run_coach = _run_coach_anthropic


async def verify_answer(
    client: Optional[Any],
    model: str,
    query: str,
    answer: str,
    facts: List[Fact],
    backend: str = "anthropic",
) -> VerificationResult:
    """High-level entry point. Guaranteed to return a VerificationResult;
    never raises. On any failure the result is LOW confidence with the
    error field populated.

    Args:
        client: an AsyncAnthropic instance (backend='anthropic') or an
                AsyncOpenAI instance (backend='openai-compatible'), or None
                if no client could be constructed.
        model:  the verification model id (typically from Config.verification_model)
        query:  the user query the answer responds to
        answer: the candidate answer under verification
        facts:  the source facts the answer claims to be grounded in
        backend: 'anthropic' (v0.12.12 default) or 'openai-compatible' (v0.12.13).
                 Determines which _run_coach_* function dispatches the call.
    """
    if client is None:
        return VerificationResult(
            query=query,
            answer=answer,
            confidence="LOW",
            error="no_anthropic_api_key" if backend == "anthropic" else "no_verification_client",
        )
    if not answer or not answer.strip():
        # An empty answer has no claims to verify. Not an error, but not
        # HIGH either — HIGH is a positive statement about groundedness.
        return VerificationResult(
            query=query,
            answer=answer,
            confidence="LOW",
            error="empty_answer",
        )
    if not facts:
        # No sources means nothing can be verified. LOW by construction.
        return VerificationResult(
            query=query,
            answer=answer,
            confidence="LOW",
            error="no_source_facts",
        )

    try:
        if backend == "openai-compatible":
            verified, unverified, pointers, reasoning = await _run_coach_openai_compatible(
                client, model, query, answer, facts
            )
        else:
            verified, unverified, pointers, reasoning = await _run_coach_anthropic(
                client, model, query, answer, facts
            )
    except json.JSONDecodeError as e:
        logger.exception("Coach returned malformed JSON")
        return VerificationResult(
            query=query,
            answer=answer,
            confidence="LOW",
            error=f"coach_malformed_response: {e.msg}",
        )
    except Exception as e:
        logger.exception("Coach call failed")
        return VerificationResult(
            query=query,
            answer=answer,
            confidence="LOW",
            error=f"coach_call_failed: {type(e).__name__}",
        )

    confidence = _confidence_from_counts(len(verified), len(unverified))
    return VerificationResult(
        query=query,
        answer=answer,
        confidence=confidence,
        verified_claims=verified,
        unverified_claims=unverified,
        source_pointers=pointers,
        coach_reasoning=reasoning,
    )
