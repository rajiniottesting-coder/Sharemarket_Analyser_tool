import pandas as pd
import sqlite3
import requests
import zipfile
import io
import os
import tempfile
from datetime import datetime
import pytz
from bse import BSE 


IST = pytz.timezone('Asia/Kolkata')
NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://www.nseindia.com/'
}

def get_nse_session():
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
    return session

def download_nse_bhavcopy(target_date):
    ds = target_date.strftime("%Y%m%d")
    url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ds}_F_0000.csv.zip"
    try:
        s = get_nse_session()
        r = s.get(url, headers=NSE_HEADERS, timeout=15)
        if r.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                return pd.read_csv(z.open(z.namelist()[0]))
    except: return None

def download_bse_bhavcopy(target_date):
    tmp_dir = tempfile.mkdtemp()
    try:
        with BSE(download_folder=tmp_dir) as bse_client:
            dt_combined = datetime.combine(target_date, datetime.min.time())
            file_path = bse_client.bhavcopyReport(date=dt_combined, folder=tmp_dir)
            if file_path and os.path.exists(file_path):
                return pd.read_csv(file_path)
    except: return None

def download_nse_delivery(target_date):
    ds = target_date.strftime("%d%m%Y")
    url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ds}.csv"
    try:
        r = get_nse_session().get(url, headers=NSE_HEADERS, timeout=15)
        return pd.read_csv(io.StringIO(r.text)) if r.status_code == 200 else None
    except: return None

def download_bse_delivery(target_date):
    ds = target_date.strftime("%d%m%y")
    url = f"https://www.bseindia.com/BhavCopy/Gross_Deliverable_{ds}.zip"
    try:
        r = requests.get(url, headers=NSE_HEADERS, timeout=15)
        if r.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                return pd.read_csv(z.open(z.namelist()[0]))
    except: return None

def download_nse_sme_bhavcopy(target_date):
    ds = target_date.strftime("%d%m%y").upper()
    url = f"https://nsearchives.nseindia.com/archives/sme/bhavcopy/sme{ds}.csv"
    try:
        r = get_nse_session().get(url, headers=NSE_HEADERS, timeout=15)
        return pd.read_csv(io.StringIO(r.text)) if r.status_code == 200 else None
    except: return None

def download_bse_sme_bhavcopy(target_date):
    df = download_bse_bhavcopy(target_date)
    return df[df['SCRIP_GRP'].isin(['M', 'MT'])] if df is not None and 'SCRIP_GRP' in df.columns else df

def download_nse_fo_participant_data(target_date):
    ds = target_date.strftime("%d%m%Y")
    url = f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_vol_{ds}.csv"
    try:
        r = get_nse_session().get(url, headers=NSE_HEADERS, timeout=15)
        return pd.read_csv(io.StringIO(r.text), skiprows=1) if r.status_code == 200 else None
    except: 
        return None
    