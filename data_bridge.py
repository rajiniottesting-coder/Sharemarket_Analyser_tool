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
            net_value          REAL DEFAULT 0
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
            symbol           TEXT PRIMARY KEY,
            date             TEXT,
            composite_score  REAL DEFAULT 0,
            early_score      REAL DEFAULT 0,
            spike_score      INTEGER DEFAULT 0,
            storm_score      REAL DEFAULT 0,
            cfv              REAL DEFAULT 0,
            mos_pct          REAL DEFAULT 0,
            verdict          TEXT DEFAULT '',
            ai_card          TEXT DEFAULT '',
            analysis_summary TEXT DEFAULT '',
            allocation_tag   TEXT DEFAULT ''
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS bulk_deals (
            symbol   TEXT,
            date     TEXT,
            client   TEXT,
            type     TEXT,
            quantity REAL DEFAULT 0,
            price    REAL DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS insider_trades (
            symbol TEXT,
            date   TEXT,
            name   TEXT,
            mode   TEXT,
            qty    REAL DEFAULT 0,
            value  REAL DEFAULT 0
        )
    """)

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
    # Keep the FIRST occurrence of each column name (TckrSymb comes first).
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

    return df


# ─────────────────────────────────────────────────────────────────────────────
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
    from reconciler import reconcile_exchanges

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

        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            _safe_insert(df, table, conn)
        else:
            streams = [
                (nse_data, "NSE"),     (bse_data,  "BSE"),
                (nse_del,  "NSE"),     (bse_del,   "BSE"),
                (sme_nse,  "NSE_SME"), (sme_bse,   "BSE_SME"),
            ]
            frames = []
            for src, exch in streams:
                if src is not None and isinstance(src, pd.DataFrame) \
                        and not src.empty:
                    std = standardize_to_v7_schema(src, exchange=exch)
                    if not std.empty:
                        frames.append(std)
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                _safe_insert(combined, "daily_prices", conn)

        if (participant_data is not None
                and isinstance(participant_data, pd.DataFrame)
                and not participant_data.empty):
            participant_data.to_sql("fo_participant_data", conn,
                                    if_exists="append", index=False)
            conn.commit()
            print(f"✅ F&O data saved: {len(participant_data)} records.")

    except Exception as e:
        print(f"❌ Database Bridge Error: {e}")
    finally:
        conn.close()


def _safe_insert(df: pd.DataFrame, table: str, conn) -> None:
    """INSERT OR IGNORE via a temp table — handles PRIMARY KEY conflicts.
    Only inserts columns that actually exist in the target table.
    Extra columns from reconciler (final_symbol, diff_pct etc.) are silently dropped.
    """
    if df is None or df.empty:
        return
    # Get the columns that exist in the target table
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        table_cols = [row[1] for row in cur.fetchall()]
    except Exception:
        table_cols = []

    if table_cols:
        # Keep only columns that exist in both df and the target table
        cols_to_insert = [c for c in table_cols if c in df.columns]
        if not cols_to_insert:
            print(f"⚠️  No matching columns for {table}. Skipping insert.")
            return
        df = df[cols_to_insert].copy()

    tmp = f"_tmp_{table}"
    df.to_sql(tmp, conn, if_exists="replace", index=False)
    conn.execute(f"INSERT OR IGNORE INTO {table} SELECT * FROM {tmp}")
    conn.execute(f"DROP TABLE IF EXISTS {tmp}")
    conn.commit()
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
    """Sections 3F, 3K & 4: Quarterly baseline trends per symbol."""
    if not symbols:
        return {}
    conn = sqlite3.connect("market_data.db")
    try:
        placeholders = ", ".join(["?"] * len(symbols))
        df = pd.read_sql_query(
            f"""SELECT * FROM v7_intelligence
                WHERE symbol IN ({placeholders})
                  AND timestamp <= datetime('now', '-90 days')
                GROUP BY symbol""",
            conn, params=symbols,
        )
        if df.empty:
            return {s: None for s in symbols}
        hist = df.set_index("symbol").to_dict("index")
        return {
            s: {
                "fii_holding": hist[s].get("fii_holding", 0),
                "pledge_pct":  hist[s].get("pledge_pct",  0),
                "total_debt":  hist[s].get("total_debt",  0),
                "dio":         hist[s].get("dio",         0),
                "dso":         hist[s].get("dso",         0),
                "roe":         hist[s].get("roe",         0),
                "networth":    hist[s].get("networth",    1),
            } if s in hist else None
            for s in symbols
        }
    except Exception:
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
    """20-day average volume for a symbol — used by priority_ranker."""
    if not symbol:
        return 0.0
    conn = sqlite3.connect("market_data.db")
    try:
        r = pd.read_sql_query(
            "SELECT AVG(volume) FROM ("
            "  SELECT volume FROM daily_prices "
            "  WHERE symbol=? ORDER BY date DESC LIMIT 20"
            ")",
            conn, params=(symbol,),
        )
        val = r.iloc[0, 0]
        return float(val) if val and val > 0 else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def get_symbol_history(symbol: str, limit: int = 250) -> pd.DataFrame:
    """Historical OHLCV for a symbol, ascending by date."""
    if not symbol:
        return pd.DataFrame()
    conn = sqlite3.connect("market_data.db")
    try:
        df = pd.read_sql_query(
            "SELECT date, open, high, low, close, volume "
            "FROM daily_prices WHERE symbol=? "
            "ORDER BY date ASC LIMIT ?",
            conn, params=(symbol, limit),
        )
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
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


def load_latest_analysis_results() -> list:
    """Load most recent AI analysis results for score comparison."""
    try:
        conn = sqlite3.connect("market_data.db")
        df = pd.read_sql("SELECT * FROM latest_analysis_results", conn)
        conn.close()
        return df.to_dict("records") if not df.empty else []
    except Exception:
        return []