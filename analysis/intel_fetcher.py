import datetime

def fetch_latest_intelligence(symbol, raw_sector):
    """
    Implements Section 2 & 3I: Dynamic Catalyst Search.
    Ensures targeted searches for high-alpha sectors.
    """
    current_year = datetime.datetime.now().year
    
    # Normalizing sector names for dictionary lookup
    sector_map = {
        "AI": "AI & Computing",
        "Semiconductor": "Semiconductor",
        "Minerals": "Critical Minerals",
        "Renewable": "Renewable Energy",
        "Defence": "Defence",
        "Pharma": "Pharma"
    }
    
    # Logic to find the key
    target_key = "Default"
    for key, val in sector_map.items():
        if key.lower() in str(raw_sector).lower():
            target_key = val
            break

    queries = {
        "AI & Computing": [
            f"{symbol} Sovereign AI GPU cluster cloud deal {current_year}",
            f"{symbol} generative AI enterprise deployment contract"
        ],
        "Semiconductor": [
            f"{symbol} FAB approval ATMP incentive win {current_year}",
            f"{symbol} semiconductor assembly testing contract India"
        ],
        "Renewable Energy": [
            f"{symbol} green hydrogen electrolyzer tender {current_year}",
            f"{symbol} solar PLI tranche win"
        ],
        "Defence": [
            f"{symbol} L1 lowest bidder MoD contract {current_year}",
            f"{symbol} export order defence technology"
        ],
        "Pharma": [
            f"{symbol} USFDA EIR inspection report {current_year}",
            f"{symbol} drug launch generic specialty approvals"
        ]
    }
    
    return queries.get(target_key, [f"latest corporate announcement {symbol} {current_year} catalyst"])