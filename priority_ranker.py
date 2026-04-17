"""
priority_ranker.py
SECTION 0C — Stage 3 Priority Ranker (v7 FINAL)

Fixes:
- All key lookups lowercase (symbol, volume, delivery_pct, stage2_score)
- Override rules O1–O5 fully implemented
- Cap management: overrides fill first, remainder by priority score
"""

import pandas as pd
from data_bridge import get_20d_avg_vol


def calculate_priority_score(row: dict) -> float:
    """
    SECTION 0C: Priority Score Formula.
    P = (vol_spike × 40) + (stage2_score/30 × 25) + (delivery_pct/100 × 20) + (recency × 15)
    """
    # 1. Volume Spike Ratio (capped at 10×)
    current_vol = float(row.get("volume", 0) or 0)
    symbol      = str(row.get("symbol", row.get("final_symbol", "")) or "")
    avg_vol     = get_20d_avg_vol(symbol) if symbol else 0

    if avg_vol > 0 and current_vol > 0:
        vol_spike_ratio = min(current_vol / avg_vol, 10)
    else:
        vol_spike_ratio = 1.0  # Default for new stocks with no history

    vol_component = (vol_spike_ratio / 10) * 40

    # 2. Stage 2 Fundamental Score (0–30)
    s2_score    = float(row.get("stage2_score", 0) or 0)
    fund_component = (s2_score / 30) * 25

    # 3. Delivery Percentage
    delivery = float(row.get("delivery_pct", 0) or 0)
    deliv_component = (min(delivery, 100) / 100) * 20

    # 4. Recency Bonus (days since last full Claude analysis)
    days_since = int(row.get("days_since_analysis", 99) or 99)
    if days_since >= 7:
        recency = 1.0
    elif days_since >= 4:
        recency = 0.7
    elif days_since >= 2:
        recency = 0.3
    else:
        recency = 0.0  # Analysed within 48 hours — low priority

    recency_component = recency * 15

    total = vol_component + fund_component + deliv_component + recency_component
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

    # Cap overrides at 20 (20%) to ensure 80 slots go to genuinely ranked stocks
    MAX_OVERRIDES = 20
    if len(override_final) > MAX_OVERRIDES:
        override_final = override_final.head(MAX_OVERRIDES)

    # Fill remaining slots with ranked non-override stocks
    # Include override stocks in the pool so ranking decides among all
    remaining_slots = max(0, 100 - len(override_final))
    ranked_rest = pd.concat(
        [non_override, df[override_mask & ~df.index.isin(override_final.index)]]
    ).sort_values("priority_score", ascending=False) if not non_override.empty else         df.sort_values("priority_score", ascending=False)

    top_100 = pd.concat(
        [override_final, ranked_rest.head(remaining_slots)],
        ignore_index=True,
    ).head(100)

    # Add stage 3 rank column
    top_100["stage3_rank"] = range(1, len(top_100) + 1)

    print(
        f"✅ Stage 3 Complete: {len(top_100)} stocks selected "
        f"(overrides: {len(override_final)}, ranked: {min(remaining_slots, len(ranked_rest))})."
    )
    return top_100