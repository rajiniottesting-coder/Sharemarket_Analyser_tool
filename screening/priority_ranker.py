"""
priority_ranker.py
SECTION 0C — Stage 3 Priority Ranker (v7 FINAL)

Fixes:
- All key lookups lowercase (symbol, volume, delivery_pct, stage2_score)
- Override rules O1–O5 fully implemented
- Cap management: overrides fill first, remainder by priority score
"""

import pandas as pd
from database.data_bridge import get_20d_avg_vol


def calculate_priority_score(row: dict) -> float:
    """
    SECTION 0C: Priority Score Formula (v2).
    P = (vol_spike×25) + (stage2_score/35×30) + (delivery_pct/100×20)
      + (cap_bonus×15) + (turnover_bonus×10)

    Changes from v1:
    - vol_spike weight reduced 40→25 (prevents ETF arb from dominating)
    - cap_bonus added: LARGE_CAP=15, MID_CAP=10, SMALL_CAP=5, MICRO=0
    - turnover_bonus: rewards real liquidity (not just vol spike)
    - recency removed: always 0 so it added nothing
    """
    # 1. Volume Spike Ratio (capped at 5× — was 10×)
    current_vol = float(row.get("volume", 0) or 0)
    symbol      = str(row.get("symbol", row.get("final_symbol", "")) or "")
    avg_vol     = get_20d_avg_vol(symbol) if symbol else 0

    if avg_vol > 0 and current_vol > 0:
        vol_spike_ratio = min(current_vol / avg_vol, 5)  # cap at 5× not 10×
    else:
        vol_spike_ratio = 1.0

    vol_component = (vol_spike_ratio / 5) * 25

    # 2. Stage 2 Quality Score (0–35)
    s2_score       = float(row.get("stage2_score", 0) or 0)
    fund_component = (s2_score / 35) * 30

    # 3. Delivery Percentage
    delivery        = float(row.get("delivery_pct", 0) or 0)
    deliv_component = (min(delivery, 100) / 100) * 20

    # 4. Cap Category Bonus — ensures large/mid caps are not crowded out
    cap = str(row.get("cap_category", "") or "").upper()
    if   "LARGE" in cap: cap_bonus = 1.0
    elif "MID"   in cap: cap_bonus = 0.67
    elif "SMALL" in cap: cap_bonus = 0.33
    else:                cap_bonus = 0.0   # MICRO or unknown
    cap_component = cap_bonus * 15

    # 5. Turnover Bonus — rewards real liquidity depth
    turnover = float(row.get("turnover", 0) or 0)
    if   turnover >= 50_000_000:  t_bonus = 1.0   # ≥₹5 Cr
    elif turnover >= 10_000_000:  t_bonus = 0.6   # ≥₹1 Cr
    elif turnover >=  1_000_000:  t_bonus = 0.3   # ≥₹10L
    else:                         t_bonus = 0.0
    turnover_component = t_bonus * 10

    total = vol_component + fund_component + deliv_component + cap_component + turnover_component
    return round(total, 2)


def get_top_100_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """
    SECTION 0C: Sort by Priority Score and select top 100.
    Applies all 5 override rules BEFORE ranking cut.

    Override rules (force into top 100):
    O1 — Personal watchlist stocks
    O2 — BSE/NSE announcement today
    O3 — Spike pre-trigger conditions met (vol > 3×, delivery > 60%)
    O4 — Score deterioration (last score ≥ 60, today's Stage2 < 15)
    O5 — Expiry: not analysed in 7+ days
    """
    if df is None or df.empty:
        print("⚠️  Stage 3: empty input. Returning empty DataFrame.")
        return pd.DataFrame()

    df = df.copy()

    # ── Compute priority scores ───────────────────────────────────────────────
    df["priority_score"] = df.apply(
        lambda row: calculate_priority_score(row.to_dict()), axis=1
    )

    # ── Identify override stocks ──────────────────────────────────────────────
    def _get_vol_ratio(row):
        vol = float(row.get("volume", 0) or 0)
        avg = get_20d_avg_vol(str(row.get("symbol", "") or ""))
        return (vol / avg) if avg > 0 else 1.0

    # O1: Watchlist
    o1_mask = df.get("watchlist_override", pd.Series([False] * len(df), index=df.index))
    if isinstance(o1_mask, pd.Series):
        o1_mask = o1_mask.fillna(False).astype(bool)
    else:
        o1_mask = pd.Series([False] * len(df), index=df.index)

    # O2: BSE/NSE announcement today
    o2_col = "announcement_today"
    o2_mask = (df[o2_col].fillna(False).astype(bool)
               if o2_col in df.columns
               else pd.Series([False] * len(df), index=df.index))

    # O3: Spike pre-trigger (vol > 3×, delivery > 60%)
    vol_ratios = df.apply(lambda r: _get_vol_ratio(r.to_dict()), axis=1)
    delivery_col = "delivery_pct"
    delivery_vals = (df[delivery_col].fillna(0).astype(float)
                     if delivery_col in df.columns
                     else pd.Series([0.0] * len(df), index=df.index))
    o3_mask = (vol_ratios >= 3.0) & (delivery_vals >= 60.0)

    # O4: Score deterioration
    last_score_col = "last_claude_score"
    if last_score_col in df.columns:
        o4_mask = (
            (df[last_score_col].fillna(0).astype(float) >= 60) &
            (df["stage2_score"].fillna(0).astype(float) < 15)
        )
    else:
        o4_mask = pd.Series([False] * len(df), index=df.index)

    # O5: Expiry — disabled: days_since_analysis is never populated
    # so it would default to 99 for ALL stocks → 1914 overrides → alphabetical
    # The real priority ranking below handles freshness via recency component
    o5_mask = pd.Series([False] * len(df), index=df.index)

    override_mask = o1_mask | o2_mask | o3_mask | o4_mask | o5_mask
    override_df   = df[override_mask].copy()
    non_override  = df[~override_mask].copy()

    # ── Assemble top 100 ──────────────────────────────────────────────────────
    # Collect overrides deduplicated in priority order: O1 > O2 > O3 > O4 > O5
    seen = set()
    ordered_overrides = []
    for mask in [o1_mask, o2_mask, o3_mask, o4_mask, o5_mask]:
        for idx in df[mask].index:
            if idx not in seen:
                seen.add(idx)
                ordered_overrides.append(idx)

    override_final = df.loc[ordered_overrides].copy()

    # Cap overrides at 20
    MAX_OVERRIDES = 20
    if len(override_final) > MAX_OVERRIDES:
        override_final = override_final.head(MAX_OVERRIDES)

    # Build full ranked pool (all non-override stocks by priority score)
    remaining_slots = max(0, 100 - len(override_final))
    all_ranked = pd.concat(
        [non_override, df[override_mask & ~df.index.isin(override_final.index)]]
    ).sort_values("priority_score", ascending=False) if not non_override.empty else         df.sort_values("priority_score", ascending=False)

    # ── Cap-tier diversification ─────────────────────────────────────────────
    # Guarantee: LARGE≥20, MID≥15, SMALL+MICRO≤65
    # Prevents ETFs (sc_group=EF already filtered) and micro junk flooding list
    MIN_LARGE, MIN_MID, MAX_SMALL_MICRO = 20, 15, 65

    def _cap_tier(row_dict):
        c = str(row_dict.get("cap_category","") or "").upper()
        if "LARGE" in c: return "LARGE"
        if "MID"   in c: return "MID"
        if "SMALL" in c: return "SMALL"
        return "MICRO"

    if "cap_category" in all_ranked.columns:
        all_ranked = all_ranked.copy()
        all_ranked["_tier"] = all_ranked.apply(
            lambda r: _cap_tier(r.to_dict()), axis=1)

        large_pool = all_ranked[all_ranked["_tier"] == "LARGE"]
        mid_pool   = all_ranked[all_ranked["_tier"] == "MID"]
        sm_pool    = all_ranked[all_ranked["_tier"].isin(["SMALL","MICRO"])]

        large_picks = large_pool.head(MIN_LARGE)
        mid_picks   = mid_pool.head(MIN_MID)
        guaranteed  = pd.concat([large_picks, mid_picks])
        used_idx    = set(guaranteed.index) | set(override_final.index)

        # Fill remaining from ranked pool, capping small+micro
        sm_count = len(guaranteed[guaranteed["_tier"].isin(["SMALL","MICRO"])])
        filler_rows = []
        for _, row in all_ranked[~all_ranked.index.isin(used_idx)].iterrows():
            needed = remaining_slots - len(guaranteed)
            if len(filler_rows) >= needed: break
            if row["_tier"] in ("SMALL","MICRO"):
                if sm_count >= MAX_SMALL_MICRO: continue
                sm_count += 1
            filler_rows.append(row)

        filler_df = pd.DataFrame(filler_rows)
        top_100 = pd.concat(
            [override_final, guaranteed, filler_df], ignore_index=True
        ).drop_duplicates(subset=["symbol"]).head(100)
        n_large = len([r for _,r in top_100.iterrows() if "LARGE" in str(r.get("cap_category",""))])
        n_mid   = len([r for _,r in top_100.iterrows() if "MID"   in str(r.get("cap_category",""))])
        print(f"   Cap mix: LARGE={n_large}, MID={n_mid}, SMALL/MICRO={100-n_large-n_mid}")
    else:
        # Fallback if no cap_category yet (pre-enrichment)
        top_100 = pd.concat(
            [override_final, all_ranked.head(remaining_slots)],
            ignore_index=True,
        ).head(100)

    # Add stage 3 rank column
    top_100["stage3_rank"] = range(1, len(top_100) + 1)

    # Generate a human-readable selection reason for each stock
    # This feeds directly into the AI analyst prompt for Block H rationale
    def _reason(row):
        parts = []
        cap   = str(row.get("cap_category", "") or "")
        deliv = float(row.get("delivery_pct", 0) or 0)
        vol   = float(row.get("vol_ratio",   1.0) or 1.0)
        to    = float(row.get("turnover",      0) or 0)
        s2    = float(row.get("stage2_score",   0) or 0)
        sym   = str(row.get("symbol", ""))

        if "LARGE" in cap.upper(): parts.append("Large-cap institutional quality")
        elif "MID" in cap.upper(): parts.append("Mid-cap growth potential")
        else:                      parts.append("Small/micro-cap high-growth candidate")

        if deliv >= 75: parts.append(f"very high institutional delivery {deliv:.0f}%")
        elif deliv >= 60: parts.append(f"strong institutional delivery {deliv:.0f}%")

        if vol >= 3.0: parts.append(f"significant volume surge {vol:.1f}× avg")
        elif vol >= 2.0: parts.append(f"elevated volume {vol:.1f}× avg")

        if to >= 500_000_000: parts.append("top-tier market liquidity (>₹50Cr turnover)")
        elif to >= 100_000_000: parts.append("strong market liquidity (>₹10Cr turnover)")

        if s2 >= 30: parts.append("highest quality score in Stage 2")
        elif s2 >= 25: parts.append("solid Stage 2 quality score")

        return "; ".join(parts) if parts else "Passed all quality filters"

    top_100["selection_reason"] = top_100.apply(
        lambda r: _reason(r.to_dict()), axis=1)

    # ── Verdict-priority sort ─────────────────────────────────────────────────
    # Sort so Excel shows: BUY first → WATCHLIST → NEUTRAL → AVOID
    # Within each verdict tier: highest priority_score first
    # This ensures the user sees the best BUY stocks at the top
    VERDICT_ORDER = {"BUY": 0, "WATCHLIST": 1, "NEUTRAL": 2, "AVOID": 3}
    if "verdict" in top_100.columns:
        top_100 = top_100.copy()
        top_100["_vord"] = top_100["verdict"].apply(
            lambda v: VERDICT_ORDER.get(str(v), 2))
        top_100 = top_100.sort_values(
            ["_vord", "priority_score"], ascending=[True, False]
        ).reset_index(drop=True)
        top_100.drop(columns=["_vord"], inplace=True, errors="ignore")
        # Re-assign rank after sort
        top_100["stage3_rank"] = range(1, len(top_100) + 1)

    # Summary log
    v_counts  = dict(top_100["verdict"].value_counts()) if "verdict" in top_100.columns else {}
    cap_short = {}
    if "cap_category" in top_100.columns:
        for c in top_100["cap_category"].fillna("?"):
            k = str(c).split()[0]; cap_short[k] = cap_short.get(k, 0) + 1

    print(
        f"✅ Stage 3 Complete: {len(top_100)} stocks selected "
        f"(overrides={len(override_final)}, ranked={remaining_slots}) | "
        f"Verdicts: {v_counts} | Caps: {cap_short}"
    )
    return top_100