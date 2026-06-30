# Multi-seed replication plan for v0.9 SWE-bench Verified benchmark

**Locked:** 2026-06-25 (before any additional seed runs)
**Parent methodology:** [`DESIGN.md`](./DESIGN.md) (locked 2026-06-17)
**Parent result:** [`RESULTS.md`](./RESULTS.md) (v0.9.1 shipped 2026-06-24)
**Author:** Saravanan Jaichandaran

This plan locks the multi-seed replication strategy before any additional seed run kicks off. The v0.9.1 ship was single-trial (seed 1). This plan adds seeds 2 and 3 on a deliberately-chosen subset to test whether the v0.9 result reproduces under model sampling variance.

## Why a subset, not a full re-run

A full 3-seed run of all 49 paired instances would cost approximately 60 hours of agent wall-clock and 180 USD. A targeted subset captures the load-bearing data points (the flips that drove the +10.2 pts headline) plus variance-floor samples (stable outcomes that establish the variance baseline), at approximately 25 percent of the cost.

The subset is locked here BEFORE any seed-2 run executes, so the subset selection cannot be moved post hoc to favor a particular outcome.

## What sampling variance means in this benchmark

Claude Code CLI does not expose `--seed` or `--temperature` flags. The Anthropic API uses sampling with temperature > 0 by default. Re-running the same (task, arm) combination produces a different patch each time because the model samples its output stochastically. "Multi-seed" here means "re-run twice more to observe model sampling variance" — not seed-controlled determinism.

## Subset selection (17 instances, locked)

Three categories:

**A. Load-bearing flips and regressions (7 instances)** — the data points that drove the v0.9 headline numbers. If these reproduce across seeds, the headline holds. If not, the headline weakens.

- `django__django-11400` (Subset 1 FAIL-to-PASS flip)
- `django__django-13212` (Subset 1 FAIL-to-PASS flip)
- `django__django-13344` (Subset 1 FAIL-to-PASS flip, recovered from empty patch in baseline)
- `sympy__sympy-16597` (Subset 1 FAIL-to-PASS flip)
- `sympy__sympy-17630` (Subset 1 PASS-to-FAIL regression)
- `scikit-learn__scikit-learn-14087` (Subset 2 cross-domain FAIL-to-PASS flip)
- `sphinx-doc__sphinx-9461` (Subset 2 cross-domain FAIL-to-PASS flip, cleanest mechanistic case)

**B. Unchanged-PASS variance floor (5 instances)** — establish how often "stable PASS" instances remain PASS across re-runs. One per repository for coverage.

- `django__django-10554`
- `sympy__sympy-11618`
- `matplotlib__matplotlib-14623`
- `scikit-learn__scikit-learn-10297`
- `sphinx-doc__sphinx-10466`

**C. Unchanged-FAIL variance floor (5 instances)** — establish how often "stable FAIL" instances remain FAIL across re-runs.

- `sympy__sympy-12489`
- `matplotlib__matplotlib-22865`
- `matplotlib__matplotlib-23314`
- `sphinx-doc__sphinx-7590`
- `sphinx-doc__sphinx-7748`

Total: 17 instances × 2 arms (baseline, treatment) × 2 additional seeds = 68 task-runs.

## Variance metrics to compute

After seed 2 and seed 3 runs complete and scoring is done, compute the following for each (instance, arm) combination across the 3 seeds:

1. **Pass rate**: fraction of seeds in which the instance passed (0, 1/3, 2/3, or 1).
2. **Stability classification**:
   - `stable_pass` — passed in all 3 seeds
   - `stable_fail` — failed in all 3 seeds
   - `flaky` — sometimes passed, sometimes failed across the 3 seeds
3. **Per-arm aggregate**: number of stable_pass, stable_fail, and flaky instances per arm.

Then compute per (instance) the paired delta across seeds:

4. **Per-instance paired delta** — for each instance, count seeds where treatment passed minus seeds where baseline passed. Range: -3 to +3.
5. **Mean paired delta across the 17 instances** — should approximately reproduce the v0.9 per-instance delta direction.
6. **Bootstrap 95 percent confidence interval** on the mean paired delta — published as the headline replication number.

## Interpretation thresholds (locked before re-runs)

Replication is judged on whether the seed-2 and seed-3 outcomes match the seed-1 outcomes for the 7 load-bearing flips and regression.

- **Strong replication**: at least 5 of the 7 load-bearing instances show the same direction as seed 1 in both seed 2 and seed 3 (treatment passed when v0.9 showed flip; treatment failed when v0.9 showed regression). The v0.9 headline holds.
- **Moderate replication**: 3 or 4 of the 7 reproduce direction. The v0.9 headline is consistent with a real effect but with wider confidence bounds. Report as such.
- **Weak replication**: 2 or fewer reproduce direction. The v0.9 headline is at risk of being single-trial variance. Honest negative update to RESULTS.md.

The variance-floor samples (categories B and C) are not part of the replication judgment. They are used only to characterize the noise floor of the benchmark.

## Acceptance criteria for publishing the multi-seed update

Either of the following is sufficient to ship a v0.9.2 documentation patch with multi-seed results:

A. Strong or moderate replication observed. RESULTS.md adds a "Multi-seed replication" section with mean ± std and bootstrap CI on the 17-instance subset.

B. Weak replication observed. RESULTS.md adds a "Multi-seed replication" section with the same data, honestly framed as updating the confidence bounds on the v0.9 headline. The cross-domain regression-rate-of-zero finding (already listed as the most fragile in v0.9 limitations) is the most likely candidate for the update.

We will NOT:
- Move the subset boundary post hoc to favor an outcome
- Cherry-pick which seed to report
- Reframe the v0.9 result without publishing the multi-seed data verbatim

## Implementation

Each seed run uses the existing `orchestrator.py` with the `--instance-ids` and `--progress-suffix` flags:

```
# Seed 2 baseline
python orchestrator.py --arm baseline \
    --instance-ids [17-instance list] \
    --progress-suffix "_seed2"

# Seed 2 treatment
python orchestrator.py --arm treatment \
    --constraints constraints.json \
    --instance-ids [17-instance list] \
    --progress-suffix "_seed2_treatment"
```

Same for seed 3, with suffix `_seed3` / `_seed3_treatment`.

For Subset 2 cross-domain (which uses only Subset 1 constraints), use the same `--constraints constraints.json` flag — this matches the v0.9 treatment-arm configuration exactly.

Aggregation is handled by `multi_seed_aggregate.py` which loads the seed-1 (existing), seed-2, and seed-3 progress and results JSONLs and computes the metrics defined above.

## Cost estimate

- Per seed: 17 × 2 arms × ~30 min avg = ~17 hours agent wall-clock, ~$30 in agent cost
- Two additional seeds: ~34 hours, ~$60 total
- Scoring on cached env images: ~5-8 hours per seed (all env images already cached from v0.9)
- Total wall-clock: approximately 50-60 hours spread over 3-5 days, depending on overnight runs
- Total cost: approximately $60 USD on a Claude Code subscription

## Output

A v0.9.2 documentation patch with:
- `multi_seed_progress.jsonl` (combined seed 1, 2, 3 progress data)
- `multi_seed_results.jsonl` (combined seed 1, 2, 3 score data)
- `multi_seed_summary.json` (variance metrics, stability classification, bootstrap CI)
- Appendix in `RESULTS.md` titled "Multi-seed replication on 17-instance subset" with the headline number and the interpretation
- Updated Zenodo preprint (v2)

## Honesty commitment

This plan is locked. No changes to the subset selection, the metrics, or the interpretation thresholds will be made after seed-2 runs begin. If a change is needed before the runs start (for example, a bug in the orchestrator), it will be documented in this file with the date of change and the reason.

## Status update (appended 2026-06-30)

Seed 2 baseline and treatment runs completed on 2026-06-29 through 2026-06-30. Harness scoring completed on 2026-06-30. No changes were made to the subset, metrics, or thresholds locked above.

**Outcome: weak replication.**

- Load-bearing replication: 0 of 7 instances had both their seed-1 baseline AND seed-1 treatment outcomes reproduced at seed 2.
- Per-arm pass rate on the 17-instance subset: v0.9 baseline 6/17 (35.3 percent), seed-2 baseline 13/17 (76.5 percent). The baseline arm swung +41 percentage points between seeds with no methodology change.
- Mean paired delta across both seeds on the 17 instances: +0.24 per instance, bootstrap 95 percent CI [0.00, 0.47].

Per the acceptance criteria locked above (criterion B: weak replication), the v0.9.2 documentation patch was prepared with a "Multi-seed replication" appendix added to `RESULTS.md` and to `paper.md`. The Zenodo record was updated to a new version.

**Decision on seed 3: skipped.** The 0 of 7 load-bearing replication is clear and not borderline. Additional seeds at this subset would tighten the confidence interval but not flip the verdict. Time was better spent on the v0.9.2 release of the honest update than on additional data that would not change the story. Future replication (for v1.0 or peer-reviewed venue submission) should run all 49 paired instances at 3-5 seeds rather than another 17-instance pass.

Full per-instance results and honest interpretation are in `RESULTS.md` (section "Multi-seed replication appendix (v0.9.2 update, 2026-06-30)") and `paper.md` (Appendix A).
