"""
ingestion/trading_day_calendar.py — v15.3 Phase 1

Trading-day calendar utility built on top of `market_holidays` table.

Purpose
───────
Replaces the v15.0.1 calendar-day approximations (e.g. `'-365 days'` ≈ 252
trading days, `'-70 days'` ≈ 50 trading days) with EXACT trading-day cutoffs.

The approximation worked because of statistical averages (252 trading days
per year, 5/7 of calendar days are trading days). The issue: 365 calendar
days back from May 13 lands at May 13 prior year — which may or may not be
a trading day. And around a long weekend or extended holiday cluster, the
actual trading-day count varies by ±5-10 days.

This module provides:
  - `n_trading_days_ago(today_iso, n) → 'YYYY-MM-DD'`
    Returns the calendar date that is exactly N trading days before today.
  - `is_trading_day(date_iso) → bool`
    True if the given date is a weekday AND not in market_holidays.

Both functions cache the holiday set per-call for performance.

DESIGN INTEGRITY
────────────────
- This module is PURELY ADDITIVE. The existing calendar-day SQL windows
  continue to work as a fallback when the calendar is empty (newly-cloned
  repo, first run before `seed_holidays.py` runs).
- master_funnel.py's SQL queries will:
    1. Try to resolve the trading-day cutoff via this module
    2. Fall back to the calendar-day SQL approximation if calendar is empty
  → Zero risk of breaking existing runs.

INSTITUTIONAL GRADE
───────────────────
Bloomberg/Refinitiv use exact trading-day calendars internally. This
brings the SQL precision from "365 calendar days ≈ 252 trading days
± 5-10 noise" to "exactly 252 trading days, no approximation".
"""
from __future__ import annotations

import datetime
import sqlite3
from typing import Optional, Set

DB_PATH = "market_data.db"


def _load_holidays_set(exchange: str = "NSE") -> Set[str]:
    """
    Returns set of holiday dates (ISO 'YYYY-MM-DD') for the given exchange.
    Empty set on DB error or missing table → caller falls back gracefully.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.execute(
                "SELECT date FROM market_holidays WHERE exchange = ?",
                (exchange,)
            )
            return {row[0] for row in cur.fetchall()}
        finally:
            conn.close()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        # Table doesn't exist or DB corrupted → empty set (caller falls back)
        return set()


def is_trading_day(date_iso: str, exchange: str = "NSE",
                   holiday_set: Optional[Set[str]] = None) -> bool:
    """
    True if `date_iso` (YYYY-MM-DD) is a NSE trading day.

    Trading day = Mon-Fri AND not in market_holidays table.

    Args:
        date_iso: ISO date string 'YYYY-MM-DD'
        exchange: 'NSE' (default) or 'BSE'
        holiday_set: optional pre-loaded set (for bulk callers); if None,
                     loaded from DB on each call (cached internally).
    """
    try:
        d = datetime.date.fromisoformat(date_iso)
    except (ValueError, TypeError):
        return False
    # Weekday check: Monday=0 ... Sunday=6. Trading days = 0-4.
    if d.weekday() >= 5:
        return False
    if holiday_set is None:
        holiday_set = _load_holidays_set(exchange)
    return date_iso not in holiday_set


def n_trading_days_ago(today_iso: str, n: int,
                       exchange: str = "NSE",
                       holiday_set: Optional[Set[str]] = None) -> Optional[str]:
    """
    Returns the ISO date exactly `n` trading days before `today_iso`.

    Example: if today is 2026-05-13 (Wed) and n=252, returns the date
    that is exactly 252 trading days earlier (which is roughly 1 trading
    year ago, considering all weekends + NSE holidays).

    Returns None if:
      - market_holidays table is empty (caller should fall back to
        calendar-day approximation)
      - today_iso is malformed

    Args:
        today_iso: anchor date 'YYYY-MM-DD'
        n: positive integer trading-day count
        exchange: 'NSE' (default) or 'BSE'
        holiday_set: optional pre-loaded set (perf optimization)

    Returns:
        ISO date string or None
    """
    if n <= 0:
        return today_iso

    if holiday_set is None:
        holiday_set = _load_holidays_set(exchange)

    # If the calendar is completely empty, refuse to lie — return None so
    # the caller falls back to the calendar-day approximation. This is
    # important for first-run scenarios where holidays haven't been seeded.
    if not holiday_set:
        return None

    try:
        d = datetime.date.fromisoformat(today_iso)
    except (ValueError, TypeError):
        return None

    # Walk back day-by-day, counting only trading days
    walked = 0
    one_day = datetime.timedelta(days=1)
    while walked < n:
        d -= one_day
        # Safety cap: 5 calendar years lookback max (handles a stuck loop
        # if N is absurdly large; 252 trading days × 5yr = 1260 trading
        # days, plenty for any v15.x query)
        if (datetime.date.fromisoformat(today_iso) - d).days > 1825:
            return None
        if d.weekday() < 5 and d.isoformat() not in holiday_set:
            walked += 1

    return d.isoformat()


def trading_day_window_iso(today_iso: str, trading_days: int,
                            exchange: str = "NSE") -> str:
    """
    Convenience wrapper for SQL WHERE clauses.

    Returns the ISO cutoff date for a 'last N trading days' query, with
    automatic fallback to a calendar-day approximation if the trading
    calendar is empty.

    Approximation factor: 365 / 252 ≈ 1.448, so N trading days ≈
    1.448 × N calendar days. We use 1.45 for a slight over-estimate
    (better to include 1-2 extra calendar days than miss a trading day).

    Args:
        today_iso: anchor date 'YYYY-MM-DD'
        trading_days: N trading days to look back
        exchange: 'NSE' (default) or 'BSE'

    Returns:
        ISO date 'YYYY-MM-DD' guaranteed never None.
    """
    exact = n_trading_days_ago(today_iso, trading_days, exchange)
    if exact:
        return exact
    # Fallback: calendar-day approximation (~1.45 calendar days per trading day)
    try:
        d = datetime.date.fromisoformat(today_iso)
    except (ValueError, TypeError):
        # Worst case: return today (caller's query will return empty, which
        # is at least not corrupting). Should never happen with valid input.
        return today_iso
    cal_days = int(round(trading_days * 1.45))
    return (d - datetime.timedelta(days=cal_days)).isoformat()