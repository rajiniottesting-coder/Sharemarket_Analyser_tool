"""
risk/correlation_aware_sizing.py — v15.4

INSTITUTIONAL VOLATILITY-ADJUSTED POSITION SIZING (RISK PARITY)
══════════════════════════════════════════════════════════════════════════

v15.3 NOTE: The original v15.3 implementation used naive sector-count
penalties (-1% per same-sector OPEN). That's a beginner heuristic, not
how institutional portfolios actually size positions.

v15.4 REPLACEMENT: Implements the **institutional risk-parity approach**:

  Target risk per position = portfolio_risk_budget_pct (e.g., 1%)
  Per-position dollar risk = position_size × |SL_pct|
  Therefore: position_size = risk_budget_pct / |SL_pct|

This is what hedge funds, prop desks, CTAs, and SEBI-RIA portfolio
managers actually do. The principle: **equalize risk contribution
across positions, not portfolio share**.

Why this matters
────────────────
Without risk parity, you can naively put 5% of portfolio into:
  • Stock A (LARGE CAP, SL=-6%):  loss-at-SL = 5% × 6%  = 0.30% portfolio
  • Stock B (SMALL CAP, SL=-15%): loss-at-SL = 5% × 15% = 0.75% portfolio

Stock B is 2.5x as risky for the same 5% allocation. With risk parity
at 1% risk-budget per position:
  • Stock A: size = 1% / 6%  = 16.67%
  • Stock B: size = 1% / 15% = 6.67%

Both contribute the SAME 1% risk to the portfolio. This is the cornerstone
of modern portfolio risk management.

Sector concentration handling (the corrected approach)
──────────────────────────────────────────────────────
Rather than penalize per same-sector position (the v15.3 approach),
v15.4 uses a hard sector exposure cap — the genuine institutional
practice:
  • Total sector exposure ≤ MAX_SECTOR_EXPOSURE_PCT (default 30%)
  • If adding a new position would exceed cap, output is capped (not
    penalized linearly)

This is what NSE's SEBI-RIA disclosure rules expect: portfolio-level
exposure limits, not per-trade sector penalties.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple

DB_PATH = "market_data.db"

# Institutional risk-parity tunables
DEFAULT_RISK_BUDGET_PCT = 1.0      # % portfolio willing to lose per trade
MAX_SECTOR_EXPOSURE_PCT = 30.0     # hard sector cap (SEBI-RIA norm 25-30%)
MIN_ALLOCATION_PCT = 1.0           # transaction-cost floor
MAX_ALLOCATION_PCT = 15.0          # concentration safety ceiling
FALLBACK_ALLOCATION_PCT = 3.0      # used when sl_pct unavailable

# Cap-category risk multipliers (illiquidity / volatility premium)
# Conservative defaults; calibratable via Phase 3 backtest later.
CAP_RISK_MULTIPLIER = {
    'LARGE CAP':  1.0,
    'MID CAP':    1.0,
    'SMALL CAP':  0.85,    # 15% smaller for same risk budget
    'MICRO CAP':  0.70,    # 30% smaller (illiquidity premium)
}


def _load_open_positions() -> List[Dict]:
    """Returns list of currently-open positions from gold_outcomes."""
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.execute("""
                SELECT r.symbol, r.cap_category, r.sector,
                       r.cmp_at_recommendation, r.stop_loss
                FROM gold_recommendations r
                INNER JOIN gold_outcomes o ON r.symbol = o.symbol
                  AND r.recommendation_date = o.recommendation_date
                WHERE o.outcome_type = 'OPEN'
            """)
            return [
                {
                    'symbol': r[0],
                    'cap_category': r[1] or '',
                    'sector': r[2] or '',
                    'cmp_at_rec': float(r[3] or 0),
                    'sl': float(r[4] or 0),
                }
                for r in cur.fetchall()
            ]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return []


def _current_sector_exposure(open_positions: List[Dict]) -> Dict[str, float]:
    """
    Returns {sector: estimated portfolio exposure %} based on current OPEN
    positions, computed by re-deriving each position's risk-parity allocation
    from its SL distance.
    """
    sector_exposure: Dict[str, float] = {}
    for pos in open_positions:
        sec = pos['sector'].strip()
        if not sec:
            continue
        if pos['cmp_at_rec'] > 0 and pos['sl'] > 0:
            sl_pct = abs((pos['sl'] - pos['cmp_at_rec']) / pos['cmp_at_rec'] * 100.0)
            if sl_pct > 0:
                alloc_est = DEFAULT_RISK_BUDGET_PCT / sl_pct * 100.0
                alloc_est = max(MIN_ALLOCATION_PCT, min(MAX_ALLOCATION_PCT, alloc_est))
                cap_mult = CAP_RISK_MULTIPLIER.get(pos['cap_category'].strip(), 1.0)
                alloc_est *= cap_mult
            else:
                alloc_est = FALLBACK_ALLOCATION_PCT
        else:
            alloc_est = FALLBACK_ALLOCATION_PCT
        sector_exposure[sec] = sector_exposure.get(sec, 0.0) + alloc_est
    return sector_exposure


def compute_suggested_allocation(
    candidate_sector: str,
    candidate_cap: str,
    sl_pct: Optional[float] = None,
    risk_budget_pct: float = DEFAULT_RISK_BUDGET_PCT,
    open_positions: Optional[List[Dict]] = None,
) -> Tuple[float, str]:
    """
    Compute the volatility-adjusted (risk-parity) suggested allocation.

    Args:
        candidate_sector: sector of the new recommendation
        candidate_cap: cap_category (LARGE/MID/SMALL/MICRO CAP)
        sl_pct: |SL distance| as percent (e.g., 8.5 for -8.5% stop). If
                None, falls back to FALLBACK_ALLOCATION_PCT.
        risk_budget_pct: % portfolio risk per position (default 1%)
        open_positions: pre-loaded list (perf: avoid DB hit)

    Returns:
        (allocation_pct, rationale_string)

    Examples:
        Risk-parity sizing for typical LARGE CAP with -6% SL:
        >>> compute_suggested_allocation("Banking", "LARGE CAP", sl_pct=6.0)
        (15.0, "Risk parity: 1.0% / 6.0% = 16.67% · LARGE×1.0 · clamped to 15.0%")
    """
    if open_positions is None:
        open_positions = _load_open_positions()

    sector_norm = (candidate_sector or '').strip()
    cap_norm = (candidate_cap or '').strip()

    # Compute base risk-parity allocation
    if sl_pct is not None and sl_pct > 0:
        raw_alloc = risk_budget_pct / sl_pct * 100.0
        cap_mult = CAP_RISK_MULTIPLIER.get(cap_norm, 1.0)
        cap_adjusted_alloc = raw_alloc * cap_mult
        rationale_parts = [
            f"Risk parity: {risk_budget_pct:.1f}% / {sl_pct:.1f}% "
            f"= {raw_alloc:.2f}%"
        ]
        if cap_mult != 1.0:
            cap_short = cap_norm.split()[0] if cap_norm else "?"
            rationale_parts.append(f"{cap_short}x{cap_mult}")
    else:
        cap_adjusted_alloc = FALLBACK_ALLOCATION_PCT
        rationale_parts = [
            f"Fallback (SL unavailable): {FALLBACK_ALLOCATION_PCT:.1f}%"
        ]

    # Apply sector exposure cap (hard limit, not linear penalty)
    # v15.7 fix: only emit "sector cap" text when sector cap is the ACTUAL
    # binding constraint (i.e., sector headroom is below the MAX_ALLOCATION
    # ceiling). Otherwise the MAX_ALLOCATION clamp is the real constraint
    # and the user should see "clamped to 15.0%", not a misleading sector-cap
    # message that incidentally has the same numeric value.
    #
    # Pre-v15.7 bug example (ITC, LARGE CAP, SL=6.2%, alone in Consumer Defensive):
    #   - _current_sector_exposure counted ITC's own OPEN position → 15% used
    #   - headroom_in_sector = 30 - 15 = 15.0%
    #   - cap_adjusted_alloc (16.16%) > headroom (15.0%) → "sector cap" branch fired
    #   - final_alloc became 15.0% — but only because MAX_ALLOCATION_PCT was also 15%
    #   - The TRUE binding constraint was MAX_ALLOCATION, not sector cap
    sector_exposures = _current_sector_exposure(open_positions)
    current_sector_pct = sector_exposures.get(sector_norm, 0.0)
    headroom_in_sector = max(0.0, MAX_SECTOR_EXPOSURE_PCT - current_sector_pct)

    sector_was_binding = False
    if cap_adjusted_alloc > headroom_in_sector and headroom_in_sector < MAX_ALLOCATION_PCT:
        # Sector cap genuinely constrains BELOW the MAX_ALLOCATION ceiling
        rationale_parts.append(
            f"sector cap: {current_sector_pct:.1f}% used, "
            f"{headroom_in_sector:.1f}% headroom"
        )
        final_alloc = headroom_in_sector
        sector_was_binding = True
    elif cap_adjusted_alloc > headroom_in_sector:
        # cap_adjusted > headroom, but headroom ≥ MAX_ALLOCATION_PCT means the
        # MAX_ALLOCATION clamp will be the binding constraint anyway — don't
        # emit the misleading "sector cap" message. Let the clamp message
        # below handle the explanation.
        final_alloc = cap_adjusted_alloc
    else:
        final_alloc = cap_adjusted_alloc

    # Safety clamps — emit "clamped to" message when the MAX/MIN clamp fires
    # AND sector wasn't already the announced binding constraint
    pre_clamp = final_alloc
    final_alloc = max(MIN_ALLOCATION_PCT, min(MAX_ALLOCATION_PCT, final_alloc))
    if final_alloc != pre_clamp and not sector_was_binding:
        rationale_parts.append(f"clamped to {final_alloc:.1f}%")
    elif final_alloc != pre_clamp and sector_was_binding:
        # Edge case: sector cap brought us above MAX or below MIN — note it
        rationale_parts.append(f"clamped to {final_alloc:.1f}%")

    rationale = " · ".join(rationale_parts)
    return round(final_alloc, 1), rationale


def compute_for_stock_dict(
    stock: Dict,
    sl_pct: Optional[float] = None,
    risk_budget_pct: float = DEFAULT_RISK_BUDGET_PCT,
    open_positions: Optional[List[Dict]] = None,
) -> Tuple[float, str]:
    """
    Convenience wrapper. Pass full stock dict + its computed sl_pct.

    Example use in master_funnel.py recommendation loop:
        from risk.correlation_aware_sizing import compute_for_stock_dict
        r = _compute_sl_t_v14_6(...)
        alloc, why = compute_for_stock_dict(stock, sl_pct=r['sl_pct'])
        stock['suggested_alloc_pct'] = alloc
        stock['alloc_rationale'] = why
    """
    return compute_suggested_allocation(
        candidate_sector=stock.get('sector', ''),
        candidate_cap=stock.get('cap_category', stock.get('cap_badge', '')),
        sl_pct=sl_pct,
        risk_budget_pct=risk_budget_pct,
        open_positions=open_positions,
    )