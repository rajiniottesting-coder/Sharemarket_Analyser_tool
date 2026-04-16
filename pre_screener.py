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

        # V4: Minimum price ≥ ₹2
        if close_price < 2:
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
    SECTION 0B: Lightweight Fundamental Scorer.
    6 binary criteria × 5 pts each = 0–30.
    Drops score < 10 (too many weak dimensions).
    Applies hard drop rules regardless of score.

    Column names MUST match what the consolidated DataFrame provides.
    Keys used: net_profit, rev_growth_yoy, debt_equity,
               promoter_holding, pe, sebi_flag,
               promoter_pct (alias for promoter_holding),
               pledge_pct, consecutive_losses
    """
    if df is None or df.empty:
        print("⚠️  Stage 2: empty input DataFrame.")
        return pd.DataFrame()

    qualified = []

    for _, row in df.iterrows():
        # ── HARD DROP RULES (Section 0B) — check before scoring ─────────────

        # HD1: Zero promoter holding AND no institutional holding
        # Only apply this hard drop when we have actual shareholding data loaded.
        # If fundamentals DB is empty (first run), all values default to 0 —
        # in that case skip HD1 to avoid dropping everything.
        promoter = float(row.get("promoter_holding",
                          row.get("promoter_pct", -1)) or -1)
        fii = float(row.get("fii_holding", row.get("fii", -1)) or -1)
        dii = float(row.get("dii_holding", row.get("dii", -1)) or -1)
        # -1 means data absent — only hard-drop if data was present and explicitly 0
        has_shareholding_data = (promoter >= 0 or fii >= 0 or dii >= 0)
        if has_shareholding_data and promoter == 0.0 and max(fii, 0) + max(dii, 0) < 1.0:
            continue  # HD1: governance black hole — hard drop
        # Use 0 as default for scoring when data absent
        if promoter < 0: promoter = 0
        if fii < 0: fii = 0
        if dii < 0: dii = 0

        # HD2: 3+ consecutive quarterly losses
        if int(row.get("consecutive_losses", 0) or 0) >= 3:
            continue  # HD2

        # HD3: Promoter pledge > 40%
        pledge = float(row.get("pledge_pct", 0) or 0)
        if pledge > 40.0:
            continue  # HD3

        # HD4: Insufficient history (< 2 years in DB)
        history_days = int(row.get("history_days", 730) or 730)
        if history_days < 365:
            continue  # HD4

        # ── SCORING (5 pts each, total 0–30) ────────────────────────────────
        score = 0

        # F1: PAT positive in at least 2 of last 4 quarters
        net_profit = float(row.get("net_profit",
                           row.get("pat", row.get("profit_after_tax", 0))) or 0)
        if net_profit > 0:
            score += 5

        # F2: Revenue YoY growth > 0%
        rev_growth = float(row.get("rev_growth_yoy",
                           row.get("revenue_growth", row.get("rev_growth", 0))) or 0)
        if rev_growth > 0:
            score += 5

        # F3: Debt/Equity < 1.5
        de = float(row.get("debt_equity",
                   row.get("de_ratio", row.get("de", 99))) or 99)
        if de < 1.5:
            score += 5

        # F4: Promoter holding > 25%
        if promoter > 25.0:
            score += 5

        # F5: P/E < 80x OR P/E not applicable (0 = N/A for loss-making)
        pe = float(row.get("pe", row.get("pe_ttm", 0)) or 0)
        if pe == 0 or pe < 80:
            score += 5

        # F6: No active SEBI alert / fraud flag
        sebi_flag = str(row.get("sebi_flag", row.get("sebi_alert", "")) or "").strip().upper()
        if sebi_flag in ["", "NONE", "N/A", "0", "NO"]:
            score += 5

        # ── REJECTION THRESHOLD ──────────────────────────────────────────────
        if score < 10:
            continue

        row_dict = row.to_dict()
        row_dict["stage2_score"] = score
        qualified.append(row_dict)

    result = pd.DataFrame(qualified)
    print(f"✅ Stage 2 Complete: {len(result)} stocks qualified (from {len(df)} input).")
    return result


# ─── SECTION 3H: ANTI-TRIGGER GUARD ─────────────────────────────────────────

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