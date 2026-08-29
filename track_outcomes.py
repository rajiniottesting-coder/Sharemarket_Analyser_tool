#!/usr/bin/env python3
"""
v14.0 — Gold-pick outcome tracker.

Walks daily_prices forward from each OPEN recommendation date and records
the first event that fires:
    SL_HIT   — daily low ≤ ORIGINAL stop_loss  (real loss; thesis failed)
    TRAIL_SL — daily low ≤ TRAILING stop after a favourable run
               (break-even / locked-profit exit; NOT a thesis failure —
               tracked separately so it doesn't pollute SL-rate stats)
    T3_HIT   — daily high ≥ T3
    T2_HIT   — daily high ≥ T2 (and not yet T3)
    T1_HIT   — daily high ≥ T1 (and not yet T2)
    EXPIRED  — 90 calendar days passed with no event

v17.0 trailing-stop recalibration: break-even threshold raised +10%→+12%
AND minimum 10-day holding gate before break-even activates (prevents
IGL/HEXT-class early exits on genuinely trending stocks). Tiers:
peak ≥25%→lock+12%, ≥20%→lock+9%, ≥15%→lock+5%,
peak ≥12% AND days_held≥10→break-even, else→no trailing (original SL only).

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
    # v17.3 — continuation tracking (observational; never writes gold_outcomes)
    get_t1_hits_needing_continuation,
    get_continuation_tracking,
    upsert_continuation,
    # v17.7 — shadow trailing stop (observational; writes only shadow_* cols)
    update_shadow_outcome,
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


# =============================================================================
# v17.7 - SHADOW REGIME-AWARE TRAILING STOP (OBSERVATIONAL ONLY)
#
# ISOLATION CONTRACT: _compute_shadow_stop is a PURE, READ-ONLY function. It
# never writes gold_outcomes / gold_recommendations and never mutates live-walk
# state. It only READS price history and returns shadow_* values, which the
# caller persists via update_shadow_outcome() (shadow_* columns only). The live
# exit classification is byte-identical whether this runs or not. G39 asserts it.
#
# MECHANISM (no fitted numbers): ATR via Wilder true-range; regime = current-ATR
# vs its own trailing baseline using the same 1.20/0.80 thresholds and +/-10%
# adjustment as the entry SL engine; Chandelier stop = peak_high - (N x ATR x
# regime_adj), N horizon-matched; ratchet UP only; no look-ahead (today low is
# tested against the stop as of the PRIOR bar, updated only after the check).
# =============================================================================
_SHADOW_N_BY_HORIZON = {"SHORT TERM": 2.5, "POSITIONAL": 3.0, "LONG TERM": 3.5}
_SHADOW_N_DEFAULT   = 3.0
_SHADOW_REGIME_HIGH = 1.20   # current ATR > 1.20x baseline -> high vol
_SHADOW_REGIME_LOW  = 0.80   # current ATR < 0.80x baseline -> low vol
_SHADOW_ADJ_HIGH    = 1.10   # high regime -> widen trail 10%
_SHADOW_ADJ_LOW     = 0.90   # low regime  -> tighten trail 10%
_SHADOW_ATR_PERIOD  = 14
_SHADOW_BASELINE    = 60


def _wilder_atr(highs, lows, closes, period=_SHADOW_ATR_PERIOD):
    """Wilder ATR series aligned to the input bars. atr[i] is None until there
    are `period` true-range values available. Read-only, no side effects."""
    n = len(closes)
    atr = [None] * n
    if n == 0:
        return atr
    trs = []
    prev_close = closes[0]
    for i in range(n):
        hi = highs[i]; lo = lows[i]; cl = closes[i]
        if i == 0:
            tr = (hi - lo) if (hi > 0 and lo > 0) else 0.0
        else:
            tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close)) if (hi > 0 and lo > 0) else 0.0
        trs.append(tr)
        prev_close = cl
        if i + 1 == period:
            atr[i] = sum(trs[:period]) / period          # seed = simple mean of first `period` TRs
        elif i + 1 > period:
            atr[i] = (atr[i - 1] * (period - 1) + tr) / period   # Wilder smoothing
    return atr


def _compute_shadow_stop(rec: dict) -> dict:
    """v17.7: regime-aware Chandelier shadow stop for one position. READ-ONLY.

    Returns a dict of shadow_* fields for update_shadow_outcome(), or {} on any
    problem. NEVER writes anything, NEVER touches live logic. See contract above.
    """
    try:
        sym      = rec["symbol"]
        rec_date = rec["recommendation_date"]
        cmp_rec  = float(rec.get("cmp_at_recommendation", 0) or 0)
        horizon  = str(rec.get("time_horizon", "") or "")
        expiry_days = int(rec.get("expiry_days") or DEFAULT_EXPIRY_DAYS)
        if expiry_days <= 0:
            expiry_days = DEFAULT_EXPIRY_DAYS
        if cmp_rec <= 0:
            return {}

        prices = _load_price_history(sym, rec_date)
        if prices is None or prices.empty:
            return {}

        rec_d = datetime.strptime(rec_date, "%Y-%m-%d").date()
        highs, lows, closes, dates = [], [], [], []
        for _, row in prices.iterrows():
            try:
                hi = float(row.get("high", 0) or 0)
                lo = float(row.get("low", 0) or 0)
                cl = float(row.get("close", 0) or 0)
            except (TypeError, ValueError):
                continue
            if cl <= 0:
                continue
            d_str = str(row["date"])[:10]
            try:
                d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if (d_obj - rec_d).days > expiry_days:
                break
            highs.append(hi); lows.append(lo); closes.append(cl); dates.append(d_str)

        if not closes:
            return {}

        atr_series = _wilder_atr(highs, lows, closes)
        N = _SHADOW_N_BY_HORIZON.get(horizon.upper(), _SHADOW_N_DEFAULT)

        peak = cmp_rec
        shadow_stop = 0.0          # 0 = not yet armed
        shadow_regime = "normal"

        for i in range(len(closes)):
            hi = highs[i]; lo = lows[i]

            # SL CHECK FIRST against the stop ratcheted at the PRIOR bar
            # (no look-ahead), only once armed above 0.
            if shadow_stop > 0 and lo > 0 and lo <= shadow_stop:
                return {
                    "shadow_peak_price": round(peak, 2),
                    "shadow_stop_price": round(shadow_stop, 2),
                    "shadow_regime":     shadow_regime,
                    "shadow_status":     "TRAIL_SL",
                    "shadow_exit_price": round(shadow_stop, 2),
                    "shadow_exit_date":  dates[i],
                    "shadow_pnl_pct":    round((shadow_stop - cmp_rec) / cmp_rec * 100, 2),
                }

            # ratchet the peak with today
            if hi > peak:
                peak = hi

            atr_now = atr_series[i]
            if atr_now is None or atr_now <= 0:
                continue           # not enough bars for ATR yet

            # regime = current ATR vs its own trailing baseline
            base_lo = max(0, i - _SHADOW_BASELINE + 1)
            base_vals = [a for a in atr_series[base_lo:i + 1] if a is not None and a > 0]
            baseline = (sum(base_vals) / len(base_vals)) if base_vals else atr_now
            ratio = (atr_now / baseline) if baseline > 0 else 1.0
            if ratio >= _SHADOW_REGIME_HIGH:
                regime_adj = _SHADOW_ADJ_HIGH;  shadow_regime = "high"
            elif ratio <= _SHADOW_REGIME_LOW:
                regime_adj = _SHADOW_ADJ_LOW;   shadow_regime = "low"
            else:
                regime_adj = 1.0;               shadow_regime = "normal"

            candidate = peak - (N * atr_now * regime_adj)
            if candidate > shadow_stop:        # ratchet UP only
                shadow_stop = candidate

        # No shadow exit — still open. Report live watch state.
        return {
            "shadow_peak_price": round(peak, 2),
            "shadow_stop_price": round(shadow_stop, 2) if shadow_stop > 0 else 0.0,
            "shadow_regime":     shadow_regime,
            "shadow_status":     "OPEN",
            "shadow_exit_price": 0.0,
            "shadow_exit_date":  "",
            "shadow_pnl_pct":    0.0,
        }
    except Exception as _e:
        print(f"   ⚠️  _compute_shadow_stop({rec.get('symbol','?')}): {_e}")
        return {}

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
            # v16.0: DD-duration defaults — this branch fires BEFORE we
            # initialize the tracking state, so use literal defaults.
            "dd_duration_days": 0,
            "dd_recovered":     1,
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

    # ─────────────────────────────────────────────────────────────────────
    # v16.0: Max DD Duration tracking (Item 2)
    # Track LONGEST consecutive run of days the position spent underwater
    # (close ≤ entry CMP). Reset the running counter every time the close
    # recovers back to entry. The MAX of these consecutive-runs is the
    # institutional "time-under-water" metric.
    #
    #   underwater_run_days : current consecutive days underwater (reset on recovery)
    #   max_dd_duration_days: largest underwater_run_days seen so far
    #   dd_recovered        : did the position recover from its longest DD
    #                         before close? Vacuously True if it never went underwater.
    # ─────────────────────────────────────────────────────────────────────
    underwater_run_days  = 0
    max_dd_duration_days = 0
    dd_recovered         = 1   # default: vacuously true (never went underwater)
    # Once we've seen ANY drawdown day, dd_recovered defaults to 1 if we
    # later see a recovery, else flips to 0 at close. Track via a state var:
    in_drawdown          = False  # are we currently underwater?

    # ─────────────────────────────────────────────────────────────────────
    # Trailing stop tracking (v16.5 recalibrated tiers)
    # Peak price seen so far drives the trailing-SL ratchet:
    #   peak_gain ≥ +25% → trailing_sl = entry + 12%
    #   peak_gain ≥ +20% → trailing_sl = entry + 9%
    #   peak_gain ≥ +15% → trailing_sl = entry + 5%
    #   peak_gain ≥ +10% → trailing_sl = entry (break-even)
    #   peak_gain < +10% → NO trailing stop (original_sl still protects)
    # Effective SL = max(original_sl, trailing_sl_price) — only ratchets UP.
    # Once activated, trailing SL never moves down even if peak retraces.
    # A trailing-stop exit returns outcome_type=TRAIL_SL (not SL_HIT).
    # ─────────────────────────────────────────────────────────────────────
    peak_price_seen  = cmp_rec
    trailing_sl_price = 0.0    # 0 = not activated
    trailing_sl_pct   = 0.0    # negative = below entry, positive = above entry
    original_sl = sl           # preserve for audit; sl variable becomes effective SL

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
                # v16.0: DD-duration defaults (no prices → no DD measured)
                "dd_duration_days": max_dd_duration_days,
                "dd_recovered":     dd_recovered,
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
            # v16.0: DD-duration defaults (no prices → 0/1)
            "dd_duration_days": max_dd_duration_days,
            "dd_recovered":     dd_recovered,
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
                # v15.0: trailing-stop snapshot
                "trailing_sl_pct":   trailing_sl_pct,
                "trailing_sl_price": trailing_sl_price,
                "peak_price_seen":   peak_price_seen,
                # v16.0: DD-duration metrics (Item 2)
                "dd_duration_days": max_dd_duration_days,
                "dd_recovered":     dd_recovered,
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

            # ──────────────────────────────────────────────────────────
            # v16.0: DD-duration state-machine update (Item 2)
            # If today's CLOSE ≤ entry CMP → still underwater (or just dropped).
            # If today's CLOSE > entry CMP → recovered (reset the counter).
            # We track this with the *close*, not high/low, because:
            #   - It's the institutional convention (close-to-close DD)
            #   - It avoids double-counting same-day intra-day dips
            #   - It's consistent with end-of-bar no-lookahead discipline
            # ──────────────────────────────────────────────────────────
            if cl <= cmp_rec:
                # underwater today
                if not in_drawdown:
                    in_drawdown = True
                    underwater_run_days = 1
                else:
                    underwater_run_days += 1
                if underwater_run_days > max_dd_duration_days:
                    max_dd_duration_days = underwater_run_days
                # If we're currently in a drawdown that's the longest one,
                # mark dd_recovered=0 — it'll get flipped back to 1 if we
                # recover later (next branch).
                if underwater_run_days == max_dd_duration_days:
                    dd_recovered = 0
            else:
                # recovered or never went underwater
                if in_drawdown:
                    # We've recovered from a drawdown — set flag accordingly.
                    # If THIS recovery is from the longest DD we've seen,
                    # mark dd_recovered=1.
                    dd_recovered = 1
                in_drawdown = False
                underwater_run_days = 0

        # v15.0: Effective SL = MAX(original_sl, trailing_sl_at_start_of_day)
        # CRITICAL: trailing SL is checked against TODAY's low using the value
        # ratcheted at end-of-PREVIOUS-day. We cannot use today's high to set
        # trailing SL and then check today's low against it — intraday order
        # (high-first vs low-first) is unknowable from daily OHLC, so this
        # would be look-ahead bias. Therefore: SL check uses previous-day
        # trailing_sl_price; trailing update happens AFTER event checks.
        effective_sl = max(original_sl, trailing_sl_price) if trailing_sl_price > 0 else original_sl

        # ─── Event check — SL beats target on same-day ties ───
        # SL hit: daily low touched/breached EFFECTIVE SL (trailing-aware)
        #
        # v16.5: distinguish two fundamentally different exit types:
        #   • SL_HIT    — price broke the ORIGINAL stop loss (a real loss;
        #                 the trade thesis failed). Counts against SL-rate.
        #   • TRAIL_SL  — price pulled back into the TRAILING stop after a
        #                 favourable run (break-even or locked-in-profit
        #                 exit). This is NOT a thesis failure — it's the
        #                 system protecting gains. Tracked separately so it
        #                 doesn't pollute the SL-rate / hit-rate statistics.
        #
        # The discriminator: if effective_sl came from the trailing ratchet
        # (trailing_sl_price active AND ≥ original_sl), it's a TRAIL_SL.
        # Otherwise the original SL was breached → genuine SL_HIT.
        if lo > 0 and lo <= effective_sl:
            _is_trailing_exit = (
                trailing_sl_price > 0 and
                trailing_sl_price >= original_sl and
                effective_sl == trailing_sl_price
            )
            _exit_type = "TRAIL_SL" if _is_trailing_exit else "SL_HIT"
            return {
                "outcome_type": _exit_type,
                "outcome_date": d_str,
                "outcome_price": effective_sl,
                "days_to_outcome": days_in,
                "max_drawdown_pct": round(max_drawdown_pct, 2),
                "max_runup_pct":   round(max_runup_pct, 2),
                "current_price":   effective_sl,
                "current_pnl_pct": round((effective_sl - cmp_rec) / cmp_rec * 100, 2),
                "last_checked_date": today.strftime("%Y-%m-%d"),
                # v15.0: persist trailing snapshot
                "trailing_sl_pct":   trailing_sl_pct,
                "trailing_sl_price": trailing_sl_price,
                "peak_price_seen":   peak_price_seen,
                # v16.0: DD-duration metrics (Item 2)
                "dd_duration_days": max_dd_duration_days,
                "dd_recovered":     dd_recovered,
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
                # v15.0: trailing-stop snapshot
                "trailing_sl_pct":   trailing_sl_pct,
                "trailing_sl_price": trailing_sl_price,
                "peak_price_seen":   peak_price_seen,
                # v16.0: DD-duration metrics (Item 2)
                "dd_duration_days": max_dd_duration_days,
                "dd_recovered":     dd_recovered,
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
                # v15.0: trailing-stop snapshot
                "trailing_sl_pct":   trailing_sl_pct,
                "trailing_sl_price": trailing_sl_price,
                "peak_price_seen":   peak_price_seen,
                # v16.0: DD-duration metrics (Item 2)
                "dd_duration_days": max_dd_duration_days,
                "dd_recovered":     dd_recovered,
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
                    # v15.0: trailing-stop snapshot
                    "trailing_sl_pct":   trailing_sl_pct,
                    "trailing_sl_price": trailing_sl_price,
                    "peak_price_seen":   peak_price_seen,
                    # v16.0: DD-duration metrics (Item 2)
                    "dd_duration_days": max_dd_duration_days,
                    "dd_recovered":     dd_recovered,
                }

        # v15.0: Trailing-SL update at END of bar (after event checks).
        # This guarantees no look-ahead bias — today's high cannot tighten
        # today's stop and then have today's low be checked against it.
        # The ratcheted trailing_sl_price will be used on the NEXT bar.
        #
        # v17.0 Fix 5 — TRAIL_SL refinement:
        #   (a) Break-even activation raised from +10% → +12%
        #   (b) Minimum 10-day holding before break-even can fire
        #
        # IMPORTANT: trailing is re-evaluated EVERY bar using the running peak,
        # not just on new-peak days. This handles the case where a stock peaked
        # at +13% on day 6, but the 10-day gate only clears on day 10 — the
        # break-even protection should activate on day 10 even though no new
        # high was set that day. Profit-lock tiers (≥15%) have no day gate.
        #
        # Tier structure:
        #   peak ≥ 25% → lock +12%    (no day gate — large gain worth protecting)
        #   peak ≥ 20% → lock +9%     (no day gate)
        #   peak ≥ 15% → lock +5%     (no day gate)
        #   peak ≥ 12% AND days_in ≥ 10 → break-even  (day gate applies)
        #   peak < 12% OR days_in < 10  → no trailing stop
        _TRAIL_BREAKEVEN_THRESHOLD = 12.0   # v17.0: was 10%
        _TRAIL_MIN_HOLDING_DAYS    = 10     # v17.0: min holding before break-even

        # Step 1: Update peak (only when a new high is made)
        if hi > 0 and hi > peak_price_seen:
            peak_price_seen = hi

        # Step 2: Re-evaluate trailing on EVERY bar using current peak
        # (not just on new-peak bars — enables day-crossing for the BE gate)
        if peak_price_seen > cmp_rec:
            peak_gain_pct = (peak_price_seen - cmp_rec) / cmp_rec * 100
            new_trailing = 0.0
            if peak_gain_pct >= 25:
                new_trailing = round(cmp_rec * 1.12, 2)   # lock in +12%, no day gate
            elif peak_gain_pct >= 20:
                new_trailing = round(cmp_rec * 1.09, 2)   # lock in +9%, no day gate
            elif peak_gain_pct >= 15:
                new_trailing = round(cmp_rec * 1.05, 2)   # lock in +5%, no day gate
            elif (peak_gain_pct >= _TRAIL_BREAKEVEN_THRESHOLD
                  and days_in >= _TRAIL_MIN_HOLDING_DAYS):
                new_trailing = round(cmp_rec * 1.00, 2)   # break-even (day-gated)
            # Trailing SL only ratchets UP — never moves down
            if new_trailing > trailing_sl_price:
                trailing_sl_price = new_trailing
                trailing_sl_pct   = round((new_trailing - cmp_rec) / cmp_rec * 100, 2)

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
        # v15.0: trailing-stop snapshot
        "trailing_sl_pct":   trailing_sl_pct,
        "trailing_sl_price": trailing_sl_price,
        "peak_price_seen":   peak_price_seen,
        # v16.0: DD-duration metrics (Item 2)
        "dd_duration_days": max_dd_duration_days,
        "dd_recovered":     dd_recovered,
    }


# ═════════════════════════════════════════════════════════════════════════
# v17.3 — CONTINUATION TRACKING (Option C, expiry-anchored)
#
# The problem: this tracker is first-event-wins. Once T1_HIT is written the
# position leaves the OPEN pool and is never walked again, so T2/T3 are
# structurally unreachable (a stock would have to leap from below T1 to
# above T2 within one bar — median 13.9pp). The T2/T3 tiles are permanent
# zeros and always will be.
#
# The fix, without touching any of the above: a SHADOW WALK that starts the
# day after T1 and runs to the position's ORIGINAL expiry date, recording
# what the stock did next. It answers "was T1 a good exit, or lucky timing?"
#
# INVARIANTS (regression test G37 enforces these):
#   · gold_outcomes is READ-ONLY — no write of any kind from this block
#   · existing hit rate / SL rate / P&L are byte-identical before and after
#   · every failure is swallowed; the tracker's exit code never changes
# ═════════════════════════════════════════════════════════════════════════

def _seed_continuation() -> int:
    """Create gold_continuation rows for T1 hits that don't have one yet.

    Runs BEFORE the walk. On first deploy this backfills every historical
    T1 hit in one pass — no manual migration, which matters because
    market_data.db is a 641 MB GitHub Actions artifact.

    Returns the number of rows seeded.

    expiry_date resolution, in order:
        1. r.expiry_date if non-empty
        2. recommendation_date + expiry_days (defaults 90)
        3. skip the row and log — never guess
    """
    seeded = 0
    for rec in get_t1_hits_needing_continuation():
        sym      = rec.get("symbol", "")
        rec_date = rec.get("recommendation_date", "")
        t1_date  = str(rec.get("t1_hit_date", "") or "")
        if not t1_date:
            print(f"   ⚠️  continuation seed skipped {sym}: no T1 hit date")
            continue

        expiry_date = str(rec.get("expiry_date", "") or "")
        if not expiry_date:
            try:
                _rd = datetime.strptime(rec_date, "%Y-%m-%d").date()
                _ed = int(rec.get("expiry_days") or DEFAULT_EXPIRY_DAYS)
                expiry_date = (_rd + timedelta(days=_ed)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                print(f"   ⚠️  continuation seed skipped {sym}: unresolvable expiry")
                continue

        try:
            _t1d = datetime.strptime(t1_date, "%Y-%m-%d").date()
            _exd = datetime.strptime(expiry_date, "%Y-%m-%d").date()
            days_remaining = max(0, (_exd - _t1d).days)
        except (ValueError, TypeError):
            print(f"   ⚠️  continuation seed skipped {sym}: bad date format")
            continue

        t1_price = float(rec.get("t1_hit_price", 0) or 0)
        ok = upsert_continuation({
            "symbol": sym,
            "recommendation_date": rec_date,
            "t1_hit_date": t1_date,
            "t1_hit_price": t1_price,
            "expiry_date": expiry_date,
            "days_remaining_at_t1": days_remaining,
            "t2_target": float(rec.get("t2_target", 0) or 0),
            "t3_target": float(rec.get("t3_target", 0) or 0),
            "original_sl": float(rec.get("original_sl", 0) or 0),
            # Baseline the peak/trough at the T1 price so a position that
            # never trades again still reports 0.0% rather than −100%.
            "peak_price_after_t1":   t1_price,
            "trough_price_after_t1": t1_price,
            "status": "TRACKING",
            "last_checked_date": "",
        })
        if ok:
            seeded += 1
    return seeded


def _walk_continuation(row: dict) -> dict:
    """Shadow-walk one position from t1_hit_date+1 to min(expiry_date, today).

    Reuses _load_price_history(), whose WHERE clause is `date > start_date`
    — strictly greater — so passing t1_hit_date gives us "start the day
    after T1" for free, with the exchange='NSE' filter already applied.

    Per bar:
        high ≥ t3  → record t3 reach (date + days after T1), once only
        high ≥ t2  → record t2 reach, once only
        high > peak / low < trough → update running extremes
        low  ≤ original_sl → broke_original_sl = 1

    The walk does NOT stop on an SL break. A stock can dip below the old SL
    and still rally to T2 before expiry; stopping early would hide that.
    Both flags can legitimately be 1 on the same row.

    status flips to 'COMPLETE' once today is past expiry_date, at which
    point final_price_at_expiry / final_pct_vs_t1 are frozen.
    """
    out = dict(row)
    sym      = row.get("symbol", "")
    t1_date  = str(row.get("t1_hit_date", "") or "")
    t1_price = float(row.get("t1_hit_price", 0) or 0)
    t2       = float(row.get("t2_target", 0) or 0)
    t3       = float(row.get("t3_target", 0) or 0)
    sl       = float(row.get("original_sl", 0) or 0)
    expiry   = str(row.get("expiry_date", "") or "")
    today    = datetime.now().date()

    if not t1_date or t1_price <= 0 or not expiry:
        out["status"] = "COMPLETE"
        out["last_checked_date"] = today.strftime("%Y-%m-%d")
        return out

    try:
        t1_d     = datetime.strptime(t1_date, "%Y-%m-%d").date()
        expiry_d = datetime.strptime(expiry, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        out["status"] = "COMPLETE"
        out["last_checked_date"] = today.strftime("%Y-%m-%d")
        return out

    # Carry prior state forward — a reach recorded on an earlier run is
    # never un-recorded, even if this run's price window is empty.
    t2_reached = int(row.get("t2_reached", 0) or 0)
    t3_reached = int(row.get("t3_reached", 0) or 0)
    t2_date    = str(row.get("t2_reached_date", "") or "")
    t3_date    = str(row.get("t3_reached_date", "") or "")
    t2_days    = int(row.get("t2_days_after_t1", 0) or 0)
    t3_days    = int(row.get("t3_days_after_t1", 0) or 0)
    broke_sl   = int(row.get("broke_original_sl", 0) or 0)
    peak       = float(row.get("peak_price_after_t1", 0) or 0) or t1_price
    trough     = float(row.get("trough_price_after_t1", 0) or 0) or t1_price
    last_close = t1_price

    prices = _load_price_history(sym, t1_date)
    for _, bar in prices.iterrows():
        bdate = str(bar["date"])[:10]
        try:
            bd = datetime.strptime(bdate, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        # HARD expiry boundary — bars past the position's own expiry are
        # not part of the runway its recommendation promised.
        if bd > expiry_d:
            break

        hi = float(bar["high"] or 0)
        lo = float(bar["low"] or 0)
        cl = float(bar["close"] or 0)
        days_after = (bd - t1_d).days

        if t3 > 0 and hi >= t3 and not t3_reached:
            t3_reached, t3_date, t3_days = 1, bdate, days_after
        if t2 > 0 and hi >= t2 and not t2_reached:
            t2_reached, t2_date, t2_days = 1, bdate, days_after
        if hi > peak:
            peak = hi
        if lo > 0 and lo < trough:
            trough = lo
        if sl > 0 and lo > 0 and lo <= sl:
            broke_sl = 1
        if cl > 0:
            last_close = cl

    out["t2_reached"]            = t2_reached
    out["t2_reached_date"]       = t2_date
    out["t2_days_after_t1"]      = t2_days
    out["t3_reached"]            = t3_reached
    out["t3_reached_date"]       = t3_date
    out["t3_days_after_t1"]      = t3_days
    out["peak_price_after_t1"]   = round(peak, 2)
    out["peak_pct_after_t1"]     = round((peak - t1_price) / t1_price * 100, 2)
    out["trough_price_after_t1"] = round(trough, 2)
    out["trough_pct_after_t1"]   = round((trough - t1_price) / t1_price * 100, 2)
    out["broke_original_sl"]     = broke_sl
    out["last_checked_date"]     = today.strftime("%Y-%m-%d")

    if today > expiry_d:
        out["status"]                = "COMPLETE"
        out["final_price_at_expiry"] = round(last_close, 2)
        out["final_pct_vs_t1"]       = round((last_close - t1_price) / t1_price * 100, 2)
    else:
        out["status"]                = "TRACKING"
        out["final_price_at_expiry"] = 0.0
        out["final_pct_vs_t1"]       = 0.0
    return out


def run_continuation_tracking() -> dict:
    """v17.3: seed then walk. Called at the END of main().

    Wrapped so that any failure here can never abort the outcome tracker —
    the daily pipeline's hit-rate numbers matter more than this diagnostic.
    Returns a small summary dict for the console line.
    """
    summary = {"seeded": 0, "walked": 0, "complete": 0, "t2": 0, "t3": 0, "sl_break": 0}
    try:
        summary["seeded"] = _seed_continuation()
        for row in get_continuation_tracking():
            res = _walk_continuation(row)
            if upsert_continuation(res):
                summary["walked"] += 1
                if res.get("status") == "COMPLETE":
                    summary["complete"] += 1
                if int(res.get("t2_reached", 0) or 0):
                    summary["t2"] += 1
                if int(res.get("t3_reached", 0) or 0):
                    summary["t3"] += 1
                if int(res.get("broke_original_sl", 0) or 0):
                    summary["sl_break"] += 1
    except Exception as e:
        print(f"   ⚠️  continuation tracking failed (non-fatal): {e}")
    return summary


def main():
    """Walk every OPEN recommendation forward and update outcomes."""
    print("=" * 70)
    print("v14.1 OUTCOME TRACKER — checking open recommendations")
    print("=" * 70)
    opens = get_open_recommendations()
    if not opens:
        print("No open recommendations to track.")
        # v17.3: closed positions can still have live continuation runways
        # (a T1 hit on day 5 of 90 has 85 days left to walk), so the shadow
        # walk must run even when the OPEN pool is empty.
        _c = run_continuation_tracking()
        if _c["seeded"] or _c["walked"]:
            print(f"Continuation: seeded {_c['seeded']}  ·  walked {_c['walked']}")
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
                # v15.0: trailing-stop persistence
                trailing_sl_pct=r.get("trailing_sl_pct", 0) or 0,
                trailing_sl_price=r.get("trailing_sl_price", 0) or 0,
                peak_price_seen=r.get("peak_price_seen", 0) or 0,
                # v16.0: DD-duration persistence (Item 2)
                dd_duration_days=r.get("dd_duration_days", 0) or 0,
                dd_recovered=r.get("dd_recovered", 1) or 0,
            )
            # v17.7: SHADOW regime-aware trailing stop — OBSERVATIONAL ONLY.
            # Computed AFTER the live update above, writes ONLY shadow_* columns
            # via update_shadow_outcome(). Fully wrapped: any shadow failure is
            # non-fatal and can never affect the live tracker result.
            try:
                _sh = _compute_shadow_stop(rec)
                if _sh:
                    update_shadow_outcome(
                        symbol=sym, recommendation_date=rec_date,
                        shadow_peak_price=_sh.get("shadow_peak_price", 0),
                        shadow_stop_price=_sh.get("shadow_stop_price", 0),
                        shadow_regime=_sh.get("shadow_regime", ""),
                        shadow_status=_sh.get("shadow_status", "OPEN"),
                        shadow_exit_price=_sh.get("shadow_exit_price", 0),
                        shadow_exit_date=_sh.get("shadow_exit_date", ""),
                        shadow_pnl_pct=_sh.get("shadow_pnl_pct", 0),
                    )
            except Exception as _she:
                print(f"   ⚠️  shadow skipped for {sym} (non-fatal): {_she}")
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
    # v17.3 FIX: TRAIL_SL was omitted from this sum, so a run that closed
    # positions via trailing stop under-reported "Closed this run" on the
    # console. The DB and the Excel were always correct — this was a
    # display-only defect introduced when TRAIL_SL was split out of SL_HIT
    # in v16.5. counts.get() is used because TRAIL_SL is not pre-seeded in
    # the counts dict initialiser.
    closed = (counts["SL_HIT"] + counts.get("TRAIL_SL", 0) + counts["T1_HIT"]
              + counts["T2_HIT"] + counts["T3_HIT"] + counts["EXPIRED"])
    print(f"Closed this run: {closed}  ·  Still open: {counts['OPEN']}", end="")
    if approaching_expiry > 0:
        print(f"  ·  ⚠ {approaching_expiry} approaching expiry (≤14d)")
    else:
        print()
    print("=" * 70)

    # ── v17.3: continuation shadow walk ───────────────────────────────────
    # Runs LAST, after every outcome is committed. Observational only —
    # gold_outcomes is not touched, and a failure here cannot change the
    # tracker's return code.
    _c = run_continuation_tracking()
    if _c["seeded"] or _c["walked"]:
        print(f"CONTINUATION AUDIT — seeded {_c['seeded']}  ·  walked {_c['walked']}  ·  "
              f"complete {_c['complete']}  ·  T2 reached {_c['t2']}  ·  "
              f"T3 reached {_c['t3']}  ·  broke old SL {_c['sl_break']}")
        print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())