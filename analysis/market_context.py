import requests
import pandas as pd
from datetime import datetime

class MarketContextPoller:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }

    def fetch_nse_indices(self):
        """Section 1A: Fetch Nifty 50 and Sectoral Indices"""
        url = "https://www.nseindia.com/api/allIndices"
        try:
            # Note: NSE API requires active session cookies
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                data = response.json()['data']
                df = pd.DataFrame(data)
                # Filter for mandatory V7 indices: Bank, IT, Pharma, Auto, etc.
                return df[['indexSymbol', 'last', 'percentChange']]
        except Exception as e:
            print(f"⚠️ NSE Index Fetch Failed: {e}")
        return None

    def poll_bse_announcements(self):
        """Section 1B & 2c: Poll for Corporate Actions every 30 mins"""
        # Targets: Dividends, Bonus, Splits, Buybacks
        url = "https://api.bseindia.com/BseOnlineData/api/AnnSubCategory/w"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                ann = response.json()
                # Flagging critical triggers for Section 8 Cards
                return pd.DataFrame(ann)
        except Exception as e:
            print(f"⚠️ BSE Announcement Polling Failed: {e}")
        return None
    
    def process_announcements(self, ann_df):
        """Section 2c & 11: Filters for Price Sensitive Actions"""
        if ann_df is None or ann_df.empty: return []
        
        # Mandatory V7 Keywords
        critical = ['L1', 'LOWEST BIDDER', 'SPLIT', 'BONUS', 'BUYBACK', 'USFDA', 'PLI']
        pattern = '|'.join(critical)
        
        # Filter the 'NEWSSUB' column from BSE/NSE
        return ann_df[ann_df['NEWSSUB'].str.upper().str.contains(pattern, na=False)]