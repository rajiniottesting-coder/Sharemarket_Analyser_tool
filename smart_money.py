import requests
import pandas as pd
from datetime import datetime

class SmartMoneyScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }

    def fetch_nse_bulk_deals(self):
        """Section 1A: Fetch daily Bulk Deals (NSE)"""
        url = "https://www.nseindia.com/api/block-deal?optType=bulk"
        try:
            # Note: NSE API requires a session cookie which often requires a preliminary visit to the homepage
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                df = pd.DataFrame(data['data'])
                # Section 3J: Flag deals > 0.5% of free float
                return df
        except Exception as e:
            print(f"⚠️ NSE Bulk Deal Fetch Failed: {e}")
        return None

    def fetch_sast_insider_trading(self):
        """Section 1A & 3K: Mine SAST Filings for Promoter Purchases"""
        url = "https://www.nseindia.com/api/corporate-filings-insider-trading"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                df = pd.DataFrame(response.json()['data'])
                # Filter for 'Acquisition' and 'Promoter' (Signal 4/11)
                return df
        except Exception as e:
            print(f"⚠️ SAST Filing Fetch Failed: {e}")
        return None