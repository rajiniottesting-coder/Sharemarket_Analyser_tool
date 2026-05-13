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
from analysis.forensics_engine import ForensicsEngine


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

        # V0: Exclude ETFs, Mutual Funds, InvITs, REITs — these are not stocks
        sc_group = str(stock.get("sc_group", "") or "").upper().strip()
        if sc_group in ("EF", "MF", "IF", "IR", "BE"):
            # EF=ETF, MF=MutualFund, IF=InvIT, IR=REIT, BE=Bond ETF
            dropped.setdefault("etf_mf", 0)
            dropped["etf_mf"] += 1
            continue

        # Symbol-based ETF/MF detection (catches NSE-listed funds)
        sym_up = sym.upper()
        _etf_kw = (
            # Liquid / bond ETFs
            "LIQUID","LIQUIDETF","LIQUIDPLUS","LIQUIDSHRI","SBILIQ","ABSLLIQ",
            "HDFCLIQ","KOTAKLIQ","AONELIQ","ICICIB22",
            # Equity index ETFs
            "BEES","NIFTYBEES","JUNIORBEES","GOLDBEES","GOLDETF","SILVERETF",
            "SILVERBEES","BANKBEES","ITBEES","PSUBNKBEES","INFRABEES",
            "AUTOBEES","PHARMABEES","CONSUMIETF","DIVOPPBEES","HNGSNGBEES",
            # Fund-of-fund / index ETFs
            "MAKEINDIA","EQUAL50","CPSE","CPSEETF","SHARIAH","SHARIABEES",
            "MAFANG","BBETF","SMALLADD","MIDADD","MONQ50","MOGSEC",
            "MOPHJINDAL","LOWVOLIETF","QUALITIETF","MOVALUE","MOMOMENTUM",
            "NETFGSC10I","NETFLOWVOL","NETFMID","HDFCNIFETF","SETFNIF50",
            "SETFNN50","MOM100ETF","MON100ETF","ALPHA","IVZIN","EDELWEISS",
            # Gold / Silver / Commodity ETFs
            "GOLD1","SILVERAG","QGOLDHALF","GOLDIETF","SILVRETF",
            # Index / international ETFs
            "QNIFTY","MSCIINDIA","MASPTOP50","N50ETF","MAFSETF",
            "NIFTY50","NIFTYMID","NIFTYIT",
            # v15.2: AMC family prefixes that escaped the filter on 12 May 2026
            # (18 ETFs slipped through into the Full Dashboard with no
            # fundamentals, polluting the score distribution).
            # IMPORTANT: keep prefixes specific. Broad prefixes like "MO" or
            # "MOTI" wrongly block real stocks (MOIL, MOSCHIP, MOTHERSON,
            # MOTILALOFS = parent operating co — all legitimate). Same for
            # "HDFC" / "ICICI" / "AXIS" / "KOTAK" — those families have real
            # operating companies (HDFCAMC parent co, ICICIBANK, etc.).
            "GROWWLIQ","GROWWNIFTY","GROWWBOND",    # Groww funds
            "EBBETF","BHARATBOND","BHARATETF",      # Bharat Bond ETFs
            # Specific symbols seen on 12 May 2026 that don't match a family
            # prefix (one-offs go here so future runs catch them too)
            "MOTOUR","MOSILVER","MOREALTY","MODEFENCE","MOCAPITAL",
            "MON100","NEXT50","ESENSEX","SENSEXBETA","NIFTYBETA",
            "GSEC10YEAR","NIFTY1","HDFCSML250","HDFCNIFTY",
            "AXISILVER","ICICIAMC",
        )
        if any(sym_up.startswith(k) for k in _etf_kw) or \
           sym_up.endswith("ETF") or sym_up.endswith("BEES") or \
           sym_up.endswith("LIQUID") or sym_up.endswith("ADD") or \
           sym_up.endswith("INDEX") or sym_up in _etf_kw:
            dropped.setdefault("etf_mf", 0)
            dropped["etf_mf"] += 1
            continue

        # v15.2: Company-name-based ETF/MF detection (most robust — catches
        # any AMC product regardless of ticker convention). yfinance and NSE
        # bhavcopy both populate the company name; this is a reliable signal
        # even when symbol-based heuristics miss exotic ticker formats.
        # Patterns that uniquely identify a non-operating-company instrument:
        _name_up = str(stock.get("company_name", "") or "").upper()
        _name_etf_markers = (
            " ETF",            # leading space prevents matching e.g. "PETF Ltd"
            "MUTUAL FUND",
            "ASSET MANAGEMENT", "ASSET MGMT",
            "INDEX FUND",
            "GOLD BEES", "SILVER ETF",
            "FUND OF FUND", "NIFTY 50 ETF", "SENSEX ETF",
            "BOND ETF", "LIQUID ETF", "G-SEC ETF",
        )
        if any(m in _name_up for m in _name_etf_markers):
            dropped.setdefault("etf_mf", 0)
            dropped["etf_mf"] += 1
            continue

        # Liquid fund NAV pattern: CMP ≈ ₹1000 exactly (liquid fund NAV)
        close_pre = float(stock.get("close", 0) or 0)
        if 995 <= close_pre <= 1005 and str(stock.get("company_name","")).upper() in (
            "","NONE") or (995 <= close_pre <= 1005 and
            any(k in sym_up for k in ("LIQUID","LIQ","CASH"))):
            dropped.setdefault("etf_mf", 0)
            dropped["etf_mf"] += 1
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

        # Estimate cap category from daily turnover (proxy for market cap)
        # Used by priority_ranker for cap-tier diversification BEFORE yfinance enrichment
        # Large cap companies typically have turnover >₹50Cr/day
        _to = turnover
        if   _to >= 500_000_000: row_dict.setdefault("cap_category", "LARGE CAP")   # >₹50Cr
        elif _to >= 100_000_000: row_dict.setdefault("cap_category", "MID CAP")     # >₹10Cr
        elif _to >=  10_000_000: row_dict.setdefault("cap_category", "SMALL CAP")   # >₹1Cr
        else:                    row_dict.setdefault("cap_category", "MICRO CAP")

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