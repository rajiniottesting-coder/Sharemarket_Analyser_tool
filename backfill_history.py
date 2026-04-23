"""
backfill_history.py  —  365-day Market Data Backfill
======================================================
Populates ALL tables needed to make the Excel dashboard show real values:

  daily_prices         — OHLCV + delivery + 52w_high/low + day_chg_pct per day
  symbol_master        — company_name, sector, cap_category, isin, bse_code
  technical_indicators — SMA200, RSI14, MACD, ADX, OBV, Supertrend etc.
  weekly_momentum      — 2w/4w/6w/8w change % per symbol per date
  delivery_stats       — daily delivery % per symbol
  fo_participant_data  — FII/DII net flows
  (preserves all existing tables: watchlist, run_stats, v7_intelligence, etc.)

INSTALL (one-time):
    pip install pandas requests pytz bse

BSE: uses `bse` pip package — handles Akamai bot-detection internally.
NSE: uses direct archive URL with session warm-up.
"""
import yfinance as yf
import sqlite3
import zipfile
import io
import os
import time
import tempfile
import datetime
from pathlib import Path
from datetime import timedelta

import pandas as pd
import requests
import pytz

try:
    from bse import BSE as BseClient
    BSE_PKG = True
except ImportError:
    BSE_PKG = False
    print("⚠️  pip install bse  — BSE data will be skipped")

import sys

IST     = pytz.timezone('Asia/Kolkata')
DB_NAME = "market_data.db"
# Accept days as command-line arg: python backfill_history.py 365
DAYS_TO_BACKFILL = int(sys.argv[1]) if len(sys.argv) > 1 else 365

NSE_HEADERS = {
    'User-Agent':    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/124.0.0.0 Safari/537.36',
    'Referer':       'https://www.nseindia.com/',
    'Accept':        'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — DATABASE SCHEMA
# ══════════════════════════════════════════════════════════════════════════════

def init_all_tables(conn):
    c = conn.cursor()

    # ── Core price table (extended) ───────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol        TEXT,
            bse_code      TEXT    DEFAULT '',
            isin          TEXT    DEFAULT '',
            date          TEXT,
            open          REAL    DEFAULT 0,
            high          REAL    DEFAULT 0,
            low           REAL    DEFAULT 0,
            close         REAL    DEFAULT 0,
            prev_close    REAL    DEFAULT 0,
            volume        REAL    DEFAULT 0,
            turnover      REAL    DEFAULT 0,
            delivery_pct  REAL    DEFAULT 0,
            exchange      TEXT    DEFAULT '',
            exchange_tag  TEXT    DEFAULT '',
            day_chg_pct   REAL    DEFAULT 0,
            week_high_52  REAL    DEFAULT 0,
            week_low_52   REAL    DEFAULT 0,
            vol_50d_avg   REAL    DEFAULT 0,
            PRIMARY KEY (symbol, date, exchange)
        )
    """)

    # ── Symbol master ─────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS symbol_master (
            symbol        TEXT PRIMARY KEY,
            company_name  TEXT    DEFAULT '',
            sector        TEXT    DEFAULT '',
            industry      TEXT    DEFAULT '',
            cap_category  TEXT    DEFAULT '',
            isin          TEXT    DEFAULT '',
            bse_code      TEXT    DEFAULT '',
            face_value    REAL    DEFAULT 10,
            listing_date  TEXT    DEFAULT '',
            exchange      TEXT    DEFAULT '',
            updated_on    TEXT    DEFAULT ''
        )
    """)

    # ── Technical indicators per symbol (latest row) ──────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS technical_indicators (
            symbol          TEXT,
            date            TEXT,
            sma_20          REAL    DEFAULT 0,
            sma_50          REAL    DEFAULT 0,
            sma_200         REAL    DEFAULT 0,
            ema_12          REAL    DEFAULT 0,
            ema_26          REAL    DEFAULT 0,
            macd            REAL    DEFAULT 0,
            macd_signal     REAL    DEFAULT 0,
            macd_hist       REAL    DEFAULT 0,
            rsi_14          REAL    DEFAULT 0,
            adx             REAL    DEFAULT 0,
            stoch_k         REAL    DEFAULT 0,
            stoch_d         REAL    DEFAULT 0,
            mfi_14          REAL    DEFAULT 0,
            obv             REAL    DEFAULT 0,
            atr_14          REAL    DEFAULT 0,
            vwap            REAL    DEFAULT 0,
            bb_upper        REAL    DEFAULT 0,
            bb_lower        REAL    DEFAULT 0,
            supertrend      TEXT    DEFAULT 'NEUTRAL',
            above_vwap      TEXT    DEFAULT 'NO',
            macd_signal_txt TEXT    DEFAULT 'NEUTRAL',
            obv_signal      TEXT    DEFAULT 'NEUTRAL',
            support1        REAL    DEFAULT 0,
            support2        REAL    DEFAULT 0,
            resist1         REAL    DEFAULT 0,
            resist2         REAL    DEFAULT 0,
            PRIMARY KEY (symbol, date)
        )
    """)

    # ── Weekly momentum ───────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS weekly_momentum (
            symbol          TEXT,
            date            TEXT,
            chg_2w          REAL    DEFAULT 0,
            chg_4w          REAL    DEFAULT 0,
            chg_6w          REAL    DEFAULT 0,
            chg_8w          REAL    DEFAULT 0,
            vol_spike_50d   REAL    DEFAULT 0,
            beta_90d        REAL    DEFAULT 0,
            PRIMARY KEY (symbol, date)
        )
    """)

    # ── Delivery stats ────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS delivery_stats (
            symbol        TEXT,
            date          TEXT,
            delivery_qty  REAL    DEFAULT 0,
            delivery_pct  REAL    DEFAULT 0,
            traded_qty    REAL    DEFAULT 0,
            series        TEXT    DEFAULT 'EQ',
            PRIMARY KEY (symbol, date)
        )
    """)

    # ── Fundamental metrics placeholder ───────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS fundamental_metrics (
            symbol           TEXT,
            date             TEXT,
            pe_ttm           REAL    DEFAULT 0,
            pb               REAL    DEFAULT 0,
            ps               REAL    DEFAULT 0,
            ev_ebitda        REAL    DEFAULT 0,
            peg              REAL    DEFAULT 0,
            pcf              REAL    DEFAULT 0,
            earn_yield       REAL    DEFAULT 0,
            roe              REAL    DEFAULT 0,
            roce             REAL    DEFAULT 0,
            roa              REAL    DEFAULT 0,
            gross_margin     REAL    DEFAULT 0,
            ebitda_margin    REAL    DEFAULT 0,
            net_margin       REAL    DEFAULT 0,
            de_ratio         REAL    DEFAULT 0,
            nd_ebitda        REAL    DEFAULT 0,
            int_coverage     REAL    DEFAULT 0,
            current_ratio    REAL    DEFAULT 0,
            quick_ratio      REAL    DEFAULT 0,
            cash_cr          REAL    DEFAULT 0,
            total_debt_cr    REAL    DEFAULT 0,
            operating_cf_cr  REAL    DEFAULT 0,
            curr_assets_cr   REAL    DEFAULT 0,
            curr_liab_cr     REAL    DEFAULT 0,
            fcf_cr           REAL    DEFAULT 0,
            fcf_yield        REAL    DEFAULT 0,
            ccc_days         REAL    DEFAULT 0,
            div_yield        REAL    DEFAULT 0,
            payout_ratio     REAL    DEFAULT 0,
            capex_rev        REAL    DEFAULT 0,
            rev_cagr_1y      REAL    DEFAULT 0,
            rev_cagr_3y      REAL    DEFAULT 0,
            pat_cagr_1y      REAL    DEFAULT 0,
            pat_cagr_3y      REAL    DEFAULT 0,
            ebitda_cagr_1y   REAL    DEFAULT 0,
            rev_yoy          REAL    DEFAULT 0,
            pat_yoy          REAL    DEFAULT 0,
            q_rev_cr         REAL    DEFAULT 0,
            q_pat_cr         REAL    DEFAULT 0,
            q_ebitda_cr      REAL    DEFAULT 0,
            npm_q1           REAL    DEFAULT 0,
            npm_q2           REAL    DEFAULT 0,
            npm_q3           REAL    DEFAULT 0,
            margin_expansion INTEGER DEFAULT 0,
            piotroski_f      REAL    DEFAULT 0,
            altman_z         REAL    DEFAULT 0,
            beneish_m        REAL    DEFAULT 0,
            PRIMARY KEY (symbol, date)
        )
    """)

    # ── Shareholding pattern ──────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS shareholding (
            symbol        TEXT,
            date          TEXT,
            promoter_pct  REAL    DEFAULT 0,
            promoter_qoq  REAL    DEFAULT 0,
            pledge_pct    REAL    DEFAULT 0,
            pledge_dir    TEXT    DEFAULT '',
            fii_pct       REAL    DEFAULT 0,
            fii_qoq       REAL    DEFAULT 0,
            dii_pct       REAL    DEFAULT 0,
            dii_qoq       REAL    DEFAULT 0,
            public_float  REAL    DEFAULT 0,
            PRIMARY KEY (symbol, date)
        )
    """)

    # ── Preserved existing tables ─────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS v7_intelligence (
            symbol        TEXT,
            timestamp     TEXT,
            fii_holding   REAL    DEFAULT 0,
            dii_holding   REAL    DEFAULT 0,
            pledge_pct    REAL    DEFAULT 0,
            promoter_pct  REAL    DEFAULT 0,
            total_debt    REAL    DEFAULT 0,
            dio           REAL    DEFAULT 0,
            dso           REAL    DEFAULT 0,
            roe           REAL    DEFAULT 0,
            networth      REAL    DEFAULT 1,
            PRIMARY KEY (symbol, timestamp)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS fo_participant_data (
            date                TEXT,
            client_type         TEXT,
            future_index_long   REAL    DEFAULT 0,
            future_index_short  REAL    DEFAULT 0,
            future_stock_long   REAL    DEFAULT 0,
            future_stock_short  REAL    DEFAULT 0,
            total_long          REAL    DEFAULT 0,
            total_short         REAL    DEFAULT 0,
            net_value           REAL    DEFAULT 0
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
        CREATE TABLE IF NOT EXISTS latest_analysis_results (
            symbol           TEXT PRIMARY KEY,
            date             TEXT,
            composite_score  REAL    DEFAULT 0,
            early_score      REAL    DEFAULT 0,
            spike_score      INTEGER DEFAULT 0,
            storm_score      REAL    DEFAULT 0,
            cfv              REAL    DEFAULT 0,
            mos_pct          REAL    DEFAULT 0,
            verdict          TEXT    DEFAULT '',
            ai_card          TEXT    DEFAULT '',
            analysis_summary TEXT    DEFAULT '',
            allocation_tag   TEXT    DEFAULT ''
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS bulk_deals (
            symbol   TEXT,
            date     TEXT,
            client   TEXT,
            type     TEXT,
            quantity REAL    DEFAULT 0,
            price    REAL    DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS insider_trades (
            symbol TEXT,
            date   TEXT,
            name   TEXT,
            mode   TEXT,
            qty    REAL    DEFAULT 0,
            value  REAL    DEFAULT 0
        )
    """)

    # ── Migrate existing daily_prices: add new columns if they don't exist ──────
    # This handles the case where daily_prices already exists with the old
    # 14-column schema from data_bridge.py and needs the 4 new columns added.
    existing_dp_cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_prices)").fetchall()}
    new_dp_cols = {
        'day_chg_pct':  'REAL DEFAULT 0',
        'week_high_52': 'REAL DEFAULT 0',
        'week_low_52':  'REAL DEFAULT 0',
        'vol_50d_avg':  'REAL DEFAULT 0',
    }
    for col, col_def in new_dp_cols.items():
        if col not in existing_dp_cols:
            conn.execute(f"ALTER TABLE daily_prices ADD COLUMN {col} {col_def}")

    # ── Indexes ───────────────────────────────────────────────────────────────
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_dp_sym_date  ON daily_prices(symbol, date)",
        "CREATE INDEX IF NOT EXISTS idx_dp_date       ON daily_prices(date)",
        "CREATE INDEX IF NOT EXISTS idx_ti_sym_date  ON technical_indicators(symbol, date)",
        "CREATE INDEX IF NOT EXISTS idx_wm_sym_date  ON weekly_momentum(symbol, date)",
        "CREATE INDEX IF NOT EXISTS idx_fm_sym_date  ON fundamental_metrics(symbol, date)",
        "CREATE INDEX IF NOT EXISTS idx_sh_sym_date  ON shareholding(symbol, date)",
        "CREATE INDEX IF NOT EXISTS idx_ds_sym_date  ON delivery_stats(symbol, date)",
    ]:
        c.execute(idx_sql)

    conn.commit()
    print("✅ All tables & indexes initialised")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — NSE DOWNLOADERS
# ══════════════════════════════════════════════════════════════════════════════


def migrate_db(conn):
    """
    Add any missing columns to existing DB tables.
    Safe to run on every startup — skips columns that already exist.
    SQLite CREATE TABLE only runs once when DB is first created, so new
    columns added to the schema definition never reach old DBs without this.
    """
    # New columns added to fundamental_metrics that old DBs may not have
    new_cols = [
        ("operating_cf_cr",  "REAL DEFAULT 0"),
        ("curr_assets_cr",   "REAL DEFAULT 0"),
        ("curr_liab_cr",     "REAL DEFAULT 0"),
        ("div_yield",        "REAL DEFAULT 0"),
        ("payout_ratio",     "REAL DEFAULT 0"),
        ("rev_yoy",          "REAL DEFAULT 0"),
        ("pat_yoy",          "REAL DEFAULT 0"),
    ]
    existing = {r[1] for r in conn.execute(
        "PRAGMA table_info(fundamental_metrics)").fetchall()}
    for col, typedef in new_cols:
        if col not in existing:
            try:
                conn.execute(
                    f"ALTER TABLE fundamental_metrics ADD COLUMN {col} {typedef}")
                print(f"   ✅ DB migration: added fundamental_metrics.{col}")
            except Exception as e:
                print(f"   ⚠️  Migration {col}: {e}")
    conn.commit()

def _nse_session():
    s = requests.Session()
    try:
        s.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=8)
    except Exception:
        pass
    return s


def download_nse_bhav(date_obj):
    ds_new = date_obj.strftime("%Y%m%d")
    ds_old = date_obj.strftime("%d%m%Y")
    urls = [
        f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ds_new}_F_0000.csv.zip",
        f"https://www1.nseindia.com/content/historical/EQUITIES/{date_obj.year}/"
        f"{date_obj.strftime('%b').upper()}/cm{ds_old}bhav.csv.zip",
    ]
    for url in urls:
        for _ in range(2):
            try:
                r = _nse_session().get(url, headers=NSE_HEADERS, timeout=20)
                if r.status_code == 200 and len(r.content) > 500:
                    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                        csv_files = [f for f in z.namelist() if f.lower().endswith('.csv')]
                        if not csv_files:
                            break
                        df = pd.read_csv(z.open(csv_files[0]))
                    for col in ['SERIES', 'SctySrs']:
                        if col in df.columns:
                            df = df[df[col].str.strip() == 'EQ'].copy()
                            break
                    return df
            except Exception:
                time.sleep(1)
    return None


def download_nse_delivery(date_obj):
    ds = date_obj.strftime("%d%m%Y")
    urls = [
        f"https://nsearchives.nseindia.com/archives/equities/mto/MTO_{ds}.DAT",
        f"https://www1.nseindia.com/archives/equities/mto/MTO_{ds}.DAT",
    ]
    for url in urls:
        try:
            r = _nse_session().get(url, headers=NSE_HEADERS, timeout=15)
            if r.status_code == 200 and len(r.content) > 100:
                lines = r.text.strip().split('\n')
                rows = []
                for line in lines:
                    if not line.startswith('2'):
                        continue
                    parts = line.split(',')
                    if len(parts) >= 6:
                        rows.append({
                            'series':       parts[1].strip(),
                            'symbol':       parts[2].strip(),
                            'traded_qty':   _safe_float(parts[3]),
                            'delivery_qty': _safe_float(parts[4]),
                            'delivery_pct': _safe_float(parts[5]),
                        })
                if rows:
                    return pd.DataFrame(rows)
        except Exception:
            pass
    return None


def _safe_float(s):
    try:
        return float(s.strip())
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — BSE DOWNLOADER
#
# DIAGNOSTIC FINDINGS (confirmed by bse_diagnose.py):
#   - All archive ZIP URLs (EQddmmyy_CSV.ZIP) return HTTP 404 — those URLs
#     are dead / never existed for this date range.
#   - The `bse` pip package returns 5015 rows for yesterday ✅
#   - The bse package uses BSE's internal reports API (not archive ZIPs)
#     and handles auth correctly for ALL dates, not just recent ones.
#
# SOLUTION: Use `bse` package exclusively for ALL 365 days.
# ══════════════════════════════════════════════════════════════════════════════

BSE_COL_MAP = {
    # Classic BSE equity bhav copy columns
    'SC_CODE':    'bse_code',  'SC_NAME':     'symbol',    'SC_GROUP':  'sc_group',
    'OPEN':       'open',      'HIGH':        'high',       'LOW':       'low',
    'CLOSE':      'close',     'PREVCLOSE':   'prev_close', 'NO_OF_SHRS':'volume',
    'NET_TURNOV': 'turnover',  'ISIN_CODE':   'isin',       'LAST':      'last',
    'NO_TRADES':  'num_trades','SC_TYPE':     'sc_type',
    # UDiFF new-format BSE columns (post-2024)
    'FinInstrmId':'bse_code',  'TckrSymb':    'symbol',     'ClsPric':   'close',
    'OpnPric':    'open',      'HghPric':     'high',       'LwPric':    'low',
    'PrvsClsgPric':'prev_close','TtlTradgVol':'volume',     'TtlTrfVal': 'turnover',
    'ISIN':       'isin',
}


def _parse_bse_df(df):
    """Rename + coerce + filter. Always returns clean DataFrame or None."""
    if df is None or df.empty:
        return None
    df = df.copy()
    df = df.rename(columns=BSE_COL_MAP)
    df.columns = [c.lower().strip() for c in df.columns]
    if 'symbol' not in df.columns and 'sc_name' in df.columns:
        df['symbol'] = df['sc_name']
    for col in ['open', 'high', 'low', 'close', 'volume', 'prev_close', 'turnover']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    for col in ['isin', 'bse_code']:
        if col not in df.columns:
            df[col] = ''
        else:
            df[col] = df[col].fillna('').astype(str).str.strip()
    if 'close' not in df.columns:
        return None
    df = df[df['close'] > 0]
    if 'symbol' in df.columns:
        df['symbol'] = df['symbol'].fillna('').astype(str).str.strip()
        df = df[df['symbol'].str.len() > 0]
    return df.reset_index(drop=True) if not df.empty else None


def download_bse_bhav(date_obj, bse_client, tmp_dir):
    """
    Download BSE bhav copy using the `bse` pip package.
    Works for ALL dates — the package handles BSE auth internally.
    Returns parsed DataFrame or None (holiday / not yet published).
    """
    if not BSE_PKG or bse_client is None:
        return None
    try:
        fp = bse_client.bhavcopyReport(
            date=datetime.datetime.combine(
                date_obj, datetime.datetime.min.time()
            ),
            folder=tmp_dir,
        )
        if fp is None or not Path(fp).exists():
            return None
        df = pd.read_csv(fp)
        try:
            os.remove(fp)
        except Exception:
            pass
        return _parse_bse_df(df)
    except (RuntimeError, FileNotFoundError):
        # RuntimeError  = report not available (holiday / future date)
        # FileNotFoundError = download failed / corrupt file
        return None
    except Exception as e:
        print(f"    ⚠️  BSE error {date_obj}: {type(e).__name__}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — COLUMN NORMALISER
# ══════════════════════════════════════════════════════════════════════════════

# Priority-ordered column map: first match wins for each target column.
# Using a list of (raw, canonical) pairs so we can apply in priority order
# and avoid duplicate column names when the raw CSV has both TckrSymb and
# FinInstrmId (both would rename to 'symbol', creating two 'symbol' columns).
NSE_COL_PRIORITY = [
    # Symbol — TckrSymb takes priority over FinInstrmId
    ('tckrsymb',      'symbol'),
    ('fininstrmid',   'symbol'),
    ('symbol',        'symbol'),
    # Close
    ('clspric',       'close'),
    ('close_price',   'close'),
    ('close',         'close'),
    # Prev close
    ('prvsclsgpric',  'prev_close'),
    ('prevclose',     'prev_close'),
    ('prev_close',    'prev_close'),
    # Open
    ('opnpric',       'open'),
    ('open_price',    'open'),
    ('open',          'open'),
    # High
    ('hghpric',       'high'),
    ('high_price',    'high'),
    ('high',          'high'),
    # Low
    ('lwpric',        'low'),
    ('low_price',     'low'),
    ('low',           'low'),
    # Volume
    ('ttltradgvol',   'volume'),
    ('tottrdqty',     'volume'),
    ('total_trd_qty', 'volume'),
    # Turnover
    ('ttltrfval',     'turnover'),
    ('tottrdval',     'turnover'),
    # Others
    ('isin',          'isin'),
]


def normalise(df, exchange):
    if df is None or df.empty:
        return None
    df = df.copy()

    # Step 1: lowercase all column names
    df.columns = [str(c).lower().strip().replace(' ', '_') for c in df.columns]

    # Step 2: rename using priority order — first match per target wins.
    # This prevents duplicate columns when raw CSV has e.g. both tckrsymb
    # AND fininstrmid (both map to 'symbol' → would create two 'symbol' cols).
    assigned_targets = set()
    rename_map = {}
    for raw, target in NSE_COL_PRIORITY:
        if raw in df.columns and target not in assigned_targets:
            rename_map[raw] = target
            assigned_targets.add(target)
    df = df.rename(columns=rename_map)

    # Step 3: drop any residual duplicate-named columns (keeps first occurrence)
    df = df.loc[:, ~df.columns.duplicated()]

    # Step 4: check required columns exist
    required = ['symbol', 'open', 'high', 'low', 'close']
    if any(c not in df.columns for c in required):
        return None

    # Step 5: coerce numerics
    for col in ['open', 'high', 'low', 'close', 'prev_close', 'volume', 'turnover']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        else:
            df[col] = 0.0

    # Step 6: ensure identity columns exist
    for col in ['isin', 'bse_code']:
        if col not in df.columns:
            df[col] = ''

    # Step 7: clean symbol — guaranteed to be a Series now
    df = df.reset_index(drop=True)
    df['symbol'] = df['symbol'].fillna('').astype(str).str.strip()
    df = df[df['symbol'].str.len() > 0].reset_index(drop=True)
    df = df[df['close'] > 0].reset_index(drop=True)
    df['exchange'] = exchange
    return df


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — TECHNICAL INDICATOR COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

def compute_rsi(series, period=14):
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def compute_adx(high, low, close, period=14):
    tr   = pd.concat([high - low,
                      (high - close.shift()).abs(),
                      (low  - close.shift()).abs()], axis=1).max(axis=1)
    up   = high - high.shift()
    dn   = low.shift() - low
    dm_p = up.where((up > dn) & (up > 0), 0.0)
    dm_n = dn.where((dn > up) & (dn > 0), 0.0)
    atr  = tr.ewm(com=period - 1, min_periods=period).mean()
    di_p = 100 * dm_p.ewm(com=period - 1, min_periods=period).mean() / atr.replace(0, 1)
    di_n = 100 * dm_n.ewm(com=period - 1, min_periods=period).mean() / atr.replace(0, 1)
    dx   = 100 * (di_p - di_n).abs() / (di_p + di_n).replace(0, 1)
    return dx.ewm(com=period - 1, min_periods=period).mean()


def compute_technicals(hist):
    if hist is None or len(hist) < 20:
        return {}

    df = hist.sort_values('date').copy()
    df = df.dropna(subset=['close', 'high', 'low'])

    c  = df['close'].astype(float)
    h  = df['high'].astype(float)
    l  = df['low'].astype(float)
    v  = df['volume'].fillna(0).astype(float)
    n  = len(df)

    sma20   = c.rolling(20).mean()
    sma50   = c.rolling(50).mean()
    sma200  = c.rolling(200).mean()
    ema12   = c.ewm(span=12, adjust=False).mean()
    ema26   = c.ewm(span=26, adjust=False).mean()
    macd_l  = ema12 - ema26
    macd_s  = macd_l.ewm(span=9, adjust=False).mean()
    macd_h  = macd_l - macd_s
    rsi     = compute_rsi(c)
    adx     = compute_adx(h, l, c) if n >= 28 else pd.Series(0.0, index=c.index)
    lo14    = l.rolling(14).min()
    hi14    = h.rolling(14).max()
    stk     = 100 * (c - lo14) / (hi14 - lo14 + 1e-10)
    std     = stk.rolling(3).mean()
    tp      = (h + l + c) / 3
    raw_mf  = tp * v
    pos_mf  = raw_mf.where(tp > tp.shift(), 0).rolling(14).sum()
    neg_mf  = raw_mf.where(tp < tp.shift(), 0).rolling(14).sum()
    mfi     = 100 - (100 / (1 + pos_mf / neg_mf.replace(0, 1e-10)))
    obv     = (v * ((c > c.shift()).astype(float) - (c < c.shift()).astype(float))).cumsum()
    tr      = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr     = tr.ewm(com=13, min_periods=14).mean()
    bb_mid  = c.rolling(20).mean()
    bb_std  = c.rolling(20).std()
    bb_up   = bb_mid + 2 * bb_std
    bb_lo   = bb_mid - 2 * bb_std
    vwap    = (tp * v).rolling(20).sum() / v.rolling(20).sum().replace(0, 1)
    atr14   = tr.rolling(14).mean()
    st_up   = ((h + l) / 2) + 3 * atr14
    st_lo   = ((h + l) / 2) - 3 * atr14
    # Supertrend signal: price vs SMA20 + ATR14 threshold
    # BUY  when close > SMA20 + 0.5*ATR14  (price trending above average)
    # SELL when close < SMA20 - 0.5*ATR14  (price trending below average)
    # NEUTRAL otherwise (consolidation)
    sma20_st   = c.rolling(20).mean()
    supertr    = pd.Series('NEUTRAL', index=c.index)
    _buy_mask  = c > (sma20_st + 0.5 * atr14)
    _sell_mask = c < (sma20_st - 0.5 * atr14)
    supertr[_buy_mask]  = 'BUY'
    supertr[_sell_mask] = 'SELL'
    sup1    = l.rolling(20).min()
    sup2    = l.rolling(40).min()
    res1    = h.rolling(20).max()
    res2    = h.rolling(40).max()

    def _v(s):
        val = s.iloc[-1]
        return float(val) if not pd.isna(val) else 0.0

    lc   = _v(c)
    lv   = _v(vwap)
    mv   = _v(macd_l)
    ms   = _v(macd_s)
    on   = _v(obv)
    o5   = float(obv.iloc[-6]) if n > 6 else on

    return {
        'sma_20':        round(_v(sma20),  2),
        'sma_50':        round(_v(sma50),  2),
        'sma_200':       round(_v(sma200), 2),
        'ema_12':        round(_v(ema12),  2),
        'ema_26':        round(_v(ema26),  2),
        'macd':          round(mv, 4),
        'macd_signal':   round(ms, 4),
        'macd_hist':     round(_v(macd_h), 4),
        'rsi_14':        round(_v(rsi),    2),
        'adx':           round(_v(adx),    2),
        'stoch_k':       round(_v(stk),    2),
        'stoch_d':       round(_v(std),    2),
        'mfi_14':        round(_v(mfi),    2),
        'obv':           round(on, 0),
        'atr_14':        round(_v(atr),    2),
        'vwap':          round(lv if lv > 0 else lc, 2),
        'bb_upper':      round(_v(bb_up),  2),
        'bb_lower':      round(_v(bb_lo),  2),
        'supertrend':    str(supertr.iloc[-1]),
        'above_vwap':    'YES' if lc > lv else 'NO',
        'macd_signal_txt': 'BUY' if mv > ms else ('SELL' if mv < ms else 'NEUTRAL'),
        'obv_signal':    'RISING' if on > o5 else 'FALLING',
        'support1':      round(_v(sup1), 2),
        'support2':      round(_v(sup2), 2),
        'resist1':       round(_v(res1), 2),
        'resist2':       round(_v(res2), 2),
    }


def compute_weekly_momentum(hist):
    if hist is None or len(hist) < 11:
        return {'chg_2w': 0, 'chg_4w': 0, 'chg_6w': 0, 'chg_8w': 0,
                'vol_spike_50d': 0, 'beta_90d': 1.0}
    df   = hist.sort_values('date')
    curr = float(df['close'].iloc[-1])

    def _chg(n):
        if len(df) >= n:
            base = float(df['close'].iloc[-n])
            return round((curr - base) / base * 100, 2) if base > 0 else 0
        return 0

    vol_now  = float(df['volume'].iloc[-1])
    vol_50d  = df['volume'].tail(50).mean()
    spike    = round(vol_now / vol_50d, 2) if vol_50d > 0 else 0
    returns  = df['close'].pct_change().dropna().tail(90)
    beta     = round(float(returns.std() * 15), 2) if len(returns) > 20 else 1.0

    return {
        'chg_2w':       _chg(11),
        'chg_4w':       _chg(21),
        'chg_6w':       _chg(31),
        'chg_8w':       _chg(41),
        'vol_spike_50d': spike,
        'beta_90d':     beta,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — SAFE UPSERT
# ══════════════════════════════════════════════════════════════════════════════

def upsert(df, table, conn):
    """
    Insert df rows into table, matching only columns that exist in both.
    Uses explicit column list (not SELECT *) so partial-column inserts
    work correctly when df has fewer columns than the table.
    """
    if df is None or df.empty:
        return
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        table_cols = [r[1] for r in cur.fetchall()]
        if not table_cols:
            return
        keep = [c for c in table_cols if c in df.columns]
        if not keep:
            return
        df = df[keep].reset_index(drop=True)
        tmp = f"_tmp_{table}"
        df.to_sql(tmp, conn, if_exists='replace', index=False)
        # Use explicit column list — prevents "N columns but M values" error
        # when df has fewer columns than the destination table
        col_list = ", ".join(keep)
        conn.execute(
            f"INSERT OR REPLACE INTO {table} ({col_list}) "
            f"SELECT {col_list} FROM {tmp}"
        )
        conn.execute(f"DROP TABLE IF EXISTS {tmp}")
        conn.commit()
    except Exception as e:
        print(f"    ⚠️  upsert({table}): {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — PRICE ENRICHMENT (52w + day_chg + vol_50d)
# ══════════════════════════════════════════════════════════════════════════════

def enrich_prices(conn, date_iso):
    try:
        df = pd.read_sql(
            "SELECT symbol, exchange, date, high, low, close, prev_close, volume "
            "FROM daily_prices WHERE date <= ? ORDER BY symbol, date",
            conn, params=(date_iso,)
        )
        if df.empty:
            return

        updates = []
        for sym, grp in df.groupby('symbol'):
            grp     = grp.sort_values('date')
            latest  = grp[grp['date'] == date_iso]
            if latest.empty:
                continue
            latest   = latest.iloc[0]
            close    = float(latest['close'])
            pclose   = float(latest['prev_close'])
            day_chg  = round((close - pclose) / pclose * 100, 2) if pclose > 0 else 0
            last_252 = grp.tail(252)
            h52      = round(float(last_252['high'].max()), 2)
            l52      = round(float(last_252['low'].min()),  2)
            vol50    = round(float(grp.tail(50)['volume'].mean()), 0)
            updates.append((day_chg, h52, l52, vol50, sym, date_iso, latest['exchange']))

        if updates:
            conn.executemany(
                "UPDATE daily_prices SET day_chg_pct=?, week_high_52=?, "
                "week_low_52=?, vol_50d_avg=? "
                "WHERE symbol=? AND date=? AND exchange=?",
                updates
            )
            conn.commit()
    except Exception as e:
        print(f"    ⚠️  enrich_prices: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — MAIN BACKFILL LOOP
# ══════════════════════════════════════════════════════════════════════════════

def run_backfill():
    print(f"\n🚀  {DAYS_TO_BACKFILL}-day backfill → {DB_NAME}\n")

    conn = sqlite3.connect(DB_NAME)
    init_all_tables(conn)
    migrate_db(conn)   # add any new columns to existing DB

    today  = datetime.datetime.now(IST).date()
    stats  = dict(nse_ok=0, nse_fail=0, bse_ok=0, bse_fail=0,
                  deliv_ok=0, deliv_fail=0)

    tmp_dir    = tempfile.mkdtemp(prefix="bse_bhav_")
    bse_client = BseClient(download_folder=tmp_dir) if BSE_PKG else None

    try:
        for i in range(DAYS_TO_BACKFILL, 0, -1):
            target   = today - timedelta(days=i)
            if target.weekday() in (5, 6):
                continue
            date_iso = target.strftime("%Y-%m-%d")
            print(f"📅 {date_iso} | ", end="", flush=True)

            # ── NSE bhav ─────────────────────────────────────────────────────
            nse_raw = download_nse_bhav(target)
            nse_df  = normalise(nse_raw, 'NSE') if nse_raw is not None else None
            if nse_df is not None:
                nse_df['date'] = date_iso
                upsert(nse_df, 'daily_prices', conn)
                # Seed symbol_master (ignore if already exists)
                sm_rows = nse_df[['symbol','isin','exchange']].drop_duplicates()
                conn.executemany(
                    "INSERT OR IGNORE INTO symbol_master "
                    "(symbol, isin, exchange, updated_on) VALUES (?,?,?,?)",
                    [(str(row['symbol']), str(row['isin']),
                      str(row['exchange']), date_iso)
                     for _, row in sm_rows.iterrows()]
                )
                conn.commit()
                print(f"NSE✅({len(nse_df)}) ", end="", flush=True)
                stats['nse_ok'] += 1
            else:
                print("NSE❌ ", end="", flush=True)
                stats['nse_fail'] += 1

            # ── BSE bhav ─────────────────────────────────────────────────────
            if bse_client:
                bse_raw = download_bse_bhav(target, bse_client, tmp_dir)
                bse_df  = normalise(bse_raw, 'BSE') if bse_raw is not None else None
                if bse_df is not None:
                    bse_df['date'] = date_iso
                    upsert(bse_df, 'daily_prices', conn)
                    if 'bse_code' in bse_df.columns:
                        for _, row in bse_df[bse_df['bse_code'] != ''][['symbol','bse_code']].drop_duplicates().iterrows():
                            conn.execute(
                                "UPDATE symbol_master SET bse_code=? WHERE symbol=?",
                                (str(row['bse_code']), row['symbol'])
                            )
                        conn.commit()
                    print(f"BSE✅({len(bse_df)}) ", end="", flush=True)
                    stats['bse_ok'] += 1
                else:
                    print("BSE❌ ", end="", flush=True)
                    stats['bse_fail'] += 1
            else:
                print("BSE⏭️  ", end="", flush=True)

            # ── NSE Delivery ──────────────────────────────────────────────────
            deliv = download_nse_delivery(target)
            if deliv is not None and not deliv.empty:
                deliv['date'] = date_iso
                upsert(deliv, 'delivery_stats', conn)
                # Reflect delivery_pct into daily_prices
                updates = [
                    (float(row['delivery_pct']), row['symbol'], date_iso)
                    for _, row in deliv[['symbol','delivery_pct']].iterrows()
                ]
                conn.executemany(
                    "UPDATE daily_prices SET delivery_pct=? WHERE symbol=? AND date=?",
                    updates
                )
                conn.commit()
                print(f"Deliv✅({len(deliv)}) ", end="", flush=True)
                stats['deliv_ok'] += 1
            else:
                stats['deliv_fail'] += 1

            # ── Enrich 52w / day_chg / vol50d ────────────────────────────────
            enrich_prices(conn, date_iso)

            print("", flush=True)
            time.sleep(1.2)

    finally:
        if bse_client:
            try:
                bse_client.__exit__(None, None, None)
            except Exception:
                pass
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

    # ── Post-backfill: compute technicals + weekly momentum ──────────────────
    print("\n🔧 Computing technical indicators & weekly momentum for all symbols...")
    _compute_all_indicators(conn)

    # ── Section 10: Fetch NSE fundamentals — tiered symbol selection ──────────
    # Tier 1: top 100 by turnover → guaranteed to cover large/mid caps
    # Tier 2: close>₹50 AND delivery>40% → quality small/micro caps
    #         (high institutional delivery = genuine interest, not penny junk)
    # Combined ~200 symbols at ~1.5s each = ~5 min total.
    # This mirrors Stage 3's MAX_SMALL_MICRO=65 intent without excluding quality
    # small/micro caps that have strong BS and high institutional delivery.
    print("\n🏦 Fetching NSE fundamentals (large/mid + quality small/micro)...")
    try:
        stage3_syms = pd.read_sql(
            """
            WITH base AS (
                SELECT
                    symbol,
                    AVG(close)        AS avg_close,
                    AVG(delivery_pct) AS avg_deliv,
                    COUNT(*)          AS days_traded,
                    SUM(turnover)     AS total_turnover
                FROM daily_prices
                WHERE exchange = 'NSE'
                  AND close > 10
                  AND delivery_pct > 20
                GROUP BY symbol
                HAVING days_traded >= 10
            ),
            tier1 AS (
                SELECT symbol, 1 AS tier FROM base
                ORDER BY total_turnover DESC LIMIT 100
            ),
            tier2 AS (
                SELECT symbol, 2 AS tier FROM base
                WHERE avg_close > 50
                  AND avg_deliv > 40
                  AND symbol NOT IN (SELECT symbol FROM tier1)
                ORDER BY avg_deliv DESC, total_turnover DESC LIMIT 100
            )
            SELECT symbol FROM tier1
            UNION ALL
            SELECT symbol FROM tier2
            """,
            conn
        )['symbol'].tolist()
        print(f"   {len(stage3_syms)} symbols selected "
              f"(top-100 by turnover + quality small/micro by delivery)")
        fetch_nse_fundamentals(conn, stage3_syms, max_symbols=200)
    except Exception as e:
        print(f"   ⚠️  Fundamentals fetch warning: {e}")

    conn.close()

    print("\n" + "─" * 60)
    print(f"✅ NSE    : {stats['nse_ok']} days  |  {stats['nse_fail']} failed")
    print(f"✅ BSE    : {stats['bse_ok']} days  |  {stats['bse_fail']} failed")
    print(f"✅ Delivery: {stats['deliv_ok']} days  |  {stats['deliv_fail']} failed")
    print(f"📦 Saved → {DB_NAME}")
    print("─" * 60)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — POST-BACKFILL INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def _compute_all_indicators(conn):
    # ── Step 1: Active symbols only (traded in last 7 days) ──────────────────
    # Processing ALL 8,700+ historical symbols one-by-one is very slow.
    # A stock not traded in the last 7 days will have volume=0 today and
    # gets dropped at Stage 1 Gate F3 BEFORE technicals are ever read.
    # So computing technicals for inactive/delisted symbols is 100% wasted work.
    # -7 days (vs -30 days) gives ~2,500 active symbols instead of 6,600+.
    active_syms = pd.read_sql(
        """
        SELECT DISTINCT symbol
        FROM   daily_prices
        WHERE  date >= date('now', '-7 days')
        ORDER  BY symbol
        """,
        conn
    )['symbol'].tolist()

    total   = len(active_syms)
    skipped = max(0, 8711 - total)
    print(f"   {total} active symbols to process "
          f"(traded last 7 days — {skipped}+ inactive/delisted skipped)...",
          flush=True)

    ti_rows   = []
    wm_rows   = []
    processed = 0

    # ── Step 2: Chunked bulk reads (500 symbols per chunk) ────────────────────
    # 1 DB query per 500 symbols instead of 1 query per symbol.
    # ~2,500 / 500 = ~5 queries vs 8,711 previously.
    # RAM per chunk: 500 syms × 250 days × ~60 bytes ≈ 12 MB
    # — well within GitHub Actions 7 GB free tier limit.
    # DataFrame is deleted after each chunk to keep memory flat.
    CHUNK  = 500
    chunks = [active_syms[i: i + CHUNK] for i in range(0, total, CHUNK)]

    for chunk_syms in chunks:
        placeholders = ",".join(["?"] * len(chunk_syms))
        chunk_hist = pd.read_sql(
            f"SELECT symbol, date, open, high, low, close, volume "
            f"FROM daily_prices "
            f"WHERE symbol IN ({placeholders}) "
            f"ORDER BY symbol, date ASC",
            conn, params=chunk_syms
        )

        for sym, hist in chunk_hist.groupby('symbol', sort=False):
            try:
                hist = hist.reset_index(drop=True)
                if len(hist) < 5:
                    continue

                latest_date = hist['date'].iloc[-1]

                ti = compute_technicals(hist)
                if ti:
                    ti['symbol'] = sym
                    ti['date']   = latest_date
                    ti_rows.append(ti)

                wm = compute_weekly_momentum(hist)
                wm['symbol'] = sym
                wm['date']   = latest_date
                wm_rows.append(wm)

            except Exception:
                pass

            processed += 1
            if processed % 250 == 0:
                print(f"   ... {processed}/{total}", flush=True)

        # Release chunk from memory immediately after processing
        del chunk_hist

    if ti_rows:
        upsert(pd.DataFrame(ti_rows), 'technical_indicators', conn)
        print(f"   ✅ technical_indicators: {len(ti_rows)} rows")

    if wm_rows:
        upsert(pd.DataFrame(wm_rows), 'weekly_momentum', conn)
        print(f"   ✅ weekly_momentum: {len(wm_rows)} rows")



# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — NSE FUNDAMENTALS FETCH (free, no API key)
# Fetches: companyName, sector, PE, EPS, 52w H/L, marketCap,
#          promoter%, pledge%, FII%, DII% for all symbols
# Runs once per symbol — skips if updated within 7 days
# ══════════════════════════════════════════════════════════════════════════════

def _make_nse_session():
    """
    Create a session that bypasses NSE's Akamai bot detection.
    Tries cloudscraper first (handles JS challenges), falls back to requests.
    GitHub Actions IPs are blocked by NSE's JSON API with plain requests.
    """
    # Try cloudscraper — already in requirements.txt, handles Akamai/Cloudflare
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        # Warm up with homepage to get cookies
        try:
            scraper.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=12)
        except Exception:
            pass
        return scraper, "cloudscraper"
    except ImportError:
        pass

    # Fallback: plain requests with full headers
    session = requests.Session()
    session.headers.update({
        'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/124.0.0.0 Safari/537.36',
        'Accept':          'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection':      'keep-alive',
        'Referer':         'https://www.nseindia.com/',
        'Origin':          'https://www.nseindia.com',
        'sec-ch-ua':       '"Chromium";v="124", "Google Chrome";v="124"',
        'sec-ch-ua-mobile':'?0',
        'sec-ch-ua-platform': '"Windows"',
        'Sec-Fetch-Dest':  'empty',
        'Sec-Fetch-Mode':  'cors',
        'Sec-Fetch-Site':  'same-origin',
        'X-Requested-With':'XMLHttpRequest',
    })
    try:
        session.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
        session.get("https://www.nseindia.com/market-data/live-equity-market", timeout=10)
        time.sleep(0.5)
    except Exception:
        pass
    return session, "requests"


def _fetch_equity_master() -> dict:
    """
    Download NSE EQUITY_L.csv — static file, no bot protection.
    Returns {symbol: company_name} for all listed NSE stocks.
    Works reliably on GitHub Actions (no Akamai, no session needed).
    """
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    try:
        r = requests.get(url, headers=NSE_HEADERS, timeout=20)
        if r.status_code == 200 and len(r.content) > 1000:
            import io
            df = pd.read_csv(io.StringIO(r.text))
            # Columns: SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, ...
            sym_col  = next((c for c in df.columns if 'SYMBOL' in c.upper()), None)
            name_col = next((c for c in df.columns if 'NAME' in c.upper()), None)
            if sym_col and name_col:
                return dict(zip(
                    df[sym_col].str.strip(),
                    df[name_col].str.strip()
                ))
    except Exception as e:
        print(f"   ⚠️  EQUITY_L.csv fetch failed: {e}")
    return {}


def _nse_quote(symbol: str, session) -> dict:
    """Fetch equity quote from NSE API using bot-bypass session."""
    try:
        url = f"https://www.nseindia.com/api/quote-equity?symbol={symbol}"
        r = session.get(url, timeout=12)
        if r.status_code != 200:
            return {}
        d = r.json()
        info  = d.get("info", {})
        quote = d.get("priceInfo", {})
        meta  = d.get("metadata", {})
        wk52  = quote.get("weekHighLow", {})
        return {
            "company_name": info.get("companyName", ""),
            "sector":       info.get("industry", ""),
            "isin":         info.get("isin", ""),
            "face_value":   float(meta.get("pdFaceValue", 10) or 10),
            "pe":           float(quote.get("pdSymbolPe") or 0),
            "eps":          float(quote.get("eps") or 0),
            "pb":           float(quote.get("pb") or 0),
            "high_52w":     float(wk52.get("max") or 0),
            "low_52w":      float(wk52.get("min") or 0),
            "mcap_cr":      float(d.get("securityInfo", {}).get("totalMcap") or 0) / 1e7,
        }
    except Exception:
        return {}


def _nse_shareholding(symbol: str, session) -> dict:
    """Fetch shareholding pattern from NSE corp-info API."""
    try:
        url = f"https://www.nseindia.com/api/corp-info?symbol={symbol}"
        r = session.get(url, timeout=12)
        if r.status_code != 200:
            return {}
        d = r.json()
        sh = d.get("shareholdingPatterns", {}).get("data", [{}])[0] if d.get("shareholdingPatterns") else {}
        return {
            "promoter_pct": float(sh.get("promoterAndPromoterGroupTotal") or 0),
            "fii_pct":      float(sh.get("fiisTotal") or 0),
            "dii_pct":      float(sh.get("diisTotal") or 0),
            "public_float": float(sh.get("publicTotal") or 0),
        }
    except Exception:
        return {}


def _fetch_nse_index_sectors() -> dict:
    """
    Download NSE index constituent CSVs to get sector/industry for ~1000 stocks.
    These are static files on NSE archives — no bot protection, works on GitHub Actions.
    Returns {symbol: industry}
    """
    urls = [
        "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
        "https://archives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
        "https://archives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
        "https://archives.nseindia.com/content/indices/ind_niftymicrocap250_list.csv",
    ]
    result = {}
    for url in urls:
        try:
            r = requests.get(url, headers=NSE_HEADERS, timeout=15)
            if r.status_code == 200 and len(r.content) > 100:
                import io
                df = pd.read_csv(io.StringIO(r.text))
                sym_col = next((c for c in df.columns if 'symbol' in c.lower()), None)
                ind_col = next((c for c in df.columns if 'industry' in c.lower() or 'sector' in c.lower()), None)
                if sym_col and ind_col:
                    for _, row in df.iterrows():
                        s = str(row[sym_col]).strip()
                        ind = str(row[ind_col]).strip()
                        if s and ind and s not in result:
                            result[s] = ind
        except Exception:
            pass
    return result


def _fetch_yfinance_data(symbols: list) -> dict:
    """
    Fetch PE, EPS, PB, Beta, MCap, Sector via yfinance (Yahoo Finance).
    Works on GitHub Actions — Yahoo Finance has no bot detection for data APIs.
    Returns {symbol: {pe, eps, pb, beta, mcap_cr, sector, company_name, div_yield}}
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    # NOTE: yfinance 1.3.0 does NOT accept a session= parameter in Ticker().
    # Passing session= raises TypeError → all symbols silently fail.
    # Plain yf.Ticker(sym) works reliably — confirmed by test_yfinance.py (165 keys).

    result = {}
    # Per-symbol loop — yf.Tickers() batch is unreliable in yfinance 1.x.
    for sym in symbols:
        try:
            info = yf.Ticker(sym + ".NS").info
            # yfinance >=0.2 uses "currentPrice"; older used "regularMarketPrice"
            if not info:
                continue
            _price = (info.get("currentPrice") or
                      info.get("regularMarketPrice") or
                      info.get("previousClose") or 0)
            if not _price:
                continue
            mcap_inr = float(info.get("marketCap", 0) or 0)
            def _yf(k, m=1.0, d=0.0):
                try: return round(float(info.get(k) or 0) * m, 4)
                except: return d
            result[sym] = {
                "pe":            _yf("trailingPE"),
                "eps":           _yf("trailingEps"),
                "pb":            _yf("priceToBook"),
                "beta":          _yf("beta", d=1.0) or 1.0,
                "mcap_cr":       round(mcap_inr / 1e7, 2),
                "sector":        str(info.get("sector") or ""),
                "company_name":  str(info.get("longName") or ""),
                # dividendYield: yfinance ALWAYS returns true fraction (0.0224 = 2.24%)
                # Store as-is (fraction). master_funnel converts to % for display.
                # Never multiply by 100 here — that caused 224% bug.
                "div_yield":     _yf("dividendYield"),   # stored as fraction e.g. 0.0224
                "roe":           _yf("returnOnEquity", m=100),
                "roa":           _yf("returnOnAssets", m=100),
                "debt_equity":   _yf("debtToEquity"),
                "current_ratio": _yf("currentRatio"),
                "quick_ratio":   _yf("quickRatio"),
                # Compute CR from balance sheet — try multiple yfinance field names
                "_cr_computed": (lambda ca, cl: round(ca/cl, 3) if ca > 0 and cl > 0 else 0)(
                    float(info.get("totalCurrentAssets") or
                          info.get("currentAssets") or 0),
                    float(info.get("totalCurrentLiabilities") or
                          info.get("currentLiabilities") or 1)
                ),
                "gross_margin":  _yf("grossMargins", m=100),
                "ebitda_margin": _yf("ebitdaMargins", m=100),
                "net_margin":    _yf("profitMargins", m=100),
                "rev_yoy":       _yf("revenueGrowth", m=100),
                "pat_yoy":       _yf("earningsGrowth", m=100),
                "total_cash":    round(float(info.get("totalCash") or 0)/1e7, 2),
                "total_debt":    round(float(info.get("totalDebt") or 0)/1e7, 2),
                "fcf":           round(float(info.get("freeCashflow") or 0)/1e7, 2),
                "operating_cf":  round(float(info.get("operatingCashflow") or 0)/1e7, 2),
                "total_current_assets": round(float(info.get("totalCurrentAssets") or 0)/1e7, 2),
                "total_current_liab":   round(float(info.get("totalCurrentLiabilities") or 0)/1e7, 2),
                "payout_ratio":  _yf("payoutRatio", m=100),
                "ps":            _yf("priceToSalesTrailing12Months"),
                "ev_ebitda":     _yf("enterpriseToEbitda"),
                "peg":           _yf("pegRatio"),
                "promoter_pct":  _yf("heldPercentInsiders", m=100),
                "inst_pct":      _yf("heldPercentInstitutions", m=100),
            }
        except Exception:
            pass
        time.sleep(0.5)  # polite delay per symbol

    # Second pass: balance_sheet fetch for stocks still missing CR
    # Fixes: correct row names (no 'Total' prefix), .NS + .BO, quarterly fallback,
    #        proper QR = (CA - Inventory) / CL, cap raised to 100
    missing_cr = [s for s, d in result.items()
                  if d.get("current_ratio", 0) == 0 and d.get("_cr_computed", 0) == 0]
    if missing_cr:
        import yfinance as _yf2

        def _get_bs_row(bs_index, keywords, excludes=()):
            """Find row matching ALL keywords, excluding any in excludes."""
            for r in bs_index:
                rs = str(r).lower()
                if all(k.lower() in rs for k in keywords) and                    not any(e.lower() in rs for e in excludes):
                    return r
            return None

        def _bs_val(bs, row):
            try:
                return float(bs.loc[row].iloc[0] or 0)
            except Exception:
                return 0.0

        for sym in missing_cr[:100]:
            _cr_val = 0.0
            _qr_val = 0.0
            for suffix in (".NS", ".BO"):
                try:
                    _tk = _yf2.Ticker(sym + suffix)
                    for _bs in [_tk.balance_sheet, _tk.quarterly_balance_sheet]:
                        if _bs is None or _bs.empty:
                            continue
                        _ca_row = _get_bs_row(_bs.index, ["current", "assets"],
                                              excludes=["non current", "noncurrent", "other"])
                        _cl_row = _get_bs_row(_bs.index, ["current", "liabilit"],
                                              excludes=["non current", "noncurrent",
                                                        "deferred", "other"])
                        if _ca_row and _cl_row:
                            _ca = _bs_val(_bs, _ca_row)
                            _cl = _bs_val(_bs, _cl_row)
                            if _ca > 0 and _cl > 0:
                                _cr_val = round(_ca / _cl, 3)
                                _inv_row = _get_bs_row(_bs.index, ["inventor"],
                                                       excludes=["non", "other"])
                                _inv = _bs_val(_bs, _inv_row) if _inv_row else 0.0
                                _qr_val = round((_ca - _inv) / _cl, 3) if _cl > 0                                           else round(_cr_val * 0.8, 3)
                                break
                    if _cr_val > 0:
                        break
                    time.sleep(0.2)
                except Exception:
                    pass

            if _cr_val > 0:
                result[sym]["current_ratio"] = _cr_val
                result[sym]["quick_ratio"]   = max(_qr_val, 0.1)

    # ── Third pass: quarterly + annual income statement for growth/margin fields ──
    # Populates: npm_q1/q2/q3, margin_expansion, q3_rev/pat/ebitda (₹Cr),
    #            rev_cagr_1y/3y, pat_cagr_1y/3y, ebitda_cagr_1y
    # Runs on the FULL symbols list (not just those that got basic info in pass 1)
    # because quarterly_income_stmt uses a different Yahoo endpoint from .info —
    # it works independently even when the batch .info call fails.
    # Capped at 150 symbols to stay within rate limits.
    _syms_for_income = symbols[:150]
    if _syms_for_income:
        import yfinance as _yf3

        def _is_val(df, row_keywords, col_idx):
            """Extract a numeric value from an income-stmt DataFrame safely.
            Tries multiple row-name variants (yfinance names differ across versions).
            row_keywords = list of keyword sets to try in order, e.g.
              [["Total Revenue"], ["Revenue"], ["TotalRevenue"]]
            """
            if df is None or df.empty:
                return 0.0
            idx_lower = {str(r).lower(): r for r in df.index}
            for kws in row_keywords:
                for idx_str, real_row in idx_lower.items():
                    if all(k.lower() in idx_str for k in kws):
                        try:
                            cols = list(df.columns)
                            if col_idx >= len(cols):
                                return 0.0
                            v = df.loc[real_row, cols[col_idx]]
                            return float(v) if v is not None and str(v) != "nan" else 0.0
                        except Exception:
                            return 0.0
            return 0.0

        def _ebitda_val(df, col_idx):
            """Get EBITDA value, preferring plain 'EBITDA' row over 'Normalized EBITDA'.
            yfinance 1.3.0 returns both; plain EBITDA is more accurate."""
            if df is None or df.empty:
                return 0.0
            idx_lower = {str(r).lower(): r for r in df.index}
            # Pass 1: exact "ebitda" row excluding any "normalized" qualifier
            for idx_str, real_row in idx_lower.items():
                if idx_str.strip() == "ebitda" or \
                   ("ebitda" in idx_str and "normalized" not in idx_str
                    and "adjusted" not in idx_str):
                    try:
                        cols = list(df.columns)
                        if col_idx >= len(cols): return 0.0
                        v = df.loc[real_row, cols[col_idx]]
                        return float(v) if v is not None and str(v) != "nan" else 0.0
                    except Exception:
                        return 0.0
            # Pass 2: any EBITDA including normalized
            for idx_str, real_row in idx_lower.items():
                if "ebitda" in idx_str:
                    try:
                        cols = list(df.columns)
                        if col_idx >= len(cols): return 0.0
                        v = df.loc[real_row, cols[col_idx]]
                        return float(v) if v is not None and str(v) != "nan" else 0.0
                    except Exception:
                        return 0.0
            # Pass 3: operating income as last resort
            return _is_val(df, [["operating income"]], col_idx)

        def _safe_cagr(v_new, v_old, years):
            """CAGR % rounded to 2dp. Returns 0 if inputs invalid or NaN."""
            try:
                import math as _math
                if v_old <= 0 or v_new <= 0 or years <= 0:
                    return 0.0
                if _math.isnan(v_new) or _math.isnan(v_old):
                    return 0.0
                result = round(((v_new / v_old) ** (1.0 / years) - 1.0) * 100, 2)
                return result if not _math.isnan(result) else 0.0
            except Exception:
                return 0.0

        # Row-name keyword lists for each metric (try in order — first match wins)
        _REV  = [["total revenue"], ["operating revenue"], ["totalrevenue"], ["revenue"]]
        _PAT  = [["net income common"], ["net income"], ["netincome"],
                 ["net profit"], ["profit after tax"]]
        # For EBITDA: prefer plain "EBITDA" row over "Normalized EBITDA"
        # _is_val will try each keyword set in order; within a set it takes first match.
        # We separate "ebitda" (no qualifier) from "normalized ebitda" by checking
        # that "normalized" is NOT in the row name for the first attempt.

        for _sym in _syms_for_income:
            try:
                _tk3 = _yf3.Ticker(_sym + ".NS")

                # ── Quarterly income statement ─────────────────────────────
                # Columns ordered newest → oldest: col[0]=most recent Q, col[1]=Q-1, etc.
                _qi = None
                for _attr in ("quarterly_income_stmt", "quarterly_financials"):
                    _qi = getattr(_tk3, _attr, None)
                    if _qi is not None and not _qi.empty:
                        break

                if _qi is not None and not _qi.empty and len(_qi.columns) >= 3:
                    # Q1 = most recent quarter (col 0), Q2 = col 1, Q3 = col 2
                    _rev_q0 = _is_val(_qi, _REV,  0)
                    _rev_q1 = _is_val(_qi, _REV,  1)
                    _rev_q2 = _is_val(_qi, _REV,  2)
                    _pat_q0 = _is_val(_qi, _PAT,  0)
                    _pat_q1 = _is_val(_qi, _PAT,  1)
                    _pat_q2 = _is_val(_qi, _PAT,  2)
                    _ebi_q2 = _ebitda_val(_qi, 2)

                    # NPM = Net Profit Margin = PAT / Revenue * 100
                    _npm_q1 = round(_pat_q0 / _rev_q0 * 100, 2) if _rev_q0 > 0 else 0.0
                    _npm_q2 = round(_pat_q1 / _rev_q1 * 100, 2) if _rev_q1 > 0 else 0.0
                    _npm_q3 = round(_pat_q2 / _rev_q2 * 100, 2) if _rev_q2 > 0 else 0.0

                    # Margin Expansion = "YES" when NPM has risen 3 consecutive quarters
                    # i.e. Q3(oldest) < Q2 < Q1(newest)  — improving trend
                    _mexp = "YES" if (_npm_q1 > 0 and _npm_q2 > 0 and _npm_q3 > 0
                                      and _npm_q3 < _npm_q2 < _npm_q1) else "NO"

                    # Ensure entry exists even if pass-1 batch .info returned nothing
                    if _sym not in result:
                        result[_sym] = {}
                    if _npm_q1 != 0: result[_sym]["npm_q1"] = _npm_q1
                    if _npm_q2 != 0: result[_sym]["npm_q2"] = _npm_q2
                    if _npm_q3 != 0: result[_sym]["npm_q3"] = _npm_q3
                    result[_sym]["margin_expansion"] = _mexp

                    # Q3 absolute figures in ₹ Crore (col[2] = third most recent quarter)
                    _INR_CR = 1e7
                    if _rev_q2 > 0:
                        result[_sym]["q3_rev"]   = round(_rev_q2 / _INR_CR, 2)
                    if _pat_q2 > 0:
                        result[_sym]["q3_pat"]   = round(_pat_q2 / _INR_CR, 2)
                    if _ebi_q2 > 0:
                        result[_sym]["q3_ebitda"] = round(_ebi_q2 / _INR_CR, 2)

                # ── Annual income statement for CAGR ──────────────────────
                # Columns ordered newest → oldest: col[0]=FY current, col[1]=FY-1, col[3]=FY-3
                _ai = None
                for _attr in ("income_stmt", "financials"):
                    _ai = getattr(_tk3, _attr, None)
                    if _ai is not None and not _ai.empty:
                        break

                if _ai is not None and not _ai.empty and len(_ai.columns) >= 2:
                    _rev_y0 = _is_val(_ai, _REV,  0)   # latest FY
                    _rev_y1 = _is_val(_ai, _REV,  1)   # FY-1
                    _pat_y0 = _is_val(_ai, _PAT,  0)
                    _pat_y1 = _is_val(_ai, _PAT,  1)
                    _ebi_y0 = _ebitda_val(_ai, 0)
                    _ebi_y1 = _ebitda_val(_ai, 1)

                    # 1Y CAGR (= simple YoY when n=1)
                    _rc1 = _safe_cagr(_rev_y0, _rev_y1, 1)
                    _pc1 = _safe_cagr(_pat_y0, _pat_y1, 1)
                    _ec1 = _safe_cagr(_ebi_y0, _ebi_y1, 1)

                    # Ensure entry exists even if pass-1 batch .info returned nothing
                    if _sym not in result:
                        result[_sym] = {}
                    if _rc1 != 0: result[_sym]["rev_cagr_1y"]   = _rc1
                    if _pc1 != 0: result[_sym]["pat_cagr_1y"]   = _pc1
                    if _ec1 != 0: result[_sym]["ebitda_cagr_1y"] = _ec1

                    # 3Y CAGR requires at least 4 annual columns
                    if len(_ai.columns) >= 4:
                        _rev_y3 = _is_val(_ai, _REV, 3)
                        _pat_y3 = _is_val(_ai, _PAT, 3)
                        _rc3 = _safe_cagr(_rev_y0, _rev_y3, 3)
                        _pc3 = _safe_cagr(_pat_y0, _pat_y3, 3)
                        if _rc3 != 0: result[_sym]["rev_cagr_3y"] = _rc3
                        if _pc3 != 0: result[_sym]["pat_cagr_3y"] = _pc3

                time.sleep(0.3)   # polite rate-limit delay per symbol
            except Exception:
                pass   # never break the outer loop — missing data is fine

    return result


def fetch_nse_fundamentals(conn, symbols: list, max_symbols: int = 500):
    """
    Populate fundamental data for symbols using multiple free sources.
    Source priority (all work on GitHub Actions without bot detection):
      1. EQUITY_L.csv         → company names (NSE static file)
      2. NSE index CSVs       → sector/industry (NSE static files)
      3. yfinance             → PE, EPS, PB, Beta, MCap, Div Yield (Yahoo Finance)
      4. NSE JSON API         → fallback (blocked on GH Actions, kept for local runs)
    Skips symbols with pe_ttm>0 AND company_name already populated (within 7 days).
    """
    today_str = datetime.datetime.now(IST).strftime("%Y-%m-%d")
    cutoff    = (datetime.datetime.now(IST) - timedelta(days=7)).strftime("%Y-%m-%d")

    # Skip symbols already fully populated within 7 days
    already_fm = {r[0] for r in conn.execute(
        """SELECT symbol FROM fundamental_metrics
           WHERE date >= ? AND pe_ttm > 0
           AND (div_yield IS NOT NULL AND div_yield > 0 AND div_yield <= 25)
           AND (current_ratio IS NOT NULL AND current_ratio > 0)""",
        (cutoff,)
    ).fetchall()}
    already_cn = {r[0] for r in conn.execute(
        "SELECT symbol FROM symbol_master WHERE company_name != '' AND company_name IS NOT NULL"
    ).fetchall()}
    already = already_fm & already_cn
    to_fetch = [s for s in symbols if s not in already][:max_symbols]

    if not to_fetch:
        print(f"   Fundamentals: all {len(symbols)} symbols up to date. Skipping.")
        return

    print(f"   Fetching fundamentals for {len(to_fetch)} symbols via NSE API...")

    # ── SOURCE 1: EQUITY_L.csv → company names ────────────────────────────────
    equity_master = _fetch_equity_master()
    if equity_master:
        print(f"   ✅ EQUITY_L.csv: {len(equity_master)} company names loaded")
        for sym in to_fetch:
            cname = equity_master.get(sym, "")
            if cname:
                conn.execute(
                    "UPDATE symbol_master SET company_name=? WHERE symbol=? "
                    "AND (company_name='' OR company_name IS NULL)",
                    (cname, sym)
                )
        conn.commit()
    else:
        print("   ⚠️  EQUITY_L.csv unavailable")

    # ── SOURCE 2: NSE index CSVs → sector/industry ───────────────────────────
    sector_map = _fetch_nse_index_sectors()
    if sector_map:
        print(f"   ✅ NSE index CSVs: {len(sector_map)} sectors loaded")
        for sym in to_fetch:
            sec = sector_map.get(sym, "")
            if sec:
                conn.execute(
                    "UPDATE symbol_master SET sector=? WHERE symbol=? "
                    "AND (sector='' OR sector IS NULL OR sector='General')",
                    (sec, sym)
                )
        conn.commit()
    else:
        print("   ⚠️  NSE index CSVs unavailable")

    # ── SOURCE 3: yfinance → PE, EPS, PB, Beta, MCap ─────────────────────────
    yf_data = _fetch_yfinance_data(to_fetch)
    print(f"   ✅ yfinance: {len(yf_data)}/{len(to_fetch)} symbols fetched")

    today_str2 = datetime.datetime.now(IST).strftime("%Y-%m-%d")
    fm_rows = []
    sh_rows = []
    ok = len(yf_data)
    fail = len(to_fetch) - ok

    for sym, d in yf_data.items():
        # Update symbol_master with yfinance data (overrides EQUITY_L where available)
        cn = d.get("company_name","")
        sec = d.get("sector","")
        eps = d.get("eps", 0)
        pe  = d.get("pe",  0)
        mcap = d.get("mcap_cr", 0)
        if cn:
            conn.execute("UPDATE symbol_master SET company_name=? WHERE symbol=?", (cn, sym))
        if sec:
            conn.execute("UPDATE symbol_master SET sector=? WHERE symbol=?", (sec, sym))
        conn.execute(
            "UPDATE symbol_master SET updated_on=? WHERE symbol=?",
            (today_str2 + f"|eps={eps}|mcap={mcap}|pe={pe}", sym)
        )

        cmp = float((conn.execute(
            "SELECT close FROM daily_prices WHERE symbol=? ORDER BY date DESC LIMIT 1", (sym,)
        ).fetchone() or (0,))[0])

        pb  = d.get("pb", 0)
        ey  = round((eps / cmp * 100), 2) if cmp > 0 and eps > 0 else 0
        roe = round(eps * pe / (pb * cmp), 2) if pb > 0 and cmp > 0 and pe > 0 else 0

        fm_rows.append({
            "symbol":       sym,   "date":         today_str2,
            "pe_ttm":       pe,    "pb":            pb,
            "earn_yield":   ey,
            "roe":          d.get("roe", 0),
            "roa":          d.get("roa", 0),
            "gross_margin": d.get("gross_margin", 0),
            "ebitda_margin":d.get("ebitda_margin", 0),
            "net_margin":   d.get("net_margin", 0),
            "de_ratio":     d.get("debt_equity", 0),    # DB col = de_ratio
            # Use direct currentRatio if available, else compute from assets/liabilities
            "current_ratio":d.get("current_ratio", 0) or d.get("_cr_computed", 0),
            "quick_ratio":  d.get("quick_ratio", 0),
            "total_debt_cr":d.get("total_debt", 0),
            "cash_cr":      d.get("total_cash", 0),
            "fcf_cr":       d.get("fcf", 0),
            "fcf_yield":    round(d.get("fcf",0)/d.get("mcap_cr",1)*100,2) if d.get("mcap_cr",0)>0 else 0,
            "ps":           d.get("ps", 0),
            "ev_ebitda":    d.get("ev_ebitda", 0),
            "peg":          d.get("peg", 0),
            # Extra balance sheet fields for P/CF and derived ratios
            "operating_cf_cr":    d.get("operating_cf", 0),
            "curr_assets_cr":     d.get("total_current_assets", 0),
            "curr_liab_cr":       d.get("total_current_liab", 0),
            # ── FIX: keys fetched by yfinance but were missing from INSERT ──
            "div_yield":    d.get("div_yield", 0),      # dividendYield ×100
            "payout_ratio": d.get("payout_ratio", 0),   # payoutRatio ×100
            "rev_yoy":      d.get("rev_yoy", 0),        # revenueGrowth ×100
            "pat_yoy":      d.get("pat_yoy", 0),        # earningsGrowth ×100
            # ── Quarterly NPM / Margin Expansion (from third pass) ─────────
            "npm_q1":           d.get("npm_q1", 0),
            "npm_q2":           d.get("npm_q2", 0),
            "npm_q3":           d.get("npm_q3", 0),
            "margin_expansion": 1 if d.get("margin_expansion") == "YES" else 0,
            # ── Q3 absolute figures in ₹ Crore ─────────────────────────────
            "q_rev_cr":         d.get("q3_rev",    0),
            "q_pat_cr":         d.get("q3_pat",    0),
            "q_ebitda_cr":      d.get("q3_ebitda", 0),
            # ── Growth CAGRs (from third pass) ─────────────────────────────
            "rev_cagr_1y":      d.get("rev_cagr_1y",    0),
            "rev_cagr_3y":      d.get("rev_cagr_3y",    0),
            "pat_cagr_1y":      d.get("pat_cagr_1y",    0),
            "pat_cagr_3y":      d.get("pat_cagr_3y",    0),
            "ebitda_cagr_1y":   d.get("ebitda_cagr_1y", 0),
        })

        # ── FIX: populate shareholding table from yfinance holding data ──────
        # sh_rows was declared but never populated — promoter% and FII% always blank
        _promo = d.get("promoter_pct", 0)
        _inst  = d.get("inst_pct", 0)    # heldPercentInstitutions = FII+DII combined
        if _promo > 0 or _inst > 0:
            sh_rows.append({
                "symbol":      sym,
                "date":        today_str2,
                "promoter_pct":round(_promo, 2),
                "promoter_qoq":0.0,          # QoQ change not available from yfinance
                "pledge_pct":  0.0,           # BSE filings only — not in yfinance
                "pledge_dir":  "",
                "fii_pct":     round(_inst, 2),  # inst_pct = FII+DII combined (best proxy)
                "fii_qoq":     0.0,
                "dii_pct":     0.0,              # Cannot separate DII from yfinance
                "dii_qoq":     0.0,
                "public_float":round(max(0, 100 - _promo - _inst), 2),
            })

    conn.commit()

    # ── SOURCE 4: NSE JSON API fallback (works locally, often blocked on GH Actions) ─
    nse_ok = 0
    if len(yf_data) < len(to_fetch) * 0.5:
        # yfinance got less than 50% → try NSE JSON API
        still_needed = [s for s in to_fetch if s not in yf_data][:50]
        session, session_type = _make_nse_session()
        print(f"   Trying NSE API ({session_type}) for {len(still_needed)} remaining symbols...")
        time.sleep(2)
        for sym in still_needed:
            try:
                quote = _nse_quote(sym, session)
                if quote and quote.get("pe", 0) > 0:
                    nse_ok += 1
                    # merge into fm_rows (same logic as yf_data above)
                    eps = quote.get("eps", 0); pe = quote.get("pe", 0)
                    pb  = quote.get("pb", 0)
                    cmp = float((conn.execute(
                        "SELECT close FROM daily_prices WHERE symbol=? ORDER BY date DESC LIMIT 1", (sym,)
                    ).fetchone() or (0,))[0])
                    ey  = round((eps / cmp * 100), 2) if cmp > 0 and eps > 0 else 0
                    fm_rows.append({
                        "symbol": sym, "date": today_str2,
                        "pe_ttm": pe, "pb": pb, "earn_yield": ey,
                        "div_yield": 0, "roe": 0, "rev_yoy": 0, "pat_yoy": 0,
                    })
                    if quote.get("company_name"):
                        conn.execute(
                            "UPDATE symbol_master SET company_name=? WHERE symbol=?",
                            (quote["company_name"], sym)
                        )
            except Exception:
                pass
            time.sleep(0.3)
        print(f"   NSE API: {nse_ok} additional symbols fetched")

    # ── v10.6 FIX (Bug #3): NSE shareholding enrichment for DII separation ────
    # yfinance only provides heldPercentInstitutions (FII+DII combined).
    # NSE corp-info API returns separate fiisTotal and diisTotal.
    # We call this for symbols where dii_pct is still 0 after yfinance pass.
    # Only attempts top-100 to respect NSE rate limits.
    if sh_rows:
        try:
            _nse_session_obj, _ = _make_nse_session()
            _nse_dii_count = 0
            _nse_attempts = 0
            for _row in sh_rows[:100]:   # rate-limit guard: top 100 only
                if _row.get("dii_pct", 0) > 0:
                    continue   # already has DII from earlier source
                _nse_attempts += 1
                try:
                    _nse_sh = _nse_shareholding(_row["symbol"], _nse_session_obj)
                    if _nse_sh and _nse_sh.get("dii_pct", 0) > 0:
                        _row["dii_pct"]      = round(_nse_sh["dii_pct"], 2)
                        # If NSE also gave us better FII (separated), prefer it
                        if _nse_sh.get("fii_pct", 0) > 0:
                            _row["fii_pct"]  = round(_nse_sh["fii_pct"], 2)
                        # Recompute public_float with the corrected breakdown
                        _p = _row.get("promoter_pct", 0)
                        _f = _row.get("fii_pct", 0)
                        _d = _row.get("dii_pct", 0)
                        _row["public_float"] = round(max(0, 100 - _p - _f - _d), 2)
                        _nse_dii_count += 1
                except Exception:
                    pass
                time.sleep(0.3)   # respect NSE rate limit
            print(f"   NSE shareholding: enriched DII for {_nse_dii_count}/{_nse_attempts} symbols")
        except Exception as _e:
            print(f"   ⚠️  NSE shareholding enrichment skipped: {_e}")

    if fm_rows:
        upsert(pd.DataFrame(fm_rows), "fundamental_metrics", conn)
    if sh_rows:
        upsert(pd.DataFrame(sh_rows), "shareholding", conn)

    print(f"   Fundamentals: {ok + nse_ok} fetched, {fail - nse_ok} failed → "
          f"fundamental_metrics: {len(fm_rows)} rows, shareholding: {len(sh_rows)} rows")

def fetch_deep_fundamentals(symbol):
    ticker = yf.Ticker(f"{symbol}.NS")
    bs = ticker.balance_sheet
    cf = ticker.cashflow
    
    data = {}
    try:
        # Extracting specific rows for CCC and Coverage
        data['inventory'] = bs.loc['Inventory'].iloc[0] if 'Inventory' in bs.index else 0
        data['receivables'] = bs.loc['Receivables'].iloc[0] if 'Receivables' in bs.index else 0
        data['payables'] = bs.loc['Accounts Payable'].iloc[0] if 'Accounts Payable' in bs.index else 0
        data['ebitda'] = ticker.info.get('ebitda', 0)
        data['int_expense'] = ticker.info.get('interestExpense', 1)
        
        # Calculate CCC (Simplified)
        # DIO = (Inv / COGS) * 365 | DSO = (Rec / Rev) * 365
        # For free tier, we proxy with Rev as COGS is often missing
        rev = ticker.info.get('totalRevenue', 1)
        data['ccc_days'] = round(((data['inventory'] + data['receivables'] - data['payables']) / rev) * 365, 0)
    except:
        pass

if __name__ == "__main__":
    run_backfill()