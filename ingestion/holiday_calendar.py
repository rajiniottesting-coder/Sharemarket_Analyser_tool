"""
ingestion/holiday_calendar.py
SECTION 12C — NSE Trading Holiday Calendar (auto-fetch)

Replaces the hardcoded HOLIDAYS_2026 dict with an automated fetch from the
NSE holiday-master API. Holidays are cached in the `market_holidays` table
and refreshed once per pipeline run.

DESIGN
──────
1. Try NSE API: https://www.nseindia.com/api/holiday-master?type=trading
   Returns Capital Market ("CM") segment entries — the ones that govern
   when the equity bhav copy is published.
2. On success: upsert all fetched dates for the API's covered year(s)
   into market_holidays (exchange='NSE'). Cache survives across runs.
3. On failure: fall back to whatever's already in market_holidays.
4. Hard-fail mode: if BOTH the API fails AND the DB has no holidays for
   the target year, return None — the caller treats this as "calendar
   unknown" and conservatively blocks the run rather than risking a run
   on an actual holiday.

NSE API quirks worth knowing
────────────────────────────
- Requires a session cookie. We GET the homepage first to obtain one,
  then call /api/holiday-master with the same session.
- Cloudflare frequently 403s from data-centre IPs (GitHub Actions). The
  DB cache is the real first-line defence — once a year's calendar is
  cached, subsequent runs work even with the API blocked.
- Response schema: {"CM": [{"tradingDate": "26-Jan-2026", "description":
  "Republic Day", "weekDay": "Monday", ...}, ...], "FO": [...], ...}
  We use the "CM" segment for equity trading holidays.
"""

from __future__ import annotations

import datetime
import sqlite3
from typing import Optional

import requests


NSE_HOMEPAGE      = "https://www.nseindia.com"
NSE_HOLIDAY_API   = "https://www.nseindia.com/api/holiday-master?type=trading"
DB_PATH           = "market_data.db"
HTTP_TIMEOUT_SEC  = 15
USER_AGENT        = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── public API ───────────────────────────────────────────────────────────────

def ensure_holiday_calendar_fresh(year: int) -> Optional[dict]:
    """
    Top-level: attempt API fetch, persist on success, return the dict for `year`.

    Returns:
        dict[str, str]   mapping "YYYY-MM-DD" → holiday name, OR
        None             if API failed AND DB has no holidays for `year`
                         (caller should conservatively block the run).
    """
    api_holidays = _fetch_nse_holidays_from_api()

    if api_holidays:
        # Persist all years the API returned — typically just the current
        # calendar year, but NSE occasionally publishes early-Jan dates of
        # next year too. Cache everything we got.
        for y, mapping in _group_by_year(api_holidays).items():
            _sync_holidays_to_db(y, mapping)
        print(f"   📅 Holiday calendar refreshed from NSE API "
              f"({len(api_holidays)} dates).")

    db_holidays = _load_holidays_from_db(year)
    if db_holidays:
        return db_holidays

    # No API, no cache — calendar unknown for this year.
    print(f"   ⚠ Holiday calendar UNKNOWN for {year}: "
          f"NSE API unreachable AND market_holidays cache is empty.")
    return None


# ── NSE API fetch ────────────────────────────────────────────────────────────

def _fetch_nse_holidays_from_api() -> Optional[dict]:
    """
    Returns dict[str, str]   "YYYY-MM-DD" → holiday name (CM segment),
            or None on any failure (network, 403, schema mismatch, etc.).

    Uses cloudscraper when available (better Cloudflare resilience on cloud
    runners), falls back to plain requests if cloudscraper isn't installed.
    """
    try:
        try:
            import cloudscraper
            s = cloudscraper.create_scraper()
        except ImportError:
            s = requests.Session()

        headers = {
            "User-Agent":      USER_AGENT,
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        # Step 1 — prime cookies by hitting the homepage. Without this NSE
        # returns 401 on the API even with a valid User-Agent.
        s.get(NSE_HOMEPAGE, headers=headers, timeout=HTTP_TIMEOUT_SEC)

        # Step 2 — call the holiday-master endpoint.
        r = s.get(NSE_HOLIDAY_API, headers=headers, timeout=HTTP_TIMEOUT_SEC)
        if r.status_code != 200:
            print(f"   ⚠ NSE holiday API returned HTTP {r.status_code}")
            return None

        payload = r.json()
        cm_entries = payload.get("CM") or []
        if not cm_entries:
            print("   ⚠ NSE holiday API response missing 'CM' segment.")
            return None

        out: dict = {}
        for entry in cm_entries:
            raw_date = (entry.get("tradingDate") or "").strip()
            name     = (entry.get("description") or "Listed Holiday").strip()
            iso = _parse_nse_date(raw_date)
            if iso:
                out[iso] = name
        return out or None

    except Exception as exc:
        print(f"   ⚠ NSE holiday API fetch failed: {exc!r}")
        return None


def _parse_nse_date(raw: str) -> Optional[str]:
    """NSE ships dates as '26-Jan-2026'. Convert to ISO 'YYYY-MM-DD'."""
    if not raw:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _group_by_year(holidays: dict) -> dict:
    """{'2026-01-26': 'Republic Day', '2027-01-26': '...'}  →  {2026: {...}, 2027: {...}}"""
    out: dict = {}
    for date_str, name in holidays.items():
        year = int(date_str[:4])
        out.setdefault(year, {})[date_str] = name
    return out


# ── DB cache ────────────────────────────────────────────────────────────────

def _sync_holidays_to_db(year: int, holidays: dict) -> None:
    """
    Upsert all `holidays` into market_holidays for the given year.
    Schema (already created elsewhere):
      market_holidays(date TEXT, name TEXT, exchange TEXT, PK(date, exchange))
    """
    if not holidays:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Defensive: ensure the table exists. data_bridge.py also creates it,
        # but a cold-start orchestrator shouldn't depend on the import order.
        c.execute("""
            CREATE TABLE IF NOT EXISTS market_holidays (
                date     TEXT,
                name     TEXT,
                exchange TEXT,
                PRIMARY KEY (date, exchange)
            )
        """)
        # Wipe then reinsert this year's NSE rows — handles the case where
        # NSE moves a holiday (rare but happens — Diwali Muhurat, etc.).
        c.execute(
            "DELETE FROM market_holidays "
            "WHERE exchange = 'NSE' AND substr(date, 1, 4) = ?",
            (str(year),),
        )
        c.executemany(
            "INSERT INTO market_holidays (date, name, exchange) VALUES (?, ?, 'NSE')",
            [(d, n) for d, n in holidays.items()],
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"   ⚠ Holiday DB sync failed for year {year}: {exc!r}")


def _load_holidays_from_db(year: int) -> dict:
    """Return {date_str: name} for the given year, or {} if none cached."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT date, name
              FROM market_holidays
             WHERE exchange = 'NSE'
               AND substr(date, 1, 4) = ?
        """, (str(year),))
        rows = c.fetchall()
        conn.close()
        return {d: n for d, n in rows}
    except Exception:
        # Table doesn't exist yet, DB locked, etc. — caller handles empty dict.
        return {}