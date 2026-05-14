"""
analysis/survivorship_audit.py — v16.0 Survivorship Bias Audit
================================================================

Detects and reports the institutional concern of "survivorship bias":
stocks that were OPEN positions in our tracker but have disappeared from
the current universe (e.g., delisted, suspended, symbol changed) and
therefore are no longer receiving price updates.

If we silently dropped these from our performance statistics, our
reported hit rates and risk metrics would be biased upward (only
"survivor" stocks counted) — a classic institutional pitfall.

WHAT THIS MODULE DOES
─────────────────────
1. Pulls the list of currently-OPEN gold_recommendations
2. Cross-checks each symbol against the current universe (latest_analysis_results)
3. Flags any OPEN symbol that's NOT in today's universe as "stale"
4. Reports counts: total OPEN, present-in-universe, stale, freshness ratio

This is INFORMATIONAL only — we don't auto-close or delete stale positions.
Instead, the audit surfaces them so a human can investigate (was the
symbol delisted? renamed? a temporary trading halt?).

USAGE
─────
    from analysis.survivorship_audit import audit_open_positions

    result = audit_open_positions()
    # → {
    #     "n_open_total": 5,
    #     "n_present_in_universe": 5,
    #     "n_stale": 0,
    #     "stale_symbols": [],
    #     "freshness_pct": 100.0,
    #   }

OUTPUT
──────
A small audit line is rendered in the Performance sheet so the user
sees the survivorship status every run.

INSTITUTIONAL CONTEXT
─────────────────────
Top-tier quant systems formally test for survivorship bias. This module
is a free-tier approximation: instead of maintaining a perpetual
delisting log (which requires paid data feeds), we use "did the symbol
trade today" as a freshness proxy. A delisted stock won't be in
today's bhavcopy → won't be in latest_analysis_results → flagged stale.
"""
import sqlite3
from typing import Dict, Any, List


def audit_open_positions(db_path: str = "market_data.db") -> Dict[str, Any]:
    """Run a survivorship audit on all currently-OPEN gold_recommendations.

    Returns a dict:
        n_open_total            : int — total OPEN positions
        n_present_in_universe   : int — symbols still trading today
        n_stale                 : int — symbols missing from today's universe
        stale_symbols           : list — names of the stale positions
        stale_details           : list of dicts — symbol + rec_date + days_held
        freshness_pct           : float — (present / total) × 100
        audit_status            : str  — 'CLEAN' / 'STALE_FOUND' / 'NO_OPEN_POSITIONS'

    Safe on empty / missing tables — returns sensible defaults.
    """
    out = {
        "n_open_total": 0,
        "n_present_in_universe": 0,
        "n_stale": 0,
        "stale_symbols": [],
        "stale_details": [],
        "freshness_pct": 100.0,
        "audit_status": "NO_OPEN_POSITIONS",
    }
    try:
        conn = sqlite3.connect(db_path)
        try:
            c = conn.cursor()
            # Get all OPEN gold positions with rec_date for context
            c.execute("""
                SELECT r.symbol, r.recommendation_date
                FROM gold_recommendations r
                INNER JOIN gold_outcomes o
                  ON r.symbol = o.symbol
                  AND r.recommendation_date = o.recommendation_date
                WHERE o.outcome_type = 'OPEN'
            """)
            open_rows = c.fetchall()
            if not open_rows:
                return out

            # Get current universe
            try:
                c.execute("SELECT DISTINCT symbol FROM latest_analysis_results")
                universe = set(row[0] for row in c.fetchall() if row[0])
            except sqlite3.OperationalError:
                # No latest_analysis_results table yet — can't audit. Return
                # informative status rather than crashing.
                out["audit_status"] = "UNIVERSE_UNAVAILABLE"
                out["n_open_total"] = len(open_rows)
                return out

            # Cross-check each OPEN position
            stale = []
            from datetime import datetime
            today = datetime.now().date()
            for sym, rec_date in open_rows:
                if sym not in universe:
                    # compute days_held for context
                    try:
                        rec_d = datetime.strptime(rec_date, "%Y-%m-%d").date()
                        days_held = (today - rec_d).days
                    except (ValueError, TypeError):
                        days_held = -1
                    stale.append({
                        "symbol": sym,
                        "rec_date": rec_date,
                        "days_held": days_held,
                    })

            n_total = len(open_rows)
            n_stale = len(stale)
            n_present = n_total - n_stale
            out["n_open_total"] = n_total
            out["n_present_in_universe"] = n_present
            out["n_stale"] = n_stale
            out["stale_symbols"] = [s["symbol"] for s in stale]
            out["stale_details"] = stale
            out["freshness_pct"] = round((n_present / n_total) * 100, 1) if n_total > 0 else 100.0
            out["audit_status"] = "STALE_FOUND" if n_stale > 0 else "CLEAN"
            return out
        finally:
            conn.close()
    except Exception as e:
        out["audit_status"] = f"ERROR: {e}"
        return out


def format_audit_line(audit: Dict[str, Any]) -> str:
    """Format a one-line audit summary suitable for the Performance sheet."""
    status = audit.get("audit_status", "UNKNOWN")
    if status == "NO_OPEN_POSITIONS":
        return "✓ Survivorship audit: no OPEN positions to check."
    if status == "UNIVERSE_UNAVAILABLE":
        return "⚠ Survivorship audit: latest_analysis_results table empty (universe unavailable for cross-check)."
    if status.startswith("ERROR"):
        return f"⚠ Survivorship audit: {status}"
    n_total = audit.get("n_open_total", 0)
    n_stale = audit.get("n_stale", 0)
    freshness = audit.get("freshness_pct", 100.0)
    if status == "CLEAN":
        return (f"✓ Survivorship audit: {n_total}/{n_total} OPEN positions present in today's "
                f"universe ({freshness}% freshness). No delisted / suspended stocks detected.")
    # STALE_FOUND
    stale = audit.get("stale_symbols", [])
    stale_str = ", ".join(stale[:5])
    if len(stale) > 5:
        stale_str += f" (+{len(stale)-5} more)"
    return (f"⚠ Survivorship audit: {n_stale}/{n_total} OPEN positions missing from today's "
            f"universe ({freshness}% freshness). Stale: {stale_str}. "
            f"Investigate for delisting / suspension / symbol change.")