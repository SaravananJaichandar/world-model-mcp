# Contradiction-resolution benchmark v0.8.1 (105 pairs, 19 categories)

This is the v0.8.1 expansion of the original 24-pair contradiction-resolution benchmark shipped in v0.7.4. Six new categories were added to exercise the v0.8.0 schema specifically (decay, provenance, confirmer). Numbers below are deterministic; re-running `python benchmarks/contradictions-200/run.py` reproduces them bit-for-bit.

The original 24-pair v0.7.4 benchmark at `benchmarks/contradictions/` is preserved unchanged; its 93.5% headline is not invalidated by this release.

## Headline

| Strategy | Score | Note |
|---|---|---|
| `keep_higher_confidence` | 85/105 (81.0%) | Below v0.7.4 because new categories deliberately include cases where higher-confidence facts should lose. |
| `keep_most_recent` | 56/105 (53.3%) | Many new categories test scenarios where age is not the right axis. |
| `keep_most_sources` | 104/105 (99.0%) | New `source_tool_corroboration` category extends without breaking source counting. |
| `auto` | 81/105 (77.1%) | Below v0.7.4 because new categories test responsibilities the auto strategy did not have. |
| `keep_higher_confidence_decayed` (v0.8.1) | 19/21 (90.5%) | Scored only on pairs with evidence_type present (84 of 105 skipped). |
| **Overall** | **345/441 (78.2%)** | Across 4 strategies and the new decayed strategy combined. |

The full per-strategy + per-category JSON is in `results.json`.

## What the numbers say

The honest finding from this run is **not** that v0.8.0 improved any of the existing strategies. It is that **the auto strategy and confidence-only strategies fail in predictable ways on the new categories the v0.8.0 schema was designed to handle.** The new `keep_higher_confidence_decayed` strategy is 90.5% accurate on the subset where it actually applies.

Three concrete patterns the data surfaces:

1. **`confirmer_overrides_pending` and `settled_beats_higher_confidence` regress the auto strategy.** Both categories have higher-confidence pending facts that should lose to settled (confirmer != NULL) facts. The current `auto` heuristic does not know about confirmer yet; it picks the higher-confidence side and gets the wrong answer. This is the design gap v0.9's `auto` rewrite should close.

2. **`evidence_type_user_correction` is solved by the decayed strategy.** User corrections have a 730-day half-life vs session's 14 days, so even a 90-day-old user correction beats a 5-day-old session assertion under decay. The 6 pairs in this category score 6/6 under `keep_higher_confidence_decayed`.

3. **`decay_advantage_session_vs_source` and `decay_advantage_stale_session` work because session decays 26x faster than source_code.** The decayed strategy gets these right where the non-decayed `keep_higher_confidence` cannot (same confidence + same age + different evidence_type = tie for non-decayed).

## Per-category breakdown

The full breakdown is in `results.json`. The five categories with non-trivial outcomes:

| Category | Pairs | Best strategy | Notes |
|---|---|---|---|
| `confirmer_overrides_pending` | 8 | manual review needed | Auto fails because confirmer awareness is not yet in the heuristic. |
| `settled_beats_higher_confidence` | 5 | manual review needed | Same as above. The dataset documents the v0.9 work needed. |
| `decay_advantage_session_vs_source` | 5 | `keep_higher_confidence_decayed` | 5/5 |
| `decay_advantage_stale_session` | 5 | `keep_higher_confidence_decayed` | 5/5 |
| `evidence_type_user_correction` | 6 | `keep_higher_confidence_decayed` | 6/6 |

## Methodology

- **Deterministic**: no LLM calls, no embeddings, no network. The benchmark uses `world_model_server.contradictions.pick_winner` and the new `world_model_server.decay.compute_decayed_confidence`, both pure functions.
- **Five strategies scored**: four canonical (v0.7) plus the new v0.8.1 `keep_higher_confidence_decayed`.
- **The new strategy is scored only on pairs where at least one fact has `evidence_type` set.** Without evidence_type, the decay function returns the input confidence unchanged and the strategy degenerates into `keep_higher_confidence`. Counting those degenerate cases would inflate the score by counting wins where decay never fired; the runner skips them and reports the skip count alongside the score.
- **The 105 pairs are hand-written**, not templated. The original 24 from v0.7.4 are included; the new 81 split between expanded coverage of the original 13 categories (~30 pairs added) and 6 new v0.8.0-specific categories (~50 pairs).

## Honest limitations

1. **The dataset is small.** 105 pairs is more than 24 but it is not a public benchmark of standing. The dataset is hand-curated by one author (me) and reflects scenarios from one project's experience; reviewers can fairly ask whether the category boundaries are well-chosen.

2. **The "expected winners" are author-labeled.** Each pair has a winner per strategy that I asserted is correct. A different reviewer might label some pairs differently, especially in the `near_tie` and `manual_required` categories. The dataset is open for PR-based corrections.

3. **The auto strategy will be rewritten in v0.9** to fold in confirmer awareness and decay awareness. When that ships, the auto score on this same dataset should rise from 77.1% toward 90%+, which is the v0.9 success metric.

4. **The benchmark does not cover everything.** Genuine ambiguity in real conversations (where the model assertion is plausible but the user never confirms) is hard to encode in test fixtures. The LoCoMo benchmark in `benchmarks/locomo/` covers the conversational-recall surface that this one does not.

5. **Source_tool corroboration is partial coverage.** The dataset includes 6 pairs where source_tool is meaningful, but the test focuses on count-via-tools rather than on the full provenance graph (e.g., a fact asserted by tool A and confirmed by tool B). Full provenance-graph evaluation is v0.9 or later.

## How to reproduce

```bash
# From the repo root
python benchmarks/contradictions-200/run.py
python benchmarks/contradictions-200/run.py --out my_results.json
python benchmarks/contradictions-200/run.py --strategy keep_higher_confidence_decayed
```

The runner writes `results.json` with per-strategy, per-category, and per-row breakdowns. Diffing two `results.json` files across releases is the regression-detection method; CI guards a minimum accuracy on the two strategies that should be stable (`keep_most_sources` >= 95%, overall >= 70%).
