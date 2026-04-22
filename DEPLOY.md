# BrickBlade — Mac mini deployment

One-time setup on the always-on Mac mini, plus a smoke test that proves
the full stack works end-to-end.

## 1. Prerequisites

- macOS with Homebrew installed
- `uv` (`brew install uv`)
- Tailscale installed and logged in on both the mini and the iPhone
- API credentials for: Rebrickable, Brickset, BrickLink (four OAuth1 tokens)

## 2. Clone and install

```bash
git clone https://github.com/jsherman999/BrickBlade.git ~/Code/BrickBlade
cd ~/Code/BrickBlade
uv sync
```

## 3. Configure

```bash
cp .env.example .env
# edit .env and fill in:
#   REBRICKABLE_KEY, BRICKSET_KEY,
#   BL_CONSUMER_KEY, BL_CONSUMER_SECRET, BL_TOKEN, BL_TOKEN_SECRET,
#   BRICKBLADE_BEARER_TOKEN   (a long random string; the iPhone stores this
#                              in Keychain and sends it as Bearer <token>)
```

Register the mini's current public IP with BrickLink's API-key allowlist —
their API rejects requests from un-allowlisted source IPs. If you ever
change ISPs or the mini moves behind a different NAT, update the allowlist.

## 4. Initialise database and catalog

```bash
uv run brickblade init-db
uv run brickblade import-catalog          # ~30–60s, ~200MB of CSVs
uv run brickblade health                  # verifies all three credential sets
```

## 5. Install launchd agents

```bash
launchd/install.sh
```

That script renders the three plist templates with this repo's absolute
path, loads them via `launchctl bootstrap`, and creates `var/logs/`.

Agents installed:

| Label                            | When                 | What                                  |
| -------------------------------- | -------------------- | ------------------------------------- |
| `com.brickblade.api`             | `KeepAlive=true`     | uvicorn on `0.0.0.0:8765`             |
| `com.brickblade.refresh-catalog` | Sundays 03:00 local  | `brickblade import-catalog`           |
| `com.brickblade.refresh-prices`  | Nightly 04:00 local  | `brickblade refresh-prices`           |

Manual trigger (useful for testing):

```bash
launchctl kickstart -k gui/$(id -u)/com.brickblade.refresh-prices
tail -f var/logs/*.stdout
```

## 6. Reach the mini from the iPhone (Tailscale)

With Tailscale up on both devices the mini is reachable at
`http://<mac-mini-hostname>.<tailnet>.ts.net:8765`. That hostname is
what the iPhone client will use for the `API_BASE_URL` once it exists.

For local-only testing from the same machine:

```bash
curl -s http://localhost:8765/api/health | jq
```

## 7. Smoke test (end-to-end)

```bash
TOKEN="$(grep ^BRICKBLADE_BEARER_TOKEN .env | cut -d= -f2)"

# Add the Titanic to inventory
curl -s -X POST http://localhost:8765/api/inventory \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"set_num":"10294-1","quantity":1,"condition":"sealed"}' | jq

# Look it up (hits Brickset + BrickLink on cache miss, serves cache on hit)
curl -s -X POST http://localhost:8765/api/lookup \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"set_num":"10294-1"}' | jq

# Or from the CLI:
uv run brickblade lookup 10294
uv run brickblade value
```

A successful lookup returns a `LookupResult` with populated `metadata`,
one `brickset` sealed price, and one `bricklink` used-sold price. The same
result served a second time within `BRICKBLADE_PRICE_TTL_HOURS` is
served from the local cache (no upstream hit).

## 8. Iterating

After editing code, `launchctl kickstart -k gui/$(id -u)/com.brickblade.api`
picks up the change (uvicorn restarts via `KeepAlive`). For the scheduled
jobs, re-running `launchd/install.sh` is idempotent — it bootstraps any
updated plist cleanly.

## 9. Backups

SQLite lives at `var/brickblade.db` (path configurable via
`BRICKBLADE_DB_URL`). Time Machine covers it; if you want something more
explicit, `sqlite3 var/brickblade.db ".backup /path/to/snapshot.db"` is
the standard single-file backup. The append-only `prices` table is the
slowly-growing piece worth preserving.
