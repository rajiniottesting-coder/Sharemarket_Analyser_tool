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

Session 22 fallback (added):
- When BSE Bhav download fails (Cloudflare blocks cloud IPs, or pip 'bse' /
  'cloudscraper' not installed), reconcile_exchanges previously tagged ALL
  stocks as NSE_ONLY, including known dual-listed names like SBIN, M&M, TITAN.
- New behaviour: when bse_df is empty, consult DUAL_LISTED_ALLOWLIST (curated
  Nifty-100-based set of known NSE+BSE dual-listed stocks) and tag matching
  symbols as DUAL_LISTED. Anything not on the list still defaults to NSE_ONLY,
  which is correct for ~95% of small/mid caps that aren't widely dual-listed.
"""

import pandas as pd
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# DUAL_LISTED_ALLOWLIST — Session 22 BSE-down fallback
# ─────────────────────────────────────────────────────────────────────────────
# Stocks confirmed to trade on BOTH NSE and BSE. Sourced from Nifty 100
# constituents (Apr 2026) plus widely-traded mid-caps. All values are NSE
# ticker symbols (uppercase) — match key used by reconciler.
#
# Maintenance note: this list rarely changes. New IPOs typically list on both
# exchanges. Removals happen only on delisting. If a stock you care about
# shows NSE_ONLY but should be DUAL_LISTED, add it here.
DUAL_LISTED_ALLOWLIST = frozenset({
    # NIFTY 50 (all dual-listed)
    "RELIANCE", "HDFCBANK", "BHARTIARTL", "TCS", "ICICIBANK", "SBIN", "INFY",
    "HINDUNILVR", "ITC", "LT", "BAJFINANCE", "KOTAKBANK", "AXISBANK", "MARUTI",
    "TITAN", "M&M", "SUNPHARMA", "ASIANPAINT", "ULTRACEMCO", "ONGC", "NTPC",
    "HCLTECH", "WIPRO", "ADANIENT", "ADANIPORTS", "JSWSTEEL", "TATAMOTORS",
    "TATASTEEL", "POWERGRID", "COALINDIA", "BAJAJFINSV", "HINDALCO", "NESTLEIND",
    "TECHM", "DRREDDY", "GRASIM", "INDUSINDBK", "BPCL", "CIPLA", "DIVISLAB",
    "EICHERMOT", "BAJAJ-AUTO", "BRITANNIA", "APOLLOHOSP", "TRENT", "HEROMOTOCO",
    "TATACONSUM", "SHRIRAMFIN", "SBILIFE", "HDFCLIFE", "JIOFIN", "ETERNAL",
    # NIFTY NEXT 50 (also all dual-listed)
    "HAL", "HINDZINC", "IOC", "ADANIGREEN", "TVSMOTOR", "VBL", "PFC", "DLF",
    "BAJAJHLDNG", "VEDL", "AMBUJACEM", "GAIL", "ICICIGI", "ICICIPRULI", "GODREJCP",
    "PIDILITIND", "BANKBARODA", "HAVELLS", "TATAPOWER", "INDHOTEL", "ATGL",
    "RECLTD", "JINDALSTEL", "SIEMENS", "DABUR", "ZOMATO", "BEL", "CHOLAFIN",
    "SHREECEM", "MOTHERSON", "ADANIPOWER", "JSWENERGY", "ABB", "BOSCHLTD",
    "MARICO", "INDIGO", "INDUSTOWER", "CGPOWER", "IRFC", "PNB", "TORNTPHARM",
    "LODHA", "HYUNDAI", "SWIGGY", "NAUKRI", "UNITDSPR", "BHEL", "POLYCAB",
    "COLPAL", "LICI", "CANBK",
    # Common NIFTY MIDCAP 100 / smallcap names that show up in screeners
    "FEDERALBNK", "M&MFIN", "BANDHANBNK", "MFSL", "MPHASIS", "PERSISTENT",
    "COFORGE", "LTIM", "LTTS", "PIIND", "VOLTAS", "BERGEPAINT", "PAGEIND",
    "AUROPHARMA", "LUPIN", "BIOCON", "ALKEM", "ZYDUSLIFE", "GLENMARK",
    "TORNTPOWER", "NHPC", "SJVN", "PETRONET", "GUJGASLTD", "MGL", "IGL",
    "OFSS", "MINDTREE", "TATAELXSI", "NMDC", "NATIONALUM", "SAIL",
    "TATACOMM", "CONCOR", "IRCTC", "RVNL", "RAILTEL", "MAZDOCK", "GRSE",
    "BDL", "BEML", "DEEPAKNTR", "ATUL", "AARTIIND", "COROMANDEL", "UPL",
    "CHAMBLFERT", "BAYERCROP", "ESCORTS", "ASHOKLEY", "BHARATFORG", "EXIDEIND",
    "MRF", "BALKRISIND", "APOLLOTYRE", "CEATLTD", "TIINDIA", "JKCEMENT",
    "RAMCOCEM", "DALBHARAT", "ABCAPITAL", "ABFRL", "PAYTM", "POLICYBZR",
    "STARHEALTH", "MAXHEALTH", "FORTIS", "SYNGENE", "GLAND", "SUNTV",
    "ZEEL", "PVRINOX", "INDIANB", "UCOBANK", "IDBI", "IDFCFIRSTB", "AUBANK",
    "RBLBANK", "YESBANK", "ESAFSFB", "EQUITASBNK", "UJJIVANSFB", "MUTHOOTFIN",
    "MANAPPURAM", "L&TFH", "BAJAJHIND", "NAVINFLUOR", "LINDEINDIA", "SRF",
    "TATACHEM", "JUBLFOOD", "KAJARIACER", "HOMEFIRST", "GRANULES", "PITTIENG",
    "VOLTAMP", "CARERATING", "STYLAMIND", "GOCOLORS", "HALEOSLABS", "AMAGI",
    "CMSINFO", "RANEHOLDIN",
    # ─── v11.0 batch addition (Apr 2026) — verified large/mid dual-listed ──
    # All confirmed by NSE+BSE listing pairs. Added to fix wrong NSE_ONLY tags
    # observed in 27-Apr-2026 dashboard run when BSE bhavcopy fails on
    # GitHub Actions runners (Cloudflare 403).
    "ABBOTINDIA", "BATAINDIA", "BHARTIHEXA", "GLAXO", "GRINDWELL", "USHAMART",
    "RKFORGE", "NAZARA", "CIEINDIA", "VENUSREM", "TALBROAUTO", "CARBORUNIV",
    "VGUARD", "ANTHEM", "INNOVACAP", "MINDACORP", "ERIS", "POLYPLEX",
    "AADHARHFC", "ASIANTILES", "FIVESTAR", "ANANDRATHI", "WEWORK", "PYRAMID",
    "WELENT", "LAXMIDENTL", "SENORES",
    # ─── PENDING manual verification — uncomment after confirming BSE pairing ─
    # "MOREALTY",   # listing pairing not fully verified
    # "KMEW",       # 2025 IPO — verify BSE code
    # "RBA",        # ticker → BSE code mapping needs verification
    # ─── NOT added — these are index ETFs/futures, NOT individual equities ──
    # "IT", "PSUBANK", "BANKNIFTY1"  — index tickers, would corrupt the allowlist semantics
})


def _is_dual_listed_known(symbol: str) -> bool:
    """Check if a symbol is on the curated dual-listed allowlist."""
    if not symbol or not isinstance(symbol, str):
        return False
    return symbol.strip().upper() in DUAL_LISTED_ALLOWLIST


def get_effective_allowlist():
    """
    v11.0.2: Returns the runtime-effective union of:
      (1) the hardcoded DUAL_LISTED_ALLOWLIST frozenset (always present)
      (2) the dual_listed_runtime SQLite table (auto-discovered via BSE merges)

    Falls back to just (1) if the runtime table is missing or unreadable —
    so this is safe on a fresh install with no DB. The return type is a
    plain frozenset, so .isin() and `in` checks work identically.
    """
    try:
        # Lazy import — avoids a hard dependency if allowlist_maintainer ever
        # has to be removed in a future refactor.
        from ingestion.allowlist_maintainer import get_runtime_allowlist
        runtime = get_runtime_allowlist()
        if runtime:
            return frozenset(DUAL_LISTED_ALLOWLIST | runtime)
    except Exception:
        pass
    return DUAL_LISTED_ALLOWLIST


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
        # Session 22: BSE bhavcopy unavailable (Cloudflare blocks cloud IPs).
        # Use curated DUAL_LISTED_ALLOWLIST so widely-traded names like SBIN,
        # M&M, TITAN, BHARTIARTL get correctly tagged as DUAL_LISTED instead
        # of all defaulting to NSE_ONLY.
        # v11.0.2: union with runtime-discovered symbols via get_effective_allowlist().
        nse_df = nse_df.copy()
        _sym_upper = nse_df["symbol"].astype(str).str.strip().str.upper()
        _eff_allow = get_effective_allowlist()
        nse_df["exchange_tag"] = np.where(
            _sym_upper.isin(_eff_allow),
            "DUAL_LISTED",
            "NSE_ONLY"
        )
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
        # v12.0.1 BUG FIX: only merge rows that have a real (12+ char) ISIN.
        # Previously the full nse_df + bse_df were merged on isin=='' which
        # produced a Cartesian explosion of false-positive DUAL_LISTED tags
        # for every NSE row with a missing ISIN against every BSE row with a
        # missing ISIN. Symptom: ~99% DUAL_LISTED, index tickers (IT, PSUBANK,
        # BANKNIFTY1) tagged DUAL_LISTED, BSE_ONLY/BSE_SME counts = 0.
        # Fix: split the merge into (a) ISIN-matched rows on real ISINs only,
        # then (b) symbol-fallback for rows that didn't have an ISIN to merge on.
        nse_with_isin = nse_df[nse_has_isin].copy()
        bse_with_isin = bse_df[bse_has_isin].copy()
        nse_without_isin = nse_df[~nse_has_isin].copy()
        bse_without_isin = bse_df[~bse_has_isin].copy()

        merged_isin = pd.merge(
            nse_with_isin,
            bse_with_isin,
            on="isin",
            how="outer",
            suffixes=("_NSE", "_BSE"),
        )

        # For rows that lacked an ISIN, fall back to symbol-merge so we don't
        # lose them entirely. Symbol-merge produces the same column shape
        # (close_NSE / close_BSE / sc_group_BSE / etc.) so _apply_exchange_tag
        # below works uniformly.
        if not nse_without_isin.empty or not bse_without_isin.empty:
            merged_sym = pd.merge(
                nse_without_isin,
                bse_without_isin,
                on="symbol",
                how="outer",
                suffixes=("_NSE", "_BSE"),
            )
            # Combine both
            merged = pd.concat([merged_isin, merged_sym], ignore_index=True, sort=False)
        else:
            merged = merged_isin

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

    # Session 22 safety override: if a stock is on the curated allowlist
    # but the BSE merge somehow tagged it as NSE_ONLY (ISIN format issue,
    # symbol case mismatch, partial BSE download, etc.), promote it back
    # to DUAL_LISTED. This handles partial-data edge cases without
    # masking real issues — the main merge path is still primary.
    _final_sym_col = "symbol_NSE" if "symbol_NSE" in merged.columns else "symbol"
    if _final_sym_col in merged.columns:
        _sym_check = merged[_final_sym_col].astype(str).str.strip().str.upper()
        # v11.0.2: union with runtime-discovered symbols
        _eff_allow = get_effective_allowlist()
        _is_known_dual = _sym_check.isin(_eff_allow)
        _was_nse_only  = (merged["exchange_tag"] == "NSE_ONLY")
        merged.loc[_is_known_dual & _was_nse_only, "exchange_tag"] = "DUAL_LISTED"

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