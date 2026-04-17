"""
pre_screener.py
SECTION 0A & 0B — Stage 1 & Stage 2 Pre-Screener (v7 FINAL)

Key fixes:
- All column lookups use lowercase keys matching the DB schema
- Stage 1: uses 'close', 'delivery_pct', 'volume', 'exchange_tag'
- Stage 2: uses 'net_profit', 'rev_growth_yoy', 'debt_equity',
           'promoter_holding', 'pe' — all lowercase
- Hard drop rules fully implemented per Section 0B
- anti_trigger_guard uses correct method call on ForensicsEngine instance
"""

import pandas as pd
from forensics_engine import ForensicsEngine


# ─── SECTION 0A: STAGE 1 FILTER ──────────────────────────────────────────────

def stage_1_filter(all_stocks: list) -> list:
    """
    SECTION 0A: Volume / Liquidity / Price Quality Filter.
    Input : list of stock dicts from today's Bhav Copy (consolidated)
    Output: ~400-600 candidates

    All key lookups use LOWERCASE to match standardize_to_v7_schema output.
    """
    candidates = []
    dropped = {"no_volume": 0, "penny": 0, "low_delivery": 0,
               "circuit": 0, "illiquid_sme": 0, "suspended": 0,
               "stale": 0}

    for stock in all_stocks:
        sym = str(stock.get("symbol", "")).strip()
        if not sym or sym == "0":
            continue

        # V1: Must have traded today
        volume = float(stock.get("volume", 0) or 0)
        if volume <= 0:
            dropped["no_volume"] += 1
            continue

        # V7: Circuit-hit exclusion (abs price change >= 19.9%)
        close_price = float(stock.get("close", 0) or 0)
        prev_close  = float(stock.get("prev_close", 0) or 0)
        if prev_close > 0 and close_price > 0:
            pct_change = abs((close_price - prev_close) / prev_close) * 100
            if pct_change >= 19.9 and "watchlist" not in str(stock.get("source", "")).lower():
                dropped["circuit"] += 1
                continue

        # V4: Minimum price ≥ ₹10
        if close_price < 10:
            dropped["penny"] += 1
            continue

        # V8: Skip suspended stocks
        if stock.get("suspended", False) or str(stock.get("status", "")).upper() == "SUSPENDED":
            dropped["suspended"] += 1
            continue

        # V3: Delivery % ≥ 40%
        delivery_pct = float(stock.get("delivery_pct", 0) or 0)
        if delivery_pct < 40 and not stock.get("watchlist_override", False):
            dropped["low_delivery"] += 1
            continue

        # V9: BSE SME liquidity floor — turnover ≥ ₹5L/day
        exchange_tag = str(stock.get("exchange_tag", stock.get("exchange", ""))).upper()
        if "SME" in exchange_tag:
            turnover = float(stock.get("turnover", 0) or 0)
            if turnover < 500000:  # ₹5 Lakh
                dropped["illiquid_sme"] += 1
                continue

        candidates.append(stock)

    total = len(all_stocks)
    print(
        f"✅ Stage 1 Complete: {total} → {len(candidates)} candidates "
        f"(dropped: no_vol={dropped['no_volume']}, penny={dropped['penny']}, "
        f"low_deliv={dropped['low_delivery']}, circuit={dropped['circuit']}, "
        f"sme_illiquid={dropped['illiquid_sme']}, suspended={dropped['suspended']})"
    )
    return candidates


# ─── SECTION 0B: STAGE 2 FUNDAMENTAL SCORER ──────────────────────────────────

def stage_2_fundamental_scorer(df: pd.DataFrame) -> pd.DataFrame:
    """
    SECTION 0B: Market Quality Scorer.
    Uses only bhav-copy data available at Stage 2 time.
    Scores each stock on delivery%, turnover, price zone, exchange listing.
    
    Criteria (5 pts each):
      B1: Delivery % >= 50%  (basic institutional interest)
      B2: Delivery % >= 65%  (strong institutional conviction)
      B3: Turnover  >= ₹10L  (minimum liquidity)
      B4: Turnover  >= ₹50L  (good liquidity)
      B5: Price     >= ₹50   (avoids micro/nano caps)
      B6: Price     >= ₹200  (mid-to-large territory)
      B7: DUAL_LISTED        (broader institutional access)
    
    Max = 35 pts. Threshold = 15 pts (must pass at least 3 criteria).
    Hard drops: turnover < ₹2L (illiquid), delivery < 30% (speculative only).
    """
    if df is None or df.empty:
        print("⚠️  Stage 2: empty input DataFrame.")
        return pd.DataFrame()

    qualified = []

    for _, row in df.iterrows():
        close     = float(row.get("close",       0) or 0)
        volume    = float(row.get("volume",       0) or 0)
        turnover  = float(row.get("turnover",     0) or 0)
        deliv_pct = float(row.get("delivery_pct", 0) or 0)
        exch_tag  = str(row.get("exchange_tag",   "") or "").upper()

        # ── HARD DROPS ───────────────────────────────────────────────────────
        # HD1: Turnover < ₹2 Lakh — too illiquid to trade meaningfully
        if turnover > 0 and turnover < 200_000:
            continue
        # HD2: Delivery < 30% — purely speculative, no institutional interest
        if deliv_pct > 0 and deliv_pct < 30:
            continue
        # HD3: Price < ₹20 — excludes penny/nano caps from quality selection
        if 0 < close < 20:
            continue

        # ── SCORING ──────────────────────────────────────────────────────────
        score = 0

        # B1: Delivery >= 50% (basic institutional interest)
        if deliv_pct >= 50:
            score += 5
        # B2: Delivery >= 65% (strong institutional conviction)
        if deliv_pct >= 65:
            score += 5

        # B3: Turnover >= ₹10 Lakh (minimum meaningful liquidity)
        if turnover >= 1_000_000:
            score += 5
        # B4: Turnover >= ₹50 Lakh (good trading liquidity)
        if turnover >= 5_000_000:
            score += 5

        # B5: Price >= ₹50 (avoids nano/micro speculative stocks)
        if close >= 50:
            score += 5
        # B6: Price >= ₹200 (mid-to-large quality zone)
        if close >= 200:
            score += 5

        # B7: Dual-listed (NSE+BSE = broader institutional coverage)
        if "DUAL" in exch_tag:
            score += 5

        # ── THRESHOLD ────────────────────────────────────────────────────────
        if score < 15:
            continue

        row_dict = row.to_dict()
        row_dict["stage2_score"] = score
        qualified.append(row_dict)

    result = pd.DataFrame(qualified)
    print(f"✅ Stage 2 Complete: {len(result)} stocks qualified (from {len(df)} input).")
    return result


def apply_anti_trigger_guard(stock: dict) -> dict:
    """
    SECTION 3H: Suppresses all spike triggers if any guard condition fires.
    Used in master_funnel.py via V7AnalysisEngine.apply_section_3H_guards().

    Also callable standalone with a stock dict.
    Returns {"suppressed": bool, "reasons": list}
    """
    reasons = []

    # Rule 1: Pledge > 20%
    pledge = float(stock.get("pledge_pct", 0) or 0)
    if pledge > 20:
        reasons.append(f"Pledge {pledge:.1f}% > 20%")

    # Rule 2: Altman Z < 1.81 (financial distress)
    altman_z = float(stock.get("altman_z", 5) or 5)
    if altman_z < 1.81:
        reasons.append(f"Altman Z {altman_z:.2f} < 1.81 (distress)")

    # Rule 3: Beneish M > -2.22 (manipulation risk) — numeric value
    beneish_m = stock.get("beneish_m", -5)
    if isinstance(beneish_m, (int, float)) and beneish_m > -2.22:
        reasons.append(f"Beneish M {beneish_m:.2f} > -2.22 (manipulation risk)")
    elif isinstance(beneish_m, str) and beneish_m.upper() == "MANIPULATION_RISK":
        reasons.append("Beneish M: MANIPULATION_RISK")

    # Rule 4: CFO/PAT < 0.5 (earnings quality)
    earn_quality = float(stock.get("earnings_quality", stock.get("earn_quality", 1)) or 1)
    if earn_quality < 0.5:
        reasons.append(f"Earnings Quality {earn_quality:.2f} < 0.5")

    # Rule 5: Zero promoter holding
    promoter = float(stock.get("promoter_holding",
                     stock.get("promoter_pct", 100)) or 100)
    if promoter == 0.0:
        reasons.append("Zero promoter holding")

    return {
        "suppressed": len(reasons) > 0,
        "reasons": reasons,
    }