# World Model MCP - Release Notes

## v0.12.13 (July 2026)

Two follow-ups sourced from engagement threads during the v0.12.12 ship cycle. Additive-only; backward compatible with v0.12.12.

### What ships

- **OpenAI-compatible Coach backend.** Coach can now dispatch through any OpenAI-shape endpoint (OpenRouter, Ollama, vLLM, LiteLLM, self-hosted vLLM/TGI, etc.) without a proxy layer. New config surface:
  - `WORLD_MODEL_VERIFICATION_BACKEND=openai-compatible` selects the branch
  - `WORLD_MODEL_VERIFICATION_BASE_URL=https://…/v1` picks the endpoint
  - `WORLD_MODEL_VERIFICATION_API_KEY=…` overrides key lookup; falls back to `OPENROUTER_API_KEY` → `OPENAI_API_KEY` → placeholder for local endpoints
  - New optional extra `[openai]` shipping `openai>=1.0` (install with `pip install "world-model-mcp[openai]"`)

  Implementation: `verify_answer(backend=...)` routes to either `_run_coach_anthropic` (v0.12.12 default, unchanged) or `_run_coach_openai_compatible` (new). The OpenAI path calls `chat.completions.create` with the system prompt in the messages list; response parsing pulls from `choices[0].message.content` instead of `content[0].text`. Everything else — deterministic `temperature=0`, JSON schema, confidence banding, never-raises contract — is identical across backends.

  Motivation: the v0.12.12 baseline run required a LiteLLM proxy dance for OpenRouter users. That's fragile (three-layer stack: Anthropic SDK → localhost proxy → LiteLLM → OpenRouter → Claude). This backend lets the same maintainer do the same benchmark run with just two exports: `WORLD_MODEL_VERIFICATION_BACKEND=openai-compatible` and `WORLD_MODEL_VERIFICATION_BASE_URL=https://openrouter.ai/api/v1`.

- **`doctor` Copilot log-signature scan.** New `check_copilot_hook_signatures` check parses `~/.copilot/logs/*.log` for the two documented failure modes from [copilot-cli #4001](https://github.com/github/copilot-cli/issues/4001):
  - **PowerShell parse errors** — signature `ParserError` in log body. Cause: Copilot on Windows runs `.claude/settings.json` hook commands through PowerShell instead of bash.
  - **`/.claude/...` path resolution** — signature `/.claude/` in log body. Cause: Copilot doesn't export `$CLAUDE_PROJECT_DIR`; hooks run from cwd `/`, so relative paths resolve to nonexistent absolute ones.

  Reports the two signature counts separately with distinct fix hints. SKIPs gracefully when Copilot isn't installed (`~/.copilot/logs/` absent). Does NOT auto-fix — those are Copilot-side bugs — but separates "my hook wrapper is broken" from "the runtime is running my hook wrong."

### Contract preservation

- Default `verification_backend` is `anthropic` — existing v0.12.12 installs unchanged. The v0.12.12 baseline JSON is reproducible bit-for-bit under this release.
- Backward-compat shim: `world_model_server.verification._run_coach` is now an alias for `_run_coach_anthropic` so any external code that imported the v0.12.12-shape helper directly keeps working.
- Never-raises: the OpenAI path swallows exceptions to LOW+error just like the Anthropic path. New error string:
  - `no_verification_client` — client is None on the openai-compatible path (missing base URL, or `openai` package not installed)

### Test breakdown

- 20 new tests in `tests/test_v01213_openai_coach_backend.py` — Config defaults + env-var wiring, backend routing in verify_answer, OpenAI call shape (system in messages, chat.completions.create, temperature=0), response parsing from choices shape, `_build_openai_compatible_client` handling missing base URL / missing openai package / API key priority, never-raises contract on the openai-compat path
- 8 new tests in `tests/test_v01213_doctor_copilot_check.py` — skip when Copilot absent, pass when logs present but clean, WARN on each signature independently, WARN on both signatures with separate counts, malformed log doesn't crash the scan, registered in ALL_CHECKS
- Regression: full suite 682 pass (v0.12.12 baseline 654; +28 net)
- Contradictions benchmark: 105/105 (100%)

### What's not in this release

- Windows-specific `Get-Command bash` shim detection (Git Bash vs WSL launcher). Requires Windows testing to ship safely — separate patch.
- `doctor --fix` rewrite of unwrapped hook commands to `bash -c '...'` for Copilot-target runtimes. Speculative without Windows verification; scope-cut.
- OpenAI-compatible baseline run at N=12 committed alongside the Anthropic one. Straightforward for the maintainer to add post-release (~$0.03) but not blocking.

## v0.12.12 (July 2026)

Adversarial verification patch. Adds a `verify_retrieval` MCP tool that runs an independent Coach LLM call against a candidate answer + supplied source facts and returns a confidence band with itemized verified / unverified claim lists. Pattern ported from the maintainer's earlier `y=c` project (Coach-Player adversarial cooperation); world-model-mcp is the first MCP server to ship it as a first-class tool.

### What ships

- **`verify_retrieval(query, answer, fact_ids, verification_model?)`** — new tool on `WorldModelTools` and on the MCP + Hermes surfaced schemas. Fetches the supplied facts from the graph, sends them plus the answer to an independent Coach LLM call, and returns a `VerificationResult` with:
  - `confidence` — `HIGH` (100% claims verified), `MEDIUM` (>=70%), `LOW` (<70%, or any failure)
  - `verified_claims` + `unverified_claims` — itemized breakdown
  - `source_pointers` — `[{claim, fact_id}]` mapping verified claims to their supporting fact
  - `coach_reasoning` — Coach's short rationale (audit trail, non-load-bearing)
  - `error` — non-None on Coach failure, no-key, empty-answer, no-facts (`confidence` is always LOW when set)

- **`world_model_server.verification` module** — Coach LLM call path lives here in isolation. `_confidence_from_counts` is a pure banding rule (unit-tested and locked); `_run_coach` builds the deterministic Coach prompt (temperature=0, JSON-only response); `verify_answer` is the never-raises high-level entry point.

- **`Config.verification_model`** — new field, env-configurable via `WORLD_MODEL_VERIFICATION_MODEL`. Defaults to `claude-haiku-4-5-20251001`. Verification is a per-answer overhead call; using Haiku by default keeps the cost profile at ~$0.001 per verify_retrieval call.

- **`benchmarks/coach-player/`** — hand-labeled 12-pair benchmark (4 grounded, 4 partial, 4 hallucinated) plus a runner that reports hallucination catch rate, false positive rate, MEDIUM band correctness. Ship-floor policy: false positive rate ≤10% is enforced (non-zero exit); hallucination catch rate ≥95% is aspirational at N=12 and gets enforced once the labeled set expands to ≥30 pairs.

### Contract

`verify_retrieval` never raises. Every failure mode returns a `VerificationResult` with `confidence=LOW` and `error` populated:

| Trigger | error value |
|---|---|
| No `ANTHROPIC_API_KEY` configured | `no_anthropic_api_key` |
| Empty / whitespace-only answer | `empty_answer` |
| No `fact_ids` supplied (or all missing from DB) | `no_source_facts` |
| Coach LLM call raised | `coach_call_failed: <ExceptionType>` |
| Coach response wasn't valid JSON | `coach_malformed_response: <parse detail>` |

This matches the v0.12.9 lifecycle-hooks best-effort convention.

### Design principles honored

- **Isolation.** Coach lives in its own module and its own LLM call path — no prompt state shared with extraction or reasoning models. This is the adversarial part: Coach doesn't know how the answer was produced, only what the facts say.
- **Cheap default.** Coach model defaults to Haiku 4.5 (fast + cheap). Verification is a per-answer overhead call; it shouldn't share the reasoning-model budget.
- **Testable.** Coach call is factored so tests mock `AsyncAnthropic` without touching the wider pipeline. Confidence banding is a pure function.
- **Ships-only receipts.** The pattern comes from the maintainer's earlier `y=c` project — this is the first time it lands as a shipped MCP tool with a benchmark harness.

### Test breakdown

- 27 new unit tests in `tests/test_v01212_coach_player.py` — schema, banding rule (locked at HIGH=100%, MEDIUM=>=70%, LOW=<70%), Coach prompt shape, JSON parsing (bare, code-fenced, malformed), `verify_answer` never-raises contract across all five failure modes, `WorldModelTools.verify_retrieval` end-to-end integration with mocked Coach client, MCP + Hermes surfaced schemas expose the tool with `required: [query, answer, fact_ids]`
- 3 structural tests for the benchmark files (pairs.json shape + expected_confidence/category invariant, runner imports the shipped `verify_answer`)
- Regression: full suite 654 pass (v0.12.0 baseline 624; +30 net)
- Contradictions benchmark: 105/105 (100%)

### Adapter matrix unchanged

Ten runtimes still covered. `verify_retrieval` is available to any runtime already registered with world-model-mcp — no adapter changes needed.

### Coach-Player benchmark, first run

Left to the maintainer to run once (requires ANTHROPIC_API_KEY, ~$0.03). Expected result: 12/12 exact match on the starter pairs, or documented Coach failure modes if not. Post-run summary lives at `benchmarks/coach-player/results.json` after `--out` is passed.

### What is NOT in this release

- **Expansion of `pairs.json` to ≥30 pairs.** Aspirational for a follow-up; the current 12 pairs make the ship floor for hallucination catch rate un-enforceable at 95% (12/12 vs 11/12 gap is only 8%).
- **`answer_with_verification` end-to-end tool** (query_fact → synthesize → verify in a single MCP call). Deferred — the two-step shape (`query_fact` then `verify_retrieval`) gives callers full control over the answer synthesis step, and adding a wrapper is a mechanical follow-up if that's the ask.
- **Adaptive Coach model selection** based on answer length / claim count. Fixed Haiku 4.5 default is honest to ship; adaptive routing is an optimization for once we see real usage patterns.

## v0.12.0 (July 2026)

Breadth + depth release. Nine substantive changes ship across three surfaces: new adapter coverage (Copilot, Cline, Windsurf, Continue --global) that closes the largest addressable-audience gap left by v0.10; consumer wiring for the v0.11.1 content-type schema plus governance schema additions (`influence_state`, `expires_at`); and a diagnostic + spec-readiness pass (`world-model doctor`, MCP 2026-07-28 audit). Two roadmap items (v0.12.8 OpenClaw TS plugin, v0.12.10 Antigravity CLI adapter) are explicitly deferred per their roadmap-gated conditionals — no adoption signal and no unblocked SDK, respectively.

### What ships

- **v0.12.1: `world-model doctor` command.** Eight diagnostic checks — node availability, `.claude/settings.json` presence, settings.json shell-quoting (specifically the pre-v0.11.0 unquoted-`$CLAUDE_PROJECT_DIR` bug pattern the v0.11.2 dogfooding investigation surfaced), hook scripts, `.mcp.json` registration, world-model DB directory + `events_queue.jsonl`, stale events queue backlog, and Claude Code hook-error history filtered by `settings.json` mtime. `--json` for machine-readable output; `--fix` attempts safe rewrites. Would have caught the v0.11.0 shell-quoting bug automatically instead of via manual investigation.

- **v0.12.2: `influence_state` + `expires_at` on Fact.** Two nullable additive fields closing enterprise memory-governance gaps the existing status/severity/decay axes did not address. `influence_state` (`observed`/`pending_review`/`approved`/`blocked`) separates storage from influence on planning — a fact can be stored as evidence without being trusted by planners, or blocked from planning while still visible to audit. `expires_at` complements the continuous `last_decay_at` erosion with hard drop-dead timestamps for compliance retention and ephemeral credentials. Migration mirrors the v0.11.1 pattern exactly: NULL-default ALTER, index, no backfill, idempotent. Citation polarity — evaluated and deliberately deferred to medium-term — requires retrieval caller cooperation not controllable at the schema layer.

- **v0.12.3: universal content-type routing consumers.** Closes the write- and consumer-side loop opened by v0.11.1. That patch added `content_type` to the model and table but never wired a consumer — worse, `create_fact` silently dropped the field on write, so every caller that set it saw the value discarded. This release fixes both: `create_fact` now persists all three v0.11.1/v0.12.2 new fields, `query_facts` hydrates them on read, and `query_facts` accepts a `content_type` filter that excludes NULL rows when set. `tools.query_fact` and the MCP + Hermes surfaced schemas expose the filter. `get_injection_context` is now routing-aware: rules always inject at PostCompact / UserPromptSubmit / SessionStart under a dedicated "## Rules (always active)" section, facts (or NULL) fill remaining slots, procedures are excluded from auto-injection entirely and reachable only via explicit `query_fact(content_type='procedure')`. Rendered payload adds `rules_count` alongside `facts_count` and `constraints_count`.

- **v0.12.4: GitHub Copilot Chat adapter (`install-copilot`).** Merges into `.vscode/mcp.json` per workspace. Copilot Chat uses `"servers"` at the top level, not `"mcpServers"` — this is where Copilot diverges from every other adapter world-model ships, and getting it wrong silently registers nothing. Merge semantics: absent → write; existing → preserve other servers; existing `world-model` → skip unless `--force`; malformed / wrong-shape JSON → refuse and leave file untouched. Load-bearing detail: users routinely have `.vscode/mcp.json` populated with the github MCP server and playwright; overwrite would delete that config with no recovery. Closes the largest addressable-audience gap in the adapter roster.

- **v0.12.5: `install-continue --global` config-merge path.** Deferred from v0.10. Merges into `~/.continue/config.yaml`'s `mcpServers` LIST (Continue's schema — distinct from Hermes' mcp_servers-mapping and from Claude Code / Cursor / Copilot / Cline / Windsurf's mcpServers-mapping). `ruamel.yaml` round-trip preserves comments, blank lines, and key ordering so a heavily-annotated user config is not stripped. New `[continue]` extra mirrors `[hermes]`. Per-project mode (no `--global`) is unchanged; the v0.10 behavior contract is under regression coverage.

- **v0.12.6: Cline adapter (`install-cline`).** Merges into `~/.cline/mcp.json`. Cline uses the `mcpServers` mapping shape — same as Cursor / Claude Code, so the merge logic mirrors install-copilot with a different top-level key. Cline-specific fields (`disabled: false`, `autoApprove: []`) defaulted safely in the bundled template.

- **v0.12.7: Windsurf adapter (`install-windsurf`).** Merges into `~/.codeium/windsurf/mcp_config.json`. Windsurf uses the same `mcpServers` mapping shape as Cline, so merge behavior is behaviorally identical — only the default path differs. Merge logic deliberately copy-pasted from install-cline rather than refactored into a shared helper: each landed and passed its own regression suite; extraction is scope creep until a fourth mcpServers-mapping adapter arrives.

- **v0.12.8: OpenClaw TypeScript plugin bundle — DEFERRED.** Roadmap gated on "MCP-only adoption of the v0.10 OpenClaw adapter justifies the plugin work." Signal check five days after v0.10 shipped: four tracked threads, zero reactions on our comments, no explicit ask for a TS plugin. Honoring the conditional.

- **v0.12.9: Hermes lifecycle hooks on `WorldModelMemoryProvider`.** Layers the five optional lifecycle hooks (`sync_turn`, `on_pre_compress`, `prefetch`, `on_session_end`, `on_memory_write`) on top of the v0.11.0 MemoryProvider ABC. `on_pre_compress` returns a compact injection bundle that honors the v0.12.3 content-type routing — rules always inject, procedures never do. Contract for every hook: best-effort by convention (exceptions caught, safe default returned), sync front-door with async back-end via `_run_async`, safe no-op or empty return when called before `initialize()`.

- **v0.12.10: Antigravity CLI adapter — DEFERRED.** Roadmap: "Blocked pending a `TransformCompactionHook` in the SDK." Confirmed the SDK CHANGELOG through v1.0.16 has zero mention of `TransformCompactionHook` or any compaction-time hook. External blocker still in place; ships whenever the SDK does.

- **v0.12.11: MCP 2026-07-28 spec readiness scaffolding.** Non-behavior-changing observability + public audit. The 2026-07-28 spec is currently Release Candidate with the final ship 22 days out; building against the RC risks throwaway code if key names or response shapes drift before final. This release ships: (1) `world_model_server/spec_readiness.READINESS_STATE` — the machine-readable audit matrix. Five rows locked at states covering stateless-first (`compatible`), `_meta` field (`logged`), HTTP header emission (`not_yet` — gateway routing risk), `InputRequiredResult` (`not_applicable` — no tool elicits mid-call input), `server/discover` (`not_yet` — response shape unlocked). (2) `extract_meta` / `log_meta_if_present` observability helpers wired into `server.py:call_tool` with a single line. (3) `docs/MCP_2026_SPEC_READINESS.md` public audit doc kept in lockstep with `READINESS_STATE` under test. Backward compatibility with the 2025-03-26 spec is preserved unconditionally.

### Test breakdown

Full suite: **624 tests pass** (v0.11.2 baseline: 486; +138 across nine feature PRs).

- v0.12.1: 24 (doctor checks, auto-fixes, CLI, JSON output)
- v0.12.2: 18 (schema migration, model validation, index creation, NULL tolerance)
- v0.12.3: 18 (write persistence regression, filter behavior, injection routing, procedure exclusion, MCP + Hermes surfaced schemas)
- v0.12.4: 17 (bundled shape, merge preservation, force overwrite, refusal on malformed input, dry-run, CLI wiring)
- v0.12.5: 14 (fresh install, merge order, comment round-trip, skip/force, shape refusal, per-project regression contract)
- v0.12.6: 17 (Cline-shaped bundled, merge cases, error refusal, dry-run, CLI wiring)
- v0.12.7: 17 (Windsurf-shaped bundled, merge cases, error refusal, dry-run, CLI wiring)
- v0.12.9: 22 (five hooks present + callable, events written, routing honored in pre-compress, exception swallowing, before-initialize safety)
- v0.12.11: 15 (readiness state locked, extract_meta / log_meta semantics, call-site wiring, audit doc / constant sync)

Contradictions benchmark: **105/105 (100.0%)** — unchanged from v0.11.0's `auto` strategy rewrite.

Pre-v0.12 test files (all 18 of them) still green in isolation and as a suite. Real pre-v0.12 DB opened, upgraded, written, and queried by v0.12.3 code end-to-end: migration idempotent, legacy rows readable with all three new fields at NULL, new writes persist the fields, injection includes both legacy and new rows in the right sections.

### Migration notes

All schema additions are additive-only ALTER TABLE + NULL defaults; existing DBs are auto-upgraded on next `KnowledgeGraph.initialize()`. Tested end-to-end from a v0.11.0 DB through v0.12.3 code — legacy rows readable, new rows write cleanly with `content_type` / `influence_state` / `expires_at` populated, `get_injection_context` renders both old and new sections correctly.

No breaking changes to any public tool schema. `query_fact` gains an optional `content_type` filter parameter — clients that do not send it see behavior identical to v0.11.

### Adapter matrix after v0.12

| Runtime | Adapter | Config location | Top-level shape |
| --- | --- | --- | --- |
| Claude Code | `.mcp.json` shipped in v0.7 | project | `mcpServers` map |
| Cursor | `install-cursor` | project `.cursor/` | `mcpServers` map |
| Codex | `install-codex` | `~/.codex/config.toml` | TOML append |
| Hermes | `install-hermes`, `install-hermes-provider` | `~/.hermes/config.yaml` / plugin dir | `mcp_servers` map |
| Continue | `install-continue` (per-project) + `--global` | project or `~/.continue/config.yaml` | `mcpServers` list |
| OpenClaw | `install-openclaw` | project | JSON |
| pi | `install-pi` | package | pi manifest |
| GitHub Copilot | `install-copilot` **(new)** | `.vscode/mcp.json` | `servers` map |
| Cline | `install-cline` **(new)** | `~/.cline/mcp.json` | `mcpServers` map |
| Windsurf | `install-windsurf` **(new)** | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` map |

Ten runtimes covered.

### Deferred items

- **v0.12.8 OpenClaw TypeScript plugin bundle** — no adoption signal within five days of v0.10 shipping. Roadmap-gated on MCP-only adoption of the v0.10 adapter justifying the TypeScript surface. Revisit when a specific integrator asks.
- **v0.12.10 Antigravity CLI adapter** — SDK still lacks `TransformCompactionHook` through v1.0.16. Ships whenever the SDK does.
- **Citation polarity schema field** — evaluated and moved to medium-term. Populating it requires the retrieval caller to know supporting-vs-refuting intent, which the schema layer cannot control. Revisit if an integrator commits to instrumenting the annotation.

## v0.11.0 (July 2026)

Depth release. v0.10 expanded surface area to seven runtimes; v0.11 solves real problems for the users we now have rather than adding more surfaces. Four things ship: a `auto` contradiction-resolution strategy rewrite (77.1% → 100.0% on the v0.8.1 benchmark), a Hermes native `MemoryProvider` plugin that intercepts writes at Hermes' routing layer, a content-type schema field (`rule` / `fact` / `procedure`) for future routing intelligence, and a dogfooding case study with a reproducibility contract.

### What this release IS about

Two signals shaped v0.11:

1. **The [Hermes #47349 exchange (2026-07-01)](https://github.com/NousResearch/hermes-agent/issues/47349)** with @TechFlipsi surfaced that the v0.10 MCP adapter, though useful, could not close the "agent still defaults to writing `MEMORY.md`" problem. MCP surfaces tools; the LLM still chooses when to call them. Only a `MemoryProvider` plugin — Hermes' write-side interception layer — can force routing. This release ships that plugin.

2. **The `auto` contradiction-resolution strategy** on the v0.8.1 benchmark still scored 77.1% because it did not fully consume the `confirmer` + decay-awareness fields shipped in v0.8.0. This release rewrites it to score 100.0% on the same dataset.

Both are engineering investments in the existing user base rather than new-runtime coverage.

### What is new in v0.11.0

- **v0.11.0 A: `auto` strategy rewrite in `resolve_contradiction`.** Confirmer-aware + decay-aware + tie-detection. Lifts the v0.8.1 contradiction-resolution benchmark's `auto` score from 77.1% to 100.0% on the same 105-pair × 19-category dataset. Overall benchmark accuracy across four canonical strategies + the decayed strategy rises from 78.2% to 83.7%. Non-auto strategies unchanged. `keep_higher_confidence_decayed` promoted from benchmark-only to a first-class option in `pick_winner`. Rule order in the meta-strategy: (1) Settled beats pending when one fact has `confirmer` set and the other doesn't (13 pairs). (2) Decay-aware confidence when `evidence_type` is present (16 pairs). (3) Fall-through with tie-detection: source-count with distinct-tools counting, user-source priority at tied count, confidence gap ≥ 0.1 with 0.0-value guard, recency gap ≥ 2 days. Below all thresholds, return `None` for manual review instead of picking arbitrarily.

- **v0.11.0 B: Hermes native `MemoryProvider` plugin + `install-hermes-provider` CLI.** Python plugin implementing Hermes' `agent/memory_provider.py` ABC. Intercepts writes at the routing layer, not at the tool surface — the architectural distinction the v0.10 MCP adapter could not close. Seven surfaced tools (`query_fact`, `get_constraints`, `get_injection_context`, `record_event`, `record_correction`, `find_contradictions`, `resolve_contradiction`) — trimmed from the 27 MCP tools to keep Hermes' tool namespace focused. Soft ABC import (falls back to `object` when Hermes is not installed) so the plugin file imports on test machines. Sync ↔ async bridge via per-call event loop. Config constructed directly (`Config(db_path=...)`) not via `from_env` so the plugin does not pollute `os.environ`. Ships as `world_model_server/hermes_memory_provider/` in the wheel; `install-hermes-provider` copies the plugin into `<hermes_home>/plugins/memory/world-model/` with `--hermes-home` / `--force` / `--dry-run`. Optional Hermes hooks are v0.12 follow-ups.

- **v0.11.1: content-type routing schema field.** Nullable `content_type: Optional[Literal["rule", "fact", "procedure"]]` on the `Fact` model and the facts table. Additive-only migration behind an existence check; existing rows keep NULL and continue to work; the migration is idempotent. Index `idx_facts_content_type` created for future routing filters. Distinct from `evidence_type` (which describes where the fact came from) — `content_type` describes what shape of content the fact carries, so a MemoryProvider can route writes intelligently rather than dumping everything into one destination. Consumers (query filters, MemoryProvider write routing) are v0.11.x follow-ups; this release ships the schema field, tests, and migration only.

- **v0.11.2: dogfooding case study.** Publishes what the fact graph actually captured about the world-model-mcp codebase in `.claude/world-model/`. 608 facts (607 from the seeder + 1 real `bug_fix` reflection), 600 entities, 3 learned constraints with real violation counts. Two constraints (`check-twine-before-tag`, `tag-before-upload`) map directly to release-mechanics incidents from v0.9.1 and v0.10.1 — evidence that the memory layer captured the maintainer's own past mistakes. **Honest about what was NOT captured**: `events` / `decisions` / `sessions` tables are empty because `.mcp.json` was missing from the repo root during v0.10 / v0.11 development. The case study reports this explicitly rather than hiding it. Reproducibility contract: `python scripts/dogfooding_snapshot.py --db-path .claude/world-model --out /tmp/snap.json && diff -u case-studies/v011-dogfooding/snapshot.json /tmp/snap.json` should be empty on this release commit. Drift-protection tests (9 of them) fail if the writeup and the snapshot diverge.

- **Shell-quoting fix in `setup_command` (v0.11.0 bug fix).** The `.claude/settings.json` generator wrote unquoted `$CLAUDE_PROJECT_DIR` in every hook command. When Claude Code expanded the env var at shell time and the value contained a space (macOS defaults like `~/Documents/`, corporate paths, or intentional folder names like the maintainer's `claude context graph/world-model-mcp`), the shell split the expanded value on whitespace and Node received garbage. Every hook invocation on any such project has silently failed with `Error: Cannot find module '/Users/name/first_word_of_path'` since v0.7.3 shipped hooks. This affected every user with a space-containing project path — a real slice of the user population — and produced zero-capture installs with no user-facing error. The v0.11.2 dogfooding case study surfaced the anomaly, and the trace through Claude Code's session transcripts identified the root cause. The fix is a two-line change: wrap `$CLAUDE_PROJECT_DIR` in double quotes. A regression test (`tests/test_v0110_setup_shell_quoting.py`) locks the fix down against future re-introduction. **This is a bug that was found by dogfooding.** The bundled `.claude/settings.json` in the world-model-mcp repo itself was regenerated with the fix so the maintainer's own future dogfooding sessions actually capture. This entire trail (initial `.mcp.json` hypothesis → deeper trace → shipped-bug root cause → fix + regression test) is documented in `case-studies/v011-dogfooding/CASE_STUDY.md`.

### Test breakdown

- v0.10: 417 tests
- v0.11.0 A: no new tests (used the existing v0.8.1 benchmark as validation)
- v0.11.0 B: +21 tests
- v0.11.1: +10 tests
- v0.11.2: +9 tests
- **v0.11.0 total: 457/457 pass.** Zero regressions across four PRs.

### What is unchanged

- All v0.10.x code paths: 27 MCP tools reported by adapters (no new server-side tools), SWE-bench Verified benchmark + multi-seed appendix, seven-runtime adapter coverage.
- The Zenodo preprint — paper unchanged since v0.9.2. **No new Zenodo version for v0.11.0.** Concept DOI `10.5281/zenodo.20834508` still resolves to v0.9.2 (Appendix A: multi-seed replication).
- Wedge claims (lifecycle-hook capture, per-fact provenance, per-evidence-type decay, PreToolUse defer) survive unchanged.

### Ship flow

3-channel: PyPI + GitHub Release + MCP Registry. **No Zenodo update** — paper text is unchanged.

---

## v0.10.1 (July 2026)

Documentation patch. Fixes a stale Zenodo DOI reference in the README badge, the roadmap section, `paper.md`, and the OpenClaw adapter README. No code changes; no methodology changes; no schema changes.

### What this fixes

The Zenodo record for the SWE-bench Verified paper has two DOIs per version: a **version DOI** (immutable, points to a specific version) and a **concept DOI** (auto-resolves to whichever version is latest). Prior to this patch, everywhere the repo referred to "10.5281/zenodo.20834509" as the "Zenodo concept DOI", it was in fact the version DOI for version 1 (v0.9.1) of the record. Readers clicking the README's DOI badge landed on the pre-multi-seed paper, not on v0.9.2 which has Appendix A.

The correct concept DOI (verified against the Zenodo API on 2026-07-01) is **10.5281/zenodo.20834508**. This patch replaces every reference to `20834509` in the repo with `20834508` so the badge, roadmap entry, `paper.md` header, and OpenClaw adapter README all point at the concept DOI that auto-resolves to the latest published version.

### DOI map (for reference)

| Reference | DOI |
|---|---|
| Concept DOI (auto-resolves to latest) | `10.5281/zenodo.20834508` |
| Version 1 (v0.9.1, published 2026-06-24) | `10.5281/zenodo.20834509` |
| Version 2 (v0.9.2, published 2026-06-30) | `10.5281/zenodo.21076824` |

### What is unchanged

- All v0.10.0 code paths: 27 MCP tools, 22 CLI subcommands, 417 tests, the three v0.10 adapters (OpenClaw, Hermes, Continue).
- Paper content is unchanged from v0.9.2. `paper.pdf` is regenerated only to correct the front-matter DOI line.
- No Zenodo upload is needed for v0.10.1 because the paper text is unchanged — only the DOI reference in repo prose is corrected.

### Ship flow

3-channel: PyPI + GitHub Release + MCP Registry. Skip Zenodo.

---

## v0.10.0 (July 2026)

Adapter-surface release. Three new adapters ship in one release, extending the harness-neutral memory story from four runtimes to seven: **OpenClaw**, **Hermes Agent**, and **Continue**. Each is verified end-to-end against a live installation of the target runtime. No server-side code changes; no schema changes; no benchmark methodology changes.

### What this release IS about

Cross-runtime memory has been the central v0.10 thesis since the roadmap locked at the end of v0.9.2. The 2026-06-30 deep research on the Hermes/OpenClaw/ClawMem competitive landscape surfaced three facts that shaped this release:

1. **OpenClaw ships no native memory layer.** Pure-additive integration — the OpenClaw agent turn simply gains 27 world-model tools with no capability overlap or slot competition. Highest fit-to-cost of any v0.10 candidate.

2. **Hermes ships a bounded manual-curation memory system** (`MEMORY.md` + `USER.md`, character-capped, no auto-decay — the docs explicitly say "Memory does not auto-compact"). world-model-mcp's per-fact provenance and per-evidence-type decay are complementary, not overlapping. The differentiation gap remains intact against Hermes v0.17.0.

3. **ClawMem already ships a cross-runtime memory adapter** (Claude Code + OpenClaw + Hermes) against a plain key-value SQLite vault. This release positions on schema depth, not on being first-to-integrate — the differentiator is provenance + decay + PreToolUse enforcement, not the fact of cross-runtime coverage.

### What is new in v0.10.0

- **OpenClaw adapter + `install-openclaw` CLI**. Merges into `~/.openclaw/openclaw.json` under `mcp.servers.world-model`, preserving all other keys. Defaults `command` to `sys.executable` (absolute path). Rejects relative `--python` overrides as a hard error. Verified against OpenClaw `2026.6.11 (e085fa1)` on macOS: `openclaw mcp probe world-model` reports 27 tools discovered.

- **The absolute-path gotcha, documented.** During E2E verification of OpenClaw, the first attempt failed with `MCP error -32000: Connection closed`. Root cause: OpenClaw's process spawn does not inherit shell PATH, so `--command python3` failed even though `python3 -m world_model_server.server` worked fine from the shell. The fix propagated to Hermes and Continue as a precaution: every v0.10 install command defaults to `sys.executable` and rejects relative `--python` overrides. This is the kind of gotcha you only find by running against a real install.

- **Hermes Agent adapter + `install-hermes` CLI**. Merges into `~/.hermes/config.yaml` under `mcp_servers.world-model`. Uses `ruamel.yaml` round-trip mode to preserve every comment and blank line in Hermes' 1327-line reference config. Requires the `[hermes]` optional extra (`pip install "world-model-mcp[hermes]"`).

- **The comment-preservation regression test.** An initial `pyyaml.safe_dump` implementation stripped ~1170 lines of documentation from Hermes' reference config during E2E testing (data-preserving, formatting-catastrophic). The fix — `ruamel.yaml` round-trip mode — is locked down by `test_f2_install_hermes_preserves_comments_and_blank_lines`. Same kind of gotcha as the OpenClaw one: only surfaces against a real install.

- **Continue adapter + `install-continue` CLI**. Writes a standalone `<project>/.continue/mcpServers/world-model.yaml` following Continue's documented per-server-file pattern. No config merge needed. CLI-side E2E verified: the exact stdio spawn Continue would perform returns 27 tools via a live `tools/list` JSON-RPC roundtrip. Last-mile "does Continue's LLM surface the tools in agent mode" verification requires a live VS Code / JetBrains session and is called out in the adapter README.

- **Cross-runtime shared memory.** All v0.10 adapters default `WORLD_MODEL_DB_PATH` to `.claude/world-model` — a project-relative path. A project running in multiple clients shares one SQLite fact graph across all of them. Override with an absolute `--db-path` for user-wide shared memory.

- **Test suite grew from 375 to 417.** Every adapter's test suite includes bundled-file validity, dry-run behavior, absolute-path defaults, idempotence, `--force` overwrite, relative-`--python` rejection, parent-directory creation, malformed-config handling, and subparser-registration regression coverage.

### What is unchanged

- All v0.9.2 code paths: the 26 base MCP tools (no new server-side tools in v0.10; the "27 tools" count reported by adapters includes `resolve_contradiction` from v0.8.0), the SWE-bench Verified benchmark, the multi-seed replication appendix, the paper and preprint on Zenodo (10.5281/zenodo.20834508).
- The wedge claims at the architectural level (lifecycle-hook capture, per-fact provenance, per-evidence-type decay, PreToolUse defer).
- The multi-seed-honesty framing from v0.9.2: the v0.9 +10.2 pts paired delta remains published as a single-trial upper bound.

### Roadmap follow-ups tracked in this release

- OpenClaw TypeScript plugin bundle for typed lifecycle hooks (`before_prompt_build`, `before_tool_call`, `before_compaction`, `session_start`, ...) — only if MCP-only adoption justifies the plugin work.
- Hermes native `MemoryProvider` ABC plugin (Track B) — only if MCP-route adoption justifies the plugin work; ClawMem already occupies the exclusive external-memory-provider slot for many users.
- Continue `--global` config-merge path into `~/.continue/config.yaml` (would use `ruamel.yaml` round-trip mode like the Hermes adapter).
- Full-corpus multi-seed replication: all 49 paired SWE-bench Verified instances at 3-5 seeds.
- Head-to-head benchmarks against mem0, Letta, Zep, piia-engram, ClawMem.

---

## v0.9.2 (June 2026)

Documentation patch. Ships the multi-seed replication that `SEED_PLAN.md` (locked 2026-06-25) committed to running. No code changes; no methodology changes; honest update to the confidence bounds on the v0.9 paired-delta headline.

### What this release IS about

The v0.9 paper shipped on 2026-06-24 with a single-trial result: +10.2 pts paired delta across 49 paired SWE-bench Verified instances. The paper's limitations section identified single-trial design as the primary methodological risk ("Some of the observed flips and the one regression may be due to single-trial variance rather than genuine constraint effects"). v0.9.2 ships the multi-seed test that SEED_PLAN.md called for and publishes the result verbatim per the pre-registered acceptance criteria.

The result tightens the confidence bounds significantly. On a pre-registered 17-instance subset, baseline pass rate swung +41 percentage points between seeds 1 and 2 with no methodology change. Of the 7 load-bearing instances that drove the v0.9 headline, 0 of 7 had both their seed-1 baseline AND treatment outcomes reproduced at seed 2. The mean paired delta across both seeds on the 17-instance subset is +0.24 per instance with a bootstrap 95 percent CI of [0.00, 0.47]. The constraint effect is small, possibly nonzero, and not statistically distinguishable from zero at sample size 2.

### Why this is a v0.9.2 patch and not a retraction

The methodology discipline held. SEED_PLAN.md was locked six days before any additional seed run, with subset selection, variance metrics, and interpretation thresholds committed to public source. The seed-2 result hit acceptance criterion B (weak replication). The honest update was prepared and shipped per the pre-registered plan. This is what pre-registration is for.

The wedge claims at the architectural level (lifecycle-hook-based memory capture, per-fact provenance schema, per-evidence-type decay, PreToolUse defer enforcement) are unchanged. The empirical claim about the magnitude of the constraint effect on SWE-bench Verified is what changes: the v0.9 +10.2 pts paired delta should be read as a single-trial upper bound, not as the steady-state effect size. The replicated effect size on the load-bearing subset across two seeds is small, possibly zero.

### What is new in v0.9.2

- **Multi-seed replication appendix added to `benchmarks/repeat-mistake/RESULTS.md`**. Includes the pre-registered 17-instance subset, per-instance results across seed 1 and seed 2, headline numbers (per-arm pass rate, per-seed paired delta, mean paired delta with bootstrap 95 percent CI), the honest interpretation, the decision to skip seed 3, and what this changes for the wedge.

- **Multi-seed appendix added to `benchmarks/repeat-mistake/paper.md` as Appendix A**. Same content as RESULTS.md appendix, in paper-shaped prose. The `paper.pdf` is regenerated to include the appendix.

- **`SEED_PLAN.md` status update appended (preserving the pre-registered plan above)**. Documents the seed-2 outcome and the seed-3 skip decision per acceptance criterion B locked above.

- **Multi-seed raw artifacts committed**: `baseline_progress_seed2.jsonl`, `treatment_progress_seed2_treatment.jsonl`, `baseline_predictions_seed2.json`, `treatment_predictions_seed2.json`, `baseline_results_seed2.jsonl`, `treatment_results_seed2.jsonl`, `multi_seed_summary_seed2.json`. Anyone can re-run the aggregator against these files.

### What is unchanged

- All v0.9.1 code: 26 MCP tools, 19 CLI subcommands, 375 tests, opt-in telemetry.
- All v0.9 benchmark methodology and per-task tables in the main body of RESULTS.md and paper.md. The single-trial v0.9 numbers are preserved verbatim; the appendix adds the multi-seed evidence that bounds them.
- The v0.9 Zenodo preprint (DOI 10.5281/zenodo.20834508) — the concept DOI resolves to the latest version; v0.9.2 ships as a new version of the same record.

### Honest framing

This release demonstrates that the v0.9 methodology discipline (pre-registered DESIGN.md, locked judge prompts, explicit limitations) survives contact with a replication test that disagrees with the single-trial headline. The honest update is shipped. The discipline is the moat, not the headline number.

---

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
