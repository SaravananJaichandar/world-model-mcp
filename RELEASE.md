# Release process for world-model-mcp

The release flow is the same as a normal Python release plus one extra
step that embeds the opt-in telemetry PAT into the wheel.

## TL;DR

```bash
# 1. (only if you don't already have it) create .env.release with the PAT
echo 'WORLD_MODEL_TELEMETRY_TOKEN=ghp_yourReleasePAT' > .env.release

# 2. embed the token into the wheel-staging file
python3 scripts/embed_token.py

# 3. build + verify + ship
python3 -m build --sdist --wheel
python3 -m twine check dist/world_model_mcp-X.Y.Z*
python3 -m twine upload dist/world_model_mcp-X.Y.Z-py3-none-any.whl dist/world_model_mcp-X.Y.Z.tar.gz

# 4. (separate but required) commit + tag + push, GitHub Release, MCP Registry republish
git add ...
git commit -m "vX.Y.Z: ..."
git tag -a vX.Y.Z -m "..."
git push origin main vX.Y.Z
gh release create vX.Y.Z dist/world-model-mcp-X.Y.Z.mcpb --title "..." --notes "..."
mcp-publisher publish
```

## Security model

The telemetry PAT lives in two places and **only two places**:

1. **Your local machine** at `.env.release` (gitignored)
2. **Inside the published wheel**, at `world_model_server/_embedded_token.py`

It must never end up in:

- Git (the file is gitignored AND there's a test that fails if the committed stub becomes non-empty)
- Chat / Slack / email / any logged channel
- GitHub Actions logs / CI logs (use repo secrets if you ever move this to CI)

If the token is exposed:

1. Revoke it immediately at GitHub Settings → Developer settings → Fine-grained tokens
2. Generate a new one, scoped only to `world-model-telemetry` repo with `Issues: read and write`
3. Drop the new value into `.env.release` locally
4. Ship a patch release (vX.Y.Z+1) so the embedded token in the wild is rotated

The token's blast radius is bounded by scope: it can only create issues in
the `SaravananJaichandar/world-model-telemetry` private repo. It cannot read
or write any other resource.

## The committed stub

`world_model_server/_embedded_token.py` is committed to git with
`EMBEDDED_TOKEN = ""`. This is intentional:

- The Hatch wheel build requires the file to exist (force-include cannot
  reference a missing file)
- Contributors who never run `embed_token.py` can still build wheels for
  development -- their wheels just have an empty token, so telemetry silently
  no-ops in their builds
- A test (`test_embedded_token_stub_is_empty`) fails if the committed value
  becomes non-empty, catching accidental leaks

`scripts/embed_token.py` overwrites the stub locally during a release build.
After uploading to PyPI, **reset the file** before committing anything:

```bash
git checkout -- world_model_server/_embedded_token.py
```

Or set `assume-unchanged`:

```bash
git update-index --assume-unchanged world_model_server/_embedded_token.py
```

(Reverse with `--no-assume-unchanged` when the stub itself needs to change.)

## Validating a release build

After running `embed_token.py`, sanity-check the wheel contains the right
token:

```bash
unzip -p dist/world_model_mcp-X.Y.Z-py3-none-any.whl \
  world_model_server/_embedded_token.py | head
```

You should see your real PAT (not `EMBEDDED_TOKEN = ""`). If it shows
empty, you forgot step 2.

## Why not GitHub Actions?

The current flow assumes you're shipping releases manually from your
workstation. If/when releases move to CI, replace `.env.release` with a
GitHub Actions secret (`WORLD_MODEL_TELEMETRY_TOKEN`) and have the release
workflow run `python3 scripts/embed_token.py` between checkout and build.
The script reads `WORLD_MODEL_TELEMETRY_TOKEN` from the env if present,
falling back to `.env.release` -- no code changes needed.
