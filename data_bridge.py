"""
data_bridge.py
SECTION 1A & 1B — Data Consolidation & DB Bridge (v7 FINAL)

Key fixes:
- standardize_to_v7_schema: handles NSE new bhav format (TckrSymb, ClsPric etc.)
  AND old format AND BSE (SC_NAME→symbol, NOT SC_CODE)
- delivery_pct joined from NSE delivery file
- save_to_database: accepts both single-df and multi-stream keyword call
- daily_prices table includes delivery_pct and isin columns
- get_today_consolidated_data signature matches master_funnel.py call
"""

import sqlite3
import pandas as pd


# ── SECTION 1: SCHEMA & TABLE INITIALISATION ─────────────────────────────────

def initialize_v7_tables(conn):
    """
    SECTION 1.2: Creates all v7 tables if they don't exist.
    """
    cursor = conn.cursor()

    # 1. Daily Market Data (core table)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol       TEXT,
            bse_code     TEXT,
            isin         TEXT,
            date         TEXT,
            open         REAL,
            high         REAL,
            low          REAL,
            close        REAL,
            prev_close   REAL,
            volume       REAL,
            turnover     REAL,
            delivery_pct REAL DEFAULT 0,
            exchange     TEXT,
            exchange_tag TEXT,
            PRIMARY KEY (symbol, date, exchange)
        )
    """)

    # 2. Watchlist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            symbol   TEXT PRIMARY KEY,
            active   INTEGER DEFAULT 1,
            added_on TEXT
        )
    """)

    # 3. Market Holidays
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_holidays (
            date     TEXT,
            name     TEXT,
            exchange TEXT,
            PRIMARY KEY (date, exchange)
        )
    """)

    # 4. Pipeline Execution Stats (Section 12B)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS run_stats (
            run_date         TEXT PRIMARY KEY,
            total_universe   INTEGER,
            stage1_passed    INTEGER,
            stage2_passed    INTEGER,
            stage3_selected  INTEGER,
            claude_analysed  INTEGER,
            gold_count       INTEGER,
            gate_check_result TEXT,
            bse_available    INTEGER,
            delivery_time    TEXT
        )
    """)

    # 5. F&O Participant Data (Section 1A)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fo_participant_data (
            date              TEXT,
            client_type       TEXT,
            future_index_long REAL,
            future_index_short REAL,
            future_stock_long REAL,
            future_stock_short REAL,
            total_long        REAL,
            total_short       REAL,
            net_value         REAL
        )
    """)

    # 6. Intelligence / Quarterly Baseline (Sections 3F, 3K, 4)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS v7_intelligence (
            symbol       TEXT,
            timestamp    TEXT,
            fii_holding  REAL,
            dii_holding  REAL,
            pledge_pct   REAL,
            promoter_pct REAL,
            total_debt   REAL,
            dio          REAL,
            dso          REAL,
            roe          REAL,
            networth     REAL,
            PRIMARY KEY (symbol, timestamp)
        )
    """)

    # 7. Final AI Analysis Results (Section 8)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS latest_analysis_results (
            symbol         TEXT PRIMARY KEY,
            date           TEXT,
            composite_score REAL,
            early_score    REAL,
            spike_score    INTEGER,
            storm_score    REAL,
            cfv            REAL,
            mos_pct        REAL,
            verdict        TEXT,
            ai_card        TEXT,
            analysis_summary TEXT,
            allocation_tag TEXT
        )
    """)

    # 8. Bulk Deals (Section 3J)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bulk_deals (
            symbol    TEXT,
            date      TEXT,
            client    TEXT,
            type      TEXT,
            quantity  REAL,
            price     REAL
        )
    """)

    # 9. Insider Trades (Section 3K)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS insider_trades (
            symbol TEXT,
            date   TEXT,
            name   TEXT,
            mode   TEXT,
            qty    REAL,
            value  REAL
        )
    """)

    conn.commit()


# ── SECTION 2: SCHEMA NORMALISATION ──────────────────────────────────────────

# Complete column mapping covering:
#   NSE new bhav (TckrSymb, ClsPric, PrvsClsgPric, HghPric, LwPric, TtlTradgVol, TtlTrfVal, ISIN, SctySrs)
#   NSE old bhav (SYMBOL, SERIES, CLOSE, PREV_CLOSE, OPEN, HIGH, LOW, TOTTRDQTY, TOTTRDVAL)
#   NSE delivery  (SYMBOL, CLOSE_PRICE, DELIV_PER)
#   BSE bhav      (SC_NAME→symbol, SC_CODE→bse_code, ISIN, CLOSE, OPEN, HIGH, LOW, NO_OF_SHRS, NET_TURNOV, SC_GROUP)

COLUMN_MAP = {
    # ── NSE new bhav format ──
    "tckrsymb":       "symbol",
    "isin":           "isin",
    "clspric":        "close",
    "prvsclsgpric":   "prev_close",
    "opnpric":        "open",
    "hghpric":        "high",
    "lwpric":         "low",
    "ttltradgvol":    "volume",
    "ttltrfval":      "turnover",
    "sctysrs":        "series",
    # ── NSE old bhav format ──
    "symbol":         "symbol",
    "series":         "series",
    "close_price":    "close",
    "close":          "close",
    "prev_close":     "prev_close",
    "prevclose":      "prev_close",
    "previous_close": "prev_close",
    "open_price":     "open",
    "open":           "open",
    "high_price":     "high",
    "high":           "high",
    "low_price":      "low",
    "low":            "low",
    "tottrdqty":      "volume",
    "total_trd_qty":  "volume",
    "ttl_trd_qnty":   "volume",
    "tottrdval":      "turnover",
    # ── NSE delivery ──
    "deliv_per":      "delivery_pct",
    "deliv_qty":      "deliv_qty",
    # ── BSE bhav ──
    "sc_name":        "symbol",       # ← ticker string e.g. "RELIANCE" — USE THIS
    "sc_code":        "bse_code",     # ← numeric 6-digit code — stored separately
    "sc_group":       "sc_group",
    "isin_code":      "isin",
    "no_of_shrs":     "volume",
    "net_turnover":   "turnover",
    "net_turnov":     "turnover",
    # ── Other aliases ──
    "no_of_trades":   "num_trades",
    "fininstrmid":    "symbol",
    "security_id":    "symbol",
    "scrip_id":       "symbol",
    "syml":           "symbol",
}

REQUIRED_COLS = ["symbol", "open", "high", "low", "close", "volume"]


def standardize_to_v7_schema(df, exchange: str = "NSE") -> pd.DataFrame:
    """
    SECTION 1A & 1B: Defensive Schema Enforcement.
    Normalises any raw Bhav Copy DataFrame to the canonical v7 column set.
    Always returns a DataFrame — never None — with at minimum the REQUIRED_COLS.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=REQUIRED_COLS + ["isin", "bse_code", "delivery_pct",
                                                       "turnover", "exchange"])

    # 1. Lowercase and strip all column names
    df = df.copy()
    df.columns = [str(c).lower().strip().replace(" ", "_") for c in df.columns]

    # 2. Apply the master column mapping
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})

    # 3. BSE special: prefer sc_name (ticker) over numeric codes
    #    If 'symbol' still looks numeric (BSE code was mapped), try to use 'sc_name' fallback
    if "symbol" in df.columns:
        # If all values are numeric strings, this is a BSE code column — not a ticker
        sample = df["symbol"].dropna().head(10)
        if sample.apply(lambda x: str(x).strip().isdigit()).all():
            # We mistakenly mapped SC_CODE to symbol — check if sc_name exists
            if "bse_code" not in df.columns:
                df["bse_code"] = df["symbol"]
            # Try to find a name-like column
            for alt in ["sc_name", "name", "security_name", "scrip_name"]:
                if alt in df.columns:
                    df["symbol"] = df[alt]
                    break

    # 4. Ensure all required columns exist
    for col in REQUIRED_COLS + ["isin", "bse_code", "delivery_pct", "turnover",
                                  "exchange", "prev_close", "sc_group"]:
        if col not in df.columns:
            df[col] = 0 if col not in ["symbol", "isin", "bse_code", "sc_group", "exchange"] else ""

    # 5. Coerce numeric columns
    for col in ["open", "high", "low", "close", "prev_close", "volume",
                "turnover", "delivery_pct"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # 6. Drop rows with no symbol or no close price
    df = df[df["symbol"].astype(str).str.strip() != ""]
    df = df[df["symbol"].astype(str).str.strip() != "0"]
    df = df[df["close"] > 0]

    # 7. Tag exchange
    df["exchange"] = exchange

    return df


def merge_delivery_data(price_df: pd.DataFrame,
                        delivery_df: pd.DataFrame,
                        exchange: str = "NSE") -> pd.DataFrame:
    """
    Joins delivery percentage from the delivery file onto the price DataFrame.
    NSE: SYMBOL + DELIV_PER (normalised to delivery_pct)
    BSE: SC_CODE + delivery data (if available)
    """
    if delivery_df is None or delivery_df.empty:
        return price_df

    deliv = delivery_df.copy()
    deliv.columns = [str(c).lower().strip().replace(" ", "_") for c in deliv.columns]
    deliv = deliv.rename(columns=COLUMN_MAP)

    if "symbol" not in deliv.columns or "delivery_pct" not in deliv.columns:
        return price_df

    deliv = deliv[["symbol", "delivery_pct"]].dropna()
    deliv["delivery_pct"] = pd.to_numeric(deliv["delivery_pct"], errors="coerce").fillna(0)
    deliv["symbol"] = deliv["symbol"].astype(str).str.strip().str.upper()

    price_df = price_df.copy()
    price_df["symbol"] = price_df["symbol"].astype(str).str.strip().str.upper()

    # Drop existing delivery_pct before merging to avoid _x/_y suffixes
    if "delivery_pct" in price_df.columns:
        price_df = price_df.drop(columns=["delivery_pct"])

    merged = price_df.merge(deliv, on="symbol", how="left")
    merged["delivery_pct"] = merged["delivery_pct"].fillna(0)
    return merged


# ── SECTION 3: CONSOLIDATION HUB ─────────────────────────────────────────────

def get_today_consolidated_data(target_date, nse_main=None, nse_sme=None,
                                bse_main=None, bse_sme=None,
                                nse_deliv=None, bse_deliv=None) -> pd.DataFrame:
    """
    V7 Consolidation Hub.
    Standardises all 4 price streams + 2 delivery streams.
    Returns a single canonical DataFrame ready for Stage 1 filter.
    """
    from reconciler import reconcile_exchanges

    date_str = str(target_date) if not hasattr(target_date, "strftime") else target_date.strftime("%Y-%m-%d")
    print(f"🔄 [V7] Consolidating market data for {date_str}...")

    # Standardise all streams
    n_m = standardize_to_v7_schema(nse_main,  exchange="NSE")
    n_s = standardize_to_v7_schema(nse_sme,   exchange="NSE_SME")
    b_m = standardize_to_v7_schema(bse_main,  exchange="BSE")
    b_s = standardize_to_v7_schema(bse_sme,   exchange="BSE_SME")

    # Merge delivery pct into price streams
    n_m = merge_delivery_data(n_m, nse_deliv, exchange="NSE")
    b_m = merge_delivery_data(b_m, bse_deliv, exchange="BSE")

    # Stack NSE and BSE streams separately
    all_nse = pd.concat([n_m, n_s], ignore_index=True)
    all_bse = pd.concat([b_m, b_s], ignore_index=True)

    if all_nse.empty and all_bse.empty:
        print("⚠️  Both NSE and BSE streams empty. Skipping consolidation.")
        return pd.DataFrame()

    # Cross-exchange reconciliation (deduplication by ISIN)
    consolidated_df = reconcile_exchanges(all_nse, all_bse)

    if consolidated_df is not None and not consolidated_df.empty:
        consolidated_df["date"] = date_str
        print(f"✅ V7 Consolidation Complete: {len(consolidated_df)} records.")
    else:
        consolidated_df = pd.DataFrame()

    return consolidated_df


# ── SECTION 4: DATA INTEGRITY CHECK (C5) ─────────────────────────────────────

def check_data_integrity(nse_df: pd.DataFrame, bse_df: pd.DataFrame) -> dict:
    """
    SECTION 12B C5: Validate downloaded data before pipeline proceeds.
    Returns {"pass": bool, "message": str}
    """
    required_nse = ["symbol", "close", "volume"]
    required_bse = ["symbol", "close"]

    def _check(df, required, name, min_rows):
        if df is None or df.empty:
            return False, f"{name}: DataFrame is empty."
        df_std = standardize_to_v7_schema(df)
        missing = [c for c in required if c not in df_std.columns or df_std[c].isna().all()]
        if missing:
            return False, f"{name}: Missing columns after normalisation: {missing}"
        if len(df_std) < min_rows:
            return False, f"{name}: Only {len(df_std)} rows (minimum {min_rows} required)."
        return True, f"{name}: OK ({len(df_std)} rows)."

    nse_pass, nse_msg = _check(nse_df, required_nse, "NSE", 500)
    bse_pass, bse_msg = _check(bse_df, required_bse, "BSE", 100)

    if not nse_pass:
        return {"pass": False, "message": f"C5 FAIL: {nse_msg}"}

    # BSE failure alone is a warning, not a blocker
    message = f"C5 PASS: {nse_msg} | {bse_msg}"
    return {"pass": True, "message": message, "bse_ok": bse_pass}


# ── SECTION 5: SAVE METHODS ───────────────────────────────────────────────────

def save_to_database(df=None, nse_data=None, bse_data=None,
                     nse_del=None, bse_del=None, sme_nse=None,
                     sme_bse=None, participant_data=None,
                     table: str = "daily_prices") -> None:
    """
    Main bridge for saving market data to SQLite.
    Accepts EITHER:
      - A single positional df (for consolidated data)
      - Named keyword streams (nse_data, bse_data, etc.)
    """
    conn = sqlite3.connect("market_data.db")
    try:
        initialize_v7_tables(conn)

        if df is not None and not isinstance(df, pd.DataFrame):
            # Called with positional non-DataFrame (safety guard)
            df = None

        if df is not None and not df.empty:
            # Direct consolidated df
            _safe_insert(df, table, conn)
        else:
            # Named streams — standardise and combine
            streams_with_exchange = [
                (nse_data, "NSE"), (bse_data, "BSE"),
                (nse_del,  "NSE"), (bse_del,  "BSE"),
                (sme_nse,  "NSE_SME"), (sme_bse, "BSE_SME"),
            ]
            frames = []
            for src, exchange in streams_with_exchange:
                if src is not None and isinstance(src, pd.DataFrame) and not src.empty:
                    std = standardize_to_v7_schema(src, exchange=exchange)
                    if not std.empty:
                        frames.append(std)
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                _safe_insert(combined, "daily_prices", conn)

        # F&O participant data
        if participant_data is not None and isinstance(participant_data, pd.DataFrame) \
                and not participant_data.empty:
            participant_data.to_sql("fo_participant_data", conn,
                                    if_exists="append", index=False)
            conn.commit()
            print(f"✅ F&O participant data saved: {len(participant_data)} records.")

    except Exception as e:
        print(f"❌ Database Bridge Error: {e}")
    finally:
        conn.close()


def _safe_insert(df: pd.DataFrame, table: str, conn) -> None:
    """Insert with duplicate handling — uses INSERT OR IGNORE for primary key conflicts."""
    if df.empty:
        return
    df.to_sql(f"_tmp_{table}", conn, if_exists="replace", index=False)
    conn.execute(f"""
        INSERT OR IGNORE INTO {table}
        SELECT * FROM _tmp_{table}
    """)
    conn.execute(f"DROP TABLE IF EXISTS _tmp_{table}")
    conn.commit()
    print(f"✅ Saved {len(df)} records to {table}.")


def save_fo_data(df: pd.DataFrame, target_date) -> None:
    """Section 1A: Saves F&O Participant Data."""
    if df is None or df.empty:
        return
    date_str = target_date.strftime("%Y-%m-%d") if hasattr(target_date, "strftime") else str(target_date)
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


# ── SECTION 6: RETRIEVAL METHODS ─────────────────────────────────────────────

def get_historical_quarter_data(symbols: list) -> dict:
    """SECTIONS 3F, 3K & 4: Quarterly Baseline Trends."""
    if not symbols:
        return {}
    conn = sqlite3.connect("market_data.db")
    try:
        placeholders = ", ".join(["?"] * len(symbols))
        query = f"""
            SELECT * FROM v7_intelligence
            WHERE symbol IN ({placeholders})
              AND timestamp <= datetime('now', '-90 days')
            GROUP BY symbol
        """
        df_hist = pd.read_sql_query(query, conn, params=symbols)
        if df_hist.empty:
            return {s: None for s in symbols}
        hist_dict = df_hist.set_index("symbol").to_dict("index")
        result = {}
        for s in symbols:
            if s in hist_dict:
                d = hist_dict[s]
                result[s] = {
                    "fii_holding":  d.get("fii_holding", 0),
                    "pledge_pct":   d.get("pledge_pct", 0),
                    "total_debt":   d.get("total_debt", 0),
                    "dio":          d.get("dio", 0),
                    "dso":          d.get("dso", 0),
                    "roe":          d.get("roe", 0),
                    "networth":     d.get("networth", 1),
                }
            else:
                result[s] = None
        return result
    except Exception:
        return {s: None for s in symbols}
    finally:
        conn.close()


def get_latest_fii_net_cash() -> float:
    """SECTION 7 & 9: Latest FII Net Cash Flow."""
    conn = sqlite3.connect("market_data.db")
    try:
        query = """
            SELECT net_value FROM fo_participant_data
            WHERE client_type = 'FII'
            ORDER BY date DESC LIMIT 1
        """
        result = pd.read_sql_query(query, conn)
        return float(result["net_value"].iloc[0]) if not result.empty else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def get_nifty_200_sma() -> float:
    """SECTION 9: Nifty 50 200-day SMA."""
    conn = sqlite3.connect("market_data.db")
    try:
        df = pd.read_sql_query(
            "SELECT close FROM daily_prices WHERE symbol = 'NIFTY 50' ORDER BY date DESC LIMIT 200",
            conn
        )
        return float(df["close"].mean()) if not df.empty else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def get_20d_avg_vol(symbol: str) -> float:
    """Used by priority_ranker for vol spike ratio."""
    conn = sqlite3.connect("market_data.db")
    try:
        query = """
            SELECT AVG(volume) FROM (
                SELECT volume FROM daily_prices
                WHERE symbol = ?
                ORDER BY date DESC LIMIT 20
            )
        """
        result = pd.read_sql_query(query, conn, params=(symbol,))
        val = result.iloc[0, 0]
        return float(val) if val and val > 0 else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def get_symbol_history(symbol: str, limit: int = 250) -> pd.DataFrame:
    """Returns historical OHLCV for a symbol, sorted ascending."""
    conn = sqlite3.connect("market_data.db")
    try:
        query = """
            SELECT date, open, high, low, close, volume
            FROM daily_prices
            WHERE symbol = ?
            ORDER BY date ASC
            LIMIT ?
        """
        df = pd.read_sql_query(query, conn, params=(symbol, limit))
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def get_nifty_52w_high_from_db() -> float:
    """SECTION 7 & 9: Nifty 50 52-week high."""
    conn = sqlite3.connect("market_data.db")
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MAX(close) FROM daily_prices "
            "WHERE symbol = 'NIFTY 50' AND date >= date('now', '-365 days')"
        )
        result = cursor.fetchone()
        return float(result[0]) if result and result[0] else 1.0
    except Exception:
        return 1.0
    finally:
        conn.close()


def load_latest_analysis_results() -> list:
    """Load last AI analysis results for score comparison."""
    try:
        conn = sqlite3.connect("market_data.db")
        df = pd.read_sql("SELECT * FROM latest_analysis_results", conn)
        conn.close()
        return df.to_dict("records") if not df.empty else []
    except Exception:
        return []
