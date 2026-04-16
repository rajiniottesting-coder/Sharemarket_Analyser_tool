import os
import datetime
import pytz
import requests
from dotenv import load_dotenv

# 1. Load your secure API Key
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

def gate_check():
    """
    SECTION 12B: Institutional Gatekeeper.
    Standardized to return a Dictionary to prevent 'TypeError: bool not subscriptable'.
    """
    try:
        # Set time to IST (India Standard Time)
        ist = pytz.timezone('Asia/Kolkata')
        today = datetime.datetime.now(ist)- datetime.timedelta(days=1)  # 2. Logic Change: 'today' is now current time minus 1 day
        today_str = today.strftime('%Y-%m-%d')
        
        print(f"--- Running Gate Check for {today_str} IST ---")

        # SECTION 12C: 2026 Holiday Calendar (Master Prompt v7)
        HOLIDAYS_2026 = {
            "2026-01-26": "Republic Day",
            "2026-03-14": "Holi",
            "2026-04-06": "Ram Navami",
            "2026-04-14": "Dr. Ambedkar Jayanti",
            "2026-05-01": "Maharashtra Day",
            "2026-11-05": "Diwali Balipratipada",
            "2026-12-25": "Christmas Day"
        }

        # C1: Weekend Check
        if today.weekday() in [5, 6]:
            return {"run": False, "reason": "SKIP: Market is closed on Weekends."}

        # C2: Holiday Check
        if today_str in HOLIDAYS_2026:
            return {"run": False, "reason": f"SKIP: Market Holiday ({HOLIDAYS_2026[today_str]})."}

        # C3: NSE Connectivity & File Availability
        # We use a session and headers to avoid being blocked by NSE
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
        
        # date_str = today.strftime('%d%m%Y')
        nse_url = f"https://www.nseindia.com/archives/equities/mto/MTO_{today_str}.DAT"
        
        try:
            # First hit the home page to get cookies
            session.get("https://www.nseindia.com", headers=headers, timeout=10)
            response = session.head(nse_url, headers=headers, timeout=10)
            
            if response.status_code != 200:
                # If the file isn't there yet, we don't crash, we just wait for the next cron run
                return {"run": False, "reason": "SKIP: NSE Data files not yet available (Status 404)."}
        except Exception as e:
            return {"run": False, "reason": f"ERROR: Connection to NSE failed: {str(e)}"}

        # C4: BSE Connectivity Check
        bse_fname = f"EQ{today_str}_CSV.ZIP"
        bse_url = f"https://www.bseindia.com/download/BhavCopy/Equity/{bse_fname}"
        
        try:
            bse_res = session.head(bse_url, headers=headers, timeout=10)
            if bse_res.status_code != 200:
                print("⚠️ BSE data not yet available. Proceeding in NSE-only mode.")
                # We still return True because we can run on NSE data alone
                return {"run": True, "reason": "NSE Available (BSE Missing)"}
        except:
            print("⚠️ BSE Connection failed. Continuing with NSE data only.")
            return {"run": True, "reason": "NSE Available (BSE Connection Fail)"}

        # FINAL APPROVAL
        return {"run": True, "reason": "Market Data Available"}

    except Exception as e:
        # FAILSAFE: Ensure we never return a bare Boolean
        return {"run": False, "reason": f"CRITICAL GATE ERROR: {str(e)}"}