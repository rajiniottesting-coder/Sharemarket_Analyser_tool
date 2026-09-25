"""
ingestion/nse_snapshot.py  —  v17.13
======================================
Pipeline-side consumer of data/nse_snapshot.json, the file written by
fetch_nse_local.py from a residential IP (NSE blocks GitHub's datacenter
IPs, so the live calls return nothing on Actions).

CONTRACT — "exists, validates, then use; else ignore":
  1. File missing            → ignore, log once, pledge/DII stay —  (today's behaviour)
  2. Malformed / wrong shape → ignore, log why, stay —
  3. fetched_at older than MAX_AGE_DAYS → ignore, log the age, stay —
     (a stale figure fed to the spike guard would look like data while
      quietly going out of date — the exact failure this guard prevents)
  4. Valid + fresh           → FILL ONLY BLANKS. A stock whose pledge/DII
     the pipeline already obtained live is never overwritten. The snapshot
     is a fallback, not an override.

Every applied value is tagged with its source so the origin is visible.
This module never raises: any error degrades to "ignore".
"""

import os
import json
import datetime as _dt

SNAPSHOT_PATH = os.path.join("data", "nse_snapshot.json")
MAX_AGE_DAYS = int(os.getenv("NSE_SNAPSHOT_MAX_AGE_DAYS", "14") or 14)
_REQUIRED_KEYS = ("version", "fetched_at", "pledge", "shareholding")


def load_snapshot(path: str = SNAPSHOT_PATH, max_age_days: int = None,
                  quiet: bool = False) -> dict:
    """Return the validated snapshot dict, or {} if it must be ignored.
    Prints exactly one line explaining the decision."""
    max_age_days = MAX_AGE_DAYS if max_age_days is None else max_age_days
    if not os.path.exists(path):
        print(f"   ℹ️  NSE snapshot: {path} not present — pledge/DII fallback unavailable")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            snap = json.load(fh)
    except Exception as e:
        print(f"   ⚠️  NSE snapshot: unreadable ({e}) — ignored")
        return {}
    if not isinstance(snap, dict) or any(k not in snap for k in _REQUIRED_KEYS):
        print(f"   ⚠️  NSE snapshot: missing keys {[k for k in _REQUIRED_KEYS if k not in snap]} — ignored")
        return {}
    if not isinstance(snap.get("pledge"), dict) or not isinstance(snap.get("shareholding"), dict):
        print("   ⚠️  NSE snapshot: pledge/shareholding must be objects — ignored")
        return {}
    # freshness
    try:
        fetched = _dt.datetime.fromisoformat(str(snap["fetched_at"]))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=_dt.timezone.utc)
        age = (_dt.datetime.now(_dt.timezone.utc) - fetched.astimezone(_dt.timezone.utc)).days
    except Exception as e:
        print(f"   ⚠️  NSE snapshot: bad fetched_at ({e}) — ignored")
        return {}
    if age > max_age_days:
        print(f"   ⚠️  NSE snapshot: {age}d old (> {max_age_days}d) — ignored as stale; "
              f"pledge/DII stay —. Run fetch_nse_local.py to refresh.")
        return {}
    n_pl, n_sh = len(snap["pledge"]), len(snap["shareholding"])
    if n_pl == 0 and n_sh == 0:
        print("   ⚠️  NSE snapshot: contains no records — ignored")
        return {}
    if not quiet:
        print(f"   ✅ NSE snapshot: {n_pl} pledge + {n_sh} shareholding records, "
              f"{age}d old (≤ {max_age_days}d) — used as fallback for blanks")
    # v17.13.6: expose provenance so the Excel can show WHEN this data was
    # fetched. _age_days and _fetched_display are derived, not stored.
    snap["_age_days"] = age
    try:
        # v17.16.1: show IST explicitly. astimezone() with no argument used the
        # RUNNER's zone (UTC on GitHub Actions), so a 06:12 IST fetch printed
        # as "00:42" with no zone — easy to misread as a midnight run.
        _ist = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
        snap["_fetched_display"] = fetched.astimezone(_ist).strftime("%d-%b-%Y %H:%M IST")
    except Exception:
        snap["_fetched_display"] = str(snap.get("fetched_at", ""))[:16]
    return snap


def _blank(v) -> bool:
    try:
        return v is None or float(v) <= 0
    except (TypeError, ValueError):
        return True


def apply_snapshot(stocks: list, snap: dict) -> dict:
    """Fill pledge_pct / promoter_pct / fii_pct / dii_pct ONLY where the stock
    currently has no value. Returns counts. Never raises."""
    out = {"pledge": 0, "dii": 0, "fii": 0, "promoter": 0}
    if not snap or not stocks:
        return out
    pledge = snap.get("pledge", {}) or {}
    share = snap.get("shareholding", {}) or {}
    for st in stocks:
        try:
            sym = str(st.get("symbol", "") or "").strip()
            if not sym:
                continue
            if sym in pledge and _blank(st.get("pledge_pct")):
                st["pledge_pct"] = float(pledge[sym])
                st["pledge_source"] = "snapshot"
                out["pledge"] += 1
            rec = share.get(sym)
            if rec:
                for key, cnt in (("dii_pct", "dii"), ("fii_pct", "fii"),
                                 ("promoter_pct", "promoter")):
                    if _blank(st.get(key)) and not _blank(rec.get(key)):
                        st[key] = float(rec[key])
                        st[f"{key}_source"] = "snapshot"
                        out[cnt] += 1
        except Exception:
            continue
    return out