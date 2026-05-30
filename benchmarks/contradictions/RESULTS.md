# Contradiction-resolution benchmark — results

A reproducible benchmark of `world-model-mcp`'s contradiction-resolution
primitives against a hand-curated set of 24 contradiction pairs. The
dataset, runner, and these results are all in `benchmarks/contradictions/`.

**Headline:** 93.5% overall accuracy across 62 strategy-pair scoring runs.
Both `keep_higher_confidence` and `keep_most_sources` score 100%;
`keep_most_recent` scores 90.9%; `auto` scores 87.5%.

The benchmark is deterministic — no LLM, no embeddings, no network. Same
inputs produce bit-identical outputs. Anyone can rerun it with one command:

```bash
python benchmarks/contradictions/run.py
```

## Headline numbers

| Strategy | Pairs scored | Passed | Accuracy |
| --- | --- | --- | --- |
| `keep_higher_confidence` | 16 | 16 | **100.0%** |
| `keep_most_recent` | 11 | 10 | 90.9% |
| `keep_most_sources` | 11 | 11 | **100.0%** |
| `auto` | 24 | 21 | 87.5% |
| **Total** | **62** | **58** | **93.5%** |

(Generated 2026-05-29 from commit at HEAD. Run `python benchmarks/contradictions/run.py --out results.json` to reproduce.)

## What the benchmark covers

The dataset's 24 pairs are deliberately spread across realistic edge cases:

- **`confidence_gap`** (2 pairs) — one side has materially higher confidence
- **`recency_gap`** (2) — one side is much more recent
- **`source_count_gap`** (2) — one side has many more independent sources
- **`auto_strategy_priority`** (3) — designed to verify `auto` picks the right
  strategy when multiple axes disagree
- **`tie`** (1) and **`manual_required`** (1) — should return no winner
- **`explicit_supersede`** (2) — explicit `supersede_a` / `supersede_b` calls
- **`near_tie`** (2) — tests the suggest_strategy threshold
- **`multi_axis`** (3) — confidence vs sources vs recency all disagree
- **`sparse_fields`** (2) — facts missing confidence or source_count
- **`long_form`** (1), **`robustness`** (1), **`boundary`** (2) — robustness

## Honest weaknesses (the 4 failed scoring runs)

These are real, documentable behaviors — not bugs hiding in the dataset.
Listing them is the point of an honest benchmark.

| Pair | Strategy | What happens | Why it's documented as a weakness |
| --- | --- | --- | --- |
| `tie-perfect` | `keep_most_recent` | Returns `b` even though timestamps are identical | When `valid_at` collides, the strategy currently falls through to the last-inserted fact rather than returning `None`. Acceptable for most uses; documented. |
| `tie-perfect` | `auto` | Returns `b` instead of `None` | `auto` does not have a "detect true tie, route to manual" path yet. Tracked as future work. |
| `manual-tie-confidence` | `auto` | Returns `b` instead of `None` | Same root cause as above. |
| `close-conf-small-gap` | `auto` | Returns `b` instead of `a` (confidence gap 0.02) | `suggest_strategy`'s confidence threshold treats this as a recency contest, which then picks `b` (or `a` arbitrarily). The threshold is intentional — sub-0.1 confidence gaps are noise — but flagged for the reader. |

## How to compare against other contradiction-resolution implementations

The benchmark is portable. The dataset's expected outcomes are agnostic to
which library implements the strategy — they describe **what** the right
answer is, not **how** to compute it.

To compare another library against this dataset, port the runner to call
that library's resolution primitive instead of `pick_winner`. Examples of
implementations worth comparing:

- `mcp-memory-service` v10.67.0 ships a 4-stage NLI contradiction pipeline
  (entity gate → embedding pre-filter → heuristic NLI → `contradicts` graph
  edge). Note the model surface is different — they detect contradictions
  via NLI, then mark a `contradicts` edge for human review; they don't pick
  a winner. Our benchmark scores winner selection, so direct comparison
  isn't apples-to-apples on this dataset until we add a `detection_only`
  scoring mode.
- `Empirica`'s "Sentinel gating" + "Practice Model" surface for findings
  has a calibration/decay layer rather than a fact-level resolver. Same
  caveat as above.

We welcome PRs to extend the runner with detection-only scoring or to add
new pairs that exercise scenarios these tools handle differently.

## Reproducing these results

```bash
git clone https://github.com/SaravananJaichandar/world-model-mcp
cd world-model-mcp
pip install -e .

# Run the benchmark
python benchmarks/contradictions/run.py

# Or write JSON output for downstream scoring
python benchmarks/contradictions/run.py --out results.json

# Restrict to a single strategy
python benchmarks/contradictions/run.py --strategy keep_higher_confidence
```

The dataset is JSONL at `benchmarks/contradictions/dataset.jsonl`. PRs
that add hard cases are welcome.

## Why this benchmark exists

Contradiction resolution is hard to evaluate without a concrete test set
that everyone can run. Stating accuracy numbers without an open benchmark
is unscientific. Publishing the numbers + the dataset + the runner is the
minimum honest version of "we have a confidence-weighted contradiction
resolver, and here's how it actually performs."

Future work:

1. Add a `detection_only` scoring mode so NLI-style tools (mcp-memory-service)
   are scored on a comparable axis.
2. Expand the dataset past 24 pairs. PRs with realistic contradiction
   pairs from production codebases are welcome.
3. Add a CI workflow that re-runs the benchmark on every release and
   fails if accuracy regresses on any strategy.
