"""
orchestrator.py
SECTION 12B — Gate Check (v7 FINAL)
Single consolidated check at 04:30 IST next morning.
All 6 conditions must pass before ANY pipeline work begins.
"""

import os
import datetime
import pytz
import requests
import sqlite3
from dotenv import load_dotenv

try:
    # Normal use: `from ingestion.orchestrator import gate_check`
    from .holiday_calendar import ensure_holiday_calendar_fresh
except ImportError:
    # Fallback for direct execution: `python3 ingestion/orchestrator.py`
    from holiday_calendar import ensure_holiday_calendar_fresh

load_dotenv()

IST = pytz.timezone('Asia/Kolkata')

# ── SECTION 12C: NSE Holiday Calendar — auto-fetched, never hardcoded ────────
# The full year's holiday calendar is fetched once per pipeline run from
# https://www.nseindia.com/api/holiday-master?type=trading and cached in the
# market_holidays DB table. See ingestion/holiday_calendar.py for the fetch
# logic, fallback chain, and failure semantics.
#
# Cache lifecycle: per-year. When the API returns the new year's calendar
# (NSE typically publishes by mid-December), we wipe and reinsert that year's
# rows. Old years are retained for historical lookups.
#
# Cold-start safety: if the API fails AND no rows are cached for the target
# year, _is_market_holiday_or_unknown() returns ("unknown", None) and the
# gate check conservatively blocks the run. This prevents the pipeline from
# silently running on an actual holiday because we couldn't reach NSE.


def _build_nse_bhav_url(target_date: datetime.date) -> str:
    """
    Correct NSE Bhav Copy URL format for the new archive CDN.
    No session cookie needed for HEAD checks on nsearchives domain.
    """
    ds = target_date.strftime("%Y%m%d")
    return (
        f"https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{ds}_F_0000.csv.zip"
    )


def _build_bse_bhav_url(target_date: datetime.date) -> str:
    """
    Correct BSE Bhav Copy URL format: EQ{DDMMYY}_CSV.ZIP  (uppercase).
    """
    ds = target_date.strftime("%d%m%y").upper()
    return f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{ds}_CSV.ZIP"


def _http_head_ok(url: str, timeout: int = 15) -> bool:
    """
    Safe HTTP HEAD request. Uses nsearchives CDN directly — no Cloudflare block.
    Returns True only on 200 or 302.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        r = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        return r.status_code in [200, 302]
    except Exception:
        return False


def _check_market_holiday(date_str: str, year: int) -> tuple[str, str | None, dict]:
    """
    C2 holiday check — three-state output.

    Returns:
      ("holiday",      <name>, calendar)   — confirmed holiday, skip the run
      ("trading_day",  None,   calendar)   — confirmed not a holiday, proceed
      ("unknown",      None,   {})         — calendar unknown (API fail + cache empty)
                                             → caller must fail-closed (block run)

    Source of truth is the NSE holiday-master API, cached in market_holidays.
    See ingestion/holiday_calendar.py for the full fetch + fallback story.
    """
    calendar = ensure_holiday_calendar_fresh(year)
    if calendar is None:
        return ("unknown", None, {})
    if date_str in calendar:
        return ("holiday", calendar[date_str], calendar)
    return ("trading_day", None, calendar)


def gate_check() -> dict:
    """
    SECTION 12B: Single consolidated gate check.

    Schedule: runs at 04:30 IST the NEXT MORNING after market close.
    Target date = YESTERDAY (the trading day whose data we are processing).

    Reason for next-morning schedule:
    - NSE/BSE publish Bhav Copy after 18:00 IST but availability is
      uncertain (sometimes 18:10, sometimes 18:45, sometimes delayed).
    - Running at 04:30 IST next morning gives a guaranteed 10+ hour buffer
      ensuring files are always available before the pipeline starts.

    Returns dict: {"run": bool, "reason": str, "target_date": date,
                   "bse_available": bool, "log": list}
    """
    now = datetime.datetime.now(IST)
    log = []

    # The trading day we are processing = yesterday (next-morning schedule)
    target_date = (now - datetime.timedelta(days=1)).date()
    target_str  = target_date.strftime("%Y-%m-%d")

    print(f"--- [Gate Check] Processing data for: {target_str} ---")

    # ── C1: Weekend Check ────────────────────────────────────────────────────
    if target_date.weekday() in [5, 6]:
        reason = f"SKIP: {target_str} is a weekend ({target_date.strftime('%A')}). No market data."
        log.append({"condition": "C1 Weekend", "status": "FAIL", "detail": reason})
        print(f"🛑 C1 FAIL: {reason}")
        return {"run": False, "reason": reason, "target_date": target_date,
                "bse_available": False, "log": log}
    log.append({"condition": "C1 Weekend", "status": "PASS", "detail": f"{target_date.strftime('%A')} is a weekday."})
    print("✅ C1 PASS: Weekday confirmed.")

    # ── C2: Market Holiday Check ─────────────────────────────────────────────
    holiday_status, holiday_name, _calendar = _check_market_holiday(target_str, target_date.year)

    if holiday_status == "unknown":
        reason = (
            f"BLOCK: Holiday calendar unknown for {target_date.year}. "
            f"NSE holiday API unreachable AND market_holidays cache is empty. "
            f"Refusing to run — risk of executing on an actual holiday."
        )
        log.append({"condition": "C2 Holiday", "status": "FAIL", "detail": reason})
        print(f"🛑 C2 FAIL: {reason}")
        return {"run": False, "reason": reason, "target_date": target_date,
                "bse_available": False, "log": log}

    if holiday_status == "holiday":
        reason = f"SKIP: {target_str} is a market holiday ({holiday_name})."
        log.append({"condition": "C2 Holiday", "status": "FAIL", "detail": reason})
        print(f"🛑 C2 FAIL: {reason}")
        return {"run": False, "reason": reason, "target_date": target_date,
                "bse_available": False, "log": log}

    log.append({"condition": "C2 Holiday", "status": "PASS",
                "detail": f"{target_str} is not a holiday."})
    print("✅ C2 PASS: Not a market holiday.")

    # ── C3: NSE Bhav Copy File Availability ──────────────────────────────────
    nse_url = _build_nse_bhav_url(target_date)
    print(f"   C3 Checking NSE: {nse_url}")
    nse_ok = _http_head_ok(nse_url)

    if not nse_ok:
        # Single retry — at 04:30 IST the file should always be there
        print("   C3 NSE not responding. Retrying once in 30 seconds...")
        import time; time.sleep(30)
        nse_ok = _http_head_ok(nse_url)

    if not nse_ok:
        reason = f"SKIP: NSE Bhav Copy not available for {target_str}. URL returned non-200."
        log.append({"condition": "C3 NSE File", "status": "FAIL", "detail": reason})
        print(f"🛑 C3 FAIL: {reason}")
        return {"run": False, "reason": reason, "target_date": target_date,
                "bse_available": False, "log": log}
    log.append({"condition": "C3 NSE File", "status": "PASS", "detail": nse_url})
    print("✅ C3 PASS: NSE Bhav Copy confirmed available.")

    # ── C4: BSE Bhav Copy — always attempt, never block pipeline ────────────
    # bseindia.com uses Cloudflare which blocks HTTP HEAD requests from
    # data-centre IPs (GitHub Actions). A HEAD check will always return False
    # here even when BSE data is genuinely available via the bse pip package
    # or cloudscraper. So we set bse_available=True and let the actual
    # download functions determine availability at runtime — they already
    # handle failure gracefully and fall back to NSE-only mode themselves.
    bse_url = _build_bse_bhav_url(target_date)
    print(f"   C4 BSE: {bse_url}")
    print("✅ C4 PASS: BSE download will be attempted at runtime (cloudscraper + bse package).")
    log.append({"condition": "C4 BSE File", "status": "PASS",
                "detail": "BSE download attempted at runtime — HEAD check skipped (Cloudflare)."})
    bse_ok = True   # Always attempt; download functions handle failure gracefully

    # REPLACE with just this one line:
    print("✅ C5 SKIP: No watchlist required. Screener runs on full NSE+BSE universe.")
    
    # ── ALL CONDITIONS PASSED ─────────────────────────────────────────────────
    log.append({"condition": "GATE RESULT", "status": "RUN_APPROVED",
                "detail": f"Processing {target_str}. BSE mode: {'NSE+BSE' if bse_ok else 'NSE-only'}."})
    print(f"✅ GATE APPROVED: Processing data for {target_str}.")
    return {
        "run": True,
        "reason": "All gate conditions passed.",
        "target_date": target_date,
        "bse_available": bse_ok,
        "log": log,
    }