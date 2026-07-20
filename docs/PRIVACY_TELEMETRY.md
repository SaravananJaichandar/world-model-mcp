# Privacy — opt-in telemetry (v0.14+)

This document is the exact spec for `world-model telemetry`. It is the truthful description of what happens if you opt in; the code that implements it is 100 % OSS, MIT-licensed, and cross-linked below.

## Default state

**Off.** A fresh install of `world-model-mcp` sends zero telemetry events.

Consent state is stored at `~/.world-model/telemetry_consent` as the literal string `enabled` or `disabled`. If the file does not exist, telemetry is treated as disabled.

You can also set `WORLD_MODEL_TELEMETRY_DISABLE=1` as a global killswitch that overrides any opt-in.

## How to change it

```bash
world-model telemetry --enable         # opt in (starts sending on next CLI invocation)
world-model telemetry --disable        # stop sending (state retained for now)
world-model telemetry --forget-me      # DELETE server-side rows + wipe local state
world-model telemetry --status         # inspect current state + sample payload
```

## What's sent when you opt in

Two event types.

### 1. Heartbeat — once per 24 h per install

Fired opportunistically on any `world-model` CLI invocation, at most once every 24 hours per install. Payload:

```json
{
  "event": "heartbeat",
  "install_id": "0f5f9e8c-4a7b-4f8d-9b8e-6c4a3f7d8e21",
  "version": "0.14.0",
  "ts": 1721476800.0,
  "os_family": "darwin",
  "python_version": "3.11",
  "adapters": ["claude-code", "cursor", "codex"]
}
```

- `install_id` — random UUID4 generated on first run, stored at `~/.world-model/install_id`. Not tied to any account, hostname, IP, or user. Deleting the file creates a new one on the next run (a single user clearing state looks like a new install, which is correct from a privacy standpoint).
- `version` — the world-model-mcp version reporting.
- `ts` — Unix timestamp on the client (used for rate-limiting only; the server records its own `received_at` for storage).
- `os_family` — one of `darwin`, `linux`, `windows`, `freebsd`. Never anything more specific (kernel version, distro, hostname).
- `python_version` — `3.11` / `3.12`. Never the patch version.
- `adapters` — detected by presence of a small set of config files (`~/.claude/settings.json`, `~/.cursor/mcp.json`, `~/.codex/config.toml`, `~/.continue/config.yaml`, `~/.cline/mcp.json`, `.vscode/mcp.json` in cwd). We read filenames only, never file contents.

### 2. Action events

Currently: `setup_completed` after `world-model setup` finishes, `demo_run` after `world-model demo` finishes. Payload matches the heartbeat shape minus `adapters`, plus an optional `fields` dict of flat primitives specific to the action.

## What is NEVER sent

The client-side payload builder in [`world_model_server/telemetry.py`](../world_model_server/telemetry.py) whitelists exactly the keys above. The server-side validator in [`src/etch/telemetry_ingest.py`](https://github.com/SaravananJaichandar/etch/blob/main/src/etch/telemetry_ingest.py) rejects any unknown top-level key with a 400.

Explicitly not sent: file paths, file contents, prompt text, tool arguments, LLM responses, hostnames, IP addresses, usernames, email addresses, cwd, git config, environment variables (other than the whitelisted `os_family` / `python_version` derived from `sys`), any authentication tokens.

## Where it goes

Sink URL: `https://etch.systems/api/telemetry/ingest`

Operated by the same maintainer as world-model-mcp. Public HTTPS endpoint on a DigitalOcean droplet.

## Server-side privacy contract

1. **No IP retention.** A pure-ASGI middleware (`TelemetryIpStripper`) zeros `scope["client"]` and strips `X-Forwarded-For` / `X-Real-IP` / `Forwarded` / `CF-Connecting-IP` headers before any downstream handler or logger runs. The uvicorn access log records `client=None` for `/api/telemetry/*` requests.
2. **Rate limit by `install_id`.** 60 events per hour per install_id. IP is not used for rate limiting.
3. **Aggregate-only public stats.** `GET /api/telemetry/stats` returns totals + histograms across a 30-day window. It never returns per-install rows or timelines. Query-string filters on `install_id` are silently ignored — regression-tested.
4. **90-day auto-TTL.** A daily systemd timer runs `python -m etch.telemetry_ingest --purge`, deleting rows older than 90 days.
5. **Right to erasure.** `DELETE /api/telemetry/install/{install_id}` removes every server-side row for that install_id. The client's `--forget-me` command wraps this and also wipes local state, so revocation works offline.

## Previous versions did not send anything

Every wheel of world-model-mcp between v0.7.3 and v0.13.x shipped with an empty `_EMBEDDED_TOKEN` stub. The old sink was a private GitHub Issues repo protected by a GitHub PAT baked into the wheel at release time — but the release script (`scripts/embed_token.py`) was never wired into the CI/publish pipeline, so every published wheel had an empty token. That code path was a silent no-op for every user who opted in during that ~2-month interval. No telemetry data was collected, transferred, or stored by the previous sink.

v0.14 removes the empty stub and its build script entirely; the new sink needs no PAT.

## If you find a privacy issue

Please open an issue on [github.com/SaravananJaichandar/world-model-mcp](https://github.com/SaravananJaichandar/world-model-mcp) or email the maintainer at the address in `git log`.
