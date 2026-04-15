import os
import datetime
import pytz
import requests
from dotenv import load_dotenv

# 1. Load your secure API Key
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

def gate_check():
    # Set time to IST (India Standard Time)
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.datetime.now(ist)
    today_str = today.strftime('%Y-%m-%d')
    
    print(f"--- Running Gate Check for {today.strftime('%Y-%m-%d %H:%M')} IST ---")

    # SECTION 12C: 2026 Holiday Calendar (Master Prompt v7)
    HOLIDAYS_2026 = {
        "2026-01-26": "Republic Day",
        "2026-03-14": "Holi",
        "2026-04-06": "Ram Navami",
        "2026-04-14": "Dr. Ambedkar Jayanti", # <--- TODAY!
        "2026-05-01": "Maharashtra Day",
        "2026-12-25": "Christmas Day"
    }

    # C1: Weekend Check (Section 12B)
    if today.weekday() in [5, 6]: # 5=Saturday, 6=Sunday
        print("SKIP: It's the weekend. No markets open.")
        return False

    # C2: Market Holiday Check (NEW)
    if today_str in HOLIDAYS_2026:
        print(f"SKIP: Today is a Market Holiday ({HOLIDAYS_2026[today_str]}).")
        return False
    # C3: NSE File Availability Check
    # We check if NSE has uploaded today's Bhavcopy yet
    date_str = today.strftime('%d%m%Y')
    nse_url = f"https://www1.nseindia.com/archives/equities/mto/MTO_{date_str}.DAT"
    
    try:
        response = requests.head(nse_url, timeout=5)
        if response.status_code != 200:
            print("SKIP: NSE data files are not yet available. Try again after 19:15 IST.")
            return False
    except:
        print("ERROR: Connection to NSE failed.")
        return False
    
    # C4: BSE Bhav Copy File Availability (Section 12B)
    bse_fname = f"EQ{today.strftime('%d%m%y')}_CSV.ZIP"
    bse_url = f"https://www.bseindia.com/download/BhavCopy/Equity/{bse_fname}"
    
    try:
        bse_res = requests.head(bse_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        bse_available = (bse_res.status_code == 200)
        if not bse_available:
            print("⚠️ BSE data not yet available. Proceeding in NSE-only mode.") 
            return False
    except:
        print("⚠️ BSE Connection failed. Continuing with NSE data only.")
        return False

    print("APPROVED: All Gate Checks passed. Starting Analysis Pipeline...")
    return True

if __name__ == "__main__":
    if gate_check():
        # This is where Pillar 2 (The Funnel) will start!
        pass