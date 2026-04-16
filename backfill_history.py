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

IST              = pytz.timezone('Asia/Kolkata')
DB_NAME          = "market_data.db"
DAYS_TO_BACKFILL = 365

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
# ══════════════════════════════════════════════════════════════════════════════

BSE_COL_MAP = {
    'SC_CODE': 'bse_code', 'SC_NAME': 'symbol', 'SC_GROUP': 'sc_group',
    'OPEN': 'open', 'HIGH': 'high', 'LOW': 'low', 'CLOSE': 'close',
    'LAST': 'last', 'PREVCLOSE': 'prev_close', 'NO_TRADES': 'num_trades',
    'NO_OF_SHRS': 'volume', 'NET_TURNOV': 'turnover',
    'ISIN_CODE': 'isin', 'SC_TYPE': 'sc_type',
}


def download_bse_bhav(date_obj, bse_client, tmp_dir):
    if not BSE_PKG or bse_client is None:
        return None
    try:
        fp = bse_client.bhavcopyReport(
            date=datetime.datetime.combine(date_obj, datetime.datetime.min.time()),
            folder=tmp_dir,
        )
        if fp is None or not Path(fp).exists():
            return None
        df = pd.read_csv(fp)
        try:
            os.remove(fp)
        except Exception:
            pass
        df = df.rename(columns=BSE_COL_MAP)
        df.columns = [c.lower().strip() for c in df.columns]
        if 'symbol' not in df.columns and 'sc_name' in df.columns:
            df['symbol'] = df['sc_name']
        for col in ['open', 'high', 'low', 'close', 'volume', 'prev_close']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['close'])
        df = df[df['close'] > 0]
        return df.reset_index(drop=True)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — COLUMN NORMALISER
# ══════════════════════════════════════════════════════════════════════════════

NSE_COL_MAP = {
    'tckrsymb': 'symbol', 'fininstrmid': 'symbol', 'symbol': 'symbol',
    'isin': 'isin',
    'clspric': 'close', 'close_price': 'close', 'close': 'close',
    'prvsclsgpric': 'prev_close', 'prevclose': 'prev_close', 'prev_close': 'prev_close',
    'opnpric': 'open', 'open_price': 'open', 'open': 'open',
    'hghpric': 'high', 'high_price': 'high', 'high': 'high',
    'lwpric': 'low', 'low_price': 'low', 'low': 'low',
    'ttltradgvol': 'volume', 'tottrdqty': 'volume', 'total_trd_qty': 'volume',
    'ttltrfval': 'turnover', 'tottrdval': 'turnover',
}


def normalise(df, exchange):
    if df is None or df.empty:
        return None
    df = df.copy()
    df.columns = [str(c).lower().strip().replace(' ', '_') for c in df.columns]
    df = df.rename(columns={k: v for k, v in NSE_COL_MAP.items() if k in df.columns})

    required = ['symbol', 'open', 'high', 'low', 'close']
    if any(c not in df.columns for c in required):
        return None

    for col in ['open', 'high', 'low', 'close', 'prev_close', 'volume', 'turnover']:
        df[col] = pd.to_numeric(df.get(col, 0), errors='coerce').fillna(0)

    for col in ['isin', 'bse_code']:
        if col not in df.columns:
            df[col] = ''

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
    supertr = pd.Series('NEUTRAL', index=c.index)
    supertr[c > st_up.shift()] = 'BUY'
    supertr[c < st_lo.shift()] = 'SELL'
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
        conn.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM {tmp}")
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
                    [(r.symbol, r.isin, r.exchange, date_iso)
                     for _, r in sm_rows.iterrows()]
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
                bse_client.exit()
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
    symbols = pd.read_sql(
        "SELECT DISTINCT symbol FROM daily_prices ORDER BY symbol", conn
    )['symbol'].tolist()

    print(f"   {len(symbols)} symbols to process...")
    ti_rows = []
    wm_rows = []

    for idx, sym in enumerate(symbols, 1):
        try:
            hist = pd.read_sql(
                "SELECT date, open, high, low, close, volume "
                "FROM daily_prices WHERE symbol=? ORDER BY date ASC",
                conn, params=(sym,)
            )
            if hist.empty or len(hist) < 5:
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

        if idx % 250 == 0:
            print(f"   ... {idx}/{len(symbols)}", flush=True)

    if ti_rows:
        upsert(pd.DataFrame(ti_rows), 'technical_indicators', conn)
        print(f"   ✅ technical_indicators: {len(ti_rows)} rows")

    if wm_rows:
        upsert(pd.DataFrame(wm_rows), 'weekly_momentum', conn)
        print(f"   ✅ weekly_momentum: {len(wm_rows)} rows")


if __name__ == "__main__":
    run_backfill()