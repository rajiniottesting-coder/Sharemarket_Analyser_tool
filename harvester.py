"""
harvester.py
SECTION 1A & 1B — Multi-Stream Market Data Downloader (v7 FINAL)

Key fixes:
- NSE archives CDN does NOT need a homepage cookie hit
- Increased timeouts + retry logic
- BSE delivery URL uses correct DDMMYY format
- SME Bhav uses correct URL format with uppercase date
- All functions accept target_date as datetime.date (not datetime)
"""

import pandas as pd
import sqlite3
import requests
import zipfile
import io
import os
import tempfile
import time
from datetime import datetime, date
import pytz

try:
    from bse import BSE
    BSE_PKG_AVAILABLE = True
except ImportError:
    BSE_PKG_AVAILABLE = False
    print("⚠️  `bse` package not found. BSE downloads will be skipped. Run: pip install bse")

IST = pytz.timezone('Asia/Kolkata')

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

BSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bseindia.com/",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


def _as_date(target_date) -> date:
    """Safely coerce datetime or date to date."""
    if isinstance(target_date, datetime):
        return target_date.date()
    return target_date


def download_nse_bhavcopy(target_date, retries: int = 3) -> pd.DataFrame | None:
    """
    SECTION 1A: Download NSE Equity Bhav Copy.
    Uses the new nsearchives CDN (no homepage cookie needed).
    Filters EQ series only.
    Column map: TckrSymb→symbol, ClsPric→close, TtlTradgVol→volume, ISIN→isin
    """
    d = _as_date(target_date)
    ds = d.strftime("%Y%m%d")
    url = (
        f"https://nsearchives.nseindia.com/content/cm/"
        f"BhavCopy_NSE_CM_0_0_0_{ds}_F_0000.csv.zip"
    )

    for attempt in range(retries):
        try:
            r = requests.get(url, headers=NSE_HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 500:
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    csv_files = [f for f in z.namelist() if f.endswith(".csv")]
                    if not csv_files:
                        return None
                    df = pd.read_csv(z.open(csv_files[0]))

                # Filter EQ series (SctySrs is the new column name)
                series_col = None
                for col in ["SctySrs", "SERIES", "Series"]:
                    if col in df.columns:
                        series_col = col
                        break
                if series_col:
                    df = df[df[series_col].str.strip() == "EQ"].copy()

                print(f"✅ NSE Bhav downloaded: {len(df)} EQ records for {d}")
                return df
        except Exception as e:
            if attempt < retries - 1:
                print(f"   NSE attempt {attempt + 1} failed: {e}. Retrying in {5 * (attempt+1)}s...")
                time.sleep(5 * (attempt + 1))
            else:
                print(f"❌ NSE Bhav download failed after {retries} attempts: {e}")
    return None


# In harvester.py — replace download_bse_bhavcopy with this:
def download_bse_bhavcopy(target_date, retries: int = 2) -> pd.DataFrame | None:
    """
    BSE Bhav Copy download.
    BSE uses Cloudflare which blocks data-centre IPs (GitHub Actions).
    Strategy: try bse package → try cloudscraper → accept None gracefully.
    The pipeline runs in NSE-only mode when BSE is unavailable.
    """
    d = _as_date(target_date)

    # Strategy 1: bse pip package (handles session internally)
    if BSE_PKG_AVAILABLE:
        tmp_dir = tempfile.mkdtemp(prefix="bse_bhav_")
        try:
            with BSE(download_folder=tmp_dir) as bse_client:
                dt_combined = datetime.combine(d, datetime.min.time())
                file_path = bse_client.bhavcopyReport(date=dt_combined, folder=tmp_dir)
                if file_path and os.path.exists(file_path):
                    df = pd.read_csv(file_path)
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
                    print(f"✅ BSE Bhav downloaded via bse package: {len(df)} records for {d}")
                    return df
        except Exception as e:
            print(f"⚠️  BSE package failed: {e}")
        finally:
            try:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    # Strategy 2: cloudscraper (bypasses Cloudflare TLS fingerprinting)
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
        ds = d.strftime("%d%m%y").upper()
        url = f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{ds}_CSV.ZIP"
        r = scraper.get(url, timeout=30)
        if r.status_code == 200 and len(r.content) > 500:
            import zipfile, io
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                csv_files = [f for f in z.namelist() if f.endswith(".csv")]
                if csv_files:
                    df = pd.read_csv(z.open(csv_files[0]))
                    print(f"✅ BSE Bhav downloaded via cloudscraper: {len(df)} records for {d}")
                    return df
    except ImportError:
        print("⚠️  cloudscraper not installed. Add it to requirements.txt.")
    except Exception as e:
        print(f"⚠️  cloudscraper failed: {e}")

    # Both strategies failed — BSE unavailable from this environment
    print(f"ℹ️  BSE Bhav not available for {d}. Running in NSE-only mode (this is normal on cloud runners).")
    return None


def download_nse_delivery(target_date, retries: int = 3) -> pd.DataFrame | None:
    """
    SECTION 1A: NSE Delivery Data — provides DELIV_PER (delivery_pct).
    Column: SYMBOL, DELIV_PER
    URL uses DDMMYYYY format.
    """
    d = _as_date(target_date)
    ds = d.strftime("%d%m%Y")
    url = (
        f"https://nsearchives.nseindia.com/products/content/"
        f"sec_bhavdata_full_{ds}.csv"
    )

    for attempt in range(retries):
        try:
            r = requests.get(url, headers=NSE_HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 200:
                df = pd.read_csv(io.StringIO(r.text))
                print(f"✅ NSE Delivery downloaded: {len(df)} records for {d}")
                return df
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                print(f"❌ NSE Delivery download failed: {e}")
    return None


def download_bse_delivery(target_date, retries: int = 3) -> pd.DataFrame | None:
    """
    SECTION 1B: BSE Gross Deliverable Data.
    URL uses DDMMYY format.
    """
    d = _as_date(target_date)
    ds = d.strftime("%d%m%y")
    url = f"https://www.bseindia.com/BhavCopy/Gross_Deliverable_{ds}.zip"

    for attempt in range(retries):
        try:
            r = requests.get(url, headers=BSE_HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 500:
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    csv_files = [f for f in z.namelist() if f.endswith(".csv")]
                    if not csv_files:
                        return None
                    df = pd.read_csv(z.open(csv_files[0]))
                    print(f"✅ BSE Delivery downloaded: {len(df)} records for {d}")
                    return df
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                print(f"❌ BSE Delivery download failed: {e}")
    return None


def download_nse_sme_bhavcopy(target_date, retries: int = 3) -> pd.DataFrame | None:
    """
    SECTION 1A: NSE SME Bhav Copy.
    URL format: sme{DDMMYY}.csv  (uppercase)
    """
    d = _as_date(target_date)
    ds = d.strftime("%d%m%y").upper()
    url = f"https://nsearchives.nseindia.com/archives/sme/bhavcopy/sme{ds}.csv"

    for attempt in range(retries):
        try:
            r = requests.get(url, headers=NSE_HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 200:
                df = pd.read_csv(io.StringIO(r.text))
                print(f"✅ NSE SME Bhav downloaded: {len(df)} records for {d}")
                return df
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                print(f"❌ NSE SME download failed: {e}")
    return None


def download_bse_sme_bhavcopy(target_date) -> pd.DataFrame | None:
    """
    SECTION 1B: BSE SME — filtered from the main BSE Bhav Copy.
    BSE SME groups: M, MT (SME Migrated), S, ST
    """
    df = download_bse_bhavcopy(target_date)
    if df is None:
        return None

    # Normalise column names to lowercase for safety
    df.columns = [str(c).strip() for c in df.columns]

    # Find the group column
    group_col = None
    for col in ["SC_GROUP", "sc_group", "SCRIP_GRP", "GROUP"]:
        if col in df.columns:
            group_col = col
            break

    if group_col:
        sme_df = df[df[group_col].isin(["M", "MT", "S", "ST"])].copy()
        print(f"✅ BSE SME filtered: {len(sme_df)} SME records for {_as_date(target_date)}")
        return sme_df

    # If no group column found, return None rather than returning all BSE data
    print("⚠️  BSE SME: Could not identify group column in BSE Bhav Copy.")
    return None


def download_nse_fo_participant_data(target_date, retries: int = 3) -> pd.DataFrame | None:
    """
    SECTION 1A: NSE F&O Participant Position Data.
    Used for FII net position tracking (Section 3J).
    URL uses DDMMYYYY format.
    """
    d = _as_date(target_date)
    ds = d.strftime("%d%m%Y")
    url = (
        f"https://nsearchives.nseindia.com/content/nsccl/"
        f"fao_participant_vol_{ds}.csv"
    )

    for attempt in range(retries):
        try:
            r = requests.get(url, headers=NSE_HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 200:
                # skiprows=1 because the first row is a report header, not column names
                df = pd.read_csv(io.StringIO(r.text), skiprows=1)
                print(f"✅ NSE F&O Participant downloaded: {len(df)} records for {d}")
                return df
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                print(f"❌ NSE F&O Participant download failed: {e}")
    return None
