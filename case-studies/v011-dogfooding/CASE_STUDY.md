# v0.11.2 dogfooding case study — what world-model-mcp captured about itself

**Version:** v0.11.2
**Date:** 2026-07-02
**Reproducibility:** `python scripts/dogfooding_snapshot.py --db-path .claude/world-model` regenerates the numbers below from the committed database. See `snapshot.json` in this directory for the machine-readable version.

## Why publish this

The v0.10.0 release notes and the r/ClaudeAI post claimed the project "dogfoods its own memory layer." That is an assertion. This document is the receipt.

Everything below comes from `.claude/world-model/` — the same SQLite fact graph the project ships to users, running in-place inside the world-model-mcp repository as the maintainer works. The numbers are what the graph actually contains at HEAD of `main` on 2026-07-02, not a synthetic fixture.

## Headline: 608 facts, 600 entities, 3 learned constraints, 1 bug-fix reflection

| Table | Rows |
|---|---:|
| facts | 608 |
| entities | 600 |
| constraints | 3 |
| events | 0 |
| decisions | 0 |
| sessions | 0 |

Two tables tell the honest story here. Facts and entities are dense: the fact graph knows about 521 functions, 46 files, and 31 classes across the codebase, sourced from the automated seeder (`world_model_server.seeder`) on the initial setup. That is a graph, and it is real, but it is not "dogfooding" — it is the same output the seeder produces for any project that runs `world-model setup`.

The genuinely dogfooded rows live elsewhere. Three learned constraints. One `bug_fix` fact. That is what the memory layer captured about the maintainer's own work.

## The three learned constraints

These constraints were written to the graph after the maintainer corrected the model or after a release incident. Verbatim:

### 1. `no-console-log` (error, 5 violations)

- **Type:** linting
- **File pattern:** `*.ts`
- **Description:** Use `logger.debug()` not `console.log()` in TypeScript source. Production logs route through pino; console.log bypasses formatting and floods stdout.
- **Example:** `console.log` → `logger.debug`
- **Last violated:** 2026-05-21

Five violation events. Each time the maintainer wrote a `console.log` in a `.ts` file, the world-model layer flagged it. The rule ships as an `error`-severity constraint, meaning under the PreToolUse enforcement tier it would hard-block a write to a `.ts` file that reintroduced the pattern.

### 2. `check-twine-before-tag` (warning, 5 violations)

- **Type:** style
- **Description:** Run `python3 -m twine check dist/*` before tagging. Catches PyPI metadata errors before the tag is pushed; saves a retraction later.
- **Example:** `git tag -a v0.7.x` → `python3 -m twine check dist/* && git tag -a v0.7.x`
- **Last violated:** 2026-05-21

Directly reflects an early release-mechanics lesson: metadata errors caught after a tag has been pushed require either a `.postN` release or a full retraction. Five violations across the v0.7.x line.

### 3. `tag-before-upload` (warning, 2 violations)

- **Type:** style
- **Description:** Always run `git tag + git push --tags` before `twine upload`. PyPI is permanent; an untagged upload pins a wheel to no git ref.
- **Example:** `twine upload dist/*` → `git tag -a v0.7.x && git push --tags && twine upload dist/*`
- **Last violated:** 2026-05-19

Companion to the previous rule. Two violations shows the maintainer got caught by this less often, but the constraint is on the graph anyway — the next release-mechanics slip on this axis is one PreToolUse hook away from a hard block.

**What these three have in common:** each captures a concrete lesson learned in a specific coding session. The v0.7.x release-mechanics rules directly correspond to the incidents documented in `RELEASE_NOTES.md` for v0.9.1 (embedded telemetry token stub not reset before build) and v0.10.1 (Zenodo DOI mislabeled as concept when it was a version DOI). The graph learned from the maintainer's mistakes and now would block a naive future ship on the same axis.

## The one bug-fix reflection

One fact carries `evidence_type = "bug_fix"`. Verbatim:

> **Bug fix:** NULL content_hash backfill must run on every `initialize()` to cover post-migration inserts. Earlier code only backfilled during the first migration; rows inserted between migration and next restart carried NULL hashes.
>
> **evidence_path:** `world_model_server/knowledge_graph.py:120-135`

This is a real bug in the world-model-mcp codebase, caught during development, encoded as a fact so that a future session that touches the migration path can query for prior bug fixes there and see the context. The fix now lives in the shipped code (the `_run_migrations` backfill runs on every initialize, not just the first) — and the graph knows why.

## What is NOT in the graph — the honest limitations

The `events`, `decisions`, and `sessions` tables are all empty on the shipping repo's DB. That is a load-bearing finding, and the investigation into why it turned out to be more interesting than the initial hypothesis.

### The initial hypothesis (wrong)

The first read was that the maintainer's Claude Code sessions on this repo simply weren't firing the hooks — perhaps because Claude Code CLI in Max plan handled hooks differently from Desktop, or because the project-scope `.claude/settings.json` was somehow being ignored. Under that hypothesis the fix was adding a project-scope `.mcp.json` at the repo root registering world-model-mcp as an MCP server. That commit landed as a chore during v0.11 development.

Two hours after that commit, the empty tables were still empty. Something else was going on.

### The actual root cause (a real shipped bug)

Digging into Claude Code's session transcripts on this project (`~/.claude/projects/-Users-saravananjaichandaran-claude-context-graph-world-model-mcp/*.jsonl`) surfaced the truth. Every `SessionStart` hook invocation on this repo, going back to the first one on 2026-06-15, had been failing with the same silent error:

```
hookName: SessionStart:startup
exitCode: 1
stderr: Error: Cannot find module '/Users/saravananjaichandaran/claude'
Node.js v23.11.0
```

The reason: **the `.claude/settings.json` generated by `setup_command` (in world-model-mcp itself) used an unquoted `$CLAUDE_PROJECT_DIR` in every hook command.** When Claude Code expanded the env var at shell time and the value contained a space, the shell split it on whitespace. Node received:

- arg 1: `/Users/saravananjaichandaran/claude`  ← treated as the module argument, does not exist
- arg 2: `context`
- arg 3: `graph/world-model-mcp/.claude/hooks/world-model-session.js`
- arg 4: `start`

This project lives at `/Users/saravananjaichandaran/claude context graph/world-model-mcp/`. The two spaces in `claude context graph` were the entire failure. Every hook invocation, on every session, for every user whose project path contains a space (macOS defaults like `~/Documents/` or `~/Desktop/`, corporate paths like `~/Work Stuff/Client X/`, or intentional folder names like this one) has silently failed the same way since v0.7.3 shipped hooks.

**This was a bug in world-model-mcp's own shipped code.** It affected every user with a space-containing path since v0.7.3, silently, for months. The dogfooding process on the maintainer's own repo was what caught it — and only because the empty-tables anomaly in the v0.11.2 case study was pushed on hard enough to trace.

### The fix (shipped in v0.11.0)

Two-line change in `setup_command`: wrap `$CLAUDE_PROJECT_DIR` in double quotes in each command string. The bundled `.claude/settings.json` in this repo was regenerated. A regression test (`tests/test_v0110_setup_shell_quoting.py`) locks the fix down against future re-introduction. Details in the v0.11.0 RELEASE_NOTES.md entry.

### Why the empty tables in this snapshot are still empty

The fix ships in v0.11.0 (this release). The snapshot committed here was frozen at the diagnosis moment, before the fix reached the maintainer's own dogfooding sessions. Future v0.12+ snapshots on this repo — captured after the fix has been active for a while — should show populated `events` / `sessions` / `decisions` tables. That is the honest test of whether the fix works, and it is deliberately future-tense here rather than backfilled into the shipping snapshot.

### What this validates and what it does not

- **Validates the constraint-learning + PreToolUse enforcement path in practice.** Three constraints landed on the graph organically, one at `error` severity, each with real violation counts. The v0.11.0 A `auto` strategy rewrite (77.1% → 100.0% on the v0.8.1 benchmark) sits on top of this.
- **Validates that dogfooding surfaces real bugs the maintainer would otherwise miss.** The shell-quoting bug had been latent for months. It was the empty-tables finding in this case study that motivated the trace.
- **Does NOT validate the sessions / events / decisions surface at scale.** Those tables are empty on the shipping snapshot. A future case study with hooks firing correctly is the test of that write path.

## Reproducing this document

```bash
git checkout <the tag or commit this document was published against>
python scripts/dogfooding_snapshot.py --db-path .claude/world-model --out /tmp/snap.json
diff -u case-studies/v011-dogfooding/snapshot.json /tmp/snap.json
```

If the diff is empty, the numbers cited above match the shipped DB byte-for-byte. If it is not empty, someone has interacted with the fact graph since publication — check `git log` on the `.claude/world-model/` files.

## Relationship to prior claims

- The **v0.9 SWE-bench Verified benchmark** (paired baseline-vs-treatment, +10.2 pts single-trial → honest multi-seed bounds in v0.9.2) tested whether the memory layer measurably reduces repeated coding-agent mistakes on a public task corpus.
- The **v0.8.1 contradiction-resolution benchmark** (105 pairs × 19 categories) tested whether the `auto` strategy picks the right winner across scenarios the schema was designed to handle. v0.11.0 A brought that number to 100%.
- **This case study** does neither of those — it reports what the memory layer captured about the codebase that maintains it. It is descriptive, not comparative.

The three claims are complementary. The benchmark work is the empirical wedge. The case study is the receipt that the wedge fires on real work, not only on synthetic test fixtures.
