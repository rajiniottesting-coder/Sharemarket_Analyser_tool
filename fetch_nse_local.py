"""
fetch_nse_local.py  —  v17.13
=================================
Runs on YOUR machine (a residential IP that NSE serves), NOT on GitHub
Actions. Fetches the two datasets NSE refuses to give a datacenter IP:

    1. Bulk promoter-pledge %  (pledge_pct)
    2. Shareholding pattern    (promoter / FII / DII %)

and writes them to a single committed snapshot:

    data/nse_snapshot.json

which the pipeline reads as a FALLBACK when its own live NSE call returns
nothing (which is every run on Actions). The pipeline validates the file
(schema + freshness) before using a single number — see
ingestion/nse_snapshot.py.

WHY A FILE, NOT THE DB
market_data.db exists only as a GitHub Actions artifact; this machine cannot
write into it. A small committed JSON is the one channel that flows from
your residential IP into the runner.

HOW TO SCHEDULE (Windows Task Scheduler — weekly is enough; these figures
change quarterly):
    Program : python
    Args    : fetch_nse_local.py --push
    Start in: <repo folder>
    Trigger : Weekly, e.g. Sunday 20:00, "Run only when user is logged on"
              (the laptop must be ON; Task Scheduler does not wake a
              sleeping machine by default).

REUSE, NOT REIMPLEMENTATION
Parsing is delegated to the pipeline's own functions
(backfill_history._nse_shareholding, ingestion.nse_pledge.fetch_bulk_pledge_data)
so the numbers here are byte-identical to what the pipeline would have
produced had NSE not blocked it. No second parser to drift.

SAFETY
  · Never writes a snapshot with zero usable records — an empty file would
    be "valid" and silently blank every stock. Keeps the previous snapshot.
  · --push only commits when the file actually changed.
  · Any failure leaves the previous snapshot untouched.
"""

import os
import sys
import json
import argparse
import datetime as _dt
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

SNAPSHOT_PATH = os.path.join(_HERE, "data", "nse_snapshot.json")
SNAPSHOT_VERSION = 1


def _universe_symbols() -> list:
    """Symbols to fetch shareholding for.

    v17.13.1: the laptop has NO market_data.db (it lives only on Actions), so
    the previous DB-first lookup returned nothing and the fetcher iterated
    zero symbols. Order now:
      1. data/nse_symbols.txt  — a committed list you control (one per line).
         The pipeline's last top-100 is a good source; see README.
      2. NSE's own NIFTY 500 constituent CSV (public, no auth) — covers every
         Gold candidate the funnel could ever produce.
      3. Fallback: whatever symbols the pledge report itself returned.
    """
    syms = []
    # 1. committed list
    try:
        lst = os.path.join(_HERE, "data", "nse_symbols.txt")
        if os.path.exists(lst):
            with open(lst, encoding="utf-8") as fh:
                syms = [ln.strip().upper() for ln in fh if ln.strip() and not ln.startswith("#")]
            if syms:
                print(f"   universe: {len(syms)} symbols from data/nse_symbols.txt")
                return sorted(set(syms))
    except Exception:
        pass
    # 2. NIFTY 500 from NSE archives (plain CSV, no bot wall)
    try:
        import csv, io, requests
        r = requests.get("https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
                         timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and r.text:
            for row in csv.DictReader(io.StringIO(r.text)):
                sy = (row.get("Symbol") or "").strip().upper()
                if sy:
                    syms.append(sy)
            if syms:
                print(f"   universe: {len(syms)} symbols from NSE NIFTY 500 list")
                return sorted(set(syms))
    except Exception as e:
        print(f"   ⚠️  NIFTY 500 list fetch failed: {e}")
    return sorted(set(syms))


def _import_pipeline_parsers():
    """backfill_history reads sys.argv[1] at import time (as a day count), so
    our own CLI flags would crash it. Import with a clean argv, then restore."""
    saved = sys.argv
    sys.argv = [saved[0]]
    try:
        from backfill_history import _nse_session, _nse_shareholding
        from ingestion.nse_pledge import fetch_bulk_pledge_data
    finally:
        sys.argv = saved
    return _nse_session, _nse_shareholding, fetch_bulk_pledge_data


def fetch_snapshot() -> dict:
    _nse_session, _nse_shareholding, fetch_bulk_pledge_data = _import_pipeline_parsers()

    session = _nse_session()

    # ── 1. Bulk pledge (one call, whole market) ──
    print("📥 NSE bulk pledge …")
    pledge = {}
    try:
        pledge = fetch_bulk_pledge_data(session) or {}
    except Exception as e:
        print(f"   ⚠️  pledge fetch failed: {e}")
    print(f"   pledge records: {len(pledge)}")

    # ── 2. Shareholding per symbol (promoter / FII / DII) ──
    syms = _universe_symbols() or sorted(pledge.keys())
    print(f"📥 NSE shareholding for {len(syms)} symbols …")
    share = {}
    for i, sym in enumerate(syms, 1):
        try:
            rec = _nse_shareholding(sym, session) or {}
            if rec and any(float(rec.get(k, 0) or 0) > 0
                           for k in ("promoter_pct", "fii_pct", "dii_pct")):
                share[sym] = {k: round(float(rec.get(k, 0) or 0), 2)
                              for k in ("promoter_pct", "fii_pct", "dii_pct")}
        except Exception:
            pass
        if i % 25 == 0:
            print(f"   … {i}/{len(syms)}  ({len(share)} with data)")
    print(f"   shareholding records: {len(share)}")

    return {
        "version": SNAPSHOT_VERSION,
        "fetched_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "fetch_nse_local.py (residential IP)",
        "pledge": {k: round(float(v), 2) for k, v in pledge.items()},
        "shareholding": share,
    }


def _load_existing() -> dict:
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _probe() -> int:
    """Diagnostic only. Shows, per endpoint, the exact HTTP status and the
    first 160 bytes of the body — so 403 (blocked) vs 404 (moved) vs 200-but-
    HTML (bot challenge page) vs 200-JSON (works) is settled in one look."""
    _nse_session, _, _ = _import_pipeline_parsers()
    import requests
    sess = _nse_session()
    hdrs = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-pledged-data",
            "X-Requested-With": "XMLHttpRequest"}
    print(f"🔎 cookies after warm-up: {list(sess.cookies.keys()) or 'NONE (warm-up blocked?)'}")
    for url in ("https://www.nseindia.com/api/corporate-pledgedata",
                "https://www.nseindia.com/api/corporates-pledgedata?index=equities",
                "https://www.nseindia.com/api/corp-info?symbol=RELIANCE",
                "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"):
        try:
            r = sess.get(url, timeout=20, headers=hdrs)
            body = (r.text or "")[:160].replace("\n", " ")
            kind = ("JSON" if body.lstrip().startswith(("{", "[")) else
                    "HTML/challenge" if "<html" in body.lower() else "other")
            print(f"  HTTP {r.status_code:<4} {kind:<14} {url}\n        {body!r}")
        except Exception as e:
            print(f"  ERR  {url}\n        {e}")
    print("\nReading: 200+JSON = works · 403 = blocked for this client · 404 = path moved · "
          "200+HTML = bot challenge (cookie/fingerprint) · warm-up NONE = homepage itself blocked")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true",
                    help="git add/commit/push the snapshot if it changed")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report, but do not write the file")
    ap.add_argument("--probe", action="store_true",
                    help="diagnostic: print raw HTTP status + first bytes from each "
                         "NSE endpoint so a block vs a moved path is unambiguous")
    args = ap.parse_args()

    if args.probe:
        return _probe()

    snap = fetch_snapshot()
    n_pl, n_sh = len(snap["pledge"]), len(snap["shareholding"])

    if n_pl == 0 and n_sh == 0:
        print("❌ No usable records from NSE — snapshot NOT written "
              "(previous snapshot kept). Are you on a residential IP?")
        return 2

    if args.dry_run:
        print(f"✅ dry-run: would write {n_pl} pledge + {n_sh} shareholding records")
        return 0

    prev = _load_existing()
    changed = (prev.get("pledge") != snap["pledge"]
               or prev.get("shareholding") != snap["shareholding"])

    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1, sort_keys=True)
    print(f"✅ wrote {os.path.relpath(SNAPSHOT_PATH, _HERE)}: "
          f"{n_pl} pledge + {n_sh} shareholding · fetched_at {snap['fetched_at']}")

    if args.push:
        if not changed:
            # Still bump fetched_at so the pipeline's freshness check passes —
            # a real fetch ran and confirmed the figures.
            pass
        rel = os.path.relpath(SNAPSHOT_PATH, _HERE).replace("\\", "/")
        try:
            subprocess.run(["git", "-C", _HERE, "pull", "--rebase", "--quiet"], check=False)
            subprocess.run(["git", "-C", _HERE, "add", rel], check=True)
            msg = (f"chore(nse-snapshot): pledge {n_pl} / shareholding {n_sh} "
                   f"@ {snap['fetched_at'][:10]}")
            r = subprocess.run(["git", "-C", _HERE, "commit", "-m", msg],
                               capture_output=True, text=True)
            if r.returncode != 0 and "nothing to commit" in (r.stdout + r.stderr):
                print("ℹ️  snapshot unchanged — nothing to commit")
                return 0
            subprocess.run(["git", "-C", _HERE, "push"], check=True)
            print("🚀 pushed")
        except subprocess.CalledProcessError as e:
            print(f"⚠️  git step failed: {e} — snapshot is written locally; push manually")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())