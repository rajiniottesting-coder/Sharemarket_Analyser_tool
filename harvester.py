import requests
import zipfile
import io
import pandas as pd
from datetime import datetime

# Headers to prevent being blocked by NSE/BSE (Section 1A)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
}

def download_nse_bhavcopy(target_date):
    """Downloads NSE Bhavcopy and extracts 52W High/Low (Section 1A)"""
    date_str = target_date.strftime('%Y%m%d')
    url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"
    
    try:
        print(f"📡 Downloading NSE: {target_date.date()}...")
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                csv_name = z.namelist()[0]
                with z.open(csv_name) as f:
                    df = pd.read_csv(f)
                    df.columns = df.columns.str.strip()
                    
                    # Section 1A Mapping (includes 52W High/Low if available in CSV)
                    mapping = {
                        'TckrSymb': 'symbol', 'ClsPric': 'close', 'OpnPric': 'open',
                        'HghPric': 'high', 'LwPric': 'low', 'PrvsClsgPric': 'prev_close',
                        'TtlTradgVol': 'volume', 'TtlTrfVal': 'mcap', 'ISIN': 'isin'
                    }
                    df.rename(columns=mapping, inplace=True)
                    df['exchange'] = 'NSE'
                    df['date'] = target_date.strftime('%Y-%m-%d')
                    
                    # V6 Logic: Ensure 52W columns exist (placeholder for separate 52W file)
                    if 'high_52w' not in df.columns:
                        df['high_52w'] = df['high']
                        df['low_52w'] = df['low']
                    
                    return df
    except Exception as e:
        print(f"❌ NSE Error: {e}")
    return None

def download_bse_bhavcopy(target_date):
    """Downloads BSE Equity Bhavcopy (Section 1B)"""
    date_str = target_date.strftime('%d%m%y')
    url = f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{date_str}_CSV.ZIP"
    
    try:
        print(f"📡 Downloading BSE: {target_date.date()}...")
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                csv_name = z.namelist()[0]
                with z.open(csv_name) as f:
                    df = pd.read_csv(f)
                    df.columns = df.columns.str.strip()
                    
                    # Section 1B Mapping
                    mapping = {
                        'SC_CODE': 'bse_code', 'SC_NAME': 'symbol', 
                        'CLOSE': 'close', 'NO_SETS': 'volume',
                        'OPEN': 'open', 'HIGH': 'high', 'LOW': 'low'
                    }
                    df.rename(columns=mapping, inplace=True)
                    df['exchange'] = 'BSE'
                    df['date'] = target_date.strftime('%Y-%m-%d')
                    return df
    except Exception as e:
        print(f"❌ BSE Error: {e}")
    return None

def download_nse_delivery(target_date):
    """
    Downloads NSE MTO (Delivery) data (Section 1A).
    URL: MTO_DDMMYYYY.DAT
    """
    date_str = target_date.strftime('%d%m%Y')
    url = f"https://archives.nseindia.com/archives/equities/mto/MTO_{date_str}.DAT"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            # Skip the first 4 lines of the .DAT header as per NSE format
            lines = response.text.split('\n')[4:]
            data = [l.split(',') for l in lines if len(l.split(',')) > 5]
            df = pd.DataFrame(data, columns=['RECORD_TYPE', 'SR_NO', 'SYMBOL', 'SERIES', 'TRADED_QTY', 'DELIV_QTY', 'DELIV_PER'])
            # Clean symbols and numeric data
            df['SYMBOL'] = df['SYMBOL'].str.strip()
            df['DELIV_PER'] = pd.to_numeric(df['DELIV_PER'], errors='coerce')
            return df
    except Exception as e:
        print(f"❌ NSE Delivery Download Error: {e}")
    return None

def download_bse_sme_bhavcopy(target_date):
    """
    Downloads BSE SME Bhavcopy (Section 1B).
    Critical for early detection of multibagger setups.
    """
    date_str = target_date.strftime('%d%m%y')
    url = f"https://www.bseindia.com/download/BhavCopy/SME/SME{date_str}_CSV.ZIP"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        print(f"📡 Downloading BSE SME: {target_date.date()}...")
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                csv_name = z.namelist()[0]
                with z.open(csv_name) as f:
                    df = pd.read_csv(f)
                    df.columns = df.columns.str.strip()
                    mapping = {'TckrSymb': 'symbol', 'ClsPric': 'close', 'TtlTradgVol': 'volume', 'ISIN': 'isin'}
                    df.rename(columns=mapping, inplace=True)
                    df['exchange'] = 'BSE_SME'
                    df['date'] = target_date.strftime('%Y-%m-%d')
                    return df
    except Exception as e:
        print(f"⚠️ BSE SME Data missing for today: {e}")
    return None

def download_bse_delivery(target_date):
    """
    Section 1B: BSE Delivery Data
    URL: https://www.bseindia.com/markets/equity/EQReports/GrossDis_index_new.aspx
    Note: BSE delivery is often a daily text/csv file.
    """
    date_str = target_date.strftime('%d%m%y')
    # BSE provides a daily gross deliverable file
    url = f"https://www.bseindia.com/BseOnlineData/getGrossDeliVery_{date_str}.csv"
    
    try:
        print(f"📡 Downloading BSE Delivery: {target_date.date()}...")
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            # BSE format: SCRIP_CD|DELIV_QTY|DELIV_PCT|DATE
            df = pd.read_csv(io.StringIO(response.text), sep='|')
            df.columns = df.columns.str.strip()
            df.rename(columns={'SCRIP_CD': 'bse_code', 'DELIV_PER': 'delivery_pct'}, inplace=True)
            return df[['bse_code', 'delivery_pct']]
    except Exception as e:
        print(f"⚠️ BSE Delivery not yet uploaded: {e}")
    return None

def download_nse_sme_bhavcopy(target_date):
    """
    Section 1A: NSE SME (EMERGE) Bhavcopy
    Critical for early detection of multibaggers.
    """
    date_str = target_date.strftime('%d%m%Y')
    # NSE SME Bhavcopy usually sits in a different directory or uses a 'SME' flag
    url = f"https://nsearchives.nseindia.com/content/sme/BhavCopy_NSE_SME_0_0_0_{date_str}_F_0000.csv.zip"
    
    try:
        print(f"📡 Downloading NSE SME: {target_date.date()}...")
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                csv_name = z.namelist()[0]
                with z.open(csv_name) as f:
                    df = pd.read_csv(f)
                    df.columns = df.columns.str.strip()
                    mapping = {'TckrSymb': 'symbol', 'ClsPric': 'close', 'TtlTradgVol': 'volume', 'ISIN': 'isin'}
                    df.rename(columns=mapping, inplace=True)
                    df['exchange'] = 'NSE_SME'
                    df['date'] = target_date.strftime('%Y-%m-%d')
                    return df
    except Exception as e:
        print(f"⚠️ NSE SME Data missing for today: {e}")
    return None

def download_nse_fo_participant_data(target_date):
    """Section 1A: NSE F&O Participant (COT) Report"""
    date_str = target_date.strftime('%d%m%Y')
    url = f"https://archives.nseindia.com/content/nsccl/fao_participant_vol_{date_str}.csv"
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return pd.read_csv(io.StringIO(response.text))
    except Exception:
        return None