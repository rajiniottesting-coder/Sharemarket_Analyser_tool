"""
backfill_history.py
===================
Backfills 60 days of NSE (+ BSE where available) market data
into the SAME market_data.db used by the live pipeline.

Run this ONCE after deleting the database, or any time you want
to rebuild historical data for vol-spike and momentum calculations.

Usage:
    python backfill_history.py           # 60 days (default)
    python backfill_history.py 30        # custom number of days
    python backfill_history.py 90        # 3 months

Requirements (already in requirements.txt):
    pip install pandas requests pytz bse
"""

import sys
import os
import io
import time
import sqlite3
import zipfile
import tempfile
import shutil
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import requests
import pytz

# ── BSE package (optional — pipeline runs NSE-only if missing) ────────────────
try:
    from bse import BSE
    BSE_PKG = True
except ImportError:
    BSE_PKG = False

# ── Config ────────────────────────────────────────────────────────────────────
DB_NAME          = "market_data.db"
DAYS_TO_BACKFILL = int(sys.argv[1]) if len(sys.argv) > 1 else 60
IST              = pytz.timezone("Asia/Kolkata")

# NSE 2026 holidays (add more as needed)
NSE_HOLIDAYS_2026 = {
    "2026-01-26", "2026-02-19", "2026-03-20", "2026-03-31",
    "2026-04-02", "2026-04-06", "2026-04-10", "2026-04-14",
    "2026-05-01", "2026-06-05", "2026-08-15", "2026-08-17",
    "2026-10-02", "2026-10-20", "2026-10-21", "2026-11-04",
    "2026-11-05", "2026-12-25",
}

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com/",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ── Schema: maps raw column names → canonical daily_prices column names ───────
COLUMN_MAP = {
    # NSE new bhav (post-July 2024 UDiFF)
    "tckrsymb":     "symbol",
    "clspric":      "close",
    "prvsclsgpric": "prev_close",
    "opnpric":      "open",
    "hghpric":      "high",
    "lwpric":       "low",
    "ttltradgvol":  "volume",
    "ttltrfval":    "turnover",
    "isin":         "isin",
    "sctysrs":      "series",
    # NSE old bhav
    "symbol":       "symbol",
    "series":       "series",
    "close":        "close",
    "close_price":  "close",
    "prev_close":   "prev_close",
    "prevclose":    "prev_close",
    "open":         "open",
    "open_price":   "open",
    "high":         "high",
    "high_price":   "high",
    "low":          "low",
    "low_price":    "low",
    "tottrdqty":    "volume",
    "ttl_trd_qnty": "volume",
    "tottrdval":    "turnover",
    # BSE bhav  ← SC_NAME is the ticker; SC_CODE is numeric (stored separately)
    "sc_name":      "symbol",
    "sc_code":      "bse_code",
    "isin_code":    "isin",
    "no_of_shrs":   "volume",
    "net_turnov":   "turnover",
    "net_turnover": "turnover",
    "sc_group":     "sc_group",
}

DAILY_PRICES_COLS = [
    "symbol", "bse_code", "isin", "date", "open", "high", "low",
    "close", "prev_close", "volume", "turnover", "delivery_pct",
    "exchange", "exchange_tag",
]


def _normalise(df, exchange: str) -> pd.DataFrame | None:
    """Normalise any raw bhav DataFrame to daily_prices schema."""
    if df is None or df.empty:
        return None

    df = df.copy()
    # Lowercase all column names
    df.columns = [str(c).lower().strip().replace(" ", "_") for c in df.columns]

    # Apply column map
    df = df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns})

    # Remove duplicate columns (e.g. both fininstrmid and tckrsymb → symbol)
    df = df.loc[:, ~df.columns.duplicated(keep="first")]

    # BSE guard: if symbol column is all-numeric it's SC_CODE not ticker
    if "symbol" in df.columns:
        sym = df["symbol"].fillna("").apply(lambda x: str(x).strip())
        non_empty = sym[sym != ""]
        if len(non_empty) > 0 and non_empty.apply(lambda x: x.isdigit()).all():
            if "bse_code" not in df.columns:
                df["bse_code"] = sym
            for alt in ["sc_name", "name", "security_name"]:
                if alt in df.columns and df[alt].notna().any():
                    df["symbol"] = df[alt].fillna("").apply(lambda x: str(x).strip())
                    break

    # Ensure required columns
    for col in DAILY_PRICES_COLS:
        if col not in df.columns:
            df[col] = "" if col in ("symbol", "bse_code", "isin", "exchange",
                                    "exchange_tag", "sc_group") else 0.0

    # Coerce numerics
    for col in ["open", "high", "low", "close", "prev_close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # Force symbol to string
    df["symbol"] = df["symbol"].fillna("").apply(lambda x: str(x).strip())

    # Drop garbage rows
    df = df[~df["symbol"].isin(["", "0", "nan"])].reset_index(drop=True)
    df = df[df["close"] > 0].reset_index(drop=True)

    # Tag exchange
    df["exchange"]     = exchange
    df["exchange_tag"] = exchange

    # Keep only daily_prices columns
    df = df[[c for c in DAILY_PRICES_COLS if c in df.columns]]

    return df if not df.empty else None


def _download_nse(d: date) -> pd.DataFrame | None:
    """Download NSE equity bhav copy for a given date."""
    ds = d.strftime("%Y%m%d")
    url = (
        f"https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{ds}_F_0000.csv.zip"
    )
    for attempt in range(3):
        try:
            r = requests.get(url, headers=NSE_HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 500:
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    csv_files = [f for f in z.namelist() if f.endswith(".csv")]
                    if not csv_files:
                        return None
                    df = pd.read_csv(z.open(csv_files[0]))
                # Filter EQ series
                for col in ["SctySrs", "SERIES", "Series"]:
                    if col in df.columns:
                        df = df[df[col].str.strip() == "EQ"].copy()
                        break
                return _normalise(df, "NSE")
        except Exception:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    return None


def _download_bse(d: date, bse_client, tmp_dir: str) -> pd.DataFrame | None:
    """Download BSE bhav copy via the bse pip package."""
    if not BSE_PKG or bse_client is None:
        return None
    try:
        file_path = bse_client.bhavcopyReport(
            date=datetime.combine(d, datetime.min.time()),
            folder=tmp_dir,
        )
        if not file_path or not Path(file_path).exists():
            return None
        df = pd.read_csv(file_path)
        try:
            os.remove(file_path)
        except Exception:
            pass
        return _normalise(df, "BSE")
    except Exception:
        return None


def _init_db(conn):
    """Create daily_prices table (matches live pipeline schema exactly)."""
    conn.execute("""
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
    conn.commit()


def _insert(df: pd.DataFrame, date_str: str, conn):
    """Insert rows, replacing any existing rows for this date+exchange."""
    exchange = df["exchange"].iloc[0] if "exchange" in df.columns else "NSE"
    conn.execute(
        "DELETE FROM daily_prices WHERE date = ? AND exchange = ?",
        (date_str, exchange)
    )
    df.to_sql("_tmp_bp", conn, if_exists="replace", index=False)
    conn.execute("INSERT OR IGNORE INTO daily_prices SELECT * FROM _tmp_bp")
    conn.execute("DROP TABLE IF EXISTS _tmp_bp")
    conn.commit()


def _db_stats(conn) -> tuple[int, float]:
    total = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
    size_mb = os.path.getsize(DB_NAME) / 1_048_576
    return total, size_mb


def run_backfill():
    print(f"""
╔══════════════════════════════════════════════════════╗
║  NSE/BSE Market Data Backfill — {DAYS_TO_BACKFILL} calendar days  ║
║  Target DB : {DB_NAME:<40}║
╚══════════════════════════════════════════════════════╝
""")

    # ── Connect and init DB ───────────────────────────────────────────────────
    conn = sqlite3.connect(DB_NAME)
    _init_db(conn)

    existing = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
    print(f"📂 Existing rows in daily_prices: {existing:,}")

    today       = datetime.now(IST).date()
    stats       = {"nse_ok": 0, "nse_skip": 0, "nse_fail": 0,
                   "bse_ok": 0, "bse_skip": 0, "bse_fail": 0}
    tmp_dir     = tempfile.mkdtemp(prefix="bse_bhav_backfill_")
    bse_client  = None

    # Open one BSE session for all dates
    if BSE_PKG:
        try:
            bse_client = BSE(download_folder=tmp_dir)
            print("✅ BSE session opened (will attempt BSE data)\n")
        except Exception as e:
            print(f"⚠️  BSE session failed: {e} — will run NSE-only\n")
    else:
        print("⚠️  `bse` package not installed — NSE-only mode\n")
        print("    Run: pip install bse   then re-run this script\n")

    print(f"{'Date':<13} {'NSE':>20} {'BSE':>20} {'DB rows':>10} {'Size':>8}")
    print("─" * 75)

    try:
        for i in range(DAYS_TO_BACKFILL, 0, -1):
            d = today - timedelta(days=i)

            # Skip weekends
            if d.weekday() >= 5:
                continue

            date_str = d.strftime("%Y-%m-%d")

            # Skip known holidays
            if date_str in NSE_HOLIDAYS_2026:
                print(f"{date_str}  HOLIDAY — skipped")
                continue

            # Check if already have data for this date (don't re-download)
            already = conn.execute(
                "SELECT COUNT(*) FROM daily_prices WHERE date = ?", (date_str,)
            ).fetchone()[0]
            if already > 0:
                print(f"{date_str}  already in DB ({already:,} rows) — skipped")
                stats["nse_skip"] += 1
                continue

            nse_msg = bse_msg = ""

            # ── NSE ──────────────────────────────────────────────────────────
            df_nse = _download_nse(d)
            if df_nse is not None:
                _insert(df_nse, date_str, conn)
                nse_msg = f"NSE ✅ {len(df_nse):,} rows"
                stats["nse_ok"] += 1
            else:
                nse_msg = "NSE ❌ (holiday/unavail)"
                stats["nse_fail"] += 1

            # ── BSE ──────────────────────────────────────────────────────────
            if bse_client:
                df_bse = _download_bse(d, bse_client, tmp_dir)
                if df_bse is not None:
                    _insert(df_bse, date_str, conn)
                    bse_msg = f"BSE ✅ {len(df_bse):,} rows"
                    stats["bse_ok"] += 1
                else:
                    bse_msg = "BSE ❌ (Cloudflare/unavail)"
                    stats["bse_fail"] += 1
            else:
                bse_msg = "BSE — skipped"
                stats["bse_skip"] += 1

            total, size_mb = _db_stats(conn)
            print(f"{date_str}  {nse_msg:>20}  {bse_msg:>25}  {total:>8,}  {size_mb:>5.2f}MB")

            # Polite delay so NSE/BSE servers don't throttle
            time.sleep(1.5)

    finally:
        # Close BSE session and clean up temp dir
        if bse_client:
            try:
                bse_client.__exit__(None, None, None)
            except Exception:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
        conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "─" * 75)
    print(f"✅  NSE: {stats['nse_ok']} days loaded  |  {stats['nse_fail']} failed  |  {stats['nse_skip']} skipped (already in DB)")
    print(f"{'✅' if stats['bse_ok'] else '⚠️ '} BSE: {stats['bse_ok']} days loaded  |  {stats['bse_fail']} failed  |  {stats['bse_skip']} skipped")

    conn2 = sqlite3.connect(DB_NAME)
    total, size_mb = _db_stats(conn2)
    conn2.close()
    print(f"\n📦  Database: {DB_NAME}")
    print(f"    Total rows in daily_prices: {total:,}")
    print(f"    DB file size: {size_mb:.2f} MB")
    print("─" * 75)

    if stats["nse_ok"] > 0:
        print("\n✅ Backfill complete. Run the main pipeline now:")
        print("   python master_funnel.py")
    else:
        print("\n⚠️  No data loaded. Check your internet connection and try again.")


if __name__ == "__main__":
    run_backfill()