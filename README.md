# World Model MCP

**Coding agents remain blind to the codebase they operate on.** They infer structure late, reduce it to prompts, and ignore it when decisions are made in real time — repeating the same mistakes, hallucinating APIs that don't exist, and forgetting learned constraints the moment context compacts.

**World Model MCP is the memory-graph infrastructure that closes that gap.** A temporal knowledge graph that validates code changes against learned constraints at the edit boundary, re-injects relevant context after compaction, tracks contradictions with confidence-weighted resolution, adversarially verifies retrievals via an independent Coach LLM, and runs across Claude Code, Cursor, Codex, pi, OpenClaw, Hermes Agent, Continue, GitHub Copilot Chat, Cline, and Windsurf.

> **Latest: v0.14.0** — opt-in telemetry sink migration to https://etch.systems + GDPR right-to-erasure (`world-model telemetry --forget-me`). Off by default. Payload spec in [docs/PRIVACY_TELEMETRY.md](docs/PRIVACY_TELEMETRY.md); [full version history](#full-version-history) below covers v0.7.0 onward.

[![PyPI](https://img.shields.io/pypi/v/world-model-mcp.svg)](https://pypi.org/project/world-model-mcp/)
[![Downloads](https://img.shields.io/pypi/dm/world-model-mcp.svg)](https://pypi.org/project/world-model-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![world-model-mcp MCP server](https://glama.ai/mcp/servers/SaravananJaichandar/world-model-mcp/badges/card.svg)](https://glama.ai/mcp/servers/SaravananJaichandar/world-model-mcp)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20834508.svg)](https://doi.org/10.5281/zenodo.20834508)

mcp-name: io.github.SaravananJaichandar/world-model-mcp

## Hosted companion: Etch

world-model-mcp is the OSS memory + tamper-evident audit layer you can run locally. **[Etch (etch.systems)](https://etch.systems)** is the hosted companion — cryptographic notary + governance plane for AI agent decisions, built on this OSS core. Same crypto primitives (hybrid Ed25519 + SLH-DSA-SHA2-128f Merkle chain), same audit-log schema, additional hosted-only features: PII scan, secret detection, session narrative overlays, client-answer PDF export, and multi-tenant per-project stores.

You can run world-model-mcp entirely offline. Etch is optional and only used if you opt into the hosted service (signup gated) or opt into anonymous telemetry (off by default, inspectable payload, right-to-erasure supported).

---

## Numbers

| Benchmark | Score | Details |
|---|---|---|
| [SWE-bench Verified repeat-mistake](https://github.com/SaravananJaichandar/coding-agent-memory-benchmark) | **+10.2 pts** (67.3% → 77.6% on 49 paired instances) | Pre-registered, Claude Code 2.1.177 headless, Zenodo DOI [10.5281/zenodo.21076824](https://doi.org/10.5281/zenodo.21076824). Within-domain +15.0 pts, cross-domain +6.9 pts with zero regressions. Multi-seed appendix documents single-trial upper bound honestly. |
| [Contradiction-resolution](benchmarks/contradictions-200/RESULTS.md) | **100.0%** on `auto` strategy | 105 pairs × 19 categories, deterministic (no LLM). Shipped since v0.11.0. |
| [Coach-Player verification](benchmarks/coach-player/) | **100.0%** exact match | 12 hand-labeled pairs (4 grounded, 4 partial, 4 hallucinated). Layer 3 adversarial verification via independent Coach LLM. Shipped since v0.12.12. |

The SWE-bench number is the load-bearing empirical claim. The other two are internal correctness benchmarks for shipped components. Reproducibility scripts in each benchmark directory or the linked repo.

## Tamper-evident audit log (v0.13, opt-in)

For compliance-track deployments where the audit trail must be cryptographically verifiable (SOC2, HIPAA, FISMA):

```bash
export WORLD_MODEL_AUDIT_LOG=on
world-model  # start server as usual
```

Every fact, constraint, event, and decision write chains into an append-only log. Every 1024 entries (env-tunable), an epoch closes with a Merkle root signed by a hybrid **Ed25519 + SLH-DSA-SHA2-128f** signature (both FIPS-approved; both required for verification). Compliance auditors call `prove_entry_inclusion(row_id)` via MCP, load the operator's public keys from `<db_path>/keys/public_keys.json`, and run the reference verifier locally — no round trip needed for verification.

- Full threat model, key management, auditor workflow: [docs/AUDIT_LOG.md](docs/AUDIT_LOG.md)
- Reference verifier (Python + TypeScript): `world-model-mcp-verifier` repo
- Storage overhead: ~3 MB per project per year for a median deployment
- Non-opt-in path is unchanged: no schema, no keys, no crypto imports if `WORLD_MODEL_AUDIT_LOG` is unset

The audit log is deliberately opt-in. If your deployment does not have a cryptographic-audit requirement, leave it off — the log adds storage, one hash per write, and crypto dependencies. None of that is worth paying for if nobody in your stack is going to audit the log.

> If world-model-mcp helped you, star the repo or open an issue with what worked or didn't. I read every one and the feedback shapes what ships next.

---

## What It Does

World Model MCP creates a **temporal knowledge graph** of your codebase that learns from every coding session to:

- **Prevent Hallucinations** -- Validates API/function references against known entities before use
- **Stop Repeated Mistakes** -- Learns constraints from corrections, applies them in future sessions
- **Reduce Regressions** -- Tracks bug fixes and warns when changes touch critical regions
- **Survive Compaction** -- Re-injects top constraints and recent facts after the agent's context window resets
- **Resolve Contradictions** -- Picks a winner between conflicting facts using confidence, recency, or source count

Think of it as a long-term memory layer that runs alongside Claude Code, Cursor, Codex, pi, OpenClaw, Hermes Agent, Continue, GitHub Copilot Chat, Cline, Windsurf, or any MCP-aware coding agent.

---

## See it working

Three cloneable starter repos show world-model-mcp wired into a real Python (FastAPI + SQLAlchemy) project across the three highest-adoption MCP runtimes. Each ships 5 seeded constraints, 1 bug-fix reflection, and a `WHAT_TO_TRY.md` with concrete workflows. Fork one, `pip install`, and see the memory layer catch a constraint violation on the first edit.

| Starter | Runtime | Config shape | Automatic enforcement |
| --- | --- | --- | --- |
| [world-model-mcp-claude-code-starter](https://github.com/SaravananJaichandar/world-model-mcp-claude-code-starter) | Claude Code CLI | `.mcp.json` + `.claude/settings.json` | Yes (4 lifecycle hook events) |
| [world-model-mcp-cursor-starter](https://github.com/SaravananJaichandar/world-model-mcp-cursor-starter) | Cursor Editor | `.cursor/mcp.json` + `.cursor/hooks.json` | Yes (3 lifecycle hook events) |
| [world-model-mcp-copilot-chat-starter](https://github.com/SaravananJaichandar/world-model-mcp-copilot-chat-starter) | VS Code + Copilot Chat | `.vscode/mcp.json` (`"servers"` key, not `"mcpServers"`) | No — Copilot Chat lacks lifecycle hooks; memory queryable via MCP tool calls only |

All three point at the same `.claude/world-model/` DB path, so installing multiple starters (or all three) on one repo produces a shared fact graph across runtimes.

---

<details id="full-version-history">
<summary><strong>Full version history (v0.7.0 onward)</strong></summary>

## What's new in v0.14.0

- **Opt-in telemetry sink migration.** Client now POSTs to `https://etch.systems/api/telemetry/ingest`. Previous v0.7.3–v0.13 series shipped an empty PAT stub, so opt-in telemetry silently no-op'd for two months — this is the first release where it actually delivers events. Endpoint is unauthenticated (rate-limited server-side by `install_id`), 8 KB payload ceiling, source IP stripped from access logs. Payload schema: `event / install_id / version / ts / os_family / python_version / adapters / fields`. Everything on that list, nothing off it — enforced by strict server-side whitelist validation.
- **Daily heartbeat.** Once per 24h per opted-in install, the CLI fires a `heartbeat` event carrying the installed-adapter list. Enables an MAU-of-OSS-users number without touching content.
- **`world-model telemetry --forget-me` (GDPR right-to-erasure).** DELETEs every server-side row for your install_id and wipes local telemetry state (install_id + consent + last-heartbeat). Works even when the server is unreachable — local state is wiped regardless, so consent revocation is honored offline.
- **90-day server-side auto-TTL.** Data minimisation enforced automatically. See [docs/PRIVACY_TELEMETRY.md](docs/PRIVACY_TELEMETRY.md) for the full retention + IP-strip + rate-limit spec.
- **Dead code removed.** `world_model_server/_embedded_token.py` and `scripts/embed_token.py` deleted along with six now-obsolete tests. No PAT dance in the release process anymore.

## What's new in v0.12.13

- **OpenAI-compatible Coach backend.** `verify_retrieval` (v0.12.12's adversarial verification tool) can now route Coach calls through any OpenAI-shape endpoint — OpenRouter, Ollama, vLLM, LiteLLM, or a self-hosted deployment — without going through a proxy. Set `WORLD_MODEL_VERIFICATION_BACKEND=openai-compatible` and `WORLD_MODEL_VERIFICATION_BASE_URL=https://openrouter.ai/api/v1` (or your endpoint of choice); the Coach client is built via `AsyncOpenAI(base_url=...)` and dispatches through `chat.completions.create` with the system prompt in the messages list (OpenAI convention). API key priority: explicit `WORLD_MODEL_VERIFICATION_API_KEY` → `OPENROUTER_API_KEY` → `OPENAI_API_KEY` → a placeholder for local endpoints that don't authenticate. New optional `[openai]` extra ships `openai>=1.0`. Backward compat: default backend stays `anthropic`; existing installs and the v0.12.12 baseline are unaffected.
- **`doctor` Copilot log-signature scan.** New `check_copilot_hook_signatures` check parses `~/.copilot/logs/*.log` for the two documented failure modes from [copilot-cli #4001](https://github.com/github/copilot-cli/issues/4001): PowerShell `ParserError` (Copilot running bash-shaped commands through PowerShell on Windows) and `/.claude/...` path resolution (Copilot not exporting `$CLAUDE_PROJECT_DIR`; hooks run from cwd `/`). SKIPs gracefully when Copilot isn't installed. Reports the two signature counts separately so users can tell which of the two Copilot-side bugs is affecting them. Does NOT fix them — the fix has to come from Copilot — but separates "my hook wrapper is broken" from "the runtime is running my hook wrong."

## What's new in v0.12.12

- **Coach-Player adversarial verification (`verify_retrieval`).** New MCP tool that runs an independent Coach LLM call against a candidate answer plus supplied source facts and returns a confidence band (HIGH / MEDIUM / LOW) with itemized verified + unverified claim lists and per-claim source pointers. Coach lives in its own module (`world_model_server/verification.py`) with its own prompt — no state shared with extraction or reasoning models. That's the adversarial part: Coach doesn't know how the answer was produced, only what the facts say. Contract: never raises. Every failure mode (no API key, empty answer, no facts, Coach LLM error, malformed Coach response) returns a LOW-confidence result with `error` populated. Cheap default: `verification_model` defaults to Haiku 4.5 (~$0.001 per verify call), env-configurable via `WORLD_MODEL_VERIFICATION_MODEL`.
- **12-pair hand-labeled benchmark (`benchmarks/coach-player/`).** 4 grounded, 4 partial, 4 hallucinated pairs plus a runner that reports hallucination catch rate, false positive rate, MEDIUM band correctness, and overall exact match. Ship-floor policy: false positive rate ≤10% is enforced (non-zero exit); hallucination catch ≥95% is aspirational at N=12 and gets enforced once `pairs.json` expands to ≥30 pairs. Full run costs ~$0.03 at Haiku 4.5 pricing.
- **Exposed on both MCP + Hermes surfaces.** MCP `list_tools` gains `verify_retrieval` (27 → 28 tools); Hermes surfaced tool count 7 → 8 with the same tool schema.
- **Pattern origin.** Ported from the maintainer's earlier `y=c` project (Coach-Player adversarial cooperation between a Player synthesizer and a Coach verifier). world-model-mcp is the first MCP server to ship it as a first-class tool with a benchmark harness.

## What's new in v0.12.0

- **`world-model doctor` command (v0.12.1).** Eight diagnostic checks including `.claude/settings.json` shell-quoting (the pre-v0.11.0 unquoted-`$CLAUDE_PROJECT_DIR` bug pattern the dogfooding investigation surfaced), hook script presence, `.mcp.json` registration, world-model DB directory + stale `events_queue.jsonl`, and Claude Code hook-error history filtered by `settings.json` mtime. `--json` for machine-readable output; `--fix` for safe auto-rewrites. Would have caught the v0.11.0 shell-quoting bug automatically instead of via manual investigation.
- **`influence_state` + `expires_at` on Fact (v0.12.2).** Two nullable additive fields. `influence_state` (`observed` / `pending_review` / `approved` / `blocked`) separates two decisions made at different times: **storage is an ingest question** (do we keep this fact at all), **influence is a per-turn question** (do we surface it to the planner right now). Conflating them forces you to re-evaluate storage every time your planning threshold changes; splitting them lets you block a high-confidence fact from influencing a plan *without deleting it* — the disputed-entity-update-pending-review case that comes up often in graph work. A single trust-score is simpler until the first time you need that separation, which in practice happens fast. `expires_at` complements the continuous `last_decay_at` erosion with hard drop-dead timestamps for compliance retention and ephemeral credentials. Migration mirrors the v0.11.1 pattern: NULL-default ALTER, index, no backfill, idempotent.
- **Universal content-type routing consumers (v0.12.3).** Closes the write- and consumer-side loop opened by v0.11.1. That patch added `content_type` to the model and table but never wired a consumer — worse, `create_fact` silently dropped the field on write. v0.12.3 fixes both: `create_fact` persists all three v0.11.1/v0.12.2 new fields, `query_facts` hydrates them on read, and `query_facts` accepts a `content_type` filter. `get_injection_context` is now routing-aware: rules always inject at PostCompact / UserPromptSubmit / SessionStart under a dedicated "## Rules (always active)" section; facts (or NULL) fill remaining slots; procedures are excluded from auto-injection entirely and reachable only via explicit `query_fact(content_type='procedure')`.
- **GitHub Copilot Chat adapter (v0.12.4, `install-copilot`).** Merges into `.vscode/mcp.json` per workspace. Copilot Chat uses top-level `"servers"` (not `"mcpServers"` like every other adapter world-model ships — silently registers nothing if wrong). Merge semantics: absent → write; existing → preserve other servers; existing `world-model` → skip unless `--force`; malformed / wrong-shape JSON → refuse and leave the file untouched.
- **`install-continue --global` config-merge path (v0.12.5).** Merges into `~/.continue/config.yaml`'s `mcpServers` LIST (Continue's schema — distinct from Hermes' mcp_servers-mapping and from Claude Code / Cursor / Copilot / Cline / Windsurf's mcpServers-mapping). `ruamel.yaml` round-trip preserves comments, blank lines, and key ordering.
- **Cline adapter (v0.12.6, `install-cline`).** Merges into `~/.cline/mcp.json`. Cline uses the `mcpServers` mapping shape — same as Cursor / Claude Code.
- **Windsurf adapter (v0.12.7, `install-windsurf`).** Merges into `~/.codeium/windsurf/mcp_config.json`. Same `mcpServers` mapping shape as Cline; only the default path differs.
- **Hermes lifecycle hooks (v0.12.9).** Layers the five optional hooks (`sync_turn`, `on_pre_compress`, `prefetch`, `on_session_end`, `on_memory_write`) on top of the v0.11.0 MemoryProvider ABC. `on_pre_compress` returns a compact injection bundle that honors the v0.12.3 content-type routing — rules always inject, procedures never do. Best-effort contract: exceptions caught, safe default returned; a broken hook must never crash the Hermes loop.
- **MCP 2026-07-28 spec readiness scaffolding (v0.12.11).** Non-behavior-changing observability + public audit against the MCP 2026-07-28 specification (Release Candidate). Ships `world_model_server.spec_readiness.READINESS_STATE` (machine-readable audit matrix locked to five row states), `extract_meta` / `log_meta_if_present` observability helpers wired into `server.py:call_tool`, and `docs/MCP_2026_SPEC_READINESS.md` public audit doc. Backward compatibility with the 2025-03-26 spec preserved unconditionally.
- **Adapter matrix now covers ten runtimes:** Claude Code, Cursor, Codex, Hermes, Continue (per-project + `--global`), OpenClaw, pi, GitHub Copilot Chat, Cline, Windsurf.
- **Deferred per roadmap-gated conditionals:** OpenClaw TypeScript plugin (v0.12.8, no adoption signal within 5 days of v0.10) and Antigravity CLI adapter (v0.12.10, SDK still lacks `TransformCompactionHook` through v1.0.16).

## What's new in v0.11.0

- **`auto` contradiction-resolution strategy rewrite (v0.11.0 A).** Folds in `confirmer` awareness, per-evidence-type decay, distinct-source-tool counting, and tie-detection. Lifts the v0.8.1 contradiction-resolution benchmark's `auto` score from **77.1% → 100.0%** on the same 105-pair × 19-category dataset. Overall benchmark accuracy across four canonical strategies + the decayed strategy rises from 78.2% to 83.7%. Non-auto strategies unchanged. The `keep_higher_confidence_decayed` strategy is promoted from benchmark-only to a first-class option in `pick_winner`. Full detail in `benchmarks/contradictions-200/`.

- **Hermes native `MemoryProvider` plugin + `install-hermes-provider` CLI (v0.11.0 B).** Python plugin implementing Hermes' `agent/memory_provider.py` ABC (`initialize`, `get_tool_schemas`, `handle_tool_call`, `get_config_schema`, `save_config`). Intercepts writes at Hermes' routing layer rather than only surfacing tools — the architectural distinction the v0.10 MCP adapter could not close. Motivated by the [Hermes #47349 exchange](https://github.com/NousResearch/hermes-agent/issues/47349) where @TechFlipsi surfaced that adding another MCP-registered store doesn't fix "the agent still defaults to writing `MEMORY.md`" — only a MemoryProvider does. Ships as `world_model_server/hermes_memory_provider/` in the wheel; `install-hermes-provider` copies the plugin to `<hermes_home>/plugins/memory/world-model/`. Seven surfaced tools (`query_fact`, `get_constraints`, `get_injection_context`, `record_event`, `record_correction`, `find_contradictions`, `resolve_contradiction`) — trimmed from the 27 exposed via MCP to keep Hermes' tool namespace focused. Optional Hermes lifecycle hooks (`sync_turn`, `on_pre_compress`, `prefetch`, `on_session_end`) tracked as v0.12.

- **Content-type routing schema field (v0.11.1).** Nullable `content_type: Optional[Literal["rule", "fact", "procedure"]]` on the `Fact` model and the facts table. Additive-only migration; existing rows keep NULL and continue to work. Distinct from `evidence_type` (which describes where the fact came from) — `content_type` describes what shape of content the fact carries, so a MemoryProvider can route writes intelligently (rules → always-inject, facts → search-on-demand, procedures → skills store) instead of dumping everything into one destination. Sourced from the Hermes #47349 architectural framing. Consumers (query filters, MemoryProvider write routing) are v0.11.x follow-ups; this ships the schema, tests, and migration only.

- **Dogfooding case study (v0.11.2) — surfaced a real shipped bug that was fixed in the same release.** Publishes what the fact graph actually captured about the world-model-mcp codebase in `.claude/world-model/`: **3 learned constraints** with real violation counts (including `check-twine-before-tag` and `tag-before-upload`, both derived from real release-mechanics incidents matching the v0.9.1 telemetry-token miss and the v0.10.1 tagging lesson), **1 bug_fix reflection** citing a real bug in `world_model_server/knowledge_graph.py:120-135`, 608 facts (607 from the seeder + the one bug_fix), 600 entities. **Honest about what was NOT captured** (empty `events` / `decisions` / `sessions` tables). Pushing on that anomaly hard enough surfaced the actual root cause: `setup_command` wrote unquoted `$CLAUDE_PROJECT_DIR` in every generated hook command, so any user whose project path contains a space (macOS defaults like `~/Documents/`, corporate paths, or the maintainer's own `claude context graph/world-model-mcp`) has been silently failing every hook invocation since v0.7.3 shipped hooks. **The fix ships in this release** — two-line shell-quoting change in `setup_command` + regression test. This is the exact kind of latent bug that dogfooding is supposed to catch, and it did. Reproducibility contract: `python scripts/dogfooding_snapshot.py --db-path .claude/world-model` regenerates the committed JSON byte-for-byte, and drift-protection tests fail if the writeup and the snapshot diverge. See [`case-studies/v011-dogfooding/`](./case-studies/v011-dogfooding/).

- **What is unchanged.** All v0.10.x code paths: the 27 MCP tools reported by adapters (no new server-side tools in v0.11), the SWE-bench Verified benchmark and its multi-seed appendix, the seven-runtime adapter coverage (Claude Code + Cursor + Codex + pi + OpenClaw + Hermes Agent + Continue), the Zenodo preprint (paper unchanged since v0.9.2; no new Zenodo version). v0.11 is a depth release — better contradiction-resolution intelligence, a second Hermes integration path, a schema axis for future routing work, and honest evidence for the dogfooding claim. Test count grew from 417 (v0.10) to 457 (+21 for the Hermes MemoryProvider plugin, +10 for the content-type schema, +9 for the case-study drift protection).

## What's new in v0.10.0

- **Three new adapters in one release: OpenClaw, Hermes Agent, Continue.** All three verified end-to-end against real installations of the target runtime:
  - **OpenClaw** — `install-openclaw` merges into `~/.openclaw/openclaw.json`. Verified against OpenClaw `2026.6.11 (e085fa1)` on macOS: `openclaw mcp probe world-model` reports 27 tools discovered. Root cause of the first-attempt "MCP error -32000: Connection closed" surfaced and fixed during E2E: OpenClaw's process spawn does not inherit shell PATH, so `--command python3` fails while an absolute path works. The CLI now defaults `command` to `sys.executable` (absolute) and rejects relative `--python` overrides as a hard error. Documented as an install-time gotcha in the adapter README.
  - **Hermes Agent** — `install-hermes` merges into `~/.hermes/config.yaml` under `mcp_servers.world-model`. Uses `ruamel.yaml` round-trip mode to preserve every comment and blank line in Hermes' heavily-commented 1327-line reference config. A regression test (`test_f2_install_hermes_preserves_comments_and_blank_lines`) locks this down after a pre-E2E `pyyaml.safe_dump` implementation stripped ~1170 lines of documentation. Verified against Hermes Agent `v0.17.0 (2026.6.19)` on macOS: `hermes mcp test world-model` reports 27 tools discovered. Hermes' built-in memory (character-capped, no auto-decay per Hermes docs) is complemented additively by world-model-mcp's provenance + decay schema. Requires the `[hermes]` optional extra (`pip install "world-model-mcp[hermes]"`) so `ruamel.yaml` is available.
  - **Continue** — `install-continue` writes a standalone `<project>/.continue/mcpServers/world-model.yaml` following Continue's documented per-server-file pattern. No config merge needed. CLI-side E2E: the exact stdio spawn Continue would perform returns 27 tools via a live `tools/list` roundtrip. Last-mile "does Continue's LLM see them in agent mode" verification requires a live VS Code / JetBrains session. Reprioritized after the SpaceX/Cursor acquisition to serve teams standardizing on OSS-neutral coding-agent workflows.

- **Absolute-path posture across all v0.10 adapters.** OpenClaw's PATH-spawn issue was caught first, but the same absolute-path default applies to Hermes and Continue as a precaution. Every new install command defaults `command` to `sys.executable` and rejects relative `--python` overrides. Users who hand-edit config files are directed to `$(which python3)` in both READMEs.

- **Cross-runtime shared memory.** All v0.10 adapters (and every prior adapter) default `WORLD_MODEL_DB_PATH` to `.claude/world-model` — a relative path resolved against the client's working directory. This means a project that runs in multiple clients (e.g., Claude Code + Continue + OpenClaw) shares one SQLite fact graph across all of them. For user-wide shared memory regardless of CWD, override with an absolute `--db-path`. The differentiator against [ClawMem](https://github.com/yoloshii/ClawMem) (which does cross-runtime memory with a plain key-value SQLite vault) is depth: per-fact provenance, per-evidence-type decay half-lives, PreToolUse defer enforcement.

- **What is unchanged.** All v0.9.2 code paths: the 26 base MCP tools (v0.10 adds no new server-side tools; the "27 tools" count reported by adapters includes `resolve_contradiction` which shipped in v0.8.0), the SWE-bench Verified benchmark, the multi-seed replication appendix, the wedge claims. v0.10 is an adapter-surface release, not a schema-or-benchmark release. Test count grew from 375 (v0.9.2) to 417 with the three new adapter test suites; every baseline test still passes.

- **Test breakdown.** 375 baseline + 14 OpenClaw + 16 Hermes + 12 Continue = 417 tests. Every adapter's test suite includes: bundled-file validity, dry-run behavior, first-install writes with absolute-path defaults, idempotence (refuse to overwrite without `--force`), `--force` overwrite, relative-`--python` rejection, parent-directory creation, malformed-config-file handling, and subparser-registration regression coverage.

## What's new in v0.9.2

- **Multi-seed replication appendix shipped per `SEED_PLAN.md`**. The v0.9 paper's primary limitation was single-trial design. v0.9.2 ships the multi-seed test that SEED_PLAN.md (locked 2026-06-25) committed to running. The result is published verbatim per the pre-registered acceptance criteria.

- **Honest update to the v0.9 headline**. On the 17-instance pre-registered subset, baseline pass rate swung +41 percentage points between seed 1 and seed 2 with no methodology change. Load-bearing replication is 0 of 7 instances. Mean paired delta across both seeds is +0.24 per instance with bootstrap 95 percent CI [0.00, 0.47]. The v0.9 +10.2 pts paired delta should be read as a single-trial upper bound; the replicated effect size is small, possibly nonzero.

- **What is unchanged**: all v0.9.1 code, the 26 MCP tools, the 19 CLI subcommands, the 375 tests, the wedge claims at the architectural level (lifecycle-hook capture, per-fact provenance, per-evidence-type decay, PreToolUse defer). Architectural claims do not depend on the empirical effect size and survive the multi-seed update.

- **Documentation diffs**: `benchmarks/repeat-mistake/RESULTS.md` adds a "Multi-seed replication appendix (v0.9.2 update)". `benchmarks/repeat-mistake/paper.md` adds Appendix A with the same content. `benchmarks/repeat-mistake/paper.pdf` is regenerated. `benchmarks/repeat-mistake/SEED_PLAN.md` adds a status update (the locked plan above is unchanged). Raw seed-2 artifacts (`baseline_progress_seed2.jsonl`, `treatment_progress_seed2_treatment.jsonl`, predictions, results, and the `multi_seed_summary_seed2.json` from `multi_seed_aggregate.py`) committed.

- **The methodology discipline held**. Pre-registration prevented goalpost-moving. The honest update is published per the locked SEED_PLAN.md acceptance criteria. This is what pre-registration is for.

## What's new in v0.9.0

- **Repeat-mistake benchmark on SWE-bench Verified** — the central wedge proof. 50 SWE-bench Verified tasks across django, sympy, matplotlib, scikit-learn, and sphinx, run as a paired baseline-vs-treatment comparison. Methodology was locked at [`benchmarks/repeat-mistake/DESIGN.md`](benchmarks/repeat-mistake/DESIGN.md) on 2026-06-17 (before the data existed) so the result cannot be accused of goalpost-moving.

- **Headline results** — Subset 1 (within-domain: django + sympy) baseline 15/20 = 75.0 percent, treatment 18/20 = 90.0 percent, delta +15.0 pts with 4 FAIL to PASS flips and 1 regression. Subset 2 (cross-domain: matplotlib + scikit-learn + sphinx) baseline 18/29 = 62.1 percent, treatment 20/29 = 69.0 percent, delta +6.9 pts with 2 flips and zero regressions. Combined paired result across 49 instances: 33/49 to 38/49, delta +10.2 pts.

- **Cross-domain transfer isolated cleanly** — the Subset 2 treatment arm loaded ONLY the 4 Subset 1 constraints (django and sympy directives), holding out the 11 Subset 2 constraints to test whether learning from one repo family generalizes to a different one. Two cross-domain flips with plausible mechanistic explanations grounded in the loaded constraints. Sphinx-9461 is the strongest case: a sympy classmethod constraint transferred to a sphinx classmethod-wrapper unwrapping bug.

- **Honest caveats embedded in RESULTS.md** — seven explicit limitations including single-trial design, constraint-failure overlap on Subset 1, the small cross-domain transfer rate, one dropped instance due to an upstream SWE-bench pip flag issue, and judge-model self-reference risk. Stated verbatim rather than hidden in an appendix.

- **Full reproducibility artifacts** — every progress JSONL, predictions JSON, results JSONL, classification JSONL, constraints JSON, and harness report JSON committed in [`benchmarks/repeat-mistake/`](benchmarks/repeat-mistake/). Locked judge prompts in `failure_classifier.py` and `learning_hook.py`. Total agent cost across both arms was approximately 90 USD on a Claude Code subscription.

## What's new in v0.8.1

- **Contradiction-resolution benchmark expansion** -- the v0.7.4 24-pair benchmark grew to 105 hand-curated pairs across 19 categories. Six new categories exercise the v0.8.0 schema specifically: `source_tool_corroboration`, `confirmer_overrides_pending`, `decay_advantage_session_vs_source`, `decay_advantage_stale_session`, `evidence_type_user_correction`, `settled_beats_higher_confidence`. Deterministic runner at [`benchmarks/contradictions-200/run.py`](benchmarks/contradictions-200/run.py); full per-strategy + per-category breakdown at [`benchmarks/contradictions-200/RESULTS.md`](benchmarks/contradictions-200/RESULTS.md).

- **Honest framing on the numbers**: the new dataset is harder than v0.7.4's 24-pair set because the new categories deliberately test schema awareness (confirmer, evidence_type, decay) rather than raw confidence ranking. Headline numbers: `keep_most_sources` 99.0%, `keep_higher_confidence` 81.0%, `auto` 77.1%, `keep_higher_confidence_decayed` 90.5% (on the 21 pairs where evidence_type is present), overall 78.2% across all strategies. The original 24-pair v0.7.4 93.5% number is preserved unchanged at `benchmarks/contradictions/` and is not invalidated; it tested a different (smaller, easier) corpus.

- **The wedge benchmark is v0.9**: "does the learning loop measurably reduce repeated coding-agent mistakes on a public task corpus?" The contradiction-resolution work in this release is internal schema-correctness validation. The empirical artifact that maps to the published essay framing — the learning loop is the durable layer — lands in v0.9 with a SWE-bench-style repeat-mistake benchmark.

## What's new in v0.8.0

- **Domain-aware confidence decay** -- new `world_model_server/decay.py` module with exponential half-life decay per `evidence_type`. Half-lives: source_code 365d, test 180d, session 14d, user_correction 730d, bug_fix 365d. Decay applies on read (no background task), so the next `query_fact` call returns the time-corrected confidence. Settled facts (`canonical` status, or any fact with `confirmer != NULL`) never auto-transition. Synthesized facts that decay below 0.2 confidence and corroborated facts that decay below 0.1 confidence auto-supersede on read, surfacing rot to the next compaction injection.

- **Per-item provenance fields on facts** -- three additive columns (`source_tool TEXT`, `confirmer TEXT`, `last_decay_at TIMESTAMP`), all NULL-defaulted, no backfill. `source_tool` records which tool wrote the fact (e.g. `claude_code`, `codex`, `cursor`, `pi`, `user`). `confirmer` records who confirmed it, distinct from the asserter; NULL means pending, non-NULL means settled. Both are exposed on the `Fact` model and propagated through `create_fact`. Honors the public commitment to Patdolitse (anthropics/claude-code#47023) and ferhimedamine (openai/codex#19195).

- **Slash command write operations** -- two new subcommands. `/world-model resolve <id>` marks a contradiction as resolved (manual; for confidence-weighted picking use the `resolve_contradiction` MCP tool). `/world-model forget <id>` sets `invalid_at` on a fact (preserved in the audit log; current-only reads skip it from then on). Both are idempotent and report cleanly on unknown ids. Help text now lists both alongside the read-only subcommands shipped in v0.7.6.

- **`resolve_contradiction` accepts `confirmer`** -- when a `confirmer` argument is provided to the MCP tool or its underlying `resolve` function, the winning fact gets its `confirmer` column stamped with that value. This is the spec primitive that distinguishes "the asserter says X" from "X is confirmed by Y" per the working group sketch.

- **Antigravity adapter held for the third consecutive release.** The 2026-06-13 re-verification found `OnCompactionHook` declared as `InspectHook` in the SDK with no `TransformCompactionHook` and no `additional_context` return field. The load-bearing memory-injection contract still does not exist in the SDK.

## What's new in v0.7.6

- **In-agent `/world-model` slash command** -- typed by the user inside the agent harness, surfaces the world model state without leaving the chat. Read-only in v0.7.6 (`status`, `contradictions`, `recent`, `help`); write operations (`resolve`, `forget`) land in v0.8. Works across Claude Code, Cursor, Codex, and pi by intercepting `UserPromptSubmit` in the existing `inject_helper`. Returns `additionalContext` in the strict camelCase shape Codex enforces (`deny_unknown_fields`), so the same wire-up serves all four harnesses without a per-harness branch.
- **`world-model status-watch` TUI widget** -- terminal pane that runs alongside the agent and refreshes every 5 seconds. Shows constraints (total, severity=error, severity=warning), unresolved contradictions, facts (canonical / synthesized / superseded), and last compaction time. Built on the `rich` library already in the dependency tree; falls back to a plain-text one-shot dump when `rich` is not installed.
- **Antigravity CLI adapter intentionally NOT shipped in this release** -- the re-verification on 2026-06-13 against `google-antigravity/antigravity-sdk-python` HEAD surfaced an architectural gap: `OnCompactionHook` is declared as an `InspectHook` (read-only, non-blocking) with no `additional_context` return field and no `TransformCompactionHook` subclass. The load-bearing memory-injection contract does not exist in the SDK today. v0.7.6 ships without Antigravity rather than against a contract that cannot do the work.

## What's new in v0.7.5

- **Codex CLI adapter** -- new `install-codex` CLI subcommand appends a `[mcp_servers.world_model]` block plus PreToolUse, PostToolUse, PostCompact, and SessionStart hooks to `~/.codex/config.toml`. The bundled snippet was verified against `openai/codex@main` at v0.138.0-alpha (server name uses underscore to dodge the tool-name hyphen-strip in `codex-rs/codex-mcp/src/mcp/mod.rs`; hook output sticks to camelCase with `deny_unknown_fields` compliance). Schema regression tests in `tests/test_v075_features.py` lock the contract down. See [adapters/codex/README.md](adapters/codex/README.md).
- **Dual-shape payload normalization in `hook_helper` and `inject_helper`** -- both helpers now accept either Claude Code's payload shape (`event`, `project_dir`) or Codex's (`hook_event_name`, `cwd`), so the same Python code drives all four adapters (Claude Code, Cursor, pi, Codex).
- **Antigravity CLI adapter intentionally NOT shipped this release** -- the Antigravity API surface is still settling (six 1.0.x releases in three weeks, the `url` field for HTTP MCP servers landed June 3, hook JSON event-name casing remains undocumented). Targeting June 25 for that adapter after the API stabilizes. Detailed reasoning in the v0.7.5 RELEASE_NOTES entry.

## What's new in v0.7.4

- **AGENTS.md / `.agents/skills/` constraint reader** -- world-model-mcp now reads declarative project conventions from `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and `.agents/skills/*.md` files and mixes them into PreToolUse enforcement alongside the SQLite-backed constraints. Supports structured fence blocks (```` ```constraint ```` and YAML frontmatter) and heuristic imperative-sentence extraction for prose-style AGENTS.md files. New MCP tool: `get_agents_md_constraints`. ([anthropics/claude-code#6235](https://github.com/anthropics/claude-code/issues/6235) has 4,000+ thumbs-up for AGENTS.md as the cross-agent format.)
- **Self-hosted Claude Managed Agents deployment guide** -- Anthropic's [official position](https://claude.com/blog/claude-managed-agents-updates): *"Memory is not yet supported in self-hosted sessions."* world-model-mcp fills that gap. New guide at [`docs/deployment/managed-agents-self-hosted.md`](docs/deployment/managed-agents-self-hosted.md), with a [Modal quickstart](examples/managed-agents-self-hosted/) you can deploy in under five minutes.
- **Reproducible contradiction-resolution benchmark** -- 24-pair dataset at [`benchmarks/contradictions/dataset.jsonl`](benchmarks/contradictions/dataset.jsonl), runner at [`benchmarks/contradictions/run.py`](benchmarks/contradictions/run.py), results at [`benchmarks/contradictions/RESULTS.md`](benchmarks/contradictions/RESULTS.md). Headline: 93.5% overall accuracy, 100% on `keep_higher_confidence` and `keep_most_sources`, with documented honest weaknesses on tie-handling and small confidence gaps. Re-run with `python benchmarks/contradictions/run.py`. CI workflow guards regressions.

## What's new in v0.7.3

- **`world-model demo`** -- one command to see every primitive working. Initializes the knowledge graph, seeds reproducible demo data via `scripts/demo_seed.py`, then exercises each primitive (PreToolUse enforcement, contradiction detection, PostCompact injection, audit log) with real outputs. New users can see the value without writing any code.
- **Opt-in telemetry** -- off by default, prompted once during `world-model setup`, inspectable with `world-model telemetry --status`, disabled with `world-model telemetry --disable`, fully erased with `world-model telemetry --forget-me` (v0.14+). No file paths, no code, no identifiers tied to a person. Sink migrated to https://etch.systems/api/telemetry/ingest in v0.14 — the previous private GitHub Issues sink shipped an empty PAT stub in every wheel so it silently no-op'd; v0.14 is the first release where opt-in telemetry actually works. See [Privacy and Security](#privacy-and-security) for the exact payload.
- **pi adapter** -- new `adapters/pi/` package. world-model-mcp now plugs into [earendil-works/pi](https://github.com/earendil-works/pi) via pi's extension API (`tool_call` -> PreToolUse, `context` -> auto-injection, `session_compact` -> audit log). Install with `world-model install-pi`.

## What v0.7.0 introduced (still active)

- **PostCompact / UserPromptSubmit auto-injection** -- when the agent's context is compacted, the hook automatically splices the top constraints and recent canonical facts back into the next turn. Configurable, fails open.
- **`defer` enforcement tier** -- PreToolUse now classifies recurring warning-level violations as `defer`, which pauses headless agents (with graceful fallback to `ask` on older clients) instead of either hard-denying or silently passing through.
- **Confidence-weighted contradiction resolution** -- the new `resolve_contradiction` tool picks a winner using `keep_higher_confidence`, `keep_most_recent`, `keep_most_sources`, or `auto`. The loser is marked superseded.
- **Compaction audit log** -- every PostCompact event writes a row with pre/post token counts and what was re-injected. Query with the `audit-compactions` CLI or export to JSONL.
- **Cursor adapter** -- harness-neutral hooks under `adapters/cursor/`. Same Python helpers, different manifest format.
- **Streamable HTTP transport (v0.7.2)** -- `WORLD_MODEL_TRANSPORT=http` so the same 25 MCP tools work behind an MCP tunnel for Claude Managed Agents with self-hosted sandboxes. See [docs/deployment/mcp-tunnel.md](docs/deployment/mcp-tunnel.md).

</details>

---

## Quick Start

### Option 1: Desktop Extension (one-click for Claude Desktop)

Download the latest `.mcpb` from [Releases](https://github.com/SaravananJaichandar/world-model-mcp/releases/latest) and drag it into Claude Desktop. Auto-installs hooks, MCP server config, and dependencies.

### Option 2: pip install (Claude Code CLI / IDE plugins)

```bash
# 1. Install the package
pip install world-model-mcp

# 2. Setup in your project (auto-seeds the knowledge graph from existing code)
cd /path/to/your/project
python -m world_model_server.cli setup

# 3. Restart Claude Code
# Done! The world model is pre-populated and active
```

You can also re-seed or seed manually at any time:

```bash
# Seed from existing codebase
world-model seed

# Re-seed with force (re-processes already seeded files)
world-model seed --force
```

### Option 3: HTTP transport for remote / MCP-tunnel deployment

For Claude Managed Agents with self-hosted sandboxes, or any deployment where
the MCP server lives behind a firewall and the agent reaches it from
Anthropic-side infrastructure, run world-model-mcp in HTTP mode.

```bash
pip install 'world-model-mcp[http]'

export WORLD_MODEL_TRANSPORT=http
export WORLD_MODEL_HTTP_PORT=8765
python -m world_model_server.server
```

Or use the bundled image:

```bash
docker compose up -d                    # Dockerfile.http + persistent volume
curl http://127.0.0.1:8765/healthz      # {"status":"ok","version":"0.7.2"}
```

Full walkthrough including Anthropic MCP tunnels setup:
[docs/deployment/mcp-tunnel.md](docs/deployment/mcp-tunnel.md).

Stdio remains the default transport for Claude Code, Cursor, and `.mcpb`
installs. Nothing changes for those flows.

### Option 4: Run the guided demo (no Claude Code required)

To see every primitive working with real outputs from a real SQLite database before committing to a full install:

```bash
pip install world-model-mcp
cd /tmp/wm-test && mkdir -p wm-test && cd wm-test
world-model demo
```

The demo initializes a knowledge graph, seeds reproducible data, and exercises PreToolUse enforcement, contradiction detection, the PostCompact injection bundle, and the compaction audit log -- with the actual JSON outputs. Re-runs are idempotent.

### Option 5: Run inside pi (experimental)

For users of [earendil-works/pi](https://github.com/earendil-works/pi):

```bash
pip install world-model-mcp           # the Python helpers
world-model install-pi                # writes adapters/world-model-pi/
pi install local:./adapters/world-model-pi
```

The pi adapter wires the same `hook_helper` and `inject_helper` you'd use from Claude Code into pi's `tool_call`, `context`, and `session_compact` events. See [adapters/pi/README.md](adapters/pi/README.md).

### Option 6: Run inside Codex CLI (experimental)

For users of OpenAI's [Codex CLI](https://github.com/openai/codex):

```bash
pip install world-model-mcp                # the Python helpers
python -m world_model_server.cli install-codex
# (appends [mcp_servers.world_model] + hook blocks to ~/.codex/config.toml)
# Restart codex; verify with: codex mcp list
```

`--dry-run` prints what would be appended without writing; `--force` re-appends even if the adapter marker is already present. The bundled snippet uses `world_model` (underscore) as the MCP server name to dodge Codex's silent hyphen-strip in its tool-name sanitizer. Hook output is camelCase with `deny_unknown_fields` compliance against Codex's strict Rust schema; the contract is locked down by tests in `tests/test_v075_features.py`. See [adapters/codex/README.md](adapters/codex/README.md).

### Option 7: Run inside OpenClaw (experimental, v0.10)

For users of [OpenClaw](https://github.com/openclaw/openclaw), the local-first personal AI assistant that routes across WhatsApp, Telegram, Slack, and Discord:

```bash
pip install world-model-mcp
python -m world_model_server.cli setup
python -m world_model_server.cli install-openclaw
# Verify: openclaw mcp probe world-model  (should report 27 tools)
```

`install-openclaw` merges an `mcp.servers.world-model` entry into `~/.openclaw/openclaw.json` while preserving all other keys in the config file. It defaults the `command` field to `sys.executable` (absolute path to the interpreter running the CLI) — necessary because OpenClaw's process spawn does not inherit shell PATH; a bare `python3` fails probe with `MCP error -32000: Connection closed`. Flags: `--force` (overwrite existing entry), `--dry-run` (print without writing), `--python <abs-path>` (override interpreter), `--db-path <path>` (override `WORLD_MODEL_DB_PATH`, default `.claude/world-model`). Relative `--python` values are rejected as a hard error.

Pure additive integration — OpenClaw ships no native memory layer, so all 27 world-model tools become available to OpenClaw agent turns without capability overlap. Verified end-to-end against OpenClaw `2026.6.11 (e085fa1)` on macOS on 2026-07-01. MCP-registration only in v0.10; a TypeScript plugin bundle for typed lifecycle hooks (`before_prompt_build`, `before_tool_call`, `before_compaction`, `session_start`, ...) is on the v0.10.x roadmap. See [adapters/openclaw/README.md](adapters/openclaw/README.md).

### Option 8: Run inside Hermes Agent (experimental, v0.10)

For users of NousResearch's [Hermes Agent](https://github.com/NousResearch/hermes-agent):

```bash
pip install "world-model-mcp[hermes]"          # the [hermes] extra pulls ruamel.yaml
python -m world_model_server.cli setup
python -m world_model_server.cli install-hermes
# From inside a Hermes session: /reload-mcp   (loads the new server without restarting)
```

`install-hermes` merges an `mcp_servers.world-model` block into `~/.hermes/config.yaml` while preserving all other keys — including every comment and blank line in Hermes' heavily-commented 1327-line reference config, via `ruamel.yaml` round-trip mode. Defaults the `command` field to `sys.executable` (absolute path). Flags: `--force`, `--dry-run`, `--python <abs-path>`, `--db-path <path>`. Relative `--python` values are rejected as a hard error.

Hermes ships its own bounded memory system (`MEMORY.md` + `USER.md`, character-capped, no auto-decay per Hermes docs). world-model-mcp adds the temporal fact graph with per-fact provenance, per-evidence-type decay, and confidence-weighted contradiction resolution on top — additive, not overlapping. The overlap with the exclusive `MemoryProvider` plugin slot (currently held by ClawMem for many users) is documented in [adapters/hermes/README.md](adapters/hermes/README.md). Verified end-to-end against Hermes v0.17.0 (2026.6.19) on macOS: `hermes mcp test world-model` reports 27 tools. MCP-registration is the v0.10 track; a native `MemoryProvider` plugin is on the v0.10+ roadmap and ships only if MCP-route adoption warrants.

### Option 9: Run inside Continue (experimental, v0.10)

For users of [Continue](https://github.com/continuedev/continue), the OSS coding-agent extension for VS Code and JetBrains (largest OSS coding-agent extension not tied to a platform vendor — reprioritized after the SpaceX/Cursor acquisition):

```bash
pip install world-model-mcp
python -m world_model_server.cli setup
python -m world_model_server.cli install-continue
# Reload the Continue extension. In agent mode, world-model tools appear under the "world-model" server.
```

`install-continue` writes a standalone `<project>/.continue/mcpServers/world-model.yaml` following Continue's per-server-file pattern. No config merge is needed because Continue's own docs use one YAML per MCP server in that directory. Defaults the `command` field to `sys.executable` (absolute path); rejects relative `--python` overrides. Flags: `--force`, `--dry-run`, `--project-dir <path>`, `--python <abs-path>`, `--db-path <path>`. Continue watches `.continue/mcpServers/` in newer builds, so auto-discovery should pick up the new server; if not, reload the extension. MCP tools are available only in Continue's agent mode. See [adapters/continue/README.md](adapters/continue/README.md).

### What Gets Installed

```
your-project/
├── .mcp.json                    # MCP server configuration
├── .claude/
│   ├── settings.json           # Hook configuration
│   ├── hooks/                  # Compiled TypeScript hooks
│   └── world-model/            # SQLite databases (~155 KB)
```

---

## Features

### 1. Hallucination Prevention

Before:
```typescript
// Claude invents an API that doesn't exist
const user = await User.findByEmail(email); // This method doesn't exist
```

After:
```typescript
// Claude checks the world model first
const user = await User.findOne({ email }); // Verified to exist
```

**Goal**: Reduce non-existent API references by validating against the knowledge graph

### 2. Learning from Corrections

**Session 1**: User corrects Claude
```typescript
// Claude writes:
console.log('debug info');

// User corrects to:
logger.debug('debug info');

// World model learns: "Use logger.debug() not console.log()"
```

**Session 2**: Claude uses the learned pattern
```typescript
// Claude automatically writes:
logger.debug('debug info'); // No correction needed
```

**Goal**: Learned patterns persist across sessions and prevent repeat violations

### 3. Regression Prevention

```typescript
// Week 1: Bug fixed (null check added)
if (user && user.email) { ... }

// Week 2: Refactoring
// World model warns: "This line preserves a critical bug fix"
// Claude preserves the null check

// Result: Bug not re-introduced
```

**Goal**: Detect potential regressions before code execution

---

## How It Works

### Architecture

```
┌──────────────────────────────────────────────────────────┐
│ Claude Code + Hooks                                      │
│ Captures: file edits, tool calls, user corrections       │
└──────────────────────────────────────────────────────────┘
                         |
                         v
┌──────────────────────────────────────────────────────────┐
│ MCP Server (Python)                                      │
│ - 22 MCP tools for querying/recording/predicting          │
│ - LLM-powered entity extraction (Claude Haiku)           │
│ - External linter integration (ESLint, Pylint, Ruff)     │
└──────────────────────────────────────────────────────────┘
                         |
                         v
┌──────────────────────────────────────────────────────────┐
│ Knowledge Graph (SQLite + FTS5)                          │
│ - entities.db: APIs, functions, classes                  │
│ - facts.db: Temporal assertions with evidence            │
│ - relationships.db: Entity dependency graph              │
│ - constraints.db: Learned rules from corrections         │
│ - sessions.db: Session history and outcomes              │
│ - events.db: Activity log with reasoning chains          │
└──────────────────────────────────────────────────────────┘
```

### Key Concepts

1. **Temporal Facts**: Every fact has `validAt` and `invalidAt` timestamps
   - "Function X existed from 2024-01-15 to 2024-03-20"
   - Query: "What was true on March 1st?"

2. **Evidence Chains**: Every assertion traces back to source
   - Fact -> Session -> Event -> Source Code Location

3. **Constraint Learning**: Pattern recognition from user corrections
   - Automatic rule type inference (linting, architecture, testing)
   - Severity detection (error, warning, info)
   - Example generation for future reference

4. **Dual Validation**: Combines two validation sources
   - World model constraints (learned from user)
   - External linters (ESLint, Pylint, Ruff)

---

## MCP Tools

Twenty-two MCP tools available to Claude Code:

### 1. `query_fact`
Check if APIs/functions exist before using them
```python
result = query_fact(
    query="Does User.findByEmail exist?",
    entity_type="function"
)
# Returns: {exists: bool, confidence: float, facts: [...]}
```

### 2. `record_event`
Capture development activity with reasoning chains
```python
record_event(
    event_type="file_edit",
    file_path="src/api/auth.ts",
    reasoning="Added JWT authentication middleware"
)
```

### 3. `validate_change`
Pre-execution validation against constraints and linters
```python
result = validate_change(
    file_path="src/api/auth.ts",
    proposed_content="..."
)
# Returns: {safe: bool, violations: [...], suggestions: [...]}
```

### 4. `get_constraints`
Retrieve project-specific rules for a file
```python
constraints = get_constraints(
    file_path="src/**/*.ts",
    constraint_types=["linting", "architecture"]
)
```

### 5. `record_correction`
Learn from user edits (HIGH PRIORITY)
```python
record_correction(
    claude_action={...},
    user_correction={...},
    reasoning="Use logger.debug instead of console.log"
)
```

### 6. `get_related_bugs`
Regression risk assessment
```python
result = get_related_bugs(
    file_path="src/api/auth.ts",
    change_description="refactoring authentication logic"
)
# Returns: {bugs: [...], risk_score: float, critical_regions: [...]}
```

### 7. `seed_project`
Scan the codebase and populate the knowledge graph with entities and relationships
```python
result = seed_project(
    project_dir=".",
    force=False
)
# Returns: {files_seeded: int, entities_created: int, relationships_created: int}
```

### 8. `ingest_pr_reviews`
Pull GitHub PR review comments and convert team feedback into constraints
```python
result = ingest_pr_reviews(
    repo="owner/repo",  # Auto-detected from git remote if omitted
    count=10
)
# Returns: {prs_scanned: int, constraints_created: int, constraints_updated: int}
```

---

## Documentation

- **[QUICKSTART.md](./QUICKSTART.md)** - 5-minute setup guide
- **[CONTRIBUTING.md](./CONTRIBUTING.md)** - Contribution guidelines
- **[RELEASE_NOTES.md](./RELEASE_NOTES.md)** - Version history and features

---

## Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=world_model_server --cov-report=html
```

186 tests covering knowledge graph CRUD, FTS5 search, constraint management, bug tracking, auto-seeding, PR review ingestion, decision traces, outcome linkage, trajectory learning, prediction layer, memory health, contradiction detection, transcript pointers, project identity, and PreToolUse enforcement. See [tests/](./tests/) for details.

---

## Configuration

### Environment Variables

```bash
# Database location (default: ./.claude/world-model/)
export WORLD_MODEL_DB_PATH="/custom/path"

# Anthropic API key (optional - enables LLM extraction)
# IMPORTANT: Never commit this! Use .env file (see .env.example)
export ANTHROPIC_API_KEY="your-api-key-here"

# Model selection
export WORLD_MODEL_EXTRACTION_MODEL="claude-3-haiku-20240307"  # Fast
export WORLD_MODEL_REASONING_MODEL="claude-3-5-sonnet-20241022"  # Accurate

# Debug mode
export WORLD_MODEL_DEBUG=1
```

**Note**: Create a `.env` file in your project root (see `.env.example`) - it's automatically ignored by git.

### Customizing Hooks

Edit `.claude/settings.json` to customize which tools trigger world model hooks:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Edit|Write|Bash",
      "hooks": [...]
    }]
  }
}
```

---

## Language Support

**Currently Supported**:
- TypeScript / JavaScript
- Python

**Coming Soon**:
- Go, Rust, Java, C++

**Extensible Architecture**: Easy to add new language parsers (see [CONTRIBUTING.md](./CONTRIBUTING.md))

---

## Privacy and Security

- **Local-First**: All knowledge graph data stays on your machine.
- **Optional LLM**: Works without API key (uses regex patterns as fallback).
- **Encrypted Storage**: SQLite databases are local files (encrypt your disk for security).

### Telemetry (opt-in, off by default)

v0.7.3 added anonymous usage telemetry. It is:

- **Off by default.** You have to explicitly opt in.
- **Asked once** during `world-model setup`, with a clear `y/N` prompt.
- **Inspectable**: `world-model telemetry --status` shows the exact JSON payload that would be sent.
- **Disable any time** with `world-model telemetry --disable`, or globally with `WORLD_MODEL_TELEMETRY_DISABLE=1`.
- **Skipped in non-TTY environments** (CI, scripts) so it never blocks an automated setup.

**What we send (only if you opt in):**

| Field | Example | Why |
| --- | --- | --- |
| `event` | `setup_completed`, `demo_run`, `hook_fired` | Which lifecycle step ran |
| `version` | `0.7.3` | Which release you're on |
| `install_id` | random UUID at `~/.world-model/install_id` | Distinguish installs without identifying users |
| `ts` | unix timestamp | When the event fired |

**What we never send:** file paths, file contents, rule names, hostnames, IP addresses, API keys, decision-trace text, fact text, or anything else that could identify a person or leak business logic. The full payload schema lives in `world_model_server/telemetry.py`.

**Where it goes:** opt-in events are posted to a dedicated private GitHub repo (`SaravananJaichandar/world-model-telemetry`) as plain issues. There is no third-party analytics service, no cookie, no fingerprint. The PAT embedded in the client is scoped to that one repo with `Issues: write` only.

### API Key Usage (only if you provide `ANTHROPIC_API_KEY`)

- Entity extraction from code changes
- Constraint inference from corrections
- Never sends: Credentials, secrets, PII

### Security Best Practices

- Never commit `.env` files
- Use `.env.example` as template
- Store API keys in environment variables or `.env` files only
- The `.gitignore` automatically excludes sensitive files

---

## Roadmap

### v0.2.x
- [x] Auto-seeding: knowledge graph populates from existing codebase on setup
- [x] PR Review Intelligence: ingest GitHub review comments as constraints
- [x] Relationship tracking: import and dependency graph between entities
- [x] Multi-language support: Python, TypeScript/JavaScript, Solidity, Go, Rust
- [x] CLI query command for knowledge graph lookups
- [x] 40 tests, 8 MCP tools

### v0.3.0
- [x] Module-level matching: query by module name finds the file and its contents
- [x] Incremental re-seeding: only re-process files changed since last seed
- [x] Fuzzy entity matching: approximate name search for typos and abbreviations
- [x] Query caching: in-memory cache with TTL for repeated lookups
- [x] Java support: complete multi-language coverage
- [x] MCP server pipeline validation on real projects

### v0.4.0
- [x] Outcome linkage: test failures linked to code changes with facts
- [x] Trajectory learning: co-edit patterns tracked across sessions
- [x] Decision trace capture: structured log of agent proposals and human corrections
- [x] Cross-project entity search with project registry
- [x] 5 new MCP tools (13 total), 104 tests

### v0.5.0
- [x] Regression prediction, "what if" simulation, test failure prediction
- [x] Multi-project knowledge transfer, memory health, fact TTL/decay
- [x] get_context_for_action pre-edit bundle, constraint violation tracking, find_contradictions
- [x] 20 MCP tools, 151 tests

### v0.6.0 — Enforcement, Provenance, Identity
- [x] PreToolUse constraint enforcement hook: deny hard violations at the edit boundary
- [x] Indexed transcript pointers: hydrate any fact back to source conversation
- [x] Project identity decoupling: stable UUID across directory renames
- [x] Content-hash deduplication for facts and constraints
- [x] Auto-generate CLAUDE.md from the knowledge graph
- [x] BetaAbstractMemoryTool subclass for Anthropic SDK integration
- [x] Desktop Extension (.mcpb) packaging for Claude Desktop
- [x] 22 MCP tools, 13 CLI subcommands, 186 tests

### v0.7.0 — Auto-injection, defer tier, contradiction resolution, harness adapters
- [x] PostCompact and UserPromptSubmit auto-injection: re-emit top constraints and recent facts after context loss
- [x] `defer` enforcement tier in PreToolUse: pause headless agents on recurring warning-level violations, with graceful fallback to `ask`
- [x] Confidence-weighted contradiction resolution: pick a winner using confidence, recency, or source count, with an `auto` strategy
- [x] Compaction audit log: query and export what was remembered across each compaction boundary
- [x] Cursor adapter package
- [x] 25 MCP tools, 14 CLI subcommands, 220 tests

### v0.7.2 — Streamable HTTP transport
- [x] HTTP transport mode for remote / MCP-tunnel deployment
- [x] /healthz endpoint, Dockerfile.http, docker-compose.yml
- [x] docs/deployment/mcp-tunnel.md walkthrough for Claude Managed Agents
- [x] 236 tests

### v0.7.3 — Onboarding, telemetry, pi adapter
- [x] `world-model demo` guided tour for first-time users
- [x] Opt-in anonymous telemetry, off by default, inspectable
- [x] pi-package adapter (`adapters/pi/`, `install-pi` CLI)
- [x] 17 CLI subcommands, 256 tests

### v0.7.4 (Current) — Interop, deployment, benchmark
- [x] AGENTS.md / `.agents/skills/` constraint reader (new MCP tool: `get_agents_md_constraints`)
- [x] Self-hosted Claude Managed Agents deployment guide + Modal quickstart
- [x] Reproducible contradiction-resolution benchmark (24-pair dataset, CI workflow, RESULTS.md)
- [x] 26 MCP tools, 17 CLI subcommands, 283 tests

### v0.7.5
- [x] Codex CLI adapter (`install-codex`, shipped 2026-06-05)

### v0.7.6
- [x] In-agent `/world-model` slash command (read-only: status, contradictions, recent, help)
- [x] `world-model status-watch` TUI status widget

### v0.8.0
- [x] Decay + provenance schema: `source_tool`, `confirmer`, `last_decay_at` columns on facts. Per-evidence-type TTL with domain-aware half-lives (source_code 365d, test 180d, session 14d, user_correction 730d, bug_fix 365d).
- [x] Slash command write operations (`/world-model resolve <id>`, `/world-model forget <id>`).
- [x] `resolve_contradiction` accepts `confirmer` to stamp the winning fact as settled.

### v0.8.1
- [x] Expanded contradiction-resolution benchmark: 24 → 105 pairs across 19 categories, including 6 new categories that test the v0.8.0 schema (decay, provenance, confirmer).
- [x] Honest per-strategy + per-category RESULTS.md with the v0.7.4 number preserved as baseline.

### v0.9 (Shipped 2026-06-24) — Repeat-mistake benchmark on SWE-bench Verified
- [x] **Pre-registered SWE-bench Verified benchmark**. The empirical test of the central wedge: does the learning loop measurably reduce repeated agent mistakes on a public task corpus? Methodology locked in [`benchmarks/repeat-mistake/DESIGN.md`](benchmarks/repeat-mistake/DESIGN.md) on 2026-06-17, a week before the benchmark ran. Pre-registered hypothesis, interpretation thresholds, judge prompts, and SWE-bench Pro 7-category failure taxonomy. No goalpost-moving.
- [x] **Result: +10.2 pts combined paired delta across 49 SWE-bench Verified instances** (baseline 33/49 = 67.3% → treatment 38/49 = 77.6%). Within-domain delta +15.0 pts on django + sympy. Cross-domain delta +6.9 pts on matplotlib + scikit-learn + sphinx with zero observed regressions on 18 baseline passes. 6 FAIL-to-PASS flips, 1 regression. Full per-task tables, mechanistic analysis of the cross-domain flips, and seven explicit limitations in [`benchmarks/repeat-mistake/RESULTS.md`](benchmarks/repeat-mistake/RESULTS.md).
- [x] Pre-registered paper preprint with DOI: [10.5281/zenodo.20834508](https://doi.org/10.5281/zenodo.20834508). CC-BY 4.0. PDF and markdown source at [`benchmarks/repeat-mistake/paper.pdf`](benchmarks/repeat-mistake/paper.pdf) / [`paper.md`](benchmarks/repeat-mistake/paper.md).
- [x] Constraint extraction pipeline grounded in the SWE-bench Pro 7-category failure taxonomy (arXiv:2509.16941). Locked classifier and extractor prompts in `failure_classifier.py` and `learning_hook.py`.
- [x] All raw artifacts committed (per-task progress, predictions, scores, classifications, constraints, harness reports) so the benchmark is reproducible from a fresh checkout.
- [x] v0.9.1 patch: restored embedded telemetry token after a release-mechanics miss in v0.9.0 (no methodology change; benchmark numbers unchanged).

### v0.9.2 (Shipped 2026-06-30) — Multi-seed replication appendix
- [x] Pre-registered 17-instance multi-seed test per `benchmarks/repeat-mistake/SEED_PLAN.md` (locked 2026-06-25). Outcome: load-bearing replication 0 of 7; mean paired delta across two seeds is +0.24 per instance, bootstrap 95 percent CI [0.00, 0.47]. The v0.9 +10.2 pts headline was substantially attributable to an unlucky baseline draw. Honest update published per the pre-registered acceptance criteria. Appendix in `RESULTS.md` and `paper.md`. Zenodo record updated to version 2.

### v0.10 (Shipped 2026-07-01) — Three new adapters
- [x] **OpenClaw adapter (MCP registration) + `install-openclaw` CLI**. Registers world-model-mcp as an MCP server inside OpenClaw via `python -m world_model_server.cli install-openclaw`. Pure additive since OpenClaw ships no native memory layer. Verified end-to-end against OpenClaw `2026.6.11 (e085fa1)` on macOS on 2026-07-01: `openclaw mcp probe world-model` reports 27 tools discovered. See [`adapters/openclaw/`](./adapters/openclaw/).
- [x] **Hermes Agent adapter (MCP registration) + `install-hermes` CLI**. Registers world-model-mcp as an external MCP server inside Hermes Agent. Uses `ruamel.yaml` round-trip mode to preserve every comment and blank line in the 1327-line reference `config.yaml`. Verified end-to-end against Hermes Agent `v0.17.0 (2026.6.19)` on macOS on 2026-07-01: `hermes mcp test world-model` reports 27 tools discovered. See [`adapters/hermes/`](./adapters/hermes/).
- [x] **Continue adapter (MCP registration) + `install-continue` CLI**. Registers world-model-mcp as an MCP tool source inside [Continue](https://github.com/continuedev/continue) (VS Code + JetBrains). CLI-side E2E verified: the exact stdio spawn Continue would perform returns 27 tools via a live `tools/list` roundtrip. See [`adapters/continue/`](./adapters/continue/).
- [x] v0.10.1: fixed a stale Zenodo DOI reference (concept vs. version DOI) across README badge, roadmap link, `paper.md`, and `paper.pdf`. No code changes.

### v0.11 (Shipped 2026-07-02) — Depth after breadth

Depth release. v0.10 expanded surface area to seven runtimes; v0.11 solves real problems for the users we now have. Two signals shaped it: [Hermes #47349 (2026-07-01)](https://github.com/NousResearch/hermes-agent/issues/47349) surfaced the write-side routing gap (MCP surfaces tools but the agent still chooses the destination); and the `auto` strategy on the v0.8.1 contradiction-resolution benchmark still scored 77.1% because it did not fully consume the `confirmer` + decay-awareness fields shipped in v0.8.0.

- [x] **v0.11.0 A: `auto` strategy rewrite for `resolve_contradiction`.** Folds in `confirmer` awareness, per-evidence-type decay, distinct-source-tool counting, and tie-detection. Lifts the v0.8.1 contradiction-resolution benchmark's `auto` score from **77.1% to 100.0%** on the same 105-pair × 19-category dataset. Overall benchmark accuracy across four canonical strategies + the decayed strategy rises from 78.2% to 83.7%. See `benchmarks/contradictions-200/`.
- [x] **v0.11.0 B: Hermes native `MemoryProvider` plugin + `install-hermes-provider` CLI.** Python plugin implementing Hermes' `agent/memory_provider.py` ABC (`initialize`, `get_tool_schemas`, `handle_tool_call`, `get_config_schema`, `save_config`). Intercepts writes at Hermes' routing layer rather than only surfacing tools — the architectural distinction MCP alone cannot close. Priority was bumped from "conditional on MCP adoption" after [#47349](https://github.com/NousResearch/hermes-agent/issues/47349) demonstrated real user demand for write-side interception. Ships as `world_model_server/hermes_memory_provider/` in the wheel; `install-hermes-provider` copies the plugin into `<hermes_home>/plugins/memory/world-model/`. See [`adapters/hermes-memory-provider/`](./adapters/hermes-memory-provider/).
- [x] **v0.11.1: Content-type routing schema field.** Nullable `content_type` on the Fact model and the facts table, distinguishing `rule` (always-inject), `fact` (search-on-demand), and `procedure` (multi-step workflow). Additive-only migration; existing rows keep NULL and continue to work. Enables the v0.11.0 B MemoryProvider (and future providers) to route writes intelligently instead of dumping everything into one store. Sourced from Hermes #47349 architectural framing.
- [x] **v0.11.2: Dogfooding case study.** Publishes what the fact graph actually captured about the world-model-mcp codebase in `.claude/world-model/`: 3 learned constraints with real violation counts (including two release-mechanics rules that map directly to the v0.9.1 telemetry-token miss and the v0.10.1 tagging lesson), 1 bug_fix reflection, 608 facts, 600 entities. Honest about what was NOT captured (empty events / decisions / sessions tables). Reproducibility contract: `python scripts/dogfooding_snapshot.py` regenerates the committed JSON byte-for-byte. See [`case-studies/v011-dogfooding/`](./case-studies/v011-dogfooding/).

### v0.12 (Shipped 2026-07-06 / 2026-07-07) — Breadth + depth + adversarial verification

Nine substantive changes in the v0.12.0 umbrella release plus the v0.12.12 adversarial-verification follow-up. Two roadmap items (v0.12.8 OpenClaw TS plugin, v0.12.10 Antigravity CLI adapter) deferred per their roadmap-gated conditionals.

- [x] **v0.12.1: `world-model doctor` command.** Eight diagnostic checks, `--json`, `--fix`. Sourced directly from the v0.11.2 dogfooding trace.
- [x] **v0.12.2: `influence_state` + `expires_at` schema additions.** Storage-vs-planning-influence separation + hard drop-dead expiry, both additive nullable fields.
- [x] **v0.12.3: universal content-type routing consumers.** Closes the write- and consumer-side loop opened by v0.11.1. `create_fact` persists `content_type`; `query_facts` accepts a `content_type` filter; `get_injection_context` splits rules / facts / procedures into three routed pools.
- [x] **v0.12.4: GitHub Copilot Chat adapter (`install-copilot`).** Merges into `.vscode/mcp.json` with careful handling of the `"servers"` vs `"mcpServers"` divergence unique to Copilot Chat.
- [x] **v0.12.5: `install-continue --global` config-merge path.** ruamel.yaml round-trip preserves comments in `~/.continue/config.yaml`.
- [x] **v0.12.6: Cline adapter (`install-cline`).** Merges into `~/.cline/mcp.json`.
- [x] **v0.12.7: Windsurf adapter (`install-windsurf`).** Merges into `~/.codeium/windsurf/mcp_config.json`.
- [x] **v0.12.9: Hermes lifecycle hooks.** Five optional hooks (`sync_turn`, `on_pre_compress`, `prefetch`, `on_session_end`, `on_memory_write`) on top of the v0.11.0 MemoryProvider ABC.
- [x] **v0.12.11: MCP 2026-07-28 spec readiness scaffolding.** Non-behavior-changing observability + public audit; five-row `READINESS_STATE` matrix locked and tested.
- [x] **v0.12.12: Coach-Player adversarial verification.** `verify_retrieval` MCP tool + isolated Coach implementation + 12-pair hand-labeled benchmark. Pattern ported from the maintainer's earlier `y=c` project.
- [ ] **v0.12.8: OpenClaw TypeScript plugin bundle** — DEFERRED. Roadmap-gated on adoption signal; no explicit user ask within five days of v0.10.
- [ ] **v0.12.10: Antigravity CLI adapter** — DEFERRED. SDK still lacks `TransformCompactionHook` through v1.0.16.

### v0.13+ (Backlog)

**Near-term:**

- [ ] **Copilot CLI Windows shim in doctor** (v0.12.13 candidate). Extend `doctor --fix` to detect Copilot-target runtimes and rewrite unwrapped hook commands to `bash -c '...'` shape with `cwd`-from-stdin fallback. Sourced from [copilot-cli #4001](https://github.com/github/copilot-cli/issues/4001).
- [ ] **Expand Coach-Player benchmark to ≥30 pairs.** Once labeled set grows, the 95% hallucination-catch floor becomes enforceable (currently aspirational at N=12).
- [ ] **`answer_with_verification` end-to-end wrapper tool.** Combines `query_fact` → synthesize → `verify_retrieval` into a single MCP call for callers who want the whole pipeline in one shot.

**Medium-term — waits for signal:**

- [ ] **Citation polarity on retrieved facts** (`supporting` / `refuting` / `neutral`). Requires retrieval caller to know intent, which the schema layer doesn't control. Revisit when a specific integrator commits to instrumenting the annotation.
- [ ] **OpenClaw TypeScript plugin bundle** — moved from v0.12.8 to medium-term. Revisit when adoption signal warrants a TypeScript surface.
- [ ] **Antigravity CLI adapter.** Blocked pending `TransformCompactionHook` in the SDK. Unblocks whenever the SDK ships it.
- [ ] **Full 2026-07-28 MCP spec compliance** — HTTP header emission (`Mcp-Method`, `Mcp-Name`), `server/discover`, `InputRequiredResult`. v0.12.11 shipped the observability scaffolding; full compliance lands after the final spec ships on 2026-07-28.

**Long-term — v1.0 territory, expensive:**

- [ ] **Full-corpus multi-seed replication** of the SWE-bench Verified benchmark: all 49 paired instances at 3-5 seeds each. The v0.9.2 update covers a 17-instance subset only. Cost is ~60 hours agent time; the honest bounds from v0.9.2 are already published, so the marginal empirical gain is smaller than the operational cost. Save for a v1.0 push.
- [ ] **Head-to-head benchmarks** against other memory layers (mem0, Letta, Zep, piia-engram, ClawMem). Competitive-positioning value only; do it once, and only once the differentiators are stable enough that the head-to-head numbers are worth locking in.
- [ ] **Explicit failure-mode-similarity scoring** to predict when cross-domain transfer will succeed. Research-heavy; needs the multi-seed data as a precondition.
- [ ] **Larger task counts per repo; broader corpus coverage** beyond the 50-task subset.

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md) for:
- Development setup
- Coding standards
- Adding language support
- Writing tests
- Submitting PRs

**Areas where help is needed**:
- Language parsers (Go, Rust, Java, C++)
- Performance optimization
- Documentation improvements
- Real-world testing feedback

---

## Stats

**Project Size**:
- ~4,800 lines of code
- 13 Python modules
- 3 TypeScript hook implementations

**Storage Efficiency**:
- Empty database: ~155 KB
- Per entity: ~500 bytes
- Per fact: ~800 bytes

---

## License

[MIT License](./LICENSE) - Free for commercial and personal use

---

## Support

- **Issues**: [GitHub Issues](https://github.com/SaravananJaichandar/world-model-mcp/issues)
- **Discussions**: [GitHub Discussions](https://github.com/SaravananJaichandar/world-model-mcp/discussions)
