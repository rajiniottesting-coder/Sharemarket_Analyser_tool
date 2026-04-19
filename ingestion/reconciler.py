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

    # Clean symbol values (used as fallback key)
    nse_df["symbol"] = nse_df["symbol"].astype(str).str.strip().str.upper()
    bse_df["symbol"] = bse_df["symbol"].astype(str).str.strip().str.upper()

    # ── Primary merge: ISIN ───────────────────────────────────────────────────
    # Filter to rows with non-empty ISINs on both sides for the ISIN merge.
    # The bse pip package sometimes returns UDiFF format with empty ISIN column —
    # in that case the ISIN merge produces 0 DUAL_LISTED and we fall back to symbol.
    nse_has_isin = nse_df["isin"].str.len() >= 12
    bse_has_isin = bse_df["isin"].str.len() >= 12

    use_isin_merge = nse_has_isin.any() and bse_has_isin.any()

    if use_isin_merge:
        merged = pd.merge(
            nse_df,
            bse_df,
            on="isin",
            how="outer",
            suffixes=("_NSE", "_BSE"),
        )
        # Check if ISIN merge produced meaningful DUAL_LISTED results
        # (at least 5% of NSE stocks should be dual-listed if BSE ISINs are valid)
        sym_nse_test = merged.get("symbol_NSE", merged.get("symbol", pd.Series()))
        sym_bse_test = merged.get("symbol_BSE", pd.Series(index=merged.index))
        dual_test = (
            sym_nse_test.notna() & (sym_nse_test.astype(str).str.strip() != "") &
            (sym_nse_test.astype(str).str.strip() != "nan") &
            sym_bse_test.notna() & (sym_bse_test.astype(str).str.strip() != "") &
            (sym_bse_test.astype(str).str.strip() != "nan")
        ).sum()
        if dual_test < len(nse_df) * 0.05:
            use_isin_merge = False  # ISIN merge failed → fall back to symbol

    if not use_isin_merge:
        # ── Fallback merge: SYMBOL ────────────────────────────────────────────
        # ~85% of dual-listed stocks have identical NSE and BSE ticker symbols.
        # BSE bse_code is preserved from BSE-side for all matched rows.
        merged = pd.merge(
            nse_df,
            bse_df,
            on="symbol",
            how="outer",
            suffixes=("_NSE", "_BSE"),
        )

    # ── Exchange Tagging ──────────────────────────────────────────────────────
    def _apply_exchange_tag(row) -> str:
        # Works for both merge types:
        # ISIN merge:   produces symbol_NSE / symbol_BSE columns
        # Symbol merge: 'symbol' is the key; close_NSE / close_BSE show which side
        sym_nse = row.get("symbol_NSE", "")
        sym_bse = row.get("symbol_BSE", "")
        grp     = str(row.get("sc_group_BSE", row.get("sc_group", "")) or "").strip().upper()

        # ISIN-merge detection: symbol_NSE/BSE are present
        if pd.notnull(sym_nse) and str(sym_nse).strip() not in ["", "nan", "0"]:
            has_nse = True
            has_bse = pd.notnull(sym_bse) and str(sym_bse).strip() not in ["", "nan", "0"]
        elif pd.notnull(sym_bse) and str(sym_bse).strip() not in ["", "nan", "0"]:
            has_nse = False
            has_bse = True
        else:
            # Symbol-merge: use close_NSE / close_BSE as presence indicators
            cn = row.get("close_NSE", None)
            cb = row.get("close_BSE", None)
            has_nse = pd.notnull(cn) and float(cn) > 0 if pd.notnull(cn) else False
            has_bse = pd.notnull(cb) and float(cb) > 0 if pd.notnull(cb) else False

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
    # Use pd.isna() to detect actual NaN (float), and string checks for empty/zero.
    # astype(str).isin(["nan"]) does NOT catch float NaN — must check separately.
    _nse_valid = (
        sym_nse_col.notna() &
        (sym_nse_col.astype(str).str.strip() != "") &
        (sym_nse_col.astype(str).str.strip() != "nan") &
        (sym_nse_col.astype(str).str.strip() != "0")
    )
    merged["final_symbol"] = sym_nse_col.where(_nse_valid, sym_bse_col)

    # ── Unified columns — all OHLCV fields Stage 1/2/3 need ────────────────────
    # After outer merge, columns become col_NSE / col_BSE.
    # We create unified col = NSE value if non-zero, else BSE value.
    merged["symbol"] = merged["final_symbol"]
    merged["close"]  = merged["final_close"]

    _num_cols = ["open", "high", "low", "prev_close",
                 "volume", "turnover", "delivery_pct"]
    _str_cols = ["bse_code", "isin", "exchange"]

    for col in _num_cols + _str_cols:
        nse_col = f"{col}_NSE"
        bse_col = f"{col}_BSE"
        if nse_col in merged.columns and bse_col in merged.columns:
            if col in _num_cols:
                nse_v = pd.to_numeric(merged[nse_col], errors="coerce").fillna(0)
                bse_v = pd.to_numeric(merged[bse_col], errors="coerce").fillna(0)
                merged[col] = nse_v.where(nse_v > 0, bse_v)
            else:
                nse_v = merged[nse_col].astype(str).str.strip()
                bse_v = merged[bse_col].astype(str).str.strip()
                _valid = nse_v.notna() & (nse_v != "") & (nse_v != "nan")
                merged[col] = nse_v.where(_valid, bse_v)
        elif nse_col in merged.columns:
            if col in _num_cols:
                merged[col] = pd.to_numeric(merged[nse_col], errors="coerce").fillna(0)
            else:
                merged[col] = merged[nse_col].astype(str).str.strip()
        elif bse_col in merged.columns:
            if col in _num_cols:
                merged[col] = pd.to_numeric(merged[bse_col], errors="coerce").fillna(0)
            else:
                merged[col] = merged[bse_col].astype(str).str.strip()

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