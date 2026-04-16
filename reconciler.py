"""
reconciler.py
SECTION 1B — Cross-Exchange Reconciliation (v7 FINAL)

Fixes:
- Uses 'isin' as primary merge key (both NSE and BSE standardised to have it)
- Handles missing 'close_NSE' / 'close_BSE' gracefully after merge
- 'final_symbol' prefers NSE ticker over BSE SC_NAME
- 'final_close' prefers NSE close
- diff_pct computation fully safe (zero-division protected)
- exchange_tag correctly identifies BSE_SME via sc_group column
"""

import pandas as pd
import numpy as np


def reconcile_exchanges(nse_df: pd.DataFrame, bse_df: pd.DataFrame) -> pd.DataFrame:
    """
    SECTION 1B: Cross-Exchange Reconciliation via ISIN.
    Identifies DUAL_LISTED, NSE_ONLY, BSE_ONLY, BSE_SME stocks.
    Computes NSE/BSE price differential for dual-listed stocks.
    """
    # Safety: handle None inputs
    if nse_df is None or nse_df.empty:
        if bse_df is None or bse_df.empty:
            return pd.DataFrame()
        bse_df = bse_df.copy()
        bse_df["exchange_tag"] = bse_df.apply(_bse_exchange_tag, axis=1)
        bse_df["final_symbol"] = bse_df["symbol"].astype(str)
        bse_df["final_close"]  = pd.to_numeric(bse_df.get("close", 0), errors="coerce").fillna(0)
        bse_df["diff_pct"]     = 0.0
        return bse_df

    if bse_df is None or bse_df.empty:
        nse_df = nse_df.copy()
        nse_df["exchange_tag"] = "NSE_ONLY"
        nse_df["final_symbol"] = nse_df["symbol"].astype(str)
        nse_df["final_close"]  = pd.to_numeric(nse_df.get("close", 0), errors="coerce").fillna(0)
        nse_df["diff_pct"]     = 0.0
        return nse_df

    # ── Ensure isin column exists in both ─────────────────────────────────────
    for df in [nse_df, bse_df]:
        if "isin" not in df.columns:
            df["isin"] = ""

    # Clean ISIN values
    nse_df = nse_df.copy()
    bse_df = bse_df.copy()
    nse_df["isin"] = nse_df["isin"].astype(str).str.strip().str.upper()
    bse_df["isin"] = bse_df["isin"].astype(str).str.strip().str.upper()

    # ── Outer merge on ISIN ───────────────────────────────────────────────────
    merged = pd.merge(
        nse_df,
        bse_df,
        on="isin",
        how="outer",
        suffixes=("_NSE", "_BSE"),
    )

    # ── Exchange Tagging ──────────────────────────────────────────────────────
    def _apply_exchange_tag(row) -> str:
        sym_nse = row.get("symbol_NSE", row.get("symbol", ""))
        sym_bse = row.get("symbol_BSE", "")
        grp     = str(row.get("sc_group_BSE", row.get("sc_group", "")) or "").strip().upper()

        has_nse = pd.notnull(sym_nse) and str(sym_nse).strip() not in ["", "nan", "0"]
        has_bse = pd.notnull(sym_bse) and str(sym_bse).strip() not in ["", "nan", "0"]

        if has_nse and has_bse:
            return "DUAL_LISTED"
        if has_bse and grp in ["M", "MT", "S", "ST"]:
            return "BSE_SME"
        if has_bse:
            return "BSE_ONLY"
        return "NSE_ONLY"

    merged["exchange_tag"] = merged.apply(_apply_exchange_tag, axis=1)

    # ── Derive close columns after merge ─────────────────────────────────────
    # After outer merge, 'close' may appear as 'close_NSE' and 'close_BSE'
    # or as a single 'close' if one side was empty
    def _get_col(df, *candidates):
        for c in candidates:
            if c in df.columns:
                return pd.to_numeric(df[c], errors="coerce").fillna(0)
        return pd.Series(0.0, index=df.index)

    close_nse = _get_col(merged, "close_NSE", "close")
    close_bse = _get_col(merged, "close_BSE")

    # ── Arbitrage Signal (NSE/BSE price diff for DUAL_LISTED) ────────────────
    merged["diff_pct"] = 0.0
    dual_mask = (
        (merged["exchange_tag"] == "DUAL_LISTED") &
        (close_nse > 0) &
        (close_bse > 0)
    )
    if dual_mask.any():
        merged.loc[dual_mask, "diff_pct"] = (
            (close_nse[dual_mask] - close_bse[dual_mask]) /
            close_bse[dual_mask] * 100
        ).round(3)

    # ── Primary Price and Symbol Selection ───────────────────────────────────
    # NSE ticker takes priority (it's the human-readable symbol)
    sym_nse_col = _get_sym_col(merged, "symbol_NSE", "symbol")
    sym_bse_col = _get_sym_col(merged, "symbol_BSE")

    merged["final_close"]  = close_nse.where(close_nse > 0, close_bse)
    merged["final_symbol"] = sym_nse_col.where(
        sym_nse_col.astype(str).str.strip().isin(["", "nan", "0"]) == False,
        sym_bse_col,
    )

    # Convenience: also provide a unified 'symbol' and 'close' for downstream use
    merged["symbol"] = merged["final_symbol"]
    merged["close"]  = merged["final_close"]

    return merged


def _bse_exchange_tag(row) -> str:
    grp = str(row.get("sc_group", "") or "").strip().upper()
    if grp in ["M", "MT", "S", "ST"]:
        return "BSE_SME"
    return "BSE_ONLY"


def _get_sym_col(df, *candidates) -> pd.Series:
    for c in candidates:
        if c in df.columns:
            return df[c].astype(str).str.strip()
    return pd.Series("", index=df.index)
