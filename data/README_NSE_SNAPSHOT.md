# NSE pledge / DII snapshot — local fetch setup

NSE serves your home/office IP but blocks GitHub's datacenter runners, so
pledge % and promoter % cannot be fetched on Actions. `fetch_nse_local.py` runs on
**your laptop** (weekly), writes `data/nse_snapshot.json`, and pushes it. The
pipeline validates the file (schema + ≤ 14 days old) and uses it only to fill
blanks — it never overwrites live data and never raises.

## One-time setup

1. **Verify by hand first** (from the repo root, on your normal broadband — not
   a VPN):
   ```
   python fetch_nse_local.py --dry-run
   ```
   You should see non-zero counts (`pledge records: 400+`, `shareholding
   records: 50+`). Zeros mean NSE is not serving you right now — retry later.

2. **First real run:**
   ```
   python fetch_nse_local.py --push
   ```
   Writes the file and commits/pushes it.

3. **Confirm on Actions** — trigger the pipeline; the log should show
   ```
   ✅ NSE snapshot: N pledge + M shareholding records, Xd old (≤ 14d) — used as fallback for blanks
   📌 NSE snapshot applied to blanks: pledge N · DII N · ...
   ```

## Schedule it (Windows Task Scheduler)

Task Scheduler → **Create Task** (not "Basic Task" — you need the Settings tab):

| Tab | Setting | Value |
|---|---|---|
| General | Name | `NSE snapshot fetch` |
| General | Security | **Run only when user is logged on** |
| Triggers | New… | **Weekly**, Sunday, **20:00** |
| Actions | Program | `python` |
| Actions | Arguments | `fetch_nse_local.py --push` |
| Actions | Start in | *your repo folder* (must be the repo root) |
| Settings | ☑ **Run task as soon as possible after a scheduled start is missed** | **ON** ← the important one |
| Settings | ☑ If the task fails, restart every | 30 min, up to 3 times |
| Settings | ☐ Wake the computer to run this task | leave OFF unless you want it |

**"Run task as soon as possible after a scheduled start is missed"** is what
handles the laptop being off at 20:00 Sunday: the moment the machine next
boots and you log in, the task runs automatically. You don't have to remember.

## Manual trigger — any time

If you'd rather not wait for the catch-up, or want to refresh on demand:

- **Double-click `fetch_nse_now.bat`** in the repo root. It runs the identical
  command the scheduler runs, shows the result, and waits for a keypress.
- Or from a terminal: `python fetch_nse_local.py --push`

Manual and scheduled runs are the same code path — there is no difference in
what gets written or pushed.

## Why weekly (not fortnightly)

The pipeline ignores a snapshot older than **14 days**. A fortnightly fetch
lands exactly on that line, so a small delay tips it to "stale" for a run.
Weekly keeps the file ≤ 7 days old with a 7-day cushion: **one missed week
costs nothing**; only two consecutive misses cause pledge/DII to show `—`.
Pledge/DII change quarterly, so weekly loses no information.

## What NSE does and does not provide (verified 18-Sep-2026)

| Field | Source | Status |
|---|---|---|
| Pledge % | `corporate-pledgedata` → `percSharesPledged` | ✅ live |
| Promoter % | `corporate-share-holdings-master` → `pr_and_prgrp` | ✅ live |
| Public % | same → `public_val` | ✅ live |
| **DII % / FII %** | — | ❌ **NSE's free API no longer exposes these separately**; the old `corp-info` endpoint that had `diisTotal`/`fiisTotal` is retired (404). They stay `—`. They are NOT derived from the public bucket — that would be a fabricated number. |

## What the file is

One file, **overwritten** on every fetch — it never accumulates. Old
snapshots survive only in git history (~50 KB each), which doubles as an
audit trail of how each stock's pledge/DII moved.

## Troubleshooting

| Log line on Actions | Meaning | Do |
|---|---|---|
| `NSE snapshot: … not present` | file never pushed | run step 2 |
| `… Xd old (> 14d) — ignored as stale` | laptop missed 2+ weeks | double-click `fetch_nse_now.bat` |
| `… missing keys` / `unreadable` | file corrupted | re-run the fetch; it overwrites |
| fetcher prints `No usable records` | NSE blocked *your* IP this moment | VPN off? retry in a few min |
| fetcher: git push failed | dirty checkout / bot collision | `git pull --rebase` then push, or use a dedicated clone |

Keep the laptop's checkout clean (commit or stash local edits) — `--push`
does a `git pull --rebase` first and a dirty tree can stall it. A separate
clone dedicated to the fetcher is the tidiest setup.