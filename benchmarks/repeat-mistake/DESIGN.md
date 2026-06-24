# Repeat-mistake benchmark — design doc (LOCKED 2026-06-17)

The empirical test of world-model-mcp's central wedge: does the
learning loop measurably reduce repeated coding-agent mistakes on a
public task corpus, when the only difference between baseline and
treatment arms is whether world-model-mcp is in the loop?

This design was verified against primary sources via three
independent LLM cross-checks (Claude top-level WebFetch + WebSearch,
Grok, Gemini). All three converged on the same conclusion: no
published precedent exists for a clean memory-layer DELTA on
SWE-bench. This benchmark is genuinely first-of-its-kind.

## Hypothesis

**H0 (null)**: world-model-mcp does not reduce repeat-mistake rate
on SWE-bench Verified task pairs vs. a baseline of `claude -p`
headless without the memory layer.

**H1 (alternative)**: world-model-mcp reduces the rate at which the
same failure category from the SWE-bench Pro 7-category taxonomy
recurs across paired tasks, with a delta meaningfully greater than
the noise floor estimated from the OpenAI Feb 2026 retrospective
(59.4% of SWE-bench Verified test failures trace to test defects,
not model failures, per OpenAI's own audit).

**Practical interpretation thresholds** (set in advance, NOT after
seeing results):

- 0-5% delta: hypothesis rejected. Memory layer does not help on
  SWE-bench-style single-PR-scope tasks. Honest negative result;
  still publishable as a contribution because it bounds where
  memory layers help.
- 5-15% delta: modest signal. Real but bounded.
- 15-30% delta: meaningful signal. Maps to the published essay
  framing.
- 30%+ delta: suspicious. Triple-check methodology before
  publishing because results this strong on the first try are
  usually evidence of contamination, data leakage, or judge bias.

## Corpus

- **Dataset**: SWE-bench Verified, 500 tasks, MIT licensed
  - Source: https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified
  - Format: parquet, 2.1MB, 500 rows
  - Required columns: `instance_id`, `patch`, `repo`, `base_commit`,
    `problem_statement`, `FAIL_TO_PASS`, `PASS_TO_PASS`,
    `test_patch`, `environment_setup_commit`
- **Subset selected for v0.9**: 50 tasks
  - Selection method: 5 repos with 10 tasks each
  - Repo selection criteria (in priority order):
    1. Repos with ≥10 tasks in Verified (rules out long tail)
    2. Repos with documented long-codebase / many-file scenarios
       where SWE-bench Pro's "Endless File Reading" failure mode
       has been observed (this is where memory pays the largest
       theoretical dividend)
    3. Repos with mixed task types (bugfix + feature) so the
       failure-mode distribution is varied
  - SHA-pinned: SWE-bench Verified commit at HEAD of
    https://github.com/SWE-bench/SWE-bench/tree/main as of
    benchmark run date (will be recorded in `results.json`)

## Honest disclosure of corpus limitations

Embedded in RESULTS.md verbatim, not hidden in an appendix:

1. **The OpenAI Feb 2026 retrospective** found 59.4% of SWE-bench
   Verified test failures trace to test defects, not model
   failures. Specifically: 35.5% overly strict tests, 18.8% tests
   for unmentioned features, 5.1% environmental dependencies. This
   noise floor affects both arms equally, so the DELTA between arms
   is still signal; absolute pass rates are not.
2. **OpenAI's original Verified selection process** filtered out
   ~68.3% of the 1,699 reviewed tasks ("overzealous filter to
   maximize confidence") and retained 500, prioritizing harder
   tasks (1-4+ hour human solve time). Even with this filter, the
   59.4% test-defect rate persists.
3. **Pebblous Lab** named SWE-bench Verified as one of 8 broken
   benchmarks because "test code shared evaluator environment;
   answers embedded in tests." This is a known issue. We do not
   contest it; we report a DELTA rather than absolute scores so
   the issue cancels.

## Methodology

### Architecture (revised 2026-06-17 after primary-source verification)

The benchmark splits cleanly into two phases per arm:

**Phase A: Prediction generation (our code)**
For each task, drive `claude -p` agentically against a checked-out
copy of the repo and capture the resulting patch. The agent runs as
`claude -p <prompt> --allowedTools Read,Edit,Bash,Glob,Grep
--permission-mode acceptEdits --output-format json` inside the repo
checkout. The agent reads files, makes edits, runs commands; we
extract the final patch via `git diff` after the agent finishes.

**Phase B: Patch evaluation (official SWE-bench harness)**
Feed all 50 patches to `python -m swebench.harness.run_evaluation
-d princeton-nlp/SWE-bench_Verified -p predictions.json -id <run_id>
-n ''`. The harness handles Docker container setup, environment
config per task, FAIL_TO_PASS / PASS_TO_PASS test execution, and
scoring. Output: official `results.json` per task with resolved /
unresolved status.

This architecture is **maximally rigorous** because:
- Patch generation matches how real users invoke Claude Code
- Patch evaluation uses the same harness as every published
  SWE-bench Verified leaderboard entry
- The two phases are independently verifiable

### Step 1: Baseline arm — prediction generation

For each of the 50 tasks WITHOUT world-model-mcp:

1. Clone `repo` at `base_commit` into a temp directory
2. Apply `test_patch` to that checkout (adds new tests but not the
   fix; per SWE-bench convention)
3. Invoke `claude -p` with the agent prompt and allowed tools
   pointed at the checkout directory
4. Wait for completion (subprocess timeout = 30 minutes per task)
5. Extract the patch via `git diff HEAD` (relative to the post-
   test-patch state, so only the agent's changes are captured)
6. Append `{instance_id, model_patch, model_name_or_path}` to
   `baseline_predictions.json`
7. Append session metadata (cost, session_id, duration) to
   `baseline_metadata.jsonl`

### Step 2: Baseline arm — patch evaluation

Run the official harness on the predictions:

```
python -m swebench.harness.run_evaluation \
  -d princeton-nlp/SWE-bench_Verified \
  -p baseline_predictions.json \
  -id v0.9-baseline-half1 \
  -n '' \
  --max_workers 1
```

Outputs `logs/run_evaluation/v0.9-baseline-half1/.../results.json`
per task with pass/fail. Combined into `baseline_results.jsonl`.

### Step 2: Failure classification

For every `unresolved` task in baseline_results, classify the
failure using the SWE-bench Pro 7-category taxonomy:

1. **Wrong Solution** — functionally incorrect patch
2. **Tool-Use** — improper tool calls
3. **Syntax Error** — compilation/runtime errors
4. **Incorrect File** — modified wrong file
5. **Endless File Reading** — non-productive exploration loops
6. **Misunderstood Problem Statement** — fundamental task misread
7. **Other** — computational limits / compounding

Judge prompt (locked, will be published verbatim in
`benchmarks/repeat-mistake/judge_prompts.py`):

```
You are evaluating a failed coding agent attempt at a real-world
software engineering task. Classify the dominant failure mode
from this exact taxonomy (categories defined in SWE-bench Pro
paper arxiv 2509.16941):

[7 category definitions here]

Read the agent transcript and the actual outcome below.
Respond with EXACTLY ONE category name, no commentary.

[transcript]
[outcome]
```

Judge model: `claude -p` headless (same as the agent — using
Claude here too because the SWE-bench Pro precedent was
GPT-5-as-judge with 87% human alignment; we use Claude because
the audience runs Claude Code and the methodology must be
reproducible by anyone with a Claude subscription).

Output: `baseline_classified.jsonl` adds `failure_category`
column.

### Step 3: World-model-mcp learning

For each baseline failure, extract a constraint and write it to
the world-model-mcp knowledge graph:

- **Tool-Use**: extract the failing tool call pattern → constraint
  with `evidence_type = "session"`, `severity = "warning"`
- **Incorrect File**: extract the file path that should NOT have
  been edited → constraint with `evidence_type = "user_correction"`,
  `severity = "error"`
- **Endless File Reading**: extract the file pattern that was
  read repeatedly → constraint with `evidence_type = "session"`,
  `severity = "warning"`
- **Wrong Solution**: extract the incorrect approach as a fact →
  fact with `evidence_type = "session"`, `confidence = 0.5`
- **Syntax Error**: extract the syntactic pattern → constraint
  with `evidence_type = "bug_fix"`, `severity = "error"`
- **Misunderstood Problem Statement**: extract user-correction
  fact → fact with `evidence_type = "user_correction"`,
  `confidence = 0.85`
- **Other**: no constraint extracted; logged

The constraint-extraction prompt is the SAME for all tasks within
a category (locked in `constraint_extraction.py`).

### Step 4: Treatment arm

Run `claude -p` headless on each of the 50 tasks WITH
world-model-mcp providing PreToolUse constraint checks and
PostCompact re-injection. The world-model-mcp constraints from
Step 3 are pre-loaded.

Same task setup, same scoring. Output: `treatment_results.jsonl`.

### Step 5: Per-category scoring

For each task that had baseline failure category C, check whether
the treatment arm:

- Resolved the task (best outcome): treatment_status = "resolved"
- Failed but with a DIFFERENT category (memory layer redirected
  the failure mode): treatment_status = "different_category"
- Failed with the SAME category (memory layer did NOT help):
  treatment_status = "same_category"

**Primary metric**:

```
category_avoidance_rate[C] = 
    (# tasks where baseline failed with C AND treatment is NOT C) /
    (# tasks where baseline failed with C)
```

This is reported per-category and overall.

**Secondary metric**:

```
pass_rate_delta = treatment_pass_rate - baseline_pass_rate
```

Reported with the explicit 59.4% test-noise caveat.

## Seeds and statistical handling

- **First run**: 1 seed (single overnight)
- **If category_avoidance_rate > 5% for any category**: re-run
  with 2 more seeds, report mean ± std
- **3-seed minimum for publication-ready numbers** (single-seed
  numbers are not statistically rigorous but they're the standard
  for sanity-check runs)

## Reproducibility commitments

Everything below is published in `benchmarks/repeat-mistake/`:

1. SHA-pinned SWE-bench commit at run time
2. `claude --version` output at run time
3. Full judge prompts (extraction + classification)
4. Per-task `instance_id` list for the 50-task subset
5. Full per-task results in `baseline_results.jsonl` and
   `treatment_results.jsonl`
6. The exact world-model-mcp constraints written during Step 3
7. A `Dockerfile` or `setup.sh` for running the benchmark cleanly

## Cost and time

- **Compute cost**: $0 marginal. Claude subscription only.
- **Wall-clock**: ~8 hours per arm × 2 arms = ~16 hours for 1 seed
- **Engineering**: 2-3 days harness build + 1 day RESULTS.md
- **Total**: 7-10 days from approval to v0.9 ship

## What this benchmark does NOT do

Honest scope limits, embedded in RESULTS.md:

1. **Does not test cross-tool memory.** All tasks run on Claude
   only. Mem0/Dakera/piia-engram-style "use the same memory
   from Claude + Cursor + Codex" is v0.10+ work.
2. **Does not test long-horizon (multi-day) tasks.** SWE-bench
   Verified tasks are single-PR-scope. The biggest theoretical
   gains from a memory layer come from multi-session work; this
   benchmark bounds the lower end.
3. **Does not test against other memory layers.** Baseline is
   `claude -p` without anything. Adding Mem0 / Dakera / piia-engram
   as third/fourth arms is genuinely useful comparative work but
   adds 2-4 weeks. It is explicitly v0.10 scope.
4. **Does not use a third-party judge model.** Using Claude for
   judging when Claude is the agent is a self-reference risk.
   We disclose this and run a manual sample-check on a random
   20% of classifications to estimate judge bias.

## Acceptance criteria for v0.9 ship

Either of these is sufficient:

A. **Compelling result**: per-category avoidance rate > 15% on at
   least 2 categories. Ship with full RESULTS.md as headline.
B. **Honest negative result**: per-category avoidance rate ≤ 5%
   across all categories. Ship with RESULTS.md framed as "memory
   layer helps on real-world cross-session tasks (per Coding
   Agents Fail arxiv 2605.29442 finding that 26.95% of real-world
   failures are Misread Developer Intent), but does NOT
   meaningfully change SWE-bench Verified single-PR-scope outcomes.
   This bounds where memory layers help and where they don't."

We will NOT ship if:
- Methodology shortcuts compromise reproducibility
- Judge bias is severe (>20% disagreement on sample-check)
- Wall-clock runs reveal harness bugs that affect both arms
  unequally

## Citations

- SWE-bench paper: https://arxiv.org/abs/2310.06770
- SWE-bench Verified introduction: OpenAI blog Aug 2024
- SWE-bench Pro failure taxonomy: https://arxiv.org/abs/2509.16941
- Coding Agents Fail (real-world): https://arxiv.org/abs/2605.29442
- OpenAI Feb 2026 retrospective on 59.4% test-defect rate
- Pebblous Lab 8-broken-benchmarks report:
  https://blog.pebblous.ai/report/ai-agent-benchmark-trust/en/
- world-model-mcp v0.8.0 schema work this benchmark validates:
  RELEASE_NOTES.md
