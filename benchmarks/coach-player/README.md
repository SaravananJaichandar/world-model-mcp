# Coach-Player benchmark (v0.12.12)

Evaluates the shipped Coach LLM (`verify_retrieval` tool) against hand-labeled query/answer/facts triples. Unlike the deterministic contradictions benchmark, this one requires a live Anthropic API call per pair.

## Cost

- ~$0.03 per full 12-pair run at Haiku 4.5 pricing (Coach default)
- Prompt is short (~600 input tokens per call, ~300 output)

## Run

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python benchmarks/coach-player/run.py
```

Optional flags:
- `--model` override the Coach model (default: `claude-haiku-4-5-20251001`)
- `--pairs PATH` custom labeled-pairs JSON
- `--out results.json` write a machine-readable summary

## Pair categories (in `pairs.json`)

- **grounded** (4 pairs) — every material claim is supported by a supplied fact. Ground truth confidence: `HIGH`.
- **partial** (4 pairs) — 3 verified + 1 unverified claim → 75% verified. Ground truth confidence: `MEDIUM` (threshold: >=70%).
- **hallucinated** (4 pairs) — no material claim is backed by a supplied fact. Ground truth confidence: `LOW`.

## Metrics reported

- **Hallucination catch rate** — fraction of `hallucinated` pairs where the Coach returns `LOW`. Ship floor: 95%. **Aspirational** at 12 pairs.
- **False positive rate** — fraction of `grounded` pairs where the Coach returns `LOW`. Ship ceiling: 10%. **Enforced** — the runner exits nonzero if breached.
- **Partial exact (MEDIUM)** — fraction of `partial` pairs where the Coach returns exactly `MEDIUM`.
- **Partial within one band** — fraction of `partial` pairs within one confidence band of the ground truth (`MEDIUM` ± `HIGH` or ± `LOW`).
- **Overall exact match** — fraction of all pairs where Coach matched ground truth exactly.

## Ship-floor policy

The runner enforces the **false positive ceiling** (grounded answers wrongly labeled LOW) because a Coach that flags real answers as unverified erodes trust the fastest. The hallucination catch rate is aspirational at N=12 — the effective floor is 12/12 or 11/12 (91.7%). Expand `pairs.json` to at least 30 pairs before enforcing 95% as a hard exit condition.

## Contributing pairs

1. Add a new entry to `pairs.json` under `pairs`
2. Set `category` to `grounded` / `partial` / `hallucinated`
3. Set `expected_confidence` to the ground-truth band
4. Provide 1-4 `facts` and the `answer` under test
5. Re-run the benchmark to confirm the Coach handles the new pair

Adversarial pairs (subtly wrong answers, near-miss facts, paraphrased matches) are especially valuable — they stress the Coach's ability to distinguish semantic support from surface-form overlap.
