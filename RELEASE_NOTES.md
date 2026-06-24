# World Model MCP - Release Notes

## v0.9.1 (June 2026)

Release-mechanics patch. No methodology changes; v0.9.0 benchmark results and RESULTS.md stand unchanged.

### What this fixes

v0.9.0 was built without running `scripts/embed_token.py` before `python -m build`. The published wheel shipped with an empty `EMBEDDED_TOKEN` stub. Per the design in `RELEASE.md`, this causes opt-in telemetry to silently no-op for v0.9.0 users. No functional regression, no crash, but the opt-in telemetry contract is not honored on v0.9.0.

v0.9.1 ships with the embed step done correctly. Opt-in telemetry now works as designed for users who set `WORLD_MODEL_TELEMETRY_ENABLED=1`. The token is bounded in scope: it can only create issues in the private `SaravananJaichandar/world-model-telemetry` repo. Stub file in git remains empty.

### What is unchanged from v0.9.0

- All v0.9 benchmark methodology, results, and artifacts in `benchmarks/repeat-mistake/`. No re-run.
- Combined paired result across 49 instances: baseline 33/49 to treatment 38/49, delta +10.2 pts.
- Within-domain Subset 1 delta +15.0 pts; cross-domain Subset 2 delta +6.9 pts; 6 flips, 1 regression, 0 cross-domain regressions.
- Full per-task tables, mechanistic analysis, and seven explicit limitations in `benchmarks/repeat-mistake/RESULTS.md`.
- 26 MCP tools, 19 CLI subcommands, 375 tests.

### Disposition of v0.9.0 on PyPI

v0.9.0 is not yanked. Users on v0.9.0 are functional but lack opt-in telemetry collection. Anyone who wants telemetry should upgrade to v0.9.1. The pattern follows the security model documented in `RELEASE.md` (ship a patch release to rotate or restore the embedded token).

---

## v0.9.0 (June 2026)

Repeat-mistake benchmark on SWE-bench Verified. The empirical wedge proof world-model-mcp was building toward.

### Headline

v0.9.0 ships the locked v0.9 repeat-mistake benchmark on SWE-bench Verified. The benchmark tests whether the persistent-knowledge layer, with provenance and constraint extraction, measurably reduces repeated coding-agent mistakes across sessions on a public task corpus. Two paired arms (baseline and treatment) were run across 50 SWE-bench Verified tasks spanning django, sympy, matplotlib, scikit-learn, and sphinx. The result is a measurable improvement in both the within-domain and cross-domain conditions, with the cross-domain condition deliberately isolated to test transfer.

Combined paired result across 49 instances: baseline 33/49 = 67.3 percent, treatment 38/49 = 77.6 percent, delta +10.2 percentage points. Within-domain delta +15.0 pts (Subset 1, django plus sympy). Cross-domain delta +6.9 pts (Subset 2, matplotlib plus scikit-learn plus sphinx). Six FAIL to PASS flips, one PASS to FAIL regression, zero cross-domain regressions on 18 baseline passes.

The methodology is locked in `benchmarks/repeat-mistake/DESIGN.md` (committed 2026-06-17, before the benchmark ran). The full results, per-task tables, mechanistic analysis of the cross-domain flips, and honest limitations are in `benchmarks/repeat-mistake/RESULTS.md`.

### What is new

- **v0.9 repeat-mistake benchmark (B0)** in `benchmarks/repeat-mistake/`, the central wedge-proof artifact this release exists to ship:
  - `DESIGN.md` (locked 2026-06-17): pre-specified hypothesis, interpretation thresholds, acceptance criteria, locked judge prompts, and honest corpus limitations. Methodology was committed before the data existed so the result cannot be accused of goalpost-moving.
  - `RESULTS.md`: full results document with per-task tables for all 49 paired instances, mechanistic analysis of the two cross-domain flips (sphinx-9461 is the cleanest case), and explicit limitations section covering single-trial design, constraint-failure overlap, dropped-instance handling, and judge-model self-reference.
  - Full raw artifacts: `baseline_progress.jsonl`, `baseline_results.jsonl`, `baseline_classified.jsonl`, `constraints.json`, `treatment_progress_s1.jsonl`, `treatment_results_s1.jsonl`, plus the Subset 2 equivalents and the cross-domain treatment artifacts.
  - Orchestrator scripts: `orchestrator.py`, `agent_runner.py`, `predictions.py`, `score.py`, `failure_classifier.py`, `learning_hook.py`. Locked judge prompts in `failure_classifier.py` and `learning_hook.py`.

- **SWE-bench Pro 7-category failure taxonomy** integrated into the classifier (arxiv 2509.16941). The judge prompt is verbatim from the SWE-bench Pro paper specification.

- **Constraint extraction prompt** locked per-category, one short directive per Wrong Solution failure. Output shape compatible with the treatment-arm orchestrator via `constraints.json`.

- **Paired comparison methodology** for cross-domain transfer: the Subset 2 treatment arm loads ONLY the Subset 1 constraints, deliberately holding out the 11 Subset 2 constraints to isolate the cross-domain effect. The 11 Subset 2 constraints are emitted to `constraints_s2.json` but NOT used in the v0.9 cross-domain test. This is the cleanest possible transfer signal from a public benchmark on this scale.

### Results

```
Per-subset and combined paired results:

  Subset 1 (within-domain: django + sympy)
    Baseline:  15/20 = 75.0%
    Treatment: 18/20 = 90.0%
    Delta:     +3 tasks, +15.0 pts
    Flips:     4 FAIL to PASS, 1 PASS to FAIL regression

  Subset 2 (cross-domain: matplotlib + sklearn + sphinx)
    Baseline:  18/29 = 62.1%   (29 paired, 1 dropped: scikit-learn-25102 upstream pip flag)
    Treatment: 20/29 = 69.0%
    Delta:     +2 tasks, +6.9 pts
    Flips:     2 FAIL to PASS, 0 regressions

  Combined (49 paired instances)
    Baseline:  33/49 = 67.3%
    Treatment: 38/49 = 77.6%
    Delta:     +5 tasks, +10.2 pts
    Flips:     6 FAIL to PASS, 1 regression
```

The two cross-domain flips both have plausible mechanistic explanations grounded in the loaded Subset 1 constraints. The strongest case is sphinx-9461 (classmethod+property documentation), where the Subset 1 sympy classmethod constraint about updating all call sites including module-level aliases transferred directly to a sphinx classmethod-wrapper unwrapping bug. See `benchmarks/repeat-mistake/RESULTS.md` section "Mechanistic analysis of the cross-domain flips" for the full reasoning.

### Honest caveats embedded in RESULTS.md

The benchmark ships with seven explicit limitations, including: single-trial design with no multi-seed replication; constraint-failure overlap on Subset 1 (the within-domain arm tests the upper bound, not generalization); the 18 percent cross-domain transfer rate is positive signal but bounded; zero cross-domain regressions is the most surprising finding and the one most likely to fail to replicate on a larger dataset; failure classification uses Claude as judge with the same model family as the agent; one dropped instance due to an upstream SWE-bench harness pip flag issue; and scoring infrastructure variance across Docker rebuilds and DNS retries. All seven limitations are stated verbatim in `RESULTS.md` rather than hidden in an appendix.

### Reproducibility

Every artifact needed to reproduce the v0.9 result is committed in this release:

- `benchmarks/repeat-mistake/subset_50.json`: the 50-task selection
- `benchmarks/repeat-mistake/verified.parquet`: SHA-pinned SWE-bench Verified snapshot
- `benchmarks/repeat-mistake/{baseline,treatment}_progress*.jsonl`: every agent attempt
- `benchmarks/repeat-mistake/{baseline,treatment}_predictions*.json`: every patch submitted to the harness
- `benchmarks/repeat-mistake/{baseline,treatment}_results*.jsonl`: every harness score
- `benchmarks/repeat-mistake/baseline_classified*.jsonl`: every failure classification
- `benchmarks/repeat-mistake/constraints*.json`: every extracted constraint
- `benchmarks/repeat-mistake/score_*.log`: full harness invocation logs
- Locked judge prompts in `failure_classifier.py` and `learning_hook.py`

Replication command sequence is in the `Reproducibility` section of `RESULTS.md`. Total agent cost across both arms was approximately 90 USD on a Claude Code subscription. Total wall-clock for scoring on a single Apple M2 Mac was approximately 40 hours including retries.

### What this means for the wedge

The v0.9 result is the empirical evidence world-model-mcp has been building toward since v0.7.0. The persistent-knowledge layer with provenance, decay, and constraint extraction produces a measurable improvement in coding-agent failure recovery, with the effect strongest within-domain (80 percent recovery rate when the constraint matches the failure mode) and present cross-domain (18 percent transfer rate, zero observed regressions on this dataset). The wedge is bounded honestly: domain-specific constraints help most when they match; out-of-domain constraints had zero observed cost on the families tested. Future work targets multi-seed replication and a larger task corpus.

---

## v0.8.1 (June 2026)

Contradiction-resolution benchmark expansion. Honest internal schema-correctness check.

### Headline

v0.8.1 is a focused incremental cut: the v0.7.4 24-pair contradiction-resolution benchmark expands to 105 hand-curated pairs across 19 categories, including 6 new categories that exercise the v0.8.0 schema specifically. The runner stays deterministic; results are reproducible bit-for-bit. The numbers are intentionally framed as internal schema-correctness validation, not as a category benchmark or wedge proof — the wedge benchmark (repeat-mistake rate on AI coding tasks) is in v0.9 design and is what the published essay framing primes for.

### What is new

- **Expanded contradiction-resolution benchmark (F1)** -- new `benchmarks/contradictions-200/` directory with three files:
  - `dataset.jsonl`: 105 pairs across 19 categories. The 13 v0.7.4 categories are preserved (with 4-10 pairs each, up from 1-3); 6 new categories test the v0.8.0 schema.
  - `run.py`: deterministic runner. Scores 5 strategies (4 v0.7 canonical + 1 new v0.8 decay-aware) per pair. The decay-aware strategy is scored only on pairs where evidence_type is present, because without it the function returns input confidence unchanged and the strategy degenerates into keep_higher_confidence; counting those degenerate wins would inflate the score.
  - `RESULTS.md`: full per-strategy + per-category breakdown with honest methodology disclosure.

- **Six new categories that test the v0.8.0 schema**:
  - `source_tool_corroboration`: distinct source_tool values across rows should count as independent corroboration
  - `confirmer_overrides_pending`: a settled fact (confirmer != NULL) should beat a higher-confidence pending fact under auto
  - `decay_advantage_session_vs_source`: same age, same confidence; the difference is evidence_type. With decay on, source_code beats session because session decays 26x faster
  - `decay_advantage_stale_session`: a younger session fact loses to an older bug_fix fact because the session has decayed below
  - `evidence_type_user_correction`: user_correction beats session even when older because the half-life is 52x longer
  - `settled_beats_higher_confidence`: a fact with confirmer="user" beats a higher-confidence pending fact

### Results

```
Per-strategy accuracy on the 105-pair dataset:
  keep_higher_confidence              85/105  (81.0%)
  keep_most_recent                    56/105  (53.3%)
  keep_most_sources                  104/105  (99.0%)
  auto                                81/105  (77.1%)
  keep_higher_confidence_decayed      19/ 21  (90.5%) [skipped 84]

Overall: 345/441 (78.2%)
```

The numbers are LOWER than the v0.7.4 93.5% headline. This is intentional and is by design of the new categories: the v0.7.4 dataset tested raw confidence ranking; the v0.8.1 dataset tests schema awareness (confirmer, evidence_type, decay). The auto strategy in particular fails on the new categories because it does not yet know about confirmer or evidence_type — that rewrite is in v0.9 scope.

### What is not in this release

- The repeat-mistake benchmark on AI coding tasks. That is v0.9 and is the wedge proof the published essay primes for. It needs careful methodology design grounded in primary sources (SWE-bench Verified subset selection, mistake-pattern taxonomy, scoring). Targeting end of June / early July.
- LoCoMo. The 2026-06-15 deep research and the sanity-slice run on 2026-06-15 surfaced two material problems: judge prompt sensitivity dominates the small-n signal, and LoCoMo's general conversational recall task does not test the world-model-mcp wedge. The harness work that was on the feature branch is preserved in git history (`feature/v0.8.1-benchmarks` branch up to commit `c99a146`) and may be revived if a future release needs general-recall validation, but it does not ship in v0.8.x.
- Antigravity adapter. Third consecutive release without it. Next re-verify 2026-06-27. If that re-verify also fails the architectural gate (no `TransformCompactionHook`), Antigravity bumps to v0.10 and off the v0.9 milestone.

### Honest framing

The published essay on 2026-06-16 ("Your AI model is temporary. Your learning loop should not be.") set up an expectation: show that the learning loop measurably reduces repeated agent mistakes. This release does not deliver that proof. It delivers an internal correctness check that validates the v0.8.0 schema math, and it names the v0.9 work explicitly so peers reading the README see what is coming.

That is the discipline. Shipping a smaller honest artifact + a credible roadmap is better than shipping a larger marketing-shaped artifact that does not survive peer scrutiny.

### Tools and surface

- 26 MCP tools (unchanged)
- 19 CLI subcommands (unchanged)
- 375 tests passing (unchanged from v0.8.0)
- Benchmarks: `benchmarks/contradictions/` (v0.7.4, 24 pairs, 93.5% headline) + `benchmarks/contradictions-200/` (v0.8.1, 105 pairs, 78.2% overall)

### Backward compatibility

- Zero changes to MCP tools, CLI, schema, or adapters.
- v0.7.4 benchmark at `benchmarks/contradictions/` is preserved unchanged.
- 375 v0.8.0 tests still pass.
- No new required dependencies. The runner is pure stdlib.

### Upgrade path

```bash
pip install -U world-model-mcp
python benchmarks/contradictions-200/run.py
```

The runner prints the per-strategy table and writes `results.json` next to the dataset.

---

## v0.8.0 (June 2026)

Decay + provenance schema. Slash command write operations. Antigravity held for the third consecutive release.

### Headline

v0.8.0 is the schema cut promised to the working group on anthropics/claude-code#47023 and openai/codex#19195. The decay + provenance work I publicly committed to Patdolitse and ferhimedamine over the past two weeks is the load-bearing change: facts now carry a `source_tool` and a `confirmer` (distinct from the asserter), and confidence decays under a per-evidence-type half-life curve so the next session sees rotted inferences as rotted instead of as fresh assertions. The benchmarks that validate this schema (LoCoMo confidence-on/off, expanded contradiction-resolution corpus, contradiction-recall methodology) ship separately as v0.8.1; bundling them into this release would have inflated scope past one ship cycle.

Antigravity is held for the third consecutive release. The 2026-06-13 re-verification against `google-antigravity/antigravity-sdk-python` HEAD confirmed `OnCompactionHook` is declared as `InspectHook` with no `TransformCompactionHook` subclass and no `additional_context` return field. The architectural gap that blocked v0.7.5 and v0.7.6 has not closed. Next re-verification 2026-06-27. If that one also fails, Antigravity is bumped to v0.9 and removed from the v0.8 milestone.

### New features

- **Domain-aware confidence decay (F1)** -- new `world_model_server/decay.py` module with three pure functions: `compute_decayed_confidence`, `should_auto_supersede`, `apply_decay_to_row`. Exponential half-life formula `confidence * (0.5 ** (age_days / ttl_days))` where `ttl_days` is set per `evidence_type` from a constant dict: source_code 365, test 180, session 14, user_correction 730, bug_fix 365 (unknown / NULL falls back to 90). The decay applies on read in `query_facts`, not as a background task, so there is no scheduler dependency and the result is deterministic across runs given a fixed `now`. Settled facts (`status == "canonical"` or `confirmer != NULL`) never auto-transition. `synthesized` facts that decay below 0.20 confidence and `corroborated` facts that decay below 0.10 confidence auto-supersede on read.

- **Per-item provenance fields on facts (F2)** -- three additive columns on the facts table: `source_tool TEXT NULL`, `confirmer TEXT NULL`, `last_decay_at TIMESTAMP NULL`. All NULL-defaulted, no backfill on existing rows. The `Fact` Pydantic model adds three optional fields with matching names. `create_fact` persists both provenance fields. Existing code creating Facts without provenance still works because the fields default to None. The schema migration is idempotent: re-initializing a KnowledgeGraph against an existing v0.8 facts.db is a no-op. Honors the public commitment to Patdolitse on anthropics/claude-code#47023#issuecomment-4636842510 (June 6 settled-vs-pending framing) and ferhimedamine on anthropics/claude-code#47023#issuecomment-4697914250 (June 13 SessionEnd + ToolResult proposal).

- **Slash command write operations (F3)** -- two new subcommands extending the v0.7.6 read-only surface. `/world-model resolve <id>` marks a contradiction as resolved (manual; for confidence-weighted automatic resolution use the `resolve_contradiction` MCP tool with an explicit strategy). `/world-model forget <id>` sets `invalid_at` on a fact, removing it from current-only reads while preserving it in the audit log. Both subcommands are idempotent (second call on an already-resolved contradiction or already-invalidated fact reports cleanly), validate the argument (missing id returns a usage hint), and return the existing camelCase `hookSpecificOutput` shape Codex enforces.

- **`resolve_contradiction` accepts `confirmer` (F4)** -- the MCP tool and its underlying `world_model_server.contradictions.resolve` function gain an optional `confirmer` parameter. When set, the winning fact gets its `confirmer` column stamped with that value, marking it as settled per the spec sketch. When omitted (the default), behavior is unchanged from v0.7.x.

### Schema migration discipline

All three new columns are `NULL`-defaulted. No backfill. The full v0.7.6 test suite (304 tests) keeps passing without modification. Behavior on rows with NULL values is identical to v0.7:

- A row with `source_tool = NULL` is treated as "tool unknown" (no behavior change)
- A row with `confirmer = NULL` is treated as "pending unless status == canonical"
- A row with `last_decay_at = NULL` triggers a one-time decay computation on next read (the result is not persisted in this release; v0.8.1 may add write-back amortization)

The migration is the same idempotent column-existence-check pattern used in v0.6.0 and v0.7.0.

### What is intentionally NOT in this release

- The benchmark publication arc (LoCoMo confidence-on/off, 200-pair contradiction expansion, contradiction-recall methodology) is split into v0.8.1 to keep scope bounded.
- Decay write-back amortization (storing the computed `last_decay_at` on read) is deferred. The decay is pure computation in this release; persistence is a v0.8.1 candidate.
- `source_tool` and `confirmer` propagation through every MCP tool that creates facts is partial: `create_fact` accepts them via the `Fact` model, `resolve_contradiction` accepts `confirmer`. Other tools (`record_decision`, `record_test_outcome`) keep their v0.7 signatures in this release.
- Domain-aware TTL configurability via config file (the constants are fixed in `decay.py`).

### Tools and CLI surface

- 26 MCP tools (unchanged; `resolve_contradiction` gains an optional kwarg)
- 19 CLI subcommands (unchanged)
- Slash command subcommands: 4 read + 2 write (up from 4 read in v0.7.6)
- New module: `world_model_server.decay`

### Tests

375 passing (was 333): 42 new in `tests/test_v080_features.py` covering decay math across all 5 evidence types (half-life at one period, half-life at two periods, distinct TTLs, default fallback, unparseable timestamp fail-open), status auto-transitions (canonical never supersedes, confirmer-set never supersedes, threshold boundaries for synthesized and corroborated), schema migration (idempotent re-init, existing rows get NULL provenance, new rows persist provenance), slash command write operations (parse arguments, unknown ids, idempotency, DB state changes), `resolve_contradiction` with and without `confirmer`, and backward-compat regression (all v0.7.6 subcommands still registered, read-only slash subcommands unchanged, existing v0.7 facts decay does not break query).

### Backward compatibility

- All v0.7.6 MCP tools and CLI subcommands work unchanged.
- `inject_helper.build_injection` and `hook_helper.classify` are unchanged.
- No schema migrations on the constraints or decisions tables.
- No new required dependencies.
- All four adapters (Claude Code, Cursor, Codex, pi) unaffected. The new provenance fields are optional on Fact creation and the decay applies transparently on read.

### Upgrade path

```bash
pip install -U world-model-mcp
# Schema migration runs automatically on first KnowledgeGraph.initialize().
# Existing facts get NULL provenance; new facts can pass source_tool /
# confirmer through the Fact model or the MCP tools.
```

### What is next (v0.8.1)

The benchmark publication arc. Three artifacts:

1. **LoCoMo run with confidence-on / confidence-off comparison.** The story: "with the v0.8.0 provenance schema turned on, recall under contradicted facts improves by X%." The raw LoCoMo number is secondary to the delta; world-model-mcp was not optimized for raw recall on LoCoMo's conversation surface, but the comparison is something Mem0 / Letta / Zep / Dakera cannot show because their schemas do not carry provenance.

2. **Expanded 200-pair contradiction-resolution benchmark published on HuggingFace.** v0.7.4 shipped a 24-pair internal corpus with 93.5% overall accuracy. Expanding to 200 pairs with more category coverage and publishing as a HuggingFace dataset makes the benchmark reproducible by anyone.

3. **New contradiction-recall benchmark methodology.** Inject contradictions into LoCoMo conversations and measure recall accuracy on the contradicted items. This is the benchmark world-model-mcp is built to win; testing it on the v0.8.0 schema is more compelling than testing the v0.7 schema.

Targeting end of June for v0.8.1 ship.

---

## v0.7.6 (June 2026)

In-agent slash command, terminal status widget, second deferral of Antigravity.

### Headline

v0.7.6 is the conversion-first cut of the v0.7 series. The two features both target the same problem: the value of world-model-mcp was invisible inside the agent harness. The `/world-model` slash command makes the state queryable without leaving the conversation; `world-model status-watch` makes it visible in a side terminal. Both are read-only in this release; write operations land in v0.8 with the schema work.

Antigravity is held for the second consecutive release. The 2026-06-13 re-verification against `google-antigravity/antigravity-sdk-python` HEAD surfaced an architectural gap that a release-cadence wait will not close: `OnCompactionHook` is declared as an `InspectHook` (read-only, non-blocking), and the SDK has no `TransformCompactionHook` subclass. The load-bearing memory-injection contract that the adapter needs simply does not exist in the SDK today. Targeting 2026-06-27 for the next re-verification.

### New features

- **`/world-model` slash command (F1)** -- new `world_model_server/slash_command.py` module plus a wire-up in `world_model_server/inject_helper.build_injection`. When the user types `/world-model <subcommand>` inside any harness (Claude Code, Cursor, Codex, pi), the existing UserPromptSubmit hook intercepts the prompt before the search-hint flow, calls into `handle_slash_command`, and returns the formatted output as `additionalContext` in the strict camelCase `hookSpecificOutput` shape. Subcommands shipped: `status` (compact summary of constraints, contradictions, facts), `contradictions` (top 10 unresolved), `recent` (last 10 facts), `help` (subcommand list). Unrecognized subcommands fall through to `help` rather than erroring inside the agent session. Schema-strictness regression-tested against Codex's `deny_unknown_fields` constraint (`hookEventName` literal-matches `UserPromptSubmit`).

- **`world-model status-watch` TUI widget (F2)** -- new `world_model_server/status_widget.py` module plus a `status-watch` CLI subcommand. Terminal pane that runs alongside the agent, refreshes every 5 seconds by default (`--interval N`), and shows constraint counts (total, severity=error, severity=warning), unresolved contradiction count, fact counts by status (canonical / synthesized / superseded), and last compaction time from the audit log. Built on `rich.live` + `rich.panel`. Falls back to plain-text dump when `rich` is not installed (which it always is as a transitive dependency, but the fail-open path is regression tested anyway).

- **Updated v0.8 roadmap** -- README roadmap section now reflects shipping reality. Codex adapter moved from "v0.8.0 Next" to "v0.7.5 shipped" (was incorrectly listed as a v0.8 to-do for nine days after the v0.7.5 ship). Slash command + TUI widget moved from "v0.8.0 Next" to "v0.7.6 shipped". v0.8 scope reduced and refocused on the decay + provenance schema work that Patdolitse and ferhimedamine called out in the working group threads, plus the benchmark publication arc (200-pair contradiction expansion to HuggingFace, LoCoMo confidence-on/off comparison, contradiction-recall benchmark methodology).

### Antigravity hold rationale (2026-06-13 re-verification)

The re-verification confirmed the second-consecutive HOLD recommendation. The single determining issue is architectural, not documentation:

```
# google/antigravity/hooks/hooks.py (HEAD)
class OnCompactionHook(InspectHook):
    """Invoked when a context compaction event occurs."""
    pass
```

`InspectHook` is defined in the SDK README as read-only and non-blocking: it can observe a compaction event but cannot modify or augment the compacted summary. There is no `TransformCompactionHook` subclass. There is no `additional_context` return field. The hook is observability-only. A memory adapter that re-injects state at the compaction boundary cannot do its core job through this surface.

Secondary findings:

- `mcp_config.json` path is now stable at `~/.gemini/config/mcp_config.json` (PASS, five releases since migration).
- `hooks.json` event-name vocabulary is still undocumented in any primary Google source the verification could reach (FAIL, identical to the 2026-06-04 finding; SDK class names like `OnCompactionHook` do not match the Claude-style `PreInvocation` / `PostInvocation` names that issue #261 user configs use).
- Release cadence is roughly one release every 2.5 days (1.0.5 to 1.0.8 in nine days); the 1.0.8 release fixed `/hooks` writing to the wrong directory only 14 hours before the verification.
- Open issues #368 and #369 (filed 2026-06-12, both open) report `call_mcp_tool` schema serialization bugs that affect any MCP server's tool invocation path, including world-model-mcp.

Re-verification scheduled for 2026-06-27 (14 days after this ship). The recommendation will revert to SHIP only when either (a) the SDK ships a `TransformCompactionHook` or adds an `additional_context` return field to `OnCompactionHook`, or (b) an official `hooks.json` schema lands on antigravity.google/docs/hooks with stable event names.

### Tools and CLI surface

- 26 MCP tools (unchanged)
- 19 CLI subcommands (was 18): added `status-watch`
- New module: `world_model_server.slash_command`
- New module: `world_model_server.status_widget`

### Tests

333 passing (was 304): 29 new in `tests/test_v076_features.py` covering slash command detection (prefix matching, case-insensitivity, default-to-help), subcommand dispatch, output shape (camelCase, Codex deny_unknown_fields compliance), inject_helper wire-up (slash bypasses search-hint flow, Codex payload shape works, non-slash prompts go through the original path), TUI widget (snapshot reading, render across initialized and uninitialized states), CLI subcommand registration, and backward-compat regression (all v0.7.5 subcommands still registered, dual-shape payload normalization still works, PostCompact path unchanged).

### Backward compatibility

- All v0.7.5 MCP tools and CLI subcommands work unchanged.
- `inject_helper.build_injection` returns the slash command output only when the prompt actually starts with `/world-model`; every other UserPromptSubmit prompt flows through the original v0.7.0 search-hint behavior. Regression-tested.
- `hook_helper.classify` is unchanged.
- No schema migrations.
- No new required dependencies (`rich` was already a transitive dependency).
- Cursor / pi / Codex / Claude Code adapters unaffected. The slash command intercept runs inside the existing UserPromptSubmit hook surface that every adapter already wires.

### Upgrade path

```bash
pip install -U world-model-mcp
# No new install step needed; the slash command works in any already-configured project.
# Optional: try the TUI in a second terminal
world-model status-watch --project-dir .
```

### What is next

v0.8 is the schema + benchmark cut. Decay + provenance fields, per-evidence-type TTL, LoCoMo confidence-on/off, 200-pair contradiction expansion on HuggingFace, contradiction-recall benchmark methodology. Targeting end of June for v0.8 ship. Antigravity adapter folds into v0.8 or v0.9 depending on the 2026-06-27 re-verification.

---

## v0.7.5 (June 2026)

Codex CLI adapter. Antigravity adapter intentionally deferred.

### Headline

OpenAI's Codex CLI shipped first-class hook and MCP support that maps cleanly onto world-model-mcp's existing primitives. v0.7.5 adds the adapter that wires both together. The Antigravity CLI adapter was on the same roadmap but is held until late June because the Antigravity API surface is still settling (six 1.0.x releases in three weeks; the `url` field for HTTP MCP servers landed June 3; hook event-name casing remains undocumented in primary sources). Shipping it today would produce the same patch-release pattern that hit the Cursor adapter at v0.7.0 / v0.7.1.

### New features

- **Codex CLI adapter (F1)** -- new `install-codex` CLI subcommand reads bundled `world_model_server/adapters/codex/{config.toml, hooks_snippet.toml}` and appends them to `~/.codex/config.toml`. Idempotent: the second run refuses to write without `--force`. Supports `--dry-run` to preview, `--config-path` to target a non-default config location. Creates parent directories. New adapter README at `adapters/codex/README.md`.

  Concrete schema details locked down by tests:

  - MCP server name is `world_model` (underscore), not `world-model` (hyphen). Codex's `sanitize_responses_api_tool_name` in `codex-rs/codex-mcp/src/mcp/mod.rs` silently strips hyphens before exposing tool names to the model, which would create model-visible name collisions and trigger Codex's hash-suffix disambiguation path.
  - Hook event names use Codex's exact 10-value enum (PreToolUse, PostToolUse, PreCompact, PostCompact, SessionStart, UserPromptSubmit, SubagentStart, SubagentStop, PermissionRequest, Stop). Anything else is rejected at config load.
  - Hook output JSON is camelCase only and compliant with Codex's `deny_unknown_fields` Rust schema (`codex-rs/hooks/src/schema.rs`). PR #24962 in v0.136 tightened this further by constraining `hookEventName` to a literal string matching the registered event; the bundled helpers return the correct event name per hook.
  - MCP server config uses current field names (`default_tools_approval_mode`, `startup_timeout_sec`, `tool_timeout_sec`, `enabled_tools`, `disabled_tools`), not the pre-v0.130 names (`trust`, `timeout`, `includeTools`, `excludeTools`).

- **Dual-shape payload normalization (F2)** -- `world_model_server.inject_helper._normalize_payload` and `world_model_server.hook_helper.classify` accept either Claude Code's payload shape (`event`, `project_dir`) or Codex's shape (`hook_event_name`, `cwd`). Same Python code now drives Claude Code, Cursor, pi, and Codex adapters; the four adapters live in separate config files so there is no cross-talk. Backward compatible: existing Claude Code adapter behavior unchanged.

- **Schema regression tests (F3)** -- 21 new tests in `tests/test_v075_features.py` cover TOML parse validity, valid event-name enum, current-not-deprecated MCP field names, camelCase-not-snake-case hook output, `hookEventName == event` strict matching per PR #24962, install-codex CLI behavior (write / idempotent / dry-run / parent-dir creation), and backward-compat CLI subcommand presence.

### Antigravity adapter -- explicit hold note

This is documented here because skipping a planned adapter is a roadmap signal worth being honest about.

The Antigravity CLI adapter was on the v0.8 roadmap (Gemini CLI sunsets June 18). Deep verification against primary sources surfaced five issues that together exceed the ship-this-week risk threshold:

1. The MCP config path migrated from `~/.gemini/antigravity/mcp_config.json` to `~/.gemini/config/mcp_config.json` in 1.0.3, with documentation still split between blogs citing the old path and the changelog citing the new.
2. The `url` field for HTTP MCP servers was added on 2026-06-03 in 1.0.5, less than 36 hours before this release date. Anything shipped today will look stale by next week.
3. The hook JSON event-name casing is undocumented in any primary source. Python SDK uses `PreToolCallDecideHook` style; third-party blogs use `PreToolUse` Claude-style. Google's docs site renders client-side and is not scrapeable.
4. The compaction context-injection contract -- the load-bearing feature for world-model-mcp -- is undocumented in the SDK README.
5. The repo has 259 open issues with active regressions (sandbox ignored in headless 1.0.4, broken first-launch OAuth, Windows IDE path mismatch). The team is fixing fundamentals rather than stabilizing APIs.

Target: re-verify around June 25, ship v0.7.6 by July 1 if the API has settled. The Cursor adapter at v0.7.0 needed a same-day patch release for similar reasons; the cost of avoiding that repeat is two weeks of waiting.

### Tools and CLI surface

- 26 MCP tools (unchanged from v0.7.4)
- 18 CLI subcommands (was 17): added `install-codex`

### Tests

304 passing (was 283): 21 new in `tests/test_v075_features.py`.

### Backward compatibility

- All v0.7.4 MCP tools and CLI subcommands work unchanged.
- `hook_helper.classify` and `inject_helper.build_injection` accept the previous Claude Code payload shape exactly as before; new Codex shape is additive.
- No schema migrations.
- No new required dependencies. The adapter snippet uses `python3` from PATH, same pattern as the other adapters.
- Cursor / pi adapters unaffected (separate config files, separate server names: `world-model` with hyphen for Cursor/pi; `world_model` with underscore for Codex).

### Upgrade path

```bash
pip install -U world-model-mcp
python -m world_model_server.cli install-codex   # if you use Codex
```

Existing Cursor / pi / Claude Code installs do not need any action.

---

## v0.7.4 (May 2026)

Interop, deployment, benchmark. No new adapters this release -- positioning over distribution surface.

### Headline

v0.7.0 through v0.7.3.1 shipped the primitives and the channels. v0.7.4 ships three things that connect them to what the ecosystem actually asked for: read the format the community standardized on, deploy where the platform left a memory gap, and publish numbers for the contradiction-resolution claim instead of just asserting it.

### New features

- **AGENTS.md / `.agents/skills/` constraint reader (F1)** -- new `world_model_server/agents_md_reader.py` parses `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and `.agents/skills/*.md` files into virtual constraints that mix into PreToolUse enforcement alongside the SQLite-backed constraints. Supports three extraction modes:
  1. Structured fence blocks (`` ```constraint `` ... `` ``` ``)
  2. YAML frontmatter with a `constraints:` list
  3. Heuristic imperative-sentence extraction ("Use X", "Never Y", "Always Z", "Prefer A over B") for prose-style AGENTS.md files

  The fence + frontmatter modes use no external YAML dependency -- the parser is hand-rolled to keep stdio installs zero-dep. Heuristic mode produces virtual constraints with severity `warning` (for strong verbs) or `info` (for soft verbs) so they never hard-deny on their own. New MCP tool: `get_agents_md_constraints`. Motivated by [anthropics/claude-code#6235](https://github.com/anthropics/claude-code/issues/6235) (4,000+ thumbs-up).

- **Self-hosted Claude Managed Agents deployment guide (F2)** -- new doc at `docs/deployment/managed-agents-self-hosted.md` plus a complete Modal quickstart under `examples/managed-agents-self-hosted/` (`deploy_modal.py` + `ant-setup.sh`). Targets the exact gap Anthropic's [May 19 blog post](https://claude.com/blog/claude-managed-agents-updates) named: *"Memory is not yet supported in self-hosted sessions."* world-model-mcp's streamable HTTP transport plus MCP tunnels covers that case. Deploy on Modal in ~5 minutes; wire into Anthropic with two `ant` CLI commands.

- **Contradiction-resolution benchmark (F3)** -- new `benchmarks/contradictions/` with a 24-pair dataset, deterministic runner, and committed results. Headline numbers: **93.5% overall**, **100% on `keep_higher_confidence` and `keep_most_sources`**, 90.9% on `keep_most_recent`, 87.5% on `auto`. RESULTS.md documents four honest failures (true-tie handling, sub-0.1 confidence gaps) instead of hiding them. New GitHub Actions workflow re-runs the benchmark on every push to `world_model_server/contradictions.py` and fails on regressions. Reproducible with one command: `python benchmarks/contradictions/run.py`.

### Why these three, not more adapters

The deep ecosystem sweep (run May 29) showed three things:

1. **mcp-memory-service shipped 22 releases in May**, including NLI contradiction detection (v10.67.0 May 28), a `/memory` slash + TUI sidebar (v10.65.0 May 24), and passive observation (v10.70.1 May 29). Building another web dashboard would have been me-too.
2. **The single most-engaged feature request across the entire space is AGENTS.md adoption** (claude-code #6235, 4,000+ thumbs-up). Already adopted natively by Zed (v1.4) and Cline (v3.86). Reading AGENTS.md is the highest-leverage interop bet available.
3. **Anthropic's own blog admits self-hosted Managed Agents have no memory primitive yet.** We already ship the HTTP transport. What was missing was the *deployment recipe* that lets enterprise users connect the dots.

The next-most-time-sensitive opportunities (Codex CLI adapter, Antigravity CLI adapter before June 18, MCP-spec 2026-07-28 refactor) move to v0.8.0. The standalone web dashboard, Continue adapter, and superagent-ai/grok-cli adapter were dropped from the roadmap entirely.

### Tool and CLI surface

- 26 MCP tools (was 25): added `get_agents_md_constraints`
- 17 CLI subcommands (unchanged)

### Tests

283 passing (was 262): 21 new tests in `tests/test_v074_features.py` cover the AGENTS.md reader (10 tests including fence extraction, frontmatter, imperative sentences, glob filtering, dedup across files, severity normalization, MCP tool, hook integration), the self-hosted deployment doc artifacts (3 tests), and the benchmark (4 tests including end-to-end run). Plus 3 backward-compat regression tests and an updated version assertion.

### Backward compatibility

- All 25 v0.7.3.1 MCP tools and all 17 CLI subcommands work unchanged
- AGENTS.md mixing into `hook_helper.classify()` is additive -- when no AGENTS.md files exist, the behavior is identical to v0.7.3.1
- No schema migrations
- No new required dependencies (the AGENTS.md parser uses only stdlib)
- Cursor / pi adapters, .mcpb desktop extension, stdio transport, HTTP transport (v0.7.2), MCP tunnel deployment (v0.7.2), `world-model demo` (v0.7.3), telemetry (v0.7.3.1) all unaffected

### Upgrade path

```bash
pip install -U world-model-mcp
# Existing projects auto-pick-up AGENTS.md / .agents/skills/ on the next
# PreToolUse hook fire -- no setup step required.
```

For self-hosted Managed Agents users: see `docs/deployment/managed-agents-self-hosted.md`.

For the benchmark: `python benchmarks/contradictions/run.py` from a clone.

---

## v0.7.3.1 (May 2026)

Patch release that activates the opt-in telemetry path introduced in v0.7.3.

### What changed

- The PAT used to write opt-in telemetry events to
  `SaravananJaichandar/world-model-telemetry` is now embedded in the wheel
  at `world_model_server/_embedded_token.py`.
- v0.7.3 shipped with that file as an empty stub (`EMBEDDED_TOKEN = ""`),
  which made `record()` silently no-op even for users who opted in.
  v0.7.3.1 ships the same file populated.
- No code changes besides the version bumps. The embed mechanism
  (`scripts/embed_token.py`, the gitignored `.env.release` file, the
  release procedure in `RELEASE.md`) was added in the prior commit;
  this release is the first one to actually use it end to end.

### Security model recap

The embedded token is scoped only to the telemetry repo with
`Issues: Read and write`. Anyone who installs the wheel can extract it
from `_embedded_token.py` -- this is intentional and standard for OSS
telemetry. The worst-case attack is spamming the private telemetry repo
with issues. If that happens: revoke, regenerate, ship v0.7.3.2.

### User-visible behavior

- Telemetry is still **off by default**. Existing installs behave
  identically until the user explicitly opts in.
- `world-model setup` still prompts once for consent.
- `world-model telemetry --status` shows the install ID and a sample
  payload. The status output's "Repo:" field now correlates with where
  events would actually land.
- `WORLD_MODEL_TELEMETRY_DISABLE=1` continues to override everything.

### Tests

262 passing (unchanged from v0.7.3). No new test surface; the embed-flow
tests added in v0.7.3 cover the wiring.

### Backward compatibility

All v0.7.3 surface unchanged: 17 CLI subcommands, 25 MCP tools, Cursor /
pi / .mcpb / HTTP transport all unaffected.

---

## v0.7.3 (May 2026)

Onboarding, opt-in telemetry, and a pi adapter. Existing surface unchanged.

### Headline

v0.7.0 - v0.7.2 added the load-bearing primitives (constraint enforcement, PostCompact injection, contradiction resolution, HTTP transport). v0.7.3 attacks the second-order problem: a new user installing for the first time sees an empty database and has no path to the value. v0.7.3 ships three things to close that gap:

1. **`world-model demo`** - a one-command guided tour that seeds reproducible data and exercises each primitive with real outputs.
2. **Opt-in telemetry** - so future product decisions are informed by actual usage data, not download counts. Off by default, prompted once, inspectable.
3. **pi adapter** - audience expansion to the 51k-star [earendil-works/pi](https://github.com/earendil-works/pi) ecosystem via a pi-package extension.

### New features

- **`world-model demo` (F1)** - new CLI subcommand. Initializes the world-model database (if missing), runs `scripts/demo_seed.py --reset --seed-after-reset` to populate realistic constraints, facts, a contradiction pair, and a compaction audit row, then prints the actual JSON output of each primitive (PreToolUse classify, find_contradictions, get_injection_context, get_compaction_audit). Reproducible end-to-end on a fresh clone.
- **Opt-in telemetry (F2)** - new `world_model_server/telemetry.py` module. urllib-only (no new required deps), fail-open on any error, rate-limited to 1 event/60s per install, async fire-and-forget. New CLI subcommand `world-model telemetry` with `--enable / --disable / --status`. `world-model setup` prompts once for consent in interactive sessions; `--no-prompt` flag and `WORLD_MODEL_NO_PROMPT=1` env var skip the prompt for CI/scripted setup. Stable opaque `install_id` at `~/.world-model/install_id`. Destination: dedicated private GitHub repo `SaravananJaichandar/world-model-telemetry` (issues-write only). Global kill switch `WORLD_MODEL_TELEMETRY_DISABLE=1`. Never collects file paths, code, hostnames, IPs, rule names, or fact text. Full payload schema documented in README.
- **pi adapter (F3)** - new `adapters/pi/` package and bundled copy at `world_model_server/adapters/pi/`. TypeScript extension subscribes to pi's `tool_call`, `context`, and `session_compact` events; spawns the existing Python `hook_helper` / `inject_helper` as subprocesses so the enforcement and injection logic stays in one place across Claude Code, Cursor, and pi. The `defer` enforcement tier is surfaced to pi as an advisory `block` with `[review]` prefix because pi has no defer tier. New CLI subcommand `world-model install-pi` writes the adapter into `<project>/adapters/world-model-pi/` for `pi install local:` consumption.

### CLI surface

- 17 CLI subcommands (was 14): added `demo`, `telemetry`, `install-pi`
- 25 MCP tools (unchanged)

### Tests

256 passing (was 236). 20 new tests in `tests/test_v073_features.py` cover:
- Telemetry off-by-default state, kill-switch precedence, install-id stability, no-token silent no-op, sync record returns False when disabled, preview payload omits sensitive keys, CLI subcommand status/enable/disable, setup `--no-prompt` flag
- `world-model demo` runs cleanly on a fresh project, creates `.claude/world-model/`, exercises each primitive
- pi adapter file existence, package.json schema, index.ts event wiring (`tool_call`/`context`/`session_compact` subscribed + correct helper modules invoked), bundled-in-package fixture, `install-pi` CLI with and without `--force`
- Backward-compat regression: all v0.6 + v0.7.0 + v0.7.2 subcommands still registered, v0.7.2 HTTP transport still boots, setup in non-TTY environment doesn't hang

### Backward compatibility

- All 22 v0.6 MCP tools work unchanged
- All 14 v0.7.2 CLI subcommands work unchanged (`setup`, `seed`, `query`, `decisions`, `register`, `projects`, `search-global`, `health`, `decay`, `recall`, `export-claude-md`, `migrate`, `status`, `audit-compactions`, `install-cursor`)
- No schema migrations
- No new required dependencies (telemetry uses stdlib `urllib`; HTTP transport extras unchanged)
- Cursor adapter, .mcpb desktop extension, stdio transport, MCP tunnel deployment all unaffected
- The Glama Dockerfile keeps its stdio shape

### Versioning note

`__version__` is now `0.7.3`. The v0.7.2 `test_f6_version_is_072` assertion was relaxed to `test_f6_version_is_at_least_072` to make future patch releases pass without manual test updates.

### Upgrade path

```bash
pip install -U world-model-mcp
world-model demo   # see all primitives running on a fresh project
```

For existing users running `world-model setup` on a project that already has `.claude/world-model/`: the telemetry prompt appears once if you've never answered it, then never again.

### Known gaps (still in v0.8 scope)

- Antigravity adapter (Google's agentic IDE; replaces Gemini CLI which sunsets June 18, 2026)
- Codex CLI adapter (OpenAI)
- Cline + Continue adapters
- Local web dashboard for the knowledge graph
- AST-based extraction substrate

---

## v0.7.2 (May 2026)

Streamable HTTP transport for remote and MCP-tunnel deployments.

### What's new

Until v0.7.1 the server only spoke stdio, which is the right transport for
Claude Code, Cursor, and `.mcpb` installs but does not work for deployments
where the MCP server lives behind a firewall and the agent reaches it from
Anthropic-side infrastructure. v0.7.2 adds an opt-in streamable HTTP
transport so world-model-mcp can be deployed as a long-lived HTTP service
inside the customer's own perimeter -- the configuration Claude Managed
Agents' MCP tunnels (research preview) target.

- **Streamable HTTP transport** -- set `WORLD_MODEL_TRANSPORT=http` to expose
  the same 25 MCP tools over HTTP instead of stdio. Default stays stdio so
  existing Claude Code / Cursor / .mcpb installs are unaffected.
- **Environment variables**: `WORLD_MODEL_TRANSPORT`, `WORLD_MODEL_HTTP_HOST`
  (default `0.0.0.0`), `WORLD_MODEL_HTTP_PORT` (default `8765`),
  `WORLD_MODEL_HTTP_PATH` (default `/mcp`).
- **`GET /healthz` endpoint** -- returns `{"status":"ok","version":"0.7.2"}`.
  Cheap probe for Docker / Kubernetes / `ant tunnels` upstream health.
- **`Dockerfile.http`** -- pre-built image that installs the `http` extras,
  exposes port 8765, and includes a `HEALTHCHECK` directive. The original
  `Dockerfile` (stdio, used by Glama) is unchanged.
- **`docker-compose.yml`** -- reference compose file with persistent volume
  for the SQLite database.
- **`docs/deployment/mcp-tunnel.md`** -- end-to-end walkthrough including
  `ant tunnels` setup for Claude Managed Agents.
- **`[http]` optional extras** -- `pip install 'world-model-mcp[http]'`
  pulls `uvicorn` and `starlette`. Stdio installs do not see these as
  required dependencies.

### Tests

236 passing (was 223): added 13 v0.7.2 tests in
`tests/test_v072_http_transport.py` covering transport selection, the
`/healthz` endpoint, MCP path mounting, custom `WORLD_MODEL_HTTP_PATH`,
helpful error on missing `http` extras, and backward-compat regression on
the stdio path.

### Backward compatibility

- All 22 v0.6 MCP tools and all 25 v0.7 MCP tools work unchanged in both
  transports
- Default transport stays stdio: existing Claude Code / Cursor / .mcpb users
  see zero behavior change
- The Glama Dockerfile (no suffix) keeps its shape: stdio entrypoint, no port
  exposed, no http extras
- The Cursor adapter and PyPI install path are not affected
- No schema migrations

---

## v0.7.1 (May 2026)

Patch release fixing the Cursor adapter shipped in v0.7.0.

### Cursor adapter rewrite

The v0.7.0 adapter declared hook events (`beforeEdit`, `afterCompact`) and
used a config shape that did not match Cursor's actual hooks API. v0.7.1
rewrites the adapter against Cursor's real schema:

- `hooks.json` now uses the object-keyed `{ "version": 1, "hooks": { eventName: [...] } }` shape
- Event names corrected to `beforeSubmitPrompt`, `preToolUse` (with `matcher`), and `preCompact`
- `timeout` is in seconds (was `timeout_ms`)
- `failClosed: false` replaces the old `fail_open: true` (inverted semantics)
- Node wrappers now live in `.cursor/hooks/` (was `.claude/hooks/`, which did not exist after the adapter install)
- `mcp.json` uses a relative `WORLD_MODEL_DB_PATH` instead of the un-documented `${workspaceFolder}` variable

### New CLI: `install-cursor`

Replaces the brittle copy-paste install step with `python -m world_model_server.cli install-cursor`. The command copies `mcp.json`, `hooks.json`, and the compiled Node hook wrappers into `.cursor/` from the installed package. Supports `--force` to overwrite existing files.

Adapter resources are now bundled inside the wheel at `world_model_server/adapters/cursor/`, so installs from PyPI ship the adapter files correctly.

### Adapter README updates

- Real install steps using the new CLI
- Note about Cursor's one-click MCP approval prompt on first run
- Section explaining the overlap with Cursor Memories and Cursor Rules
- Note that `defer` maps to `ask` in Cursor (no separate headless decision)
- Note that `preCompact` runs before summarization (no `postCompact` in Cursor yet)

### Tests

- Updated `test_f4_cursor_adapter_hooks_json_is_valid` to assert the new object-keyed schema
- 220 tests still passing

### Backward compatibility

- v0.7.0 PyPI / MCP registry / .mcpb release is unchanged; the broken Cursor adapter in that release will simply fail to load when Cursor parses it -- it does not break Claude Code
- Users on v0.7.0 should upgrade with `pip install -U world-model-mcp` then rerun `python -m world_model_server.cli install-cursor --force` to refresh the adapter files

---

## v0.7.0 (May 2026)

### Headline

Enforcement, provenance, and harness-neutral memory. v0.7.0 extends the
v0.6 enforcement boundary with a new `defer` tier for headless agents,
re-injects context after compaction, resolves contradictions with
confidence weighting, audits every compaction event, and ships a
Cursor adapter so the same primitives run outside Claude Code.

### New features

- **PostCompact + UserPromptSubmit auto-injection (F1)** -- the new
  `world-model-inject` hook calls a Python helper that returns a compact
  bundle of top constraints and recent canonical facts to splice into
  the agent's working context after compaction or on user prompt. The
  helper reads constraints and facts read-only and fails open on any
  error. New MCP tool: `get_injection_context`.
- **`defer` enforcement tier in PreToolUse (F2)** -- warning-severity
  violations seen 5+ times now return `permissionDecision: "defer"`
  (configurable threshold) when the client supports it, falling back
  to `ask` otherwise. The `ValidationResult.enforcement_decision`
  enum now includes `defer`.
- **Confidence-weighted contradiction resolution (F3)** -- new
  `resolve_contradiction` MCP tool picks a winner with strategies
  `keep_higher_confidence`, `keep_most_recent`, `keep_most_sources`,
  `supersede_a`, `supersede_b`, `manual`, or `auto` (chooses based on
  the largest signal gap). The loser is marked `status='superseded'`
  with `invalid_at=now`. `find_contradictions` now returns
  `confidence_a`, `confidence_b`, `source_count_a`, `source_count_b`
  on every pair.
- **Compaction audit log (F5)** -- new `audit.db` and
  `compaction_audit` table. Each PostCompact write records pre/post
  token counts and what was re-injected. New MCP tools:
  `record_compaction_audit`, `get_compaction_audit`. New CLI:
  `world-model audit-compactions [--export <path>]`.
- **Cursor adapter (F4)** -- `adapters/cursor/` ships `hooks.json` and
  `mcp.json` templates that wire the same `inject_helper` and
  `hook_helper` into Cursor's `beforeSubmitPrompt`, `beforeEdit`, and
  `afterCompact` events. Experimental.

### Schema changes (backward-compatible)

- `facts.source_count INTEGER DEFAULT 1` -- number of independent
  sources supporting a fact
- `facts.last_confirmed_at TIMESTAMP` -- most recent re-observation
- New `audit.db` with `compaction_audit` table

All migrations run idempotently via `_existing_columns()`.

### Tool and CLI surface

- 25 MCP tools (was 22): added `get_injection_context`,
  `record_compaction_audit`, `get_compaction_audit`,
  `resolve_contradiction`
- 14 CLI subcommands (was 13): added `audit-compactions`

### Tests

220 passing (was 186): added 34 v0.7.0 tests in `tests/test_v070_features.py`
covering each feature plus backward-compat regression checks.

### Compatibility

- All 22 v0.6 MCP tools continue to work unchanged
- All 13 v0.6 CLI subcommands continue to work unchanged
- v0.6 databases auto-migrate on first `initialize()` call
- Older MCP clients that do not understand `defer` see `ask` instead

---

## v0.1.1 (March 2026)

### Bug Fixes
- Fixed `get_constraints()` failing to match `**` glob patterns (e.g. `src/api/**/*.ts` now correctly matches `src/api/users.ts`)
- Replaced `fnmatch` with a custom `_glob_match` method that handles recursive directory patterns

### Improvements
- Cleaned up README and documentation to remove placeholder URLs and inaccurate claims
- Updated QUICKSTART.md with correct repository URLs and PyPI install option

### Tests
- Added `test_constraint_double_star_glob` to verify recursive glob matching
- Total: 18 tests passing

---

## v0.1.0 (January 2026)

### Initial Release

First public release of World Model MCP. Core knowledge graph and MCP tools are functional but early-stage.

### Core Features

#### 1. LLM-Powered Entity Extraction
- Automatically extracts entities (APIs, functions, classes) from code changes
- Uses Claude Haiku for fast, cost-effective extraction
- Fallback to regex patterns when API key not available
- Supports TypeScript, JavaScript, Python with extensible architecture

#### 2. External Linter Integration
- Integrates with ESLint, Pylint, and Ruff
- Pre-execution validation catches errors before code runs
- Combines world model constraints with linter rules

#### 3. Intelligent Constraint Inference
- LLM-powered pattern recognition from user corrections
- Automatically learns project conventions
- Infers constraint type, severity, and applicability
- Generates reusable examples

#### 4. Temporal Knowledge Graph
- 6 SQLite databases with full-text search (FTS5)
- Temporal facts with validity periods (`validAt`/`invalidAt`)
- Evidence chains for every assertion

**Databases:**
- `entities.db` - Resolved identities (files, APIs, functions)
- `facts.db` - Temporal assertions with FTS5 search
- `relationships.db` - Entity relationship graph
- `constraints.db` - Learned rules with violation tracking
- `sessions.db` - Session history and outcomes
- `events.db` - Activity log with reasoning chains

#### 5. Claude Code Hooks
- TypeScript hooks for event capture and validation
- Non-blocking async execution
- Full session lifecycle management

**Hooks:**
- `PostToolUse` - Capture file edits, test runs, tool calls
- `PreToolUse` - Validate changes before execution
- `SessionStart/End` - Manage session lifecycle

#### 6. MCP Tools

Six MCP tools:

1. **`query_fact`** - Check if APIs/functions exist
2. **`record_event`** - Capture development actions
3. **`validate_change`** - Pre-lint and constraint check
4. **`get_constraints`** - Retrieve rules for a file
5. **`record_correction`** - Learn from user edits
6. **`get_related_bugs`** - Find bugs fixed in a file

#### 7. Ingest Bridge
- Bridge between hooks flat files (.jsonl) and SQLite knowledge graph
- `ingest_queued_events()` reads events-queue.jsonl into events.db
- `ingest_session_files()` reads session-*.json into sessions.db
- Automatic cleanup of source files after ingestion

---

### Known Issues

1. **Pydantic Deprecation Warnings** - Using class-based config instead of ConfigDict (cosmetic only)
2. **Hook Path Resolution** - Requires absolute paths in some environments

### Limitations

- **Language Support**: Currently optimized for TypeScript/JavaScript and Python
- **LLM Dependency**: Best results with Anthropic API key (falls back to patterns without it)
- **Cold Start**: First session has minimal knowledge (improves with each session)

---

### Roadmap

#### v0.2.0
- Enhanced entity resolution with fuzzy matching
- Multi-language support (Go, Rust, Java)
- Performance optimizations (caching, batch processing)

#### v0.3.0
- Trajectory learning (co-edit patterns)
- Structural embeddings
- Relationship graph visualization

#### v0.4.0
- World model simulation ("what if" queries)
- Test failure prediction
- Multi-project knowledge transfer

---

## Support

- **Issues**: https://github.com/SaravananJaichandar/world-model-mcp/issues
- **Discussions**: https://github.com/SaravananJaichandar/world-model-mcp/discussions

---

**License**: MIT
**Python**: 3.11+
