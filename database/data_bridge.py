"""
data_bridge.py
SECTION 1A & 1B — Data Consolidation & DB Bridge (v7 FINAL)

Handles:
  NSE new bhav  : TckrSymb, ClsPric, PrvsClsgPric, HghPric, LwPric,
                  TtlTradgVol, TtlTrfVal, ISIN, SctySrs
  NSE old bhav  : SYMBOL, SERIES, CLOSE, PREV_CLOSE, OPEN, HIGH, LOW,
                  TOTTRDQTY, TOTTRDVAL
  NSE delivery  : SYMBOL, CLOSE_PRICE, DELIV_PER
  BSE bhav      : SC_NAME→symbol, SC_CODE→bse_code, ISIN, CLOSE,
                  OPEN, HIGH, LOW, NO_OF_SHRS, NET_TURNOV, SC_GROUP
  BSE = None    : treated as NSE-only mode, never blocks pipeline
"""

import sqlite3
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — TABLE INITIALISATION
# ─────────────────────────────────────────────────────────────────────────────

def initialize_v7_tables(conn):
    """Creates all v7 tables if they don't exist. Safe to call every run."""
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol       TEXT,
            bse_code     TEXT DEFAULT '',
            isin         TEXT DEFAULT '',
            date         TEXT,
            open         REAL DEFAULT 0,
            high         REAL DEFAULT 0,
            low          REAL DEFAULT 0,
            close        REAL DEFAULT 0,
            prev_close   REAL DEFAULT 0,
            volume       REAL DEFAULT 0,
            turnover     REAL DEFAULT 0,
            delivery_pct REAL DEFAULT 0,
            exchange     TEXT DEFAULT '',
            exchange_tag TEXT DEFAULT '',
            PRIMARY KEY (symbol, date, exchange)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            symbol   TEXT PRIMARY KEY,
            active   INTEGER DEFAULT 1,
            added_on TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS market_holidays (
            date     TEXT,
            name     TEXT,
            exchange TEXT,
            PRIMARY KEY (date, exchange)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS run_stats (
            run_date          TEXT PRIMARY KEY,
            total_universe    INTEGER DEFAULT 0,
            stage1_passed     INTEGER DEFAULT 0,
            stage2_passed     INTEGER DEFAULT 0,
            stage3_selected   INTEGER DEFAULT 0,
            claude_analysed   INTEGER DEFAULT 0,
            gold_count        INTEGER DEFAULT 0,
            gate_check_result TEXT    DEFAULT '',
            bse_available     INTEGER DEFAULT 0,
            delivery_time     TEXT    DEFAULT ''
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS fo_participant_data (
            date               TEXT,
            client_type        TEXT,
            future_index_long  REAL DEFAULT 0,
            future_index_short REAL DEFAULT 0,
            future_stock_long  REAL DEFAULT 0,
            future_stock_short REAL DEFAULT 0,
            total_long         REAL DEFAULT 0,
            total_short        REAL DEFAULT 0,
            net_value          REAL DEFAULT 0,
            PRIMARY KEY (date, client_type)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS v7_intelligence (
            symbol       TEXT,
            timestamp    TEXT,
            fii_holding  REAL DEFAULT 0,
            dii_holding  REAL DEFAULT 0,
            pledge_pct   REAL DEFAULT 0,
            promoter_pct REAL DEFAULT 0,
            total_debt   REAL DEFAULT 0,
            dio          REAL DEFAULT 0,
            dso          REAL DEFAULT 0,
            roe          REAL DEFAULT 0,
            networth     REAL DEFAULT 1,
            PRIMARY KEY (symbol, timestamp)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS latest_analysis_results (
            symbol                          TEXT PRIMARY KEY,
            date                            TEXT,
            composite_score                 REAL DEFAULT 0,
            early_score                     REAL DEFAULT 0,
            spike_score                     INTEGER DEFAULT 0,
            storm_score                     REAL DEFAULT 0,
            cfv                             REAL DEFAULT 0,
            mos_pct                         REAL DEFAULT 0,
            verdict                         TEXT DEFAULT '',
            ai_card                         TEXT DEFAULT '',
            analysis_summary                TEXT DEFAULT '',
            allocation_tag                  TEXT DEFAULT '',
            consecutive_avoid_quarters      INTEGER DEFAULT 0,
            consecutive_recovery_quarters   INTEGER DEFAULT 0
        )
    """)

    # v11.0.2: Backward-compat ALTER for DBs created before streak columns existed.
    # SQLite ADD COLUMN is idempotent if we catch the OperationalError.
    for col_def in ("consecutive_avoid_quarters INTEGER DEFAULT 0",
                    "consecutive_recovery_quarters INTEGER DEFAULT 0"):
        try:
            c.execute(f"ALTER TABLE latest_analysis_results ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass  # column already exists — fine

    c.execute("""
        CREATE TABLE IF NOT EXISTS bulk_deals (
            symbol   TEXT,
            date     TEXT,
            client   TEXT,
            type     TEXT,
            quantity REAL DEFAULT 0,
            price    REAL DEFAULT 0,
            PRIMARY KEY (symbol, date, client, type)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS insider_trades (
            symbol TEXT,
            date   TEXT,
            name   TEXT,
            mode   TEXT,
            qty    REAL DEFAULT 0,
            value  REAL DEFAULT 0,
            PRIMARY KEY (symbol, date, name, mode)
        )
    """)
    # NEW: Historical Shareholding Table for QoQ Delta
    c.execute("""
        CREATE TABLE IF NOT EXISTS shareholding_history (
            symbol TEXT,
            date TEXT,
            promoter_pct REAL,
            fii_pct REAL,
            dii_pct REAL,
            pledge_pct REAL,
            PRIMARY KEY (symbol, date)
        )
    """)
    # ────────────────────────────────────────────────────────────────────
    # v14.0 — OUTCOME TRACKING TABLES
    # gold_recommendations: append-only log of every Gold-sheet pick.
    # Written when generate_excel_reports() builds the Gold sheet.
    # PRIMARY KEY (symbol, recommendation_date) prevents same-day duplicates;
    # the master_funnel writer also enforces "first-appearance only" by
    # checking if any OPEN recommendation already exists for that symbol
    # before inserting a new one.
    # ────────────────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS gold_recommendations (
            recommendation_date     TEXT,
            symbol                  TEXT,
            company_name            TEXT DEFAULT '',
            sector                  TEXT DEFAULT '',
            cap_category            TEXT DEFAULT '',
            cmp_at_recommendation   REAL DEFAULT 0,
            entry_low               REAL DEFAULT 0,
            entry_high              REAL DEFAULT 0,
            stop_loss               REAL DEFAULT 0,
            t1                      REAL DEFAULT 0,
            t2                      REAL DEFAULT 0,
            t3                      REAL DEFAULT 0,
            cfv                     REAL DEFAULT 0,
            mos_pct                 REAL DEFAULT 0,
            composite_score         REAL DEFAULT 0,
            early_entry_score       REAL DEFAULT 0,
            quick_pick_label        TEXT DEFAULT '',
            verdict                 TEXT DEFAULT '',
            time_horizon            TEXT DEFAULT '',
            predicted_rr            REAL DEFAULT 0,
            PRIMARY KEY (symbol, recommendation_date)
        )
    """)
    # gold_outcomes: written by track_outcomes.py after walking forward
    # through daily_prices. ONE row per closed/open recommendation.
    # outcome_type ∈ {SL_HIT, T1_HIT, T2_HIT, T3_HIT, EXPIRED, OPEN}
    # When OPEN, outcome_date is NULL — the tracker overwrites the row
    # each run until the recommendation closes.
    c.execute("""
        CREATE TABLE IF NOT EXISTS gold_outcomes (
            recommendation_date     TEXT,
            symbol                  TEXT,
            outcome_type            TEXT DEFAULT 'OPEN',
            outcome_date            TEXT DEFAULT '',
            outcome_price           REAL DEFAULT 0,
            days_to_outcome         INTEGER DEFAULT 0,
            max_drawdown_pct        REAL DEFAULT 0,
            max_runup_pct           REAL DEFAULT 0,
            current_price           REAL DEFAULT 0,
            current_pnl_pct         REAL DEFAULT 0,
            last_checked_date       TEXT DEFAULT '',
            PRIMARY KEY (symbol, recommendation_date)
        )
    """)
    # Index for fast lookups of OPEN recommendations during track_outcomes
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_gold_outcomes_open
        ON gold_outcomes (outcome_type) WHERE outcome_type = 'OPEN'
    """)
    # ────────────────────────────────────────────────────────────────────
    # v14.1 — Horizon-aware expiry + reappearance tracking
    # Backward-compat: ALTER TABLE ADD COLUMN. SQLite raises OperationalError
    # if column already exists — caught silently. Idempotent on re-runs.
    # gold_recommendations gets:
    #   expiry_days       INTEGER — captured at log time per Horizon
    #   expiry_date       TEXT    — pre-computed YYYY-MM-DD
    #   times_reappeared  INTEGER — count of subsequent same-day reappearances
    #                               while this recommendation is still OPEN
    # gold_outcomes gets:
    #   last_reappeared_date TEXT — most recent same-day reappearance while OPEN
    # ────────────────────────────────────────────────────────────────────
    for col_def in [
        ("gold_recommendations", "expiry_days INTEGER DEFAULT 90"),
        ("gold_recommendations", "expiry_date TEXT DEFAULT ''"),
        ("gold_recommendations", "times_reappeared INTEGER DEFAULT 0"),
        ("gold_outcomes",        "last_reappeared_date TEXT DEFAULT ''"),
        # ────────────────────────────────────────────────────────────────
        # v15.0 — Trailing stop + earnings awareness + ATR snapshot
        # ────────────────────────────────────────────────────────────────
        # gold_recommendations gets:
        #   original_stop_loss  REAL — frozen SL at log time (for audit even
        #                              if trailing SL has moved up); equal
        #                              to stop_loss at log time
        #   atr_at_rec          REAL — ATR-14 % at recommendation (audit/debug)
        #   regime_at_rec       TEXT — 'high'/'neutral'/'low' regime at log
        #   next_earnings_date  TEXT — YYYY-MM-DD of next quarterly results
        # gold_outcomes gets:
        #   trailing_sl_pct     REAL — current trailing SL as % below CMP
        #                              (0 means trailing hasn't activated)
        #   trailing_sl_price   REAL — absolute price level of trailing SL
        #   peak_price_seen     REAL — highest price observed during tracking
        # ────────────────────────────────────────────────────────────────
        ("gold_recommendations", "original_stop_loss REAL DEFAULT 0"),
        ("gold_recommendations", "atr_at_rec REAL DEFAULT 0"),
        ("gold_recommendations", "regime_at_rec TEXT DEFAULT 'neutral'"),
        ("gold_recommendations", "next_earnings_date TEXT DEFAULT ''"),
        ("gold_outcomes",        "trailing_sl_pct REAL DEFAULT 0"),
        ("gold_outcomes",        "trailing_sl_price REAL DEFAULT 0"),
        ("gold_outcomes",        "peak_price_seen REAL DEFAULT 0"),
        # ────────────────────────────────────────────────────────────────
        # v15.7 — Institutional risk-parity sizing surface on Performance
        # ────────────────────────────────────────────────────────────────
        # gold_recommendations gets:
        #   suggested_alloc_pct REAL — v15.5 risk-parity allocation % at
        #                              log time (frozen for audit; can be
        #                              recomputed any time from sector+cap+sl_pct)
        #   alloc_rationale     TEXT — human-readable derivation, e.g.,
        #                              "Risk parity: 1.0% / 8.0% = 12.50%"
        # These let Performance sheet's OPEN POSITIONS render the sizing
        # alongside running P&L without needing to re-compute every refresh.
        ("gold_recommendations", "suggested_alloc_pct REAL DEFAULT 0"),
        ("gold_recommendations", "alloc_rationale TEXT DEFAULT ''"),
    ]:
        try:
            c.execute(f"ALTER TABLE {col_def[0]} ADD COLUMN {col_def[1]}")
        except sqlite3.OperationalError:
            pass  # column already exists — fine
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — COLUMN MAPPING
# ─────────────────────────────────────────────────────────────────────────────

# All keys are LOWERCASE (we lowercase headers before lookup).
# Maps every known raw column name → canonical v7 name.
COLUMN_MAP = {
    # NSE new bhav (post-July 2024 UDiFF format)
    "tckrsymb":        "symbol",
    "isin":            "isin",
    "clspric":         "close",
    "prvsclsgpric":    "prev_close",
    "opnpric":         "open",
    "hghpric":         "high",
    "lwpric":          "low",
    "ttltradgvol":     "volume",
    "ttltrfval":       "turnover",
    "sctysrs":         "series",
    # "fininstrmid" intentionally NOT mapped to symbol —
    # FinInstrmId is NSE's internal numeric instrument ID, NOT the ticker.
    # TckrSymb is the correct ticker symbol (e.g. "RELIANCE").
    # Mapping both to "symbol" creates duplicate columns → KeyError: 0.
    # NSE old bhav (pre-July 2024)
    "symbol":          "symbol",
    "series":          "series",
    "close_price":     "close",
    "close":           "close",
    "prev_close":      "prev_close",
    "prevclose":       "prev_close",
    "previous_close":  "prev_close",
    "open_price":      "open",
    "open":            "open",
    "high_price":      "high",
    "high":            "high",
    "low_price":       "low",
    "low":             "low",
    "tottrdqty":       "volume",
    "total_trd_qty":   "volume",
    "ttl_trd_qnty":    "volume",
    "tottrdval":       "turnover",
    # NSE delivery file
    "deliv_per":       "delivery_pct",
    "deliv_qty":       "deliv_qty",
    # BSE bhav  ← SC_NAME is the ticker string; SC_CODE is the numeric code
    "sc_name":         "symbol",      # "RELIANCE", "HDFCBANK" — USE THIS
    "sc_code":         "bse_code",    # 500325 — store separately, NOT as symbol
    "sc_group":        "sc_group",
    "isin_code":       "isin",
    "no_of_shrs":      "volume",
    "net_turnover":    "turnover",
    "net_turnov":      "turnover",
    # Generic aliases
    "no_of_trades":    "num_trades",
    "security_id":     "symbol",
    "scrip_id":        "symbol",
    "syml":            "symbol",
    "name":            "company_name",
}

REQUIRED_COLS  = ["symbol", "open", "high", "low", "close", "volume"]
OPTIONAL_COLS  = ["isin", "bse_code", "prev_close", "turnover",
                  "delivery_pct", "sc_group", "exchange", "exchange_tag"]

def get_qoq_delta(symbol, current_val, field_name):
    """Calculates change vs approx 90 days ago."""
    try:
        conn = sqlite3.connect("market_data.db")
        # Look back 80-100 days to find the previous quarter's data
        query = f"SELECT {field_name} FROM shareholding_history WHERE symbol = ? AND date < date('now', '-80 days') ORDER BY date DESC LIMIT 1"
        prev_val = conn.execute(query, (symbol,)).fetchone()
        conn.close()
        
        if prev_val and prev_val[0] is not None:
            delta = current_val - prev_val[0]
            return round(delta, 2)
    except Exception:
        pass
    return 0.0
# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — SCHEMA NORMALISATION
# ─────────────────────────────────────────────────────────────────────────────

def standardize_to_v7_schema(df, exchange: str = "NSE") -> pd.DataFrame:
    """
    Normalises ANY raw Bhav Copy DataFrame to the canonical v7 column set.

    Safe against:
      - None input
      - Mixed-type symbol columns (int, float, str)
      - BSE numeric SC_CODE being mapped to symbol by mistake
      - Missing columns (filled with safe defaults)
      - .str accessor on non-string series (AttributeError fixed)

    Always returns a DataFrame, never None.
    """
    # ── Guard: invalid input ──────────────────────────────────────────────────
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        empty = pd.DataFrame(columns=REQUIRED_COLS + OPTIONAL_COLS)
        return empty

    df = df.copy()

    # ── Step 1: Normalise column names to lowercase, no spaces ───────────────
    df.columns = [str(c).lower().strip().replace(" ", "_") for c in df.columns]

    # ── Step 2: Apply master column map ──────────────────────────────────────
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items()
                             if k in df.columns})

    # Remove duplicate column names — if two source cols mapped to the same
    # target (e.g. both TckrSymb and FinInstrmId → "symbol") the DataFrame
    # ends up with two cols both named "symbol". df["symbol"] then returns
    # a 2-column DataFrame instead of a Series → KeyError: 0.
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    # Reset index — prevents index-alignment errors on subsequent assignments
    df = df.reset_index(drop=True)

    # ── Step 3: BSE guard — SC_CODE (numeric) must NOT become symbol ─────────
    # After rename, if 'symbol' column is all-numeric it means SC_CODE was
    # mapped (SC_NAME was missing or came after).  Move it to bse_code and
    # look for a name-like column to use as symbol instead.
    if "symbol" in df.columns:
        # Force to string first so .str accessor works safely
        sym_series = df["symbol"].fillna("").apply(lambda x: str(x).strip())
        non_empty  = sym_series[sym_series != ""]
        if len(non_empty) > 0 and non_empty.apply(lambda x: x.isdigit()).all():
            # All values are numeric → this is BSE SC_CODE, not the ticker
            if "bse_code" not in df.columns:
                df["bse_code"] = sym_series
            # Replace symbol with the name column if available
            for alt in ["sc_name", "name", "security_name", "scrip_name", "company_name"]:
                if alt in df.columns and df[alt].notna().any():
                    df["symbol"] = df[alt].fillna("").apply(lambda x: str(x).strip())
                    break
            else:
                # No name column found — symbol stays numeric (BSE code)
                df["symbol"] = sym_series

    # ── Step 4: Guarantee all required + optional columns exist ──────────────
    for col in REQUIRED_COLS + OPTIONAL_COLS:
        if col not in df.columns:
            if col in ("symbol", "isin", "bse_code", "sc_group",
                       "exchange", "exchange_tag"):
                df[col] = ""
            else:
                df[col] = 0.0

    # ── Step 5: Coerce numeric columns ───────────────────────────────────────
    for col in ["open", "high", "low", "close", "prev_close",
                "volume", "turnover", "delivery_pct"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # ── Step 6: Force symbol to clean string (MUST come before any filtering)─
    df["symbol"] = df["symbol"].fillna("").apply(lambda x: str(x).strip())

    # ── Step 7: Drop garbage rows ─────────────────────────────────────────────
    df = df[~df["symbol"].isin(["", "0", "nan", "none", "NaN"])]
    df = df[df["close"] > 0]
    df = df.reset_index(drop=True)

    # ── Step 8: Tag exchange ──────────────────────────────────────────────────
    df["exchange"] = exchange

    # ── Step 9: Trim to only DB storage columns ─────────────────────────────────
    # Drops series, num_trades, sc_group etc. — not needed in DB.
    # Does NOT affect get_today_consolidated_data() which runs after this
    # and adds date, final_symbol etc. for screening.
    _STORE_COLS = ["symbol", "bse_code", "isin", "open", "high", "low", "close",
                   "prev_close", "volume", "turnover", "delivery_pct",
                   "exchange", "exchange_tag"]
    df = df[[c for c in _STORE_COLS if c in df.columns]]

    return df


# ──────────────────────────────────────────────────────────────────────────────
# SECTION 4 — DELIVERY DATA MERGE
# ─────────────────────────────────────────────────────────────────────────────

def merge_delivery_data(price_df: pd.DataFrame,
                        delivery_df,
                        exchange: str = "NSE") -> pd.DataFrame:
    """
    Left-joins delivery_pct from the NSE/BSE delivery file onto the price df.
    Safe when delivery_df is None — returns price_df unchanged.
    """
    if delivery_df is None or not isinstance(delivery_df, pd.DataFrame) \
            or delivery_df.empty:
        return price_df

    if price_df is None or not isinstance(price_df, pd.DataFrame) \
            or price_df.empty:
        return price_df

    try:
        deliv = delivery_df.copy()
        deliv.columns = [str(c).lower().strip().replace(" ", "_")
                         for c in deliv.columns]
        deliv = deliv.rename(columns={k: v for k, v in COLUMN_MAP.items()
                                       if k in deliv.columns})

        if "symbol" not in deliv.columns or "delivery_pct" not in deliv.columns:
            return price_df

        deliv = deliv[["symbol", "delivery_pct"]].copy()
        deliv["delivery_pct"] = pd.to_numeric(
            deliv["delivery_pct"], errors="coerce").fillna(0)
        # Normalise symbol to uppercase string for reliable join
        deliv["symbol"] = deliv["symbol"].fillna("").apply(
            lambda x: str(x).strip().upper())

        price = price_df.copy()
        price["symbol"] = price["symbol"].fillna("").apply(
            lambda x: str(x).strip().upper())

        # Remove existing delivery_pct to avoid _x/_y suffix collision
        if "delivery_pct" in price.columns:
            price = price.drop(columns=["delivery_pct"])

        merged = price.merge(deliv, on="symbol", how="left")
        merged["delivery_pct"] = merged["delivery_pct"].fillna(0)
        return merged

    except Exception as e:
        print(f"⚠️  merge_delivery_data error: {e}. Skipping delivery merge.")
        return price_df


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — CONSOLIDATION HUB
# ─────────────────────────────────────────────────────────────────────────────

def get_today_consolidated_data(target_date,
                                nse_main=None, nse_sme=None,
                                bse_main=None, bse_sme=None,
                                nse_deliv=None, bse_deliv=None) -> pd.DataFrame:
    """
    Standardises all 4 price streams + 2 delivery streams into one DataFrame.
    BSE streams being None is normal in NSE-only mode — handled gracefully.
    """
    from ingestion.reconciler import reconcile_exchanges

    date_str = (target_date.strftime("%Y-%m-%d")
                if hasattr(target_date, "strftime")
                else str(target_date))
    print(f"🔄 [V7] Consolidating market data for {date_str}...")

    # Standardise all 4 streams (None-safe)
    n_m = standardize_to_v7_schema(nse_main, exchange="NSE")
    n_s = standardize_to_v7_schema(nse_sme,  exchange="NSE_SME")
    b_m = standardize_to_v7_schema(bse_main, exchange="BSE")
    b_s = standardize_to_v7_schema(bse_sme,  exchange="BSE_SME")

    # Merge delivery percentages
    n_m = merge_delivery_data(n_m, nse_deliv, exchange="NSE")
    b_m = merge_delivery_data(b_m, bse_deliv, exchange="BSE")

    # Stack NSE and BSE separately (concat is safe even if both sides empty)
    all_nse = pd.concat([n_m, n_s], ignore_index=True)
    all_bse = pd.concat([b_m, b_s], ignore_index=True)

    if all_nse.empty and all_bse.empty:
        print("⚠️  Both NSE and BSE streams empty. Returning empty DataFrame.")
        return pd.DataFrame()

    # Cross-exchange reconciliation via ISIN
    try:
        consolidated_df = reconcile_exchanges(all_nse, all_bse)
    except Exception as e:
        print(f"⚠️  Reconciler error: {e}. Using NSE-only data.")
        consolidated_df = all_nse

    # v11.0.2: When BSE merge actually succeeded, record any new dual-listed
    # observations to the runtime allowlist table so they survive future
    # BSE-blocked runs. Failures here are non-fatal (allowlist still works
    # from hardcoded DUAL_LISTED_ALLOWLIST).
    if (consolidated_df is not None and not consolidated_df.empty
            and all_bse is not None and not all_bse.empty):
        try:
            from ingestion.allowlist_maintainer import record_dual_listed_observations
            from ingestion.reconciler import DUAL_LISTED_ALLOWLIST
            new_count = record_dual_listed_observations(
                consolidated_df,
                today_iso=date_str,
                hardcoded_allowlist=DUAL_LISTED_ALLOWLIST,
            )
            if new_count > 0:
                print(f"   📥 Allowlist auto-add: {new_count} new dual-listed symbol(s) observed today")
        except Exception as _e:
            print(f"   ⚠️  Allowlist recorder skipped: {_e}")

    if consolidated_df is not None and not consolidated_df.empty:
        consolidated_df["date"] = date_str
        print(f"✅ V7 Consolidation Complete: {len(consolidated_df)} records.")
        return consolidated_df

    return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — DATA INTEGRITY CHECK (C5 Gate)
# ─────────────────────────────────────────────────────────────────────────────

def check_data_integrity(nse_df, bse_df) -> dict:
    """
    Lightweight C5 check: validates only row count and DataFrame type.
    Does NOT call standardize_to_v7_schema — that runs in consolidation.
    BSE=None is always acceptable (NSE-only mode).
    """
    # NSE is the only hard requirement
    if nse_df is None:
        return {"pass": False,
                "message": "C5 FAIL: NSE DataFrame is None — download failed."}
    if not isinstance(nse_df, pd.DataFrame):
        return {"pass": False,
                "message": f"C5 FAIL: NSE unexpected type {type(nse_df)}."}
    if nse_df.empty:
        return {"pass": False,
                "message": "C5 FAIL: NSE DataFrame is empty."}
    if len(nse_df) < 500:
        return {"pass": False,
                "message": (f"C5 FAIL: NSE only {len(nse_df)} rows "
                            f"(minimum 500). File may be corrupt.")}

    nse_msg = f"NSE OK — {len(nse_df)} rows, {len(nse_df.columns)} columns"
    print(f"✅ C5 PASS: {nse_msg}")

    if bse_df is None or not isinstance(bse_df, pd.DataFrame) or bse_df.empty:
        bse_msg = "BSE not available (NSE-only mode — normal on cloud runners)"
    else:
        bse_msg = f"BSE OK — {len(bse_df)} rows"

    return {
        "pass":    True,
        "message": f"C5 PASS: {nse_msg} | {bse_msg}",
        "bse_ok":  isinstance(bse_df, pd.DataFrame) and not bse_df.empty,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — SAVE METHODS
# ─────────────────────────────────────────────────────────────────────────────

def save_to_database(df=None, nse_data=None, bse_data=None,
                     nse_del=None, bse_del=None,
                     sme_nse=None, sme_bse=None,
                     participant_data=None,
                     table: str = "daily_prices") -> None:
    """
    Saves market data to SQLite.
    Accepts EITHER a single positional df OR named keyword streams.
    None streams are silently skipped.
    """
    conn = sqlite3.connect("market_data.db")
    try:
        initialize_v7_tables(conn)
        from datetime import date as _date
        today_str = _date.today().strftime("%Y-%m-%d")

        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            if table == "bulk_deals":
                df = df.copy()
                df.columns = [str(c).lower().strip() for c in df.columns]
                _bmap = {
                    "symbol":"symbol",  "scrip_cd":"symbol",
                    "clientname":"client", "client_name":"client", "client":"client",
                    "dealtype":"type",  "deal_type":"type",
                    "buysell":"type",   "buy_sell":"type",
                    "trdqty":"quantity","quantity":"quantity",
                    "price":"price",    "trdval":"price",
                }
                df = df.rename(columns={k:v for k,v in _bmap.items()
                                         if k in df.columns and v not in df.columns})
                if "symbol" not in df.columns:
                    for _alt in ["scrip_name","security","company"]:
                        if _alt in df.columns:
                            df["symbol"] = df[_alt]; break
                if "date" not in df.columns:
                    df["date"] = today_str
            elif table == "insider_trades":
                df = df.copy()
                df.columns = [str(c).lower().strip() for c in df.columns]
                _imap = {
                    "symbol":"symbol",    "company":"symbol",
                    "name":"name",        "personname":"name",   "person_name":"name",
                    "acqmode":"mode",     "acqui_mode":"mode",   "mode":"mode",
                    "secacq":"qty",       "qty":"qty",
                    "totacqshares":"qty", "secval":"value",      "value":"value",
                }
                df = df.rename(columns={k:v for k,v in _imap.items()
                                         if k in df.columns and v not in df.columns})
                if "date" not in df.columns:
                    df["date"] = today_str
            _safe_insert(df, table, conn)
        else:
            # Delivery streams (nse_del, bse_del) excluded intentionally:
            # they contain DELIV_PER not OHLCV, so standardize_to_v7_schema
            # produces volume=0 rows that contaminate the price universe
            # and cause Stage 1 to drop all stocks as no_vol.
            # Delivery pct is applied separately in get_today_consolidated_data.
            streams = [
                (nse_data, "NSE"),
                (bse_data, "BSE"),
                (sme_nse,  "NSE_SME"),
                (sme_bse,  "BSE_SME"),
            ]
            frames = []
            for stream_src, exch in streams:
                if stream_src is not None and isinstance(stream_src, pd.DataFrame) \
                        and not stream_src.empty:
                    std = standardize_to_v7_schema(stream_src, exchange=exch)
                    if not std.empty:
                        if "date" not in std.columns:
                            std["date"] = today_str
                        frames.append(std)
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                if "date" not in combined.columns:
                    combined["date"] = today_str
                # v12.7 (#11 FIX): scope the DELETE to the date(s) actually
                # present in `combined`. Pre-fix this used today_str (server
                # wallclock) which can differ from the data's date when
                # (a) the runner clock has crossed midnight UTC but IST
                # is still on the previous day, or (b) gap-fill is
                # writing rows for a non-today date. The mismatch could
                # DELETE the wrong day's rows (silently no-op) and then
                # INSERT OR IGNORE could skip the insert on PK conflict
                # if rows already existed. Use the actual data dates.
                _data_dates = sorted({str(d) for d in combined["date"].unique() if d})
                try:
                    if _data_dates:
                        _ph = ",".join(["?"] * len(_data_dates))
                        conn.execute(
                            f"DELETE FROM daily_prices WHERE date IN ({_ph})",
                            _data_dates
                        )
                        conn.commit()
                except Exception:
                    pass
                _safe_insert(combined, "daily_prices", conn)

        if (participant_data is not None
                and isinstance(participant_data, pd.DataFrame)
                and not participant_data.empty):
            fo = participant_data.copy()
            fo.columns = [str(c).lower().strip().replace(" ", "_") for c in fo.columns]
            fo_col_map = {
                "client_type":           "client_type",
                "clienttype":            "client_type",
                "client":                "client_type",
                "future_index_long":     "future_index_long",
                "fut_index_long":        "future_index_long",
                "future_index_short":    "future_index_short",
                "fut_index_short":       "future_index_short",
                "future_stock_long":     "future_stock_long",
                "fut_stk_long":          "future_stock_long",
                "future_stock_short":    "future_stock_short",
                "fut_stk_short":         "future_stock_short",
                "total_long_contracts":  "total_long",
                "total_long":            "total_long",
                "total_short_contracts": "total_short",
                "total_short":           "total_short",
                "net":                   "net_value",
                "net_oi":                "net_value",
                "net_value":             "net_value",
            }
            fo = fo.rename(columns={k: v for k, v in fo_col_map.items() if k in fo.columns})
            # Compute net_value = total_long - total_short if not in CSV
            if "net_value" not in fo.columns:
                tl = pd.to_numeric(fo.get("total_long",  0), errors="coerce").fillna(0)
                ts = pd.to_numeric(fo.get("total_short", 0), errors="coerce").fillna(0)
                fo["net_value"] = tl - ts
            for _c in ["future_index_long","future_index_short","future_stock_long",
                       "future_stock_short","total_long","total_short","net_value"]:
                if _c in fo.columns:
                    fo[_c] = pd.to_numeric(fo[_c], errors="coerce").fillna(0)
            fo["date"] = today_str
            try:
                conn.execute("DELETE FROM fo_participant_data WHERE date = ?", (today_str,))
                conn.commit()
            except Exception:
                pass
            _safe_insert(fo, "fo_participant_data", conn)

    except Exception as e:
        print(f"❌ Database Bridge Error: {e}")
    finally:
        conn.close()


def _safe_insert(df: pd.DataFrame, table: str, conn) -> None:
    """INSERT OR IGNORE via a temp table — handles PRIMARY KEY conflicts."""
    if df is None or df.empty:
        return

    # ── Column guard: only keep columns that exist in the target table ───────
    # Prevents "table X has N columns but M values were supplied" when the
    # source DataFrame (e.g. from SmartMoneyScraper) has extra columns.
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        table_cols = [row[1] for row in cursor.fetchall()]
        if table_cols:
            # Keep only columns present in both df and the table
            keep = [c for c in table_cols if c in df.columns]
            if not keep:
                print(f"⚠️  _safe_insert: no matching columns for table '{table}'. Skipping.")
                return
            df = df[keep]
    except Exception:
        pass  # If PRAGMA fails, proceed as-is

    tmp = f"_tmp_{table}"
    df.to_sql(tmp, conn, if_exists="replace", index=False)
    col_list = ", ".join(keep)
    conn.execute(
        f"INSERT OR IGNORE INTO {table} ({col_list}) "
        f"SELECT {col_list} FROM {tmp}"
    )
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.commit()
    try:
        import os as _os
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
        if db_path and _os.path.exists(db_path):
            size_mb = _os.path.getsize(db_path) / 1_048_576
            row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"✅ Saved {len(df)} records → {table} | DB total: {row_count:,} rows | Size: {size_mb:.2f} MB")
        else:
            print(f"✅ Saved {len(df)} records → {table}.")
    except Exception:
        print(f"✅ Saved {len(df)} records → {table}.")


def save_fo_data(df: pd.DataFrame, target_date) -> None:
    """Saves F&O Participant Data (Section 1A)."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return
    date_str = (target_date.strftime("%Y-%m-%d")
                if hasattr(target_date, "strftime") else str(target_date))
    conn = sqlite3.connect("market_data.db")
    try:
        initialize_v7_tables(conn)
        df = df.copy()
        df["date"] = date_str
        df.to_sql("fo_participant_data", conn, if_exists="append", index=False)
        conn.commit()
        print(f"✅ F&O data saved for {date_str}")
    except Exception as e:
        print(f"❌ F&O DB Error: {e}")
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — RETRIEVAL METHODS
# ─────────────────────────────────────────────────────────────────────────────

def get_historical_quarter_data(symbols: list) -> dict:
    """
    Sections 3F, 3K & 4: Quarterly baseline per symbol for QoQ-Δ computation.

    Returns the shareholding snapshot closest to 90 days old for each symbol.
    If no row exists older than 90 days, returns the oldest available row for
    that symbol (better than None — at least gives a baseline to compare to).
    """
    import sqlite3
    import pandas as pd

    if not symbols:
        return {}

    conn = sqlite3.connect("market_data.db")
    try:
        placeholders = ", ".join(["?"] * len(symbols))

        # Primary source: shareholding table (has all 4 holding categories)
        try:
            df = pd.read_sql_query(
                f"""
                SELECT sh.symbol, sh.date,
                       sh.promoter_pct, sh.fii_pct, sh.dii_pct, sh.pledge_pct
                FROM shareholding sh
                INNER JOIN (
                    SELECT symbol, MAX(date) AS md
                    FROM shareholding
                    WHERE symbol IN ({placeholders})
                      AND date <= date('now', '-90 days')
                    GROUP BY symbol
                ) lt
                  ON sh.symbol = lt.symbol AND sh.date = lt.md
                """,
                conn, params=symbols,
            )
        except Exception:
            df = pd.DataFrame()

        # Fallback: if no 90-day-old row exists, take the oldest available row
        # per symbol. This gives SOME baseline even for recently-added stocks.
        missing = [s for s in symbols if s not in set(df["symbol"]) if not df.empty] \
                  if not df.empty else list(symbols)
        if missing:
            try:
                placeholders2 = ", ".join(["?"] * len(missing))
                fallback_df = pd.read_sql_query(
                    f"""
                    SELECT sh.symbol, sh.date,
                           sh.promoter_pct, sh.fii_pct, sh.dii_pct, sh.pledge_pct
                    FROM shareholding sh
                    INNER JOIN (
                        SELECT symbol, MIN(date) AS md
                        FROM shareholding
                        WHERE symbol IN ({placeholders2})
                        GROUP BY symbol
                    ) lt
                      ON sh.symbol = lt.symbol AND sh.date = lt.md
                    """,
                    conn, params=missing,
                )
                if not fallback_df.empty:
                    df = pd.concat([df, fallback_df], ignore_index=True) \
                         if not df.empty else fallback_df
            except Exception:
                pass

        # Final fallback: v7_intelligence (legacy; no dii_pct so dii_qoq stays 0)
        still_missing = [s for s in symbols
                         if df.empty or s not in set(df.get("symbol", []))]
        v7_df = pd.DataFrame()
        if still_missing:
            try:
                placeholders3 = ", ".join(["?"] * len(still_missing))
                v7_df = pd.read_sql_query(
                    f"""
                    SELECT symbol,
                           promoter_pct,
                           fii_holding AS fii_pct,
                           0          AS dii_pct,
                           pledge_pct,
                           total_debt,
                           networth,
                           dio, dso, roe
                    FROM v7_intelligence
                    WHERE symbol IN ({placeholders3})
                      AND timestamp <= datetime('now', '-90 days')
                    GROUP BY symbol
                    """,
                    conn, params=still_missing,
                )
            except Exception:
                v7_df = pd.DataFrame()

        # Build output dict
        out = {}
        if not df.empty:
            for _, r in df.iterrows():
                out[r["symbol"]] = {
                    "promoter_pct": float(r.get("promoter_pct", 0) or 0),
                    "fii_pct":      float(r.get("fii_pct", 0)      or 0),
                    "dii_pct":      float(r.get("dii_pct", 0)      or 0),
                    "pledge_pct":   float(r.get("pledge_pct", 0)   or 0),
                    "fii_holding":  float(r.get("fii_pct", 0)      or 0),  # legacy alias
                    "total_debt":   0.0,
                    "networth":     1.0,
                }
        if not v7_df.empty:
            for _, r in v7_df.iterrows():
                sym = r["symbol"]
                if sym in out:
                    continue
                out[sym] = {
                    "promoter_pct": float(r.get("promoter_pct", 0) or 0),
                    "fii_pct":      float(r.get("fii_pct", 0)      or 0),
                    "dii_pct":      0.0,
                    "pledge_pct":   float(r.get("pledge_pct", 0)   or 0),
                    "fii_holding":  float(r.get("fii_pct", 0)      or 0),
                    "total_debt":   float(r.get("total_debt", 0)   or 0),
                    "networth":     float(r.get("networth", 1)     or 1),
                    "dio":          float(r.get("dio", 0)          or 0),
                    "dso":          float(r.get("dso", 0)          or 0),
                    "roe":          float(r.get("roe", 0)          or 0),
                }

        # Any symbol still not found → None (caller handles this — existing behaviour)
        return {s: out.get(s) for s in symbols}

    except Exception as e:
        print(f"   ⚠️  get_historical_quarter_data failed: {e}")
        return {s: None for s in symbols}
    finally:
        conn.close()




def get_latest_fii_net_cash() -> float:
    """Latest FII net cash flow from F&O participant data."""
    conn = sqlite3.connect("market_data.db")
    try:
        r = pd.read_sql_query(
            "SELECT net_value FROM fo_participant_data "
            "WHERE client_type='FII' ORDER BY date DESC LIMIT 1", conn)
        return float(r["net_value"].iloc[0]) if not r.empty else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def get_nifty_200_sma() -> float:
    """Nifty 50 200-day simple moving average."""
    conn = sqlite3.connect("market_data.db")
    try:
        df = pd.read_sql_query(
            "SELECT close FROM daily_prices "
            "WHERE symbol='NIFTY 50' ORDER BY date DESC LIMIT 200", conn)
        return float(df["close"].mean()) if not df.empty else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def get_20d_avg_vol(symbol: str) -> float:
    """20-day average volume for a symbol — used by priority_ranker.

    v12.7 (#6 FIX): filter exchange='NSE'. Pre-fix, dual-listed symbols
    had 2× rows per date, so LIMIT 20 picked the last 20 ROWS = ~10
    trading days × 2 exchanges. AVG(volume) ended up being a mix of
    NSE and BSE volumes — typically pulling avg_vol DOWN (BSE volumes
    are usually 5-10× smaller than NSE) and inflating the priority-
    ranker's vol_spike_ratio = current_vol / avg_vol for dual-listed
    stocks, biasing Stage 3 selection toward them.
    """
    if not symbol:
        return 0.0
    conn = sqlite3.connect("market_data.db")
    try:
        r = pd.read_sql_query(
            "SELECT AVG(volume) FROM ("
            "  SELECT volume FROM daily_prices "
            "  WHERE symbol=? AND exchange='NSE' "
            "  ORDER BY date DESC LIMIT 20"
            ")",
            conn, params=(symbol,),
        )
        val = r.iloc[0, 0]
        return float(val) if val and val > 0 else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def get_20d_avg_vol_batch(symbols) -> dict:
    """
    v10.13: Batch-fetch 20-day average volume for many symbols in ONE query.
    Replaces N round-trips to SQLite (~1,500 in a typical Stage 3 run) with a
    single grouped query. Returns dict: {symbol: avg_volume}.

    Preserves the same "last 20 trading days" semantics as get_20d_avg_vol
    by using a ROW_NUMBER() window inside a CTE. Missing symbols get 0.0.
    """
    if not symbols:
        return {}
    # Deduplicate + normalise
    syms = sorted({str(s).strip() for s in symbols if s and str(s).strip()})
    if not syms:
        return {}

    conn = sqlite3.connect("market_data.db")
    try:
        # One SQL using window function: for each symbol, keep only the 20
        # most-recent rows, then AVG(volume). This matches get_20d_avg_vol's
        # semantics exactly (most-recent 20 by date per symbol).
        #
        # v12.7 (#7 FIX): added exchange='NSE' filter to the inner SELECT.
        # Pre-fix the ROW_NUMBER() partition was over (symbol) with no
        # exchange filter, so for dual-listed symbols PARTITION BY symbol
        # picked 20 rows mixing both exchanges (~10 NSE + ~10 BSE).
        # Same root cause and same fix as get_20d_avg_vol single-symbol.
        placeholders = ",".join("?" for _ in syms)
        sql = f"""
            WITH ranked AS (
                SELECT symbol, volume,
                       ROW_NUMBER() OVER (
                         PARTITION BY symbol ORDER BY date DESC
                       ) AS rn
                FROM daily_prices
                WHERE symbol IN ({placeholders})
                  AND exchange='NSE'
            )
            SELECT symbol, AVG(volume) AS avg_vol
            FROM ranked
            WHERE rn <= 20
            GROUP BY symbol
        """
        df = pd.read_sql_query(sql, conn, params=syms)
        result = {}
        for _, row in df.iterrows():
            v = row["avg_vol"]
            if v is not None and v > 0:
                result[row["symbol"]] = float(v)
        # Missing symbols implicitly return 0.0 via dict.get default
        return result
    except Exception as e:
        # Fall back to per-symbol calls so pipeline doesn't hard-fail
        # (matches the pre-v10.13 behavior if the window-function path breaks)
        print(f"⚠️ get_20d_avg_vol_batch failed ({e}); falling back to per-symbol lookups")
        return {s: get_20d_avg_vol(s) for s in syms}
    finally:
        conn.close()


def get_symbol_history(symbol: str, limit: int = 250) -> pd.DataFrame:
    """Historical OHLCV for a symbol, ascending by date.

    v12.7 (#5 FIX): two related bugs corrected here.
      a) Pre-fix, no exchange filter — for dual-listed symbols this
         returned 2× rows interleaved on each date (NSE + BSE rows for
         every trading day).
      b) Pre-fix, ORDER BY date ASC LIMIT N — for stocks with more rows
         than `limit`, this returned the OLDEST N rows, not the most
         recent N. Combined with (a), a dual-listed stock with 247×2 = 494
         rows and limit=250 returned a series ending in ~November 2025
         (the first 125 trading days of NSE+BSE pairs), not "today".
         Master_funnel:1176 then took history.iloc[-1]["close"] as
         "today's price" and walked back iloc[-11/21/31/41] to compute
         2W/4W/6W/8W changes — values shown in the Excel were 6-month-
         stale and based on interleaved exchange rows.
    Fix: filter exchange='NSE', take latest N via ORDER BY DESC LIMIT N,
    then sort ascending in pandas so the function still returns
    chronologically-ordered rows (callers depend on iloc[-1] being today).
    """
    if not symbol:
        return pd.DataFrame()
    conn = sqlite3.connect("market_data.db")
    try:
        df = pd.read_sql_query(
            "SELECT date, open, high, low, close, volume "
            "FROM daily_prices WHERE symbol=? AND exchange='NSE' "
            "ORDER BY date DESC LIMIT ?",
            conn, params=(symbol, limit),
        )
        if not df.empty:
            # Re-sort ascending so iloc[-1] is the most recent date —
            # preserves the original API contract for downstream callers
            # (master_funnel uses iloc[-1] / iloc[-n] for current/N-back).
            df = df.sort_values("date").reset_index(drop=True)
            df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce")
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def get_nifty_52w_high_from_db() -> float:
    """Nifty 50 52-week high close price."""
    conn = sqlite3.connect("market_data.db")
    try:
        c = conn.cursor()
        c.execute("SELECT MAX(close) FROM daily_prices "
                  "WHERE symbol='NIFTY 50' "
                  "AND date >= date('now','-365 days')")
        r = c.fetchone()
        return float(r[0]) if r and r[0] else 1.0
    except Exception:
        return 1.0
    finally:
        conn.close()


def get_nifty_close_from_db() -> float:
    """Most recent NIFTY 50 close price.

    v12.7 (#8 FIX): added so master_funnel can populate `nifty_close`
    correctly. Pre-fix, master_funnel mapped both `nifty_close` and
    `nifty_52w_high` to get_nifty_52w_high_from_db() — semantically
    wrong (nifty_close should be today's close, not the 52-week max).
    The daily_report_generator computed mood as
        "BULLISH" if nifty > sma200 else "BEARISH"
    so the mood was always wrong: with nifty == 52w_high (always >=
    sma200), mood would be BULLISH; or with both queries returning 0
    (NIFTY isn't ingested into daily_prices), mood was always BEARISH.
    Returns 0.0 if no NIFTY data is in the DB — daily_report_generator
    is patched in v12.7 to render "—" mood when both nifty fields are 0.
    """
    conn = sqlite3.connect("market_data.db")
    try:
        c = conn.cursor()
        c.execute("SELECT close FROM daily_prices "
                  "WHERE symbol='NIFTY 50' "
                  "ORDER BY date DESC LIMIT 1")
        r = c.fetchone()
        return float(r[0]) if r and r[0] else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def load_latest_analysis_results() -> list:
    """Load most recent AI analysis results for score comparison."""
    try:
        conn = sqlite3.connect("market_data.db")
        df = pd.read_sql("SELECT * FROM latest_analysis_results", conn)
        conn.close()
        return df.to_dict("records") if not df.empty else []
    except Exception:
        return []


def get_prior_analysis_map() -> dict:
    """
    v10.13: Returns dict {symbol: {last_score, last_verdict, date, days_since,
                                    consecutive_avoid_quarters,
                                    consecutive_recovery_quarters}}
    sourced from the latest_analysis_results table.

    Used by priority_ranker to:
      (a) populate `last_claude_score` so override rule O4 (score deterioration —
          last≥60 + today's Stage2<15) can actually fire.
      (b) populate `days_since_analysis` so override rule O5 (expiry re-check,
          not analysed in 7+ days) can fire. Both were dormant pre-v10.13.
      (c) v11.0.2: surface verdict-streak counters so chronic-AVOID demotion
          (feature B) and turnaround flag (feature C) can fire.

    Free-tier friendly: one small SELECT on an in-process SQLite table; no
    external calls.
    """
    import datetime as _dt
    out = {}
    try:
        conn = sqlite3.connect("market_data.db")
        # Tolerant SELECT — older DBs may not have streak columns; treat missing as 0.
        try:
            df = pd.read_sql(
                "SELECT symbol, date, composite_score, verdict, "
                "       consecutive_avoid_quarters, consecutive_recovery_quarters "
                "FROM latest_analysis_results", conn,
            )
        except Exception:
            df = pd.read_sql(
                "SELECT symbol, date, composite_score, verdict "
                "FROM latest_analysis_results", conn,
            )
        conn.close()
    except Exception:
        return out

    if df.empty:
        return out

    today = _dt.date.today()
    for _, row in df.iterrows():
        sym = str(row.get("symbol", "") or "").strip()
        if not sym:
            continue
        try:
            score = float(row.get("composite_score") or 0)
        except (ValueError, TypeError):
            score = 0.0
        verdict = str(row.get("verdict") or "").strip()

        days_since = 99   # default: very stale
        d_raw = row.get("date")
        if d_raw:
            try:
                dt_obj = _dt.datetime.strptime(str(d_raw)[:10], "%Y-%m-%d").date()
                days_since = max(0, (today - dt_obj).days)
            except (ValueError, TypeError):
                pass

        try:
            avoid_streak = int(row.get("consecutive_avoid_quarters") or 0)
        except (ValueError, TypeError):
            avoid_streak = 0
        try:
            recovery_streak = int(row.get("consecutive_recovery_quarters") or 0)
        except (ValueError, TypeError):
            recovery_streak = 0

        out[sym] = {
            "last_score":                     score,
            "last_verdict":                   verdict,
            "date":                           str(d_raw)[:10] if d_raw else "",
            "days_since":                     days_since,
            "consecutive_avoid_quarters":     avoid_streak,
            "consecutive_recovery_quarters":  recovery_streak,
        }
    return out


def update_verdict_streaks(stocks_today: list) -> dict:
    """
    v11.0.2: For each stock analysed today, advance or reset its verdict-streak
    counters based on the previous run's values vs today's verdict and score.

    Rules
    -----
    consecutive_avoid_quarters:
      • += 1 when today's verdict == "AVOID"
      • reset to 0 on any other verdict
      Reaching ≥ 2 triggers chronic-AVOID demotion in priority_ranker (feature B).

    consecutive_recovery_quarters:
      • += 1 when previous verdict was "AVOID" AND today's composite_score ≥ 50
        (i.e. emerging from an AVOID streak)
      • += 1 again when streak is already running AND today's composite ≥ 50
      • reset to 0 if today's composite drops below 50
      • also reset to 0 if today's verdict is back to "AVOID"
      Reaching ≥ 2 triggers Section H "Turnaround Candidate" flag (feature C).

    Parameters
    ----------
    stocks_today : list[dict]
        Each dict must have at minimum 'symbol', 'verdict', 'composite_score'.
        Typically the final_100_list passed to ExcelGenerator.

    Returns dict {symbol: {avoid: int, recovery: int}} of the NEW values
    written to the DB. Useful for tests and for stamping into stocks_today
    so downstream consumers (daily report) can read them in-process.
    """
    if not stocks_today:
        return {}

    prior = get_prior_analysis_map()
    new_streaks = {}

    for stk in stocks_today:
        sym = str(stk.get("symbol", "") or "").strip().upper()
        if not sym:
            continue
        verdict = str(stk.get("verdict") or "").strip().upper()
        try:
            score = float(stk.get("composite_score") or 0)
        except (ValueError, TypeError):
            score = 0.0

        prev = prior.get(sym, {})
        prev_avoid = int(prev.get("consecutive_avoid_quarters") or 0)
        prev_recovery = int(prev.get("consecutive_recovery_quarters") or 0)
        prev_verdict = str(prev.get("last_verdict") or "").upper()

        # Avoid streak
        if verdict == "AVOID":
            new_avoid = prev_avoid + 1
        else:
            new_avoid = 0

        # Recovery streak — fires only after coming out of AVOID
        if verdict == "AVOID":
            new_recovery = 0  # back in AVOID territory; reset
        elif prev_verdict == "AVOID" and score >= 50:
            new_recovery = 1  # first quarter out of AVOID with healthy score
        elif prev_recovery >= 1 and score >= 50:
            new_recovery = prev_recovery + 1  # streak continues
        else:
            new_recovery = 0  # below 50 → reset

        new_streaks[sym] = {"avoid": new_avoid, "recovery": new_recovery}
        # Stamp back onto stock dict so downstream (daily report) can read it
        stk["consecutive_avoid_quarters"]    = new_avoid
        stk["consecutive_recovery_quarters"] = new_recovery
        stk["turnaround_candidate"] = (new_recovery >= 2)

    # Persist to DB
    try:
        conn = sqlite3.connect("market_data.db")
        for sym, vals in new_streaks.items():
            conn.execute(
                """
                UPDATE latest_analysis_results
                   SET consecutive_avoid_quarters = ?,
                       consecutive_recovery_quarters = ?
                 WHERE symbol = ?
                """,
                (vals["avoid"], vals["recovery"], sym),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"   ⚠️  update_verdict_streaks: persist failed: {e}")

    return new_streaks

# ════════════════════════════════════════════════════════════════════════════
# v14.0 — OUTCOME TRACKING HELPERS
# ════════════════════════════════════════════════════════════════════════════

def has_open_recommendation(symbol: str) -> bool:
    """v14.0: True if this symbol already has an OPEN recommendation.
    Used by master_funnel to enforce "first-appearance only" — we only
    log a new recommendation when no prior one is still being tracked.
    A recommendation closes when track_outcomes marks it SL_HIT / T1_HIT /
    T2_HIT / T3_HIT / EXPIRED."""
    try:
        conn = sqlite3.connect("market_data.db")
        try:
            c = conn.cursor()
            c.execute(
                "SELECT 1 FROM gold_outcomes "
                "WHERE symbol = ? AND outcome_type = 'OPEN' LIMIT 1",
                (symbol,)
            )
            return c.fetchone() is not None
        finally:
            conn.close()
    except Exception:
        # Conservative default: if DB query fails, treat as "no open" so we
        # don't suppress legitimate recommendations on transient errors.
        return False


def insert_gold_recommendation(rec: dict) -> bool:
    """v14.0/v14.1/v14.3: Insert one Gold-sheet recommendation.

    Returns:
      True  — row was newly inserted (cursor.rowcount == 1 on the gold_recommendations INSERT)
      False — row already existed (PRIMARY KEY collision on symbol+recommendation_date)
              OR DB error occurred. Either way the caller should treat as "not logged this call".

    v14.3 audit fix: previously returned True even when INSERT OR IGNORE silently
    skipped due to PK collision, masking duplicate-key issues. Caller (master_funnel)
    now uses the False return as a signal that the row was a same-day duplicate, not
    a fresh log. The first-appearance gate still happens upstream via
    has_open_recommendation() — this is a defense-in-depth check.

    Caller should have already verified has_open_recommendation()==False
    for this symbol (first-appearance rule).
    Also seeds a corresponding row in gold_outcomes with outcome_type=OPEN.

    v14.1: caller should pass `expiry_days` (int) and `expiry_date` (YYYY-MM-DD str)
    derived from the recommendation's time_horizon. Falls back to 90 / "" if absent
    so existing v14.0 callers continue to work."""
    try:
        conn = sqlite3.connect("market_data.db")
        try:
            c = conn.cursor()
            # v15.0: INSERT now writes 4 audit columns (original_stop_loss,
            # atr_at_rec, regime_at_rec, next_earnings_date) so the
            # Performance sheet and any future analyzer can see the v15.0
            # multi-factor context per pick. Schema migration in v15.0
            # already added these columns; the INSERT was previously
            # leaving them at DEFAULT (0 / '' / 'neutral') — silent loss
            # of audit trail.
            c.execute("""
                INSERT OR IGNORE INTO gold_recommendations
                (recommendation_date, symbol, company_name, sector, cap_category,
                 cmp_at_recommendation, entry_low, entry_high, stop_loss,
                 t1, t2, t3, cfv, mos_pct, composite_score, early_entry_score,
                 quick_pick_label, verdict, time_horizon, predicted_rr,
                 expiry_days, expiry_date, times_reappeared,
                 original_stop_loss, atr_at_rec, regime_at_rec, next_earnings_date,
                 suggested_alloc_pct, alloc_rationale)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                rec.get("recommendation_date", ""),
                rec.get("symbol", ""),
                rec.get("company_name", ""),
                rec.get("sector", ""),
                rec.get("cap_category", ""),
                float(rec.get("cmp_at_recommendation", 0) or 0),
                float(rec.get("entry_low", 0) or 0),
                float(rec.get("entry_high", 0) or 0),
                float(rec.get("stop_loss", 0) or 0),
                float(rec.get("t1", 0) or 0),
                float(rec.get("t2", 0) or 0),
                float(rec.get("t3", 0) or 0),
                float(rec.get("cfv", 0) or 0),
                float(rec.get("mos_pct", 0) or 0),
                float(rec.get("composite_score", 0) or 0),
                float(rec.get("early_entry_score", 0) or 0),
                rec.get("quick_pick_label", ""),
                rec.get("verdict", ""),
                rec.get("time_horizon", ""),
                float(rec.get("predicted_rr", 0) or 0),
                int(rec.get("expiry_days", 90) or 90),
                rec.get("expiry_date", ""),
                0,  # times_reappeared starts at 0
                # v15.0 audit columns
                float(rec.get("original_stop_loss", rec.get("stop_loss", 0)) or 0),
                float(rec.get("atr_at_rec", 0) or 0),
                str(rec.get("regime_at_rec", "neutral") or "neutral"),
                str(rec.get("next_earnings_date", "") or ""),
                # v15.7: risk-parity sizing frozen at log time
                float(rec.get("suggested_alloc_pct", 0) or 0),
                str(rec.get("alloc_rationale", "") or ""),
            ))
            # v14.3: capture rowcount BEFORE the second INSERT overwrites it
            inserted = (c.rowcount == 1)
            # Seed gold_outcomes with OPEN row
            c.execute("""
                INSERT OR IGNORE INTO gold_outcomes
                (recommendation_date, symbol, outcome_type, last_checked_date,
                 current_price)
                VALUES (?,?,?,?,?)
            """, (
                rec.get("recommendation_date", ""),
                rec.get("symbol", ""),
                "OPEN",
                rec.get("recommendation_date", ""),
                float(rec.get("cmp_at_recommendation", 0) or 0),
            ))
            conn.commit()
            return inserted
        finally:
            conn.close()
    except Exception as e:
        print(f"   ⚠️  insert_gold_recommendation({rec.get('symbol','?')}): {e}")
        return False


def get_open_recommendations() -> list:
    """v14.0: Return all OPEN recommendations with their target/SL data
    for the outcome tracker to walk forward through price history.
    Joins gold_recommendations × gold_outcomes."""
    try:
        conn = sqlite3.connect("market_data.db")
        try:
            df = pd.read_sql_query("""
                SELECT
                    r.symbol, r.recommendation_date,
                    r.cmp_at_recommendation,
                    r.entry_low, r.entry_high,
                    r.stop_loss, r.t1, r.t2, r.t3,
                    r.composite_score, r.quick_pick_label,
                    r.time_horizon,
                    COALESCE(r.expiry_days, 90) AS expiry_days,
                    COALESCE(r.expiry_date, '') AS expiry_date,
                    o.last_checked_date, o.outcome_type,
                    o.max_drawdown_pct, o.max_runup_pct
                FROM gold_recommendations r
                INNER JOIN gold_outcomes o
                  ON r.symbol = o.symbol
                  AND r.recommendation_date = o.recommendation_date
                WHERE o.outcome_type = 'OPEN'
                ORDER BY r.recommendation_date ASC
            """, conn)
            return df.to_dict("records")
        finally:
            conn.close()
    except Exception as e:
        print(f"   ⚠️  get_open_recommendations: {e}")
        return []


def update_outcome(symbol: str, recommendation_date: str,
                   outcome_type: str, outcome_date: str = "",
                   outcome_price: float = 0,
                   days_to_outcome: int = 0,
                   max_drawdown_pct: float = 0,
                   max_runup_pct: float = 0,
                   current_price: float = 0,
                   current_pnl_pct: float = 0,
                   last_checked_date: str = "",
                   trailing_sl_pct: float = 0,
                   trailing_sl_price: float = 0,
                   peak_price_seen: float = 0) -> bool:
    """v14.0: Update gold_outcomes row for a recommendation. Used by
    track_outcomes.py both to finalize closed outcomes (SL/T1/T2/T3/EXPIRED)
    and to update OPEN row tracking (current_price, max_runup, last_checked).

    v15.0: Also persists trailing-stop state (trailing_sl_pct, trailing_sl_price,
    peak_price_seen). Default 0 means trailing has not yet activated; once
    activated, these fields ratchet up only.
    """
    try:
        conn = sqlite3.connect("market_data.db")
        try:
            c = conn.cursor()
            c.execute("""
                UPDATE gold_outcomes
                SET outcome_type = ?,
                    outcome_date = ?,
                    outcome_price = ?,
                    days_to_outcome = ?,
                    max_drawdown_pct = ?,
                    max_runup_pct = ?,
                    current_price = ?,
                    current_pnl_pct = ?,
                    last_checked_date = ?,
                    trailing_sl_pct = ?,
                    trailing_sl_price = ?,
                    peak_price_seen = ?
                WHERE symbol = ?
                  AND recommendation_date = ?
            """, (
                outcome_type, outcome_date, outcome_price,
                days_to_outcome, max_drawdown_pct, max_runup_pct,
                current_price, current_pnl_pct, last_checked_date,
                trailing_sl_pct, trailing_sl_price, peak_price_seen,
                symbol, recommendation_date,
            ))
            conn.commit()
            return c.rowcount > 0
        finally:
            conn.close()
    except Exception as e:
        print(f"   ⚠️  update_outcome({symbol}): {e}")
        return False


def get_outcome_stats() -> dict:
    """v14.0: Aggregate stats across all closed outcomes — used by the
    Excel Performance sheet. Returns headline counts + breakdowns.

    v15.0: SELECT now pulls trailing-SL state (trailing_sl_pct,
    trailing_sl_price, peak_price_seen) and audit columns
    (original_stop_loss, atr_at_rec, regime_at_rec, next_earnings_date)
    so the Performance sheet can render the v15.0 context per row.
    """
    try:
        conn = sqlite3.connect("market_data.db")
        try:
            df = pd.read_sql_query("""
                SELECT r.*, o.outcome_type, o.outcome_date, o.outcome_price,
                       o.days_to_outcome, o.max_drawdown_pct, o.max_runup_pct,
                       o.current_price, o.current_pnl_pct,
                       o.trailing_sl_pct, o.trailing_sl_price, o.peak_price_seen
                FROM gold_recommendations r
                INNER JOIN gold_outcomes o
                  ON r.symbol = o.symbol
                  AND r.recommendation_date = o.recommendation_date
            """, conn)
        finally:
            conn.close()
        return {"all_recommendations": df}
    except Exception as e:
        print(f"   ⚠️  get_outcome_stats: {e}")
        return {"all_recommendations": pd.DataFrame()}


def increment_reappearance(symbol: str, today_iso: str) -> bool:
    """v14.1: Increment the times_reappeared counter on the OPEN
    recommendation for this symbol. Also stamps the last_reappeared_date.

    Called from master_funnel when a stock shows up in Gold but already
    has an OPEN recommendation — provides visibility into 'system kept
    saying buy this' without changing the original entry/SL/T levels.

    Idempotent within the same day: if last_reappeared_date already equals
    today_iso, the counter is NOT incremented (avoids double-counting if
    pipeline re-runs on the same calendar day)."""
    try:
        conn = sqlite3.connect("market_data.db")
        try:
            c = conn.cursor()
            # Find the OPEN row for this symbol
            c.execute("""
                SELECT r.recommendation_date, o.last_reappeared_date
                FROM gold_recommendations r
                INNER JOIN gold_outcomes o
                  ON r.symbol = o.symbol
                  AND r.recommendation_date = o.recommendation_date
                WHERE r.symbol = ?
                  AND o.outcome_type = 'OPEN'
                LIMIT 1
            """, (symbol,))
            row = c.fetchone()
            if not row:
                return False
            rec_date, last_reap = row
            # Idempotency #1: skip if already incremented today
            if last_reap == today_iso:
                return False
            # Idempotency #2: skip if today IS the recommendation_date.
            # This handles the case where the pipeline runs multiple times on
            # the same day the stock was first logged — without this guard,
            # the 2nd run-of-day-1 would falsely register a "re-appearance"
            # against the just-created row. (Bug found in Q1 verification — fixed v14.1.1.)
            if rec_date == today_iso:
                return False
            c.execute("""
                UPDATE gold_recommendations
                SET times_reappeared = COALESCE(times_reappeared, 0) + 1
                WHERE symbol = ? AND recommendation_date = ?
            """, (symbol, rec_date))
            c.execute("""
                UPDATE gold_outcomes
                SET last_reappeared_date = ?
                WHERE symbol = ? AND recommendation_date = ?
            """, (today_iso, symbol, rec_date))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        print(f"   ⚠️  increment_reappearance({symbol}): {e}")
        return False


def horizon_to_expiry_days(time_horizon: str) -> int:
    """v14.1: Map a recommendation's Horizon string to its expiry window in days.

    Mapping (matches the Excel Horizon tooltip "SHORT TERM: 2-4 weeks |
    POSITIONAL: 1-3 mo | LONG TERM: 3-12 mo"):
        SHORT TERM  → 30 days  (upper bound of "2-4 weeks")
        POSITIONAL  → 90 days  (median of "1-3 months")
        LONG TERM   → 270 days (median of "3-12 months", ~9 months)
        anything else → 90 days (conservative default)

    Case-insensitive, tolerates extra whitespace and dotted variants."""
    h = str(time_horizon or "").strip().upper()
    if "SHORT" in h:
        return 30
    if "LONG" in h:
        return 270
    # POSITIONAL or unknown → 90 (conservative default matches v14.0 behavior)
    return 90