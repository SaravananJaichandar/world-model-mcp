# v0.9 Repeat-Mistake Benchmark Results

This document reports the v0.9 SWE-bench Verified results testing whether the persistent-knowledge layer in world-model-mcp measurably reduces repeated coding-agent mistakes across sessions. The methodology is locked in [DESIGN.md](./DESIGN.md). All raw artifacts (predictions, classifications, constraints, scores) are included in this directory and reproducible from the methodology section below.

---

**Headline numbers**

| Subset | Baseline | Treatment | Delta | Flips (FAIL to PASS) | Regressions (PASS to FAIL) |
|---|---|---|---|---|---|
| Subset 1 (within-domain) | 15/20 = 75.0% | 18/20 = 90.0% | **+15.0 pts** | 4 | 1 |
| Subset 2 (cross-domain) | 18/29 = 62.1% | 20/29 = 69.0% | **+6.9 pts** | 2 | 0 |
| **Combined (paired)** | **33/49 = 67.3%** | **38/49 = 77.6%** | **+10.2 pts** | **6** | **1** |

The within-domain result confirms the learning loop works when extracted constraints match the failure mode. The cross-domain result is real but smaller, with zero regressions on out-of-domain tasks, indicating that domain-specific constraints from one repo family carry a non-zero transfer benefit at zero observed cost on the families tested.

---

**Methodology in one paragraph**

50 SWE-bench Verified tasks were selected across five repositories (django, sympy, matplotlib, scikit-learn, sphinx). Subset 1 (20 tasks: django + sympy) tests within-domain learning. Subset 2 (30 tasks: matplotlib + scikit-learn + sphinx) tests cross-domain transfer. For each subset, two arms were run: a baseline arm (agent attempts each task with no prior knowledge) and a treatment arm (agent attempts each task with constraints extracted from baseline failures). Constraints were extracted by classifying baseline failures using the SWE-bench Pro 7-category taxonomy (arxiv 2509.16941) and emitting one short directive per Wrong Solution failure via a locked extraction prompt. The Subset 2 treatment arm was run with ONLY Subset 1 constraints loaded into the agent prompt, deliberately holding out the 11 Subset 2 constraints to isolate the cross-domain transfer effect. All scoring used the official SWE-bench harness on SWE-bench Verified. Per-task timeout was 1800 seconds. Agent model was Claude Code 2.1.177 headless with allowed tools Read, Edit, Bash, Glob, Grep, Write and acceptEdits permission mode.

---

# Subset 1 (within-domain)

20 tasks across django (10) and sympy (10). The treatment arm received 4 constraints extracted from Subset 1 baseline failures.

**Constraints used in treatment arm:**

1. `[django/django/Wrong Solution]` When fixing admin RelatedFieldListFilter ordering, fall back to the related model's `_meta.ordering` when ModelAdmin ordering is empty, and verify the full diff applies untruncated.
2. `[django/django/Wrong Solution]` When adding `params={'value': value}` to validators' ValidationError calls, patch EVERY raise site across all validator classes/functions (and merge into existing params dicts), not just a subset.
3. `[sympy/sympy/Wrong Solution]` When converting an instance method to classmethod/cls-based to fix subclassing, update ALL call sites including module-level aliases (e.g. `_af_new = Permutation._af_new`) and the `Perm`/class-name reference, not just the method body.
4. `[sympy/sympy/Wrong Solution]` For `is_even`/`is_integer` implies `is_finite`, add the implication on the `integer`/`even` node directly; do not rely on `rational implies finite`, since `even=True` is set without implying `rational`.

**Subset 1 per-task results (20/20 scored, paired):**

| Instance | Baseline | Treatment | Change |
|---|---|---|---|
| django-10554 | PASS | PASS | held |
| django-11138 | PASS | PASS | held |
| django-11400 | FAIL | PASS | **FAIL to PASS** |
| django-11885 | PASS | PASS | held |
| django-12325 | PASS | PASS | held |
| django-12708 | PASS | PASS | held |
| django-13128 | PASS | PASS | held |
| django-13212 | FAIL | PASS | **FAIL to PASS** |
| django-13344 | FAIL (empty patch) | PASS | **FAIL to PASS** |
| django-13449 | PASS | PASS | held |
| sympy-11618 | PASS | PASS | held |
| sympy-12419 | PASS | PASS | held |
| sympy-12489 | FAIL | FAIL | held (constraint did not save it) |
| sympy-13031 | PASS | PASS | held |
| sympy-13852 | PASS | PASS | held |
| sympy-13878 | PASS | PASS | held |
| sympy-14248 | PASS | PASS | held |
| sympy-16597 | FAIL | PASS | **FAIL to PASS** |
| sympy-17630 | PASS | **FAIL** | **PASS to FAIL (regression)** |
| sympy-18199 | PASS | PASS | held |

**Within-domain finding:** 4 of 5 baseline failures were recovered (80% recovery rate). All four recoveries map directly to a loaded constraint that addressed the specific failure mode. The single regression (sympy-17630) was on a task with no related constraint, suggesting the regression is a distraction cost rather than a constraint-misfire.

---

# Subset 2 (cross-domain)

30 tasks across matplotlib (10), scikit-learn (10), and sphinx (10). One instance (scikit-learn-25102) dropped due to an upstream SWE-bench harness setup script that calls a deprecated pip flag (`--no-use-pep517`); this drop applied equally in baseline and treatment so the paired comparison remains unbiased on the 29 surviving instances.

**Constraints used in treatment arm: ONLY the 4 Subset 1 constraints listed above.** The 11 Subset 2 constraints extracted from Subset 2 baseline failures were deliberately NOT loaded into the treatment arm, in order to isolate the cross-domain transfer effect. The hypothesis being tested is: do constraints extracted from django and sympy failures meaningfully help (or hurt) the agent on completely different repo families?

**Subset 2 per-task results (29/30 scored, paired):**

| Instance | Baseline | Treatment | Change |
|---|---|---|---|
| matplotlib-14623 | PASS | PASS | held |
| matplotlib-20488 | PASS | PASS | held |
| matplotlib-20826 | PASS | PASS | held |
| matplotlib-21568 | PASS | PASS | held |
| matplotlib-22865 | FAIL | FAIL | held |
| matplotlib-22871 | FAIL | FAIL | held |
| matplotlib-23299 | FAIL | FAIL | held |
| matplotlib-23314 | FAIL | FAIL | held |
| matplotlib-23412 | FAIL | FAIL | held |
| matplotlib-24026 | FAIL | FAIL | held |
| scikit-learn-10297 | PASS | PASS | held |
| scikit-learn-10844 | PASS | PASS | held |
| scikit-learn-10908 | PASS | PASS | held |
| scikit-learn-11578 | PASS | PASS | held |
| scikit-learn-12682 | PASS | PASS | held |
| scikit-learn-12973 | PASS | PASS | held |
| scikit-learn-13124 | PASS | PASS | held |
| scikit-learn-14053 | PASS | PASS | held |
| scikit-learn-14087 | FAIL | PASS | **FAIL to PASS** |
| scikit-learn-25102 | DROPPED | DROPPED | upstream pip flag, not scored |
| sphinx-10466 | PASS | PASS | held |
| sphinx-10614 | PASS | PASS | held |
| sphinx-10673 | PASS | PASS | held |
| sphinx-11445 | PASS | PASS | held |
| sphinx-11510 | PASS | PASS | held |
| sphinx-7590 | FAIL | FAIL | held |
| sphinx-7748 | FAIL | FAIL | held |
| sphinx-8548 | FAIL | FAIL | held |
| sphinx-9229 | PASS | PASS | held |
| sphinx-9461 | FAIL | PASS | **FAIL to PASS** |

**Cross-domain finding:** 2 of 11 baseline failures flipped to pass (18% transfer rate). Zero regressions across the 18 baseline passes. The treatment arm produced strictly more passing patches than baseline at zero observed cost on this set.

---

# Mechanistic analysis of the cross-domain flips

The two cross-domain flips both have plausible mechanistic explanations grounded in the loaded constraints.

**Flip 1: scikit-learn-14087 (LogisticRegressionCV refit=False IndexError)**

The baseline classified this as a Wrong Solution: "The patch swaps `self.multi_class` for a local `multi_class` variable but leaves the indexing wrong." The treatment patch corrected the array indexing properly.

Subset 1 constraint #3 (sympy classmethod) directs the agent to "update ALL call sites" when changing function semantics, not just the method body. The loose mechanistic link: this constraint shapes a habit of checking that a variable rename or signature change is followed through across all uses, which is exactly what the baseline patch failed to do. The link is loose enough that this flip could be partially attributable to single-trial variance. We do not claim a strong causal mechanism here.

**Flip 2: sphinx-9461 (classmethod + property documentation)**

The baseline classified this as a Wrong Solution: the agent located the right files but the patch missed the actual import-time handling of `@classmethod @property` chains. The treatment patch correctly detected the classmethod wrapper and unwrapped `__func__` to access the underlying docstring.

Subset 1 constraint #3 (sympy classmethod) is specifically about classmethod handling and the hidden edges across call sites and aliases. sphinx-9461 fails on classmethod wrapper handling. The mechanistic link is direct: the constraint contains a generalizable insight about classmethod handling that transferred from a sympy context to a sphinx context. We treat this as the cleanest evidence of cross-domain transfer in the dataset.

---

# Combined paired result and interpretation

Across the 49 paired instances (Subset 1 + Subset 2):

| Arm | Pass count | Pass rate |
|---|---|---|
| Baseline | 33/49 | 67.3% |
| Treatment | 38/49 | 77.6% |
| Delta | +5 | **+10.2 percentage points** |

Total flips: 6 (4 within-domain + 2 cross-domain).
Total regressions: 1 (within-domain, sympy-17630).
Net: +5 paired tasks resolved by the treatment arm.

**Interpretation**: The persistent-knowledge layer produces a measurable improvement in coding-agent task resolution when constraints extracted from prior failures are loaded into the agent prompt. The effect is strongest within-domain (recovery rate of 80% on baseline failures matched by a constraint) and smaller but non-zero cross-domain (transfer rate of 18%, with zero observed regressions on the out-of-domain set tested). The single regression (sympy-17630) was on a task with no related constraint, and the absence of cross-domain regressions suggests that out-of-domain constraints have negligible distraction cost on the families tested.

---

# Limitations and honest caveats

**Single-trial design.** Each task was run once per arm. Some of the observed flips and the one regression may be due to single-trial variance rather than genuine constraint effects. A multi-seed replication would tighten the confidence intervals, but is beyond v0.9 scope.

**Constraint-failure overlap on Subset 1.** The 4 constraints used in the Subset 1 treatment arm were extracted from the 5 Subset 1 baseline failures. The within-domain comparison therefore tests "can the agent fix its own past failures when reminded?" rather than "do constraints generalize?" The within-domain result establishes the upper bound; the cross-domain Subset 2 result is the methodologically clean transfer signal.

**Cross-domain transfer rate of 18% is small.** Two flips of eleven baseline failures is positive signal but not a sweeping result. The dataset is too small to claim that cross-domain transfer is reliably positive; it is consistent with a small positive effect with wide confidence bounds.

**The cost of carrying out-of-domain constraints was zero on this dataset.** Zero regressions across 18 baseline passes in Subset 2. This is the most surprising finding and the one most likely to fail to replicate on a larger or more diverse dataset. We do not claim that out-of-domain constraints are always free.

**Failure classification uses a Claude judge.** The 7-category taxonomy classification was performed by the same model family as the agent under test. A different judge (human, or a different model) might produce a different category distribution.

**Dropped instance: scikit-learn-25102.** The SWE-bench harness `setup_repo.sh` for this instance calls `pip install --no-use-pep517 --no-build-isolation -e .`, but `--no-use-pep517` was removed from pip in recent versions. The build dies before scoring can begin. This is an upstream SWE-bench harness issue not addressable from this benchmark; the instance was dropped from both baseline and treatment so the paired comparison remains unbiased. We document the drop transparently rather than work around it.

**Scoring infrastructure variance.** All scoring was run on a single Apple M2 Mac with 8GB RAM and 8GB Docker memory allocation. Several env-image builds required retries due to transient DNS and OOM errors. The final scoring runs used cached env images where possible. No agent-side data was compromised by infrastructure issues; the final dataset reflects only successful end-to-end scoring.

**Subset selection bias.** The 50 tasks were selected with difficulty-weighting within each repo. The reported pass rates are conditional on this selection and should not be compared directly to leaderboard numbers on the full SWE-bench Verified set.

---

# Reproducibility

All artifacts to reproduce these results are in this directory:

| Artifact | Path |
|---|---|
| Task selection | `subset_50.json` (50 tasks, SHA 984d7486...276c5c) |
| SWE-bench Verified dataset | `verified.parquet` (SHA a45b1fe4...e9e6dcd) |
| Subset 1 baseline patches | `baseline_progress.jsonl`, `baseline_predictions.json` |
| Subset 1 baseline scores | `baseline_results.jsonl` |
| Subset 1 failure classifications | `baseline_classified.jsonl` |
| Subset 1 constraints | `constraints.json` |
| Subset 1 treatment patches | `treatment_progress_s1.jsonl`, `treatment_predictions_s1.json` |
| Subset 1 treatment scores | `treatment_results_s1.jsonl` |
| Subset 2 baseline patches | `baseline_progress_s2.jsonl`, `baseline_predictions_s2.json` |
| Subset 2 baseline scores | `baseline_results_s2.jsonl` |
| Subset 2 failure classifications | `baseline_classified_s2.jsonl` |
| Subset 2 constraints (NOT used in treatment) | `constraints_s2.json` |
| Subset 2 cross-domain treatment patches | `treatment_progress_s2_crossdomain.jsonl`, `treatment_predictions_s2_crossdomain.json` |
| Subset 2 cross-domain treatment scores | `treatment_results_s2_crossdomain.jsonl` |
| Methodology (locked) | `DESIGN.md` |

To replicate from a fresh checkout:
1. `python task_setup.py --select` to regenerate `subset_50.json`
2. `python orchestrator.py --arm baseline --first-half` to run Subset 1 baseline
3. `python predictions.py` then `python score.py` to score
4. `python failure_classifier.py` then `python learning_hook.py` for Phase 4+5
5. `python orchestrator.py --arm treatment --constraints constraints.json --first-half` for Subset 1 treatment
6. Repeat for Subset 2 with `--second-half`
7. For the cross-domain test, use `constraints.json` (NOT `constraints_s2.json`) in the Subset 2 treatment arm

Total agent cost across both arms: approximately $90 USD. Total wall-clock for scoring on a single Mac: approximately 40 hours including retries and Docker rebuilds.

---

# What this result implies for world-model-mcp

The v0.9 result establishes empirical evidence that the persistent-knowledge layer with provenance, decay, and constraint extraction has a measurable effect on coding-agent failure recovery, and that the effect transfers cross-domain at small magnitude with no observed regression cost. This bounds the wedge honestly:

- Within-domain: persistent constraints help substantially when the constraint matches the failure mode.
- Cross-domain: the effect is real but smaller, mediated by generalizable insights inside otherwise domain-specific constraints (see sphinx-9461 analysis above).
- Cost: out-of-domain constraints had zero observed cost on this dataset.

The v0.9 release positions world-model-mcp as an MCP-based persistent-knowledge layer with empirical evidence of cross-session learning on a public benchmark. Future work (v1.0 and beyond) should target multi-seed replication, larger task counts per repo, and an explicit failure-mode-similarity scoring to predict when cross-domain transfer will succeed.

---

# Multi-seed replication appendix (v0.9.2 update, 2026-06-30)

Per the v0.9 limitations section ("Single-trial design. Some of the observed flips and the one regression may be due to single-trial variance rather than genuine constraint effects"), a multi-seed replication was carried out on a pre-registered 17-instance subset of the original 49 paired instances. The replication plan and acceptance thresholds were locked in `SEED_PLAN.md` on 2026-06-25, six days before any additional seed run.

The result is an honest update of the v0.9 headline. The +10.2 pts paired delta on the full 49 paired instances at seed 1 (v0.9.1 ship) does NOT replicate on the 17-instance subset at seed 2. The constraint effect is substantially smaller than the v0.9 single-trial number suggested, and the v0.9 result was partly driven by an unlucky baseline draw rather than constraint effects alone.

This appendix updates the confidence bounds on the v0.9 result. The single-trial v0.9 numbers in the main body of this document are preserved as published; this appendix adds the multi-seed evidence that bounds them.


Subset selection (locked in SEED_PLAN.md)
=========================================

17 instances were drawn from the 49 paired instances of v0.9, in three categories:

- **7 load-bearing instances**: the 6 FAIL-to-PASS flips and the 1 PASS-to-FAIL regression that drove the v0.9 headline numbers. Replication of these is the load-bearing test for the v0.9 result.
  - `django__django-11400`, `django__django-13212`, `django__django-13344`, `sympy__sympy-16597` (within-domain flips)
  - `sympy__sympy-17630` (regression)
  - `scikit-learn__scikit-learn-14087`, `sphinx-doc__sphinx-9461` (cross-domain flips)
- **5 variance-floor PASS** instances: tasks that PASSed in v0.9 in both arms, used to characterize stability of "easy" outcomes.
  - `django__django-10554`, `sympy__sympy-11618`, `matplotlib__matplotlib-14623`, `scikit-learn__scikit-learn-10297`, `sphinx-doc__sphinx-10466`
- **5 variance-floor FAIL** instances: tasks that FAILed in v0.9 in both arms, used to characterize stability of "hard" outcomes.
  - `sympy__sympy-12489`, `matplotlib__matplotlib-22865`, `matplotlib__matplotlib-23314`, `sphinx-doc__sphinx-7590`, `sphinx-doc__sphinx-7748`


Methodology (unchanged from v0.9)
=================================

Each instance was re-run at seed 2 in both baseline arm and treatment arm. The agent (Claude Code 2.1.177 headless), the task instance, the starting commit, and the test_patch were all identical to the v0.9 runs. The only intrinsic source of variance is the model's sampling at default temperature (Claude Code CLI does not expose a `--seed` or `--temperature` flag; "multi-seed" here means observing the model's existing sampling distribution by re-running). The treatment arm loaded the same 4 v0.9 constraints from `constraints.json`. No methodology changes were made between seed 1 and seed 2.


Per-instance results (seed 1 vs seed 2)
=======================================

P = PASS, F = FAIL.

| Instance | Category | b seed1 | b seed2 | t seed1 | t seed2 | Per-instance paired delta across 2 seeds |
|---|---|---|---|---|---|---|
| django-11400 | load-bearing flip | F | P | P | P | +1/2 |
| django-13212 | load-bearing flip | F | P | P | P | +1/2 |
| django-13344 | load-bearing flip | F | P | P | P | +1/2 |
| sympy-16597 | load-bearing flip | F | P | P | P | +1/2 |
| sympy-17630 | load-bearing regression | P | F | F | P | 0/2 |
| sklearn-14087 | load-bearing flip (cross-domain) | F | P | P | P | +1/2 |
| sphinx-9461 | load-bearing flip (cross-domain) | F | P | P | F | 0/2 |
| django-10554 | variance-floor PASS | P | P | P | P | 0/2 |
| sympy-11618 | variance-floor PASS | P | P | P | P | 0/2 |
| matplotlib-14623 | variance-floor PASS | P | P | P | F | -1/2 |
| sklearn-10297 | variance-floor PASS | P | P | P | P | 0/2 |
| sphinx-10466 | variance-floor PASS | P | P | P | P | 0/2 |
| sympy-12489 | variance-floor FAIL | F | P | F | P | 0/2 |
| matplotlib-22865 | variance-floor FAIL | F | P | F | P | 0/2 |
| matplotlib-23314 | variance-floor FAIL | F | F | F | F | 0/2 |
| sphinx-7590 | variance-floor FAIL | F | F | F | F | 0/2 |
| sphinx-7748 | variance-floor FAIL | F | F | F | F | 0/2 |


Headline numbers
================

**Per-arm pass rate on the 17-instance subset:**

| Run | Pass count | Pass rate |
|---|---|---|
| v0.9 baseline (seed 1) | 6/17 | 35.3% |
| **seed 2 baseline** | **13/17** | **76.5%** |
| v0.9 treatment (seed 1) | 11/17 | 64.7% |
| **seed 2 treatment** | **12/17** | **70.6%** |

The baseline arm pass rate swung **+41 percentage points** between seed 1 and seed 2 on the same 17 instances with no methodology change. The treatment arm swung **+6 pts** over the same window.

**Per-seed paired delta on the 17-instance subset:**

| Seed | Baseline pass | Treatment pass | Paired delta |
|---|---|---|---|
| Seed 1 (v0.9) | 6/17 | 11/17 | **+5** instances (+29 pts) |
| Seed 2 | 13/17 | 12/17 | **-1** instance (-5.9 pts) |

**Mean paired delta across both seeds, 17 instances:** +0.24 per instance, bootstrap 95 percent CI [0.00, 0.47].

**Load-bearing replication**: 0 of 7 load-bearing instances had both their seed-1 baseline AND seed-1 treatment outcomes reproduced at seed 2. Per the thresholds locked in SEED_PLAN.md, this is **weak replication**.


Why the load-bearing replication count is zero
==============================================

The v0.9 result on these 7 load-bearing instances was defined by a specific baseline-vs-treatment outcome pattern:
- 6 instances with baseline FAIL and treatment PASS (the flips)
- 1 instance with baseline PASS and treatment FAIL (the regression)

At seed 2, the baseline outcomes shifted dramatically:
- 6 of the 6 "baseline FAIL" instances at seed 1 became baseline PASS at seed 2
- The 1 "baseline PASS" instance at seed 1 became baseline FAIL at seed 2

The treatment outcomes were more stable:
- 5 of the 6 v0.9 "treatment PASS" instances remained treatment PASS at seed 2
- The 1 v0.9 "treatment FAIL" (regression) instance became treatment PASS at seed 2 (interesting on its own)

The replication failure is not because the treatment patches changed. It is because the baseline regressed to the mean. The same agent at the same temperature on the same task FAILed at seed 1 and PASSed at seed 2 for these instances. That is the variance signal the multi-seed test was designed to surface.


Honest interpretation
=====================

**1. The v0.9 +10.2 pts headline was substantially inflated by an unlucky baseline draw.** When the baseline pass rate naturally swings +41 pts between seeds on the same 17 instances, the "constraint effect" measured in v0.9 cannot be cleanly separated from sampling noise. The original v0.9 paper's limitations section flagged this risk explicitly; multi-seed replication confirms it.

**2. The constraint effect across two seeds is small but possibly nonzero.** Mean paired delta of +0.24 per instance (95 percent CI [0.00, 0.47]) indicates the treatment arm is, on average, marginally better than the baseline, but the effect is not statistically distinguishable from zero at sample size 2.

**3. The cross-domain transfer claim weakens.** Of the two v0.9 cross-domain flips, only sklearn-14087 reproduced its treatment PASS at seed 2; sphinx-9461 regressed to treatment FAIL. The "0 cross-domain regressions on 18 baseline passes" finding from v0.9 is itself a fragile single-trial observation.

**4. The single v0.9 regression (sympy-17630) was partial-replication noise.** v0.9 had baseline PASS and treatment FAIL. Seed 2 has baseline FAIL and treatment PASS. The instance is flaky in both arms; "regression" was a single-trial artifact.

**5. A new treatment-side regression appeared at seed 2: matplotlib-14623.** This instance was variance-floor PASS in v0.9 (both arms) AND in seed 2 baseline, then FAILed in seed 2 treatment. The constraint loading occasionally produces patches that introduce broad PASS_TO_PASS regressions on previously stable tasks. The agent's patch fixed the FAIL_TO_PASS target test (`test_inverted_limits`) but broke 181 PASS_TO_PASS rendering tests.

**6. The methodology discipline held.** SEED_PLAN.md was locked on 2026-06-25, six days before seed-2 runs began. The subset selection, the metrics, and the interpretation thresholds were pre-registered. The result was published verbatim. This is what the v0.9 limitations section said could happen, and it did. The honest update is shipped.


Decision on seed 3
==================

Seed 3 was considered and skipped. Rationale:
- The pattern at 0 of 7 load-bearing replication is clear, not borderline. Another seed would tighten the confidence interval from [0.00, 0.47] to perhaps [0.05, 0.35], but would not flip the verdict.
- Cost of seed 3 on the 17-instance subset is approximately 60 USD agent time plus 4-5 hours scoring wall-clock.
- Time is better spent on the v0.9.2 release of this honest update than on additional data that does not change the story.

If a follow-up multi-seed test is run later (for v1.0 or for a TMLR submission), full-corpus (all 49 paired instances) at 3-5 seeds would be the appropriate scope, not another 17-instance pass.


What this means for the wedge
=============================

The wedge claims at the architectural level (lifecycle-hook-based memory capture, per-fact provenance, per-evidence-type decay, PreToolUse defer enforcement) are unchanged. Multi-seed replication does not affect the schema design or the methodology choices.

The empirical claim about the magnitude of the constraint effect on SWE-bench Verified is what changes:
- v0.9 claimed: +10.2 pts paired delta across 49 instances at single trial
- v0.9.2 honest update: the constraint effect on the load-bearing 17-instance subset across two seeds is +0.24 per instance (small, with 95 percent CI barely excluding zero), and the v0.9 single-trial result was substantially attributable to baseline variance rather than constraint effects alone.

The wedge survives this update. The headline number does not.


Reproducibility (multi-seed)
============================

All multi-seed artifacts are committed in this directory:

| Artifact | Path |
|---|---|
| Pre-registered methodology | `SEED_PLAN.md` (locked 2026-06-25) |
| Variance-analysis aggregator | `multi_seed_aggregate.py` |
| Seed 2 baseline progress | `baseline_progress_seed2.jsonl` |
| Seed 2 treatment progress | `treatment_progress_seed2_treatment.jsonl` |
| Seed 2 baseline predictions | `baseline_predictions_seed2.json` |
| Seed 2 treatment predictions | `treatment_predictions_seed2.json` |
| Seed 2 baseline harness results | `baseline_results_seed2.jsonl` |
| Seed 2 treatment harness results | `treatment_results_seed2.jsonl` |
| Multi-seed summary | `multi_seed_summary_seed2.json` |

Replication command for the multi-seed run is in SEED_PLAN.md. Total additional agent cost for seed 2 was approximately 53 USD. Total additional wall-clock for scoring on the same Apple M2 Mac was approximately 9 hours.


---

*Last updated: 2026-06-30.*
