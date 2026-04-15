"""
Market Data Backfill — NSE + BSE
=================================
INSTALL (one-time):
    pip install pandas requests pytz bse

The `bse` package (pip install bse) handles all BSE session/auth/anti-bot
internally. It was last updated Feb 2026 and is the only reliable way
to download BSE bhav copies programmatically in 2026.
"""

import pandas as pd
import sqlite3
import requests
import zipfile
import io
import tempfile
import os
from datetime import datetime, timedelta
from pathlib import Path
import pytz
import time

# ── BSE via the official pip package ──────────────────────────────────────────
try:
    from bse import BSE
    BSE_PKG_AVAILABLE = True
    print("✅ `bse` package found")
except ImportError:
    BSE_PKG_AVAILABLE = False
    print("❌ `bse` package NOT found")
    print("   Run this ONCE in your terminal, then re-run the script:")
    print("   pip install bse")
    print()

# ─── Configuration ─────────────────────────────────────────────────────────────
DB_NAME          = "market_data.db"
DAYS_TO_BACKFILL = 60
IST              = pytz.timezone('Asia/Kolkata')

NSE_HEADERS = {
    'User-Agent':    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/124.0.0.0 Safari/537.36',
    'Referer':       'https://www.nseindia.com/',
    'Accept':        'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

# ─── Column Normaliser ──────────────────────────────────────────────────────────
def normalise_columns(df, exchange):
    tag_map = {
        'SYMBOL': 'symbol', 'TckrSymb': 'symbol', 'FinInstrmId': 'symbol',
        'SC_CODE': 'symbol', 'scrip_code': 'symbol',
        'OPEN':  'open',  'OpnPric': 'open',
        'HIGH':  'high',  'HghPric': 'high',
        'LOW':   'low',   'LwPric':  'low',
        'CLOSE': 'close', 'ClsPric': 'close',
        'TOTTRDQTY':  'volume',   'TtlTradgVol': 'volume',   'NO_SHARES':  'volume',
        'TOTTRDVAL':  'turnover', 'TtlTrfVal':   'turnover', 'NET_TURNOV': 'turnover',
    }
    df = df.rename(columns=tag_map)
    df.columns = [c.lower() for c in df.columns]

    # BSE uses numeric FinInstrmId
    if exchange == 'BSE' and 'fininstrmid' in df.columns and 'symbol' not in df.columns:
        df['symbol'] = df['fininstrmid'].astype(str).str.strip()

    required = ['symbol', 'open', 'high', 'low', 'close', 'volume']
    if any(c not in df.columns for c in required):
        return None

    if 'turnover' not in df.columns:
        df['turnover'] = 0.0

    for col in ['open', 'high', 'low', 'close', 'volume', 'turnover']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['open', 'high', 'low', 'close'])

    return df[['symbol', 'open', 'high', 'low', 'close', 'volume', 'turnover']]


# ─── NSE Downloader (unchanged — working fine) ─────────────────────────────────
def download_nse(date_obj):
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
                session = requests.Session()
                session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=8)
                r = session.get(url, headers=NSE_HEADERS, timeout=15)

                if r.status_code == 200 and len(r.content) > 500:
                    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                        csv_files = [f for f in z.namelist() if f.endswith('.csv')]
                        if not csv_files:
                            break
                        df = pd.read_csv(z.open(csv_files[0]))

                    if 'SERIES' in df.columns:
                        df = df[df['SERIES'].str.strip() == 'EQ'].copy()
                    elif 'SctySrs' in df.columns:
                        df = df[df['SctySrs'].str.strip() == 'EQ'].copy()

                    result = normalise_columns(df, 'NSE')
                    if result is not None and len(result) > 0:
                        return result
            except Exception:
                time.sleep(2)
    return None


# ─── BSE Downloader — uses `bse` pip package ───────────────────────────────────
def download_bse(date_obj, bse_client, tmp_dir):
    """
    Uses BennyThadikaran's `bse` package (pip install bse) which correctly
    handles BSE's Akamai session/cookie auth internally.
    bse_client: an open BSE() instance shared across all dates.
    tmp_dir: a temp folder for the downloaded CSV files.
    """
    if not BSE_PKG_AVAILABLE or bse_client is None:
        return None

    try:
        # bhavcopyReport() downloads ZIP → extracts CSV → returns Path
        file_path = bse_client.bhavcopyReport(
            date=datetime.combine(date_obj, datetime.min.time()),
            folder=tmp_dir
        )

        if file_path is None or not Path(file_path).exists():
            return None

        df = pd.read_csv(file_path)

        # Clean up the temp file so the folder doesn't grow
        try:
            os.remove(file_path)
        except Exception:
            pass

        result = normalise_columns(df, 'BSE')
        if result is not None and len(result) > 0:
            return result

    except (RuntimeError, FileNotFoundError):
        # RuntimeError = report unavailable (holiday / not yet published)
        # FileNotFoundError = download failed
        pass
    except Exception:
        pass

    return None


# ─── Main Backfill ──────────────────────────────────────────────────────────────
def run_backfill():
    if not BSE_PKG_AVAILABLE:
        print("⛔ Cannot run: `bse` package missing. Install it first:")
        print("   pip install bse")
        return

    print(f"\n🚀 Starting {DAYS_TO_BACKFILL}-day backfill → {DB_NAME}\n")

    conn = sqlite3.connect(DB_NAME)
    conn.execute("DROP TABLE IF EXISTS stock_prices")
    conn.execute('''
        CREATE TABLE stock_prices (
            symbol   TEXT,
            open     REAL,
            high     REAL,
            low      REAL,
            close    REAL,
            volume   REAL,
            turnover REAL,
            exchange TEXT,
            date     TEXT
        )
    ''')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sym_date ON stock_prices(symbol, date)")
    conn.commit()

    today = datetime.now(IST).date()
    stats = {'nse_ok': 0, 'nse_fail': 0, 'bse_ok': 0, 'bse_fail': 0}

    # Create one temp dir and one BSE session, shared for all dates
    tmp_dir = tempfile.mkdtemp(prefix="bse_bhav_")

    with BSE(download_folder=tmp_dir) as bse_client:
        for i in range(DAYS_TO_BACKFILL, 0, -1):
            target_date = today - timedelta(days=i)
            if target_date.weekday() in (5, 6):
                continue

            date_iso = target_date.strftime("%Y-%m-%d")
            print(f"📅 {date_iso} | ", end="", flush=True)

            # NSE
            df_nse = download_nse(target_date)
            if df_nse is not None:
                df_nse['exchange'] = 'NSE'
                df_nse['date']     = date_iso
                df_nse.to_sql('stock_prices', conn, if_exists='append', index=False)
                conn.commit()
                print(f"NSE✅({len(df_nse)}) ", end="", flush=True)
                stats['nse_ok'] += 1
            else:
                print("NSE❌ ", end="", flush=True)
                stats['nse_fail'] += 1

            # BSE
            df_bse = download_bse(target_date, bse_client, tmp_dir)
            if df_bse is not None:
                df_bse['exchange'] = 'BSE'
                df_bse['date']     = date_iso
                df_bse.to_sql('stock_prices', conn, if_exists='append', index=False)
                conn.commit()
                print(f"BSE✅({len(df_bse)}) ", end="", flush=True)
                stats['bse_ok'] += 1
            else:
                print("BSE❌ ", end="", flush=True)
                stats['bse_fail'] += 1

            print("")
            time.sleep(1.2)

    conn.close()

    # Clean up temp dir
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    print("\n" + "─" * 55)
    print(f"✅ NSE : {stats['nse_ok']} days loaded  |  {stats['nse_fail']} failed")
    print(f"✅ BSE : {stats['bse_ok']} days loaded  |  {stats['bse_fail']} failed")
    print(f"📦 Saved → {DB_NAME}")
    print("─" * 55)


if __name__ == "__main__":
    run_backfill()