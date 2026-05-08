#!/usr/bin/env python3
"""
v14.0 — Gold-pick outcome tracker.

Walks daily_prices forward from each OPEN recommendation date and records
the first event that fires:
    SL_HIT   — daily low ≤ stop_loss      (highest priority — wins ties)
    T3_HIT   — daily high ≥ T3
    T2_HIT   — daily high ≥ T2 (and not yet T3)
    T1_HIT   — daily high ≥ T1 (and not yet T2)
    EXPIRED  — 90 calendar days passed with no event

Design rules (locked in v14_state.md):
    - First-event-wins: tracker stops at the first day a target/SL fires
    - SL beats target on same-day ties (we use daily OHLC; can't tell intraday order)
    - Target priority on a single day: T3 > T2 > T1 (highest target wins)
    - Tracks max_drawdown_pct + max_runup_pct along the way for diagnostics
    - Updates current_price + current_pnl_pct + last_checked_date for OPEN rows
      so the dashboard can show "where is each open position right now?"

Run as: `python3 track_outcomes.py` after the daily pipeline completes.
Idempotent: closed rows are skipped; OPEN rows are re-walked each day to
catch any new events since last run.

Exit code: 0 on success, 1 if DB unreachable.
"""
import sys, sqlite3, os
from datetime import datetime, timedelta
import pandas as pd

# Add project root to path so this script works whether invoked from
# project dir or elsewhere
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from database.data_bridge import (
    get_open_recommendations,
    update_outcome,
)

# ─── Config ───────────────────────────────────────────────────────────
# v14.0 → v14.1: EXPIRY_DAYS is now a per-recommendation value, captured
# at log time from each pick's Horizon (SHORT TERM=30 / POSITIONAL=90 /
# LONG TERM=270). The constant below is a fallback for legacy rows that
# pre-date v14.1 (where expiry_days column may be NULL) — kept at 90 to
# match v14.0 behavior. New rows always carry their own expiry_days.
DEFAULT_EXPIRY_DAYS = 90
DB_PATH             = "market_data.db"
EXCHANGE            = "NSE"   # match the rest of the pipeline (v12.7+ filter)


def _load_price_history(symbol: str, start_date: str) -> pd.DataFrame:
    """Load daily OHLC for `symbol` on/after `start_date` (YYYY-MM-DD).
    Filters exchange='NSE' to match the rest of the pipeline (v12.7 fix).
    Returns DataFrame with columns: date, open, high, low, close.
    Sorted by date ascending. Empty DataFrame on any error."""
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            df = pd.read_sql_query(
                """
                SELECT date, open, high, low, close
                FROM daily_prices
                WHERE symbol = ?
                  AND exchange = ?
                  AND date > ?
                ORDER BY date ASC
                """,
                conn,
                params=(symbol, EXCHANGE, start_date),
            )
            return df
        finally:
            conn.close()
    except Exception as e:
        print(f"   ⚠️  _load_price_history({symbol}): {e}")
        return pd.DataFrame()


def _walk_forward(rec: dict) -> dict:
    """Walk forward through daily_prices for one OPEN recommendation.
    Returns a dict suitable for update_outcome():
        outcome_type, outcome_date, outcome_price, days_to_outcome,
        max_drawdown_pct, max_runup_pct, current_price, current_pnl_pct,
        last_checked_date

    v14.1: expiry window is now per-recommendation, read from the rec's
    `expiry_days` field (captured at log time from the Horizon column).
    Falls back to DEFAULT_EXPIRY_DAYS (90) if missing — preserves v14.0
    behavior for legacy rows.

    If no SL/T1/T2/T3 hit and within expiry window → outcome_type stays OPEN
    but tracking metrics are refreshed."""
    sym       = rec["symbol"]
    rec_date  = rec["recommendation_date"]   # YYYY-MM-DD
    cmp_rec   = float(rec.get("cmp_at_recommendation", 0) or 0)
    sl        = float(rec.get("stop_loss", 0) or 0)
    t1        = float(rec.get("t1", 0) or 0)
    t2        = float(rec.get("t2", 0) or 0)
    t3        = float(rec.get("t3", 0) or 0)
    # v14.1: per-rec expiry window
    expiry_days = int(rec.get("expiry_days") or DEFAULT_EXPIRY_DAYS)
    if expiry_days <= 0:
        expiry_days = DEFAULT_EXPIRY_DAYS

    # Defensive: if any critical level is 0/missing, mark closed-error so
    # we don't keep retrying. These shouldn't happen because master_funnel
    # only logs Gold picks (which always have valid trade levels), but just in case.
    if cmp_rec <= 0 or sl <= 0 or t1 <= 0:
        return {
            "outcome_type": "EXPIRED",   # use EXPIRED bucket so it shows up but doesn't pollute hit-rate stats
            "outcome_date": rec_date,
            "outcome_price": cmp_rec,
            "days_to_outcome": 0,
            "max_drawdown_pct": 0.0,
            "max_runup_pct":   0.0,
            "current_price":   cmp_rec,
            "current_pnl_pct": 0.0,
            "last_checked_date": datetime.now().strftime("%Y-%m-%d"),
            "_note": "missing trade levels — bucketed as EXPIRED",
        }

    prices = _load_price_history(sym, rec_date)
    today = datetime.now().date()
    rec_d = datetime.strptime(rec_date, "%Y-%m-%d").date()
    days_elapsed = (today - rec_d).days

    # Track running min/max for drawdown / runup
    max_runup_pct    = 0.0
    max_drawdown_pct = 0.0
    last_close       = cmp_rec
    last_date        = rec_date

    if prices.empty:
        # No price history yet — could be brand-new recommendation
        # OR symbol's prices haven't refreshed since. Stay OPEN.
        # If past expiry window AND no prices, expire it.
        if days_elapsed >= expiry_days:
            return {
                "outcome_type": "EXPIRED",
                "outcome_date": (rec_d + timedelta(days=expiry_days)).strftime("%Y-%m-%d"),
                "outcome_price": cmp_rec,
                "days_to_outcome": expiry_days,
                "max_drawdown_pct": 0.0,
                "max_runup_pct":   0.0,
                "current_price":   cmp_rec,
                "current_pnl_pct": 0.0,
                "last_checked_date": today.strftime("%Y-%m-%d"),
            }
        return {
            "outcome_type": "OPEN",
            "outcome_date": "",
            "outcome_price": 0.0,
            "days_to_outcome": 0,
            "max_drawdown_pct": 0.0,
            "max_runup_pct":   0.0,
            "current_price":   cmp_rec,
            "current_pnl_pct": 0.0,
            "last_checked_date": today.strftime("%Y-%m-%d"),
        }

    # Walk each day chronologically
    for _, row in prices.iterrows():
        d_str = str(row["date"])[:10]
        # Days since recommendation (not since first price row — uses recommendation date as anchor)
        try:
            d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        days_in = (d_obj - rec_d).days

        # v14.1: per-rec expiry check (was hardcoded 90 in v14.0).
        # If we crossed the recommendation's own expiry window without an event,
        # bucket as EXPIRED at the expiry mark — HARD CUTOFF, no grace period.
        if days_in > expiry_days:
            expiry_d = (rec_d + timedelta(days=expiry_days))
            return {
                "outcome_type": "EXPIRED",
                "outcome_date": expiry_d.strftime("%Y-%m-%d"),
                "outcome_price": last_close,   # last close before expiry
                "days_to_outcome": expiry_days,
                "max_drawdown_pct": round(max_drawdown_pct, 2),
                "max_runup_pct":   round(max_runup_pct, 2),
                "current_price":   last_close,
                "current_pnl_pct": round((last_close - cmp_rec) / cmp_rec * 100, 2),
                "last_checked_date": today.strftime("%Y-%m-%d"),
            }

        hi  = float(row.get("high", 0) or 0)
        lo  = float(row.get("low",  0) or 0)
        cl  = float(row.get("close", 0) or 0)

        # Update tracking metrics BEFORE event check
        if cl > 0:
            day_runup    = (hi - cmp_rec) / cmp_rec * 100 if hi > 0 else 0
            day_drawdown = (lo - cmp_rec) / cmp_rec * 100 if lo > 0 else 0
            if day_runup > max_runup_pct:    max_runup_pct = day_runup
            if day_drawdown < max_drawdown_pct: max_drawdown_pct = day_drawdown
            last_close = cl
            last_date  = d_str

        # ─── Event check — SL beats target on same-day ties ───
        # SL hit: daily low touched/breached SL
        if lo > 0 and lo <= sl:
            return {
                "outcome_type": "SL_HIT",
                "outcome_date": d_str,
                "outcome_price": sl,    # the SL level (where exit would have triggered)
                "days_to_outcome": days_in,
                "max_drawdown_pct": round(max_drawdown_pct, 2),
                "max_runup_pct":   round(max_runup_pct, 2),
                "current_price":   sl,
                "current_pnl_pct": round((sl - cmp_rec) / cmp_rec * 100, 2),
                "last_checked_date": today.strftime("%Y-%m-%d"),
            }
        # Target hit: highest target wins on a single day
        if hi > 0:
            if t3 > 0 and hi >= t3:
                return {
                    "outcome_type": "T3_HIT",
                    "outcome_date": d_str,
                    "outcome_price": t3,
                    "days_to_outcome": days_in,
                    "max_drawdown_pct": round(max_drawdown_pct, 2),
                    "max_runup_pct":   round(max_runup_pct, 2),
                    "current_price":   t3,
                    "current_pnl_pct": round((t3 - cmp_rec) / cmp_rec * 100, 2),
                    "last_checked_date": today.strftime("%Y-%m-%d"),
                }
            if t2 > 0 and hi >= t2:
                return {
                    "outcome_type": "T2_HIT",
                    "outcome_date": d_str,
                    "outcome_price": t2,
                    "days_to_outcome": days_in,
                    "max_drawdown_pct": round(max_drawdown_pct, 2),
                    "max_runup_pct":   round(max_runup_pct, 2),
                    "current_price":   t2,
                    "current_pnl_pct": round((t2 - cmp_rec) / cmp_rec * 100, 2),
                    "last_checked_date": today.strftime("%Y-%m-%d"),
                }
            if t1 > 0 and hi >= t1:
                return {
                    "outcome_type": "T1_HIT",
                    "outcome_date": d_str,
                    "outcome_price": t1,
                    "days_to_outcome": days_in,
                    "max_drawdown_pct": round(max_drawdown_pct, 2),
                    "max_runup_pct":   round(max_runup_pct, 2),
                    "current_price":   t1,
                    "current_pnl_pct": round((t1 - cmp_rec) / cmp_rec * 100, 2),
                    "last_checked_date": today.strftime("%Y-%m-%d"),
                }

    # End of price data, no event, not yet expired → OPEN, refresh metrics
    return {
        "outcome_type": "OPEN",
        "outcome_date": "",
        "outcome_price": 0.0,
        "days_to_outcome": 0,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "max_runup_pct":   round(max_runup_pct, 2),
        "current_price":   last_close,
        "current_pnl_pct": round((last_close - cmp_rec) / cmp_rec * 100, 2),
        "last_checked_date": today.strftime("%Y-%m-%d"),
    }


def main():
    """Walk every OPEN recommendation forward and update outcomes."""
    print("=" * 70)
    print("v14.1 OUTCOME TRACKER — checking open recommendations")
    print("=" * 70)
    opens = get_open_recommendations()
    if not opens:
        print("No open recommendations to track.")
        return 0

    counts = {"SL_HIT": 0, "T1_HIT": 0, "T2_HIT": 0, "T3_HIT": 0,
              "EXPIRED": 0, "OPEN": 0, "ERROR": 0}
    approaching_expiry = 0   # v14.1: track stocks within 14 days of expiry
    print(f"Found {len(opens)} open recommendation(s). Walking forward...")
    print()
    for rec in opens:
        sym = rec["symbol"]
        rec_date = rec["recommendation_date"]
        # v14.1: surface horizon + expiry window in output
        horizon = str(rec.get("time_horizon", "") or "—")
        expiry_d = int(rec.get("expiry_days") or DEFAULT_EXPIRY_DAYS)
        try:
            r = _walk_forward(rec)
            ok = update_outcome(
                symbol=sym, recommendation_date=rec_date,
                outcome_type=r["outcome_type"],
                outcome_date=r["outcome_date"],
                outcome_price=r["outcome_price"],
                days_to_outcome=r["days_to_outcome"],
                max_drawdown_pct=r["max_drawdown_pct"],
                max_runup_pct=r["max_runup_pct"],
                current_price=r["current_price"],
                current_pnl_pct=r["current_pnl_pct"],
                last_checked_date=r["last_checked_date"],
            )
            counts[r["outcome_type"]] = counts.get(r["outcome_type"], 0) + 1
            tag = r["outcome_type"]
            if r["outcome_type"] != "OPEN":
                print(f"   {tag:<8} {sym:<14}  rec={rec_date}  "
                      f"day {r['days_to_outcome']:>3}/{expiry_d}  "
                      f"({horizon:<10})  "
                      f"@ ₹{r['outcome_price']:.2f}  "
                      f"P&L: {r['current_pnl_pct']:+.1f}%")
            else:
                # v14.1: compute days_left for approaching-expiry warning
                try:
                    rec_dt = datetime.strptime(rec_date, "%Y-%m-%d").date()
                    days_held = (datetime.now().date() - rec_dt).days
                    days_left = max(0, expiry_d - days_held)
                except (ValueError, TypeError):
                    days_left = expiry_d
                warning = ""
                if days_left <= 14:
                    warning = f"  ⚠ {days_left}d to expiry"
                    approaching_expiry += 1
                print(f"   OPEN     {sym:<14}  rec={rec_date}  "
                      f"day {days_held}/{expiry_d}  "
                      f"({horizon:<10})  "
                      f"now ₹{r['current_price']:.2f}  "
                      f"P&L: {r['current_pnl_pct']:+.1f}%  "
                      f"runup max: {r['max_runup_pct']:+.1f}%{warning}")
        except Exception as e:
            counts["ERROR"] += 1
            print(f"   ⚠️  ERROR on {sym}: {e}")

    print()
    print("=" * 70)
    print("Summary: " + "  ".join(f"{k}={v}" for k, v in counts.items() if v > 0))
    closed = counts["SL_HIT"] + counts["T1_HIT"] + counts["T2_HIT"] + counts["T3_HIT"] + counts["EXPIRED"]
    print(f"Closed this run: {closed}  ·  Still open: {counts['OPEN']}", end="")
    if approaching_expiry > 0:
        print(f"  ·  ⚠ {approaching_expiry} approaching expiry (≤14d)")
    else:
        print()
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())