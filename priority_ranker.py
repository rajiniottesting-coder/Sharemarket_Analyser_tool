import pandas as pd
from data_bridge import get_20d_avg_vol

def calculate_priority_score(row):
    """
    Implements Section 0C: Priority Score Formula
    """
    # 1. REAL Volume Spike Ratio (Section 0C)
    current_vol = row.get('volume', 0)
    avg_vol = get_20d_avg_vol(row.get('symbol')) 
    
    if avg_vol > 0:
        vol_spike_ratio = current_vol / avg_vol
    else:
        vol_spike_ratio = 1.0 # Default if no history exists yet
        
    vol_spike = min(vol_spike_ratio, 10) / 10 
    
    # 2. Stage 2 Score (0-30)
    s2_score = (row.get('stage2_score', 0) / 30)
    
    # 3. Delivery %
    deliv = (row.get('delivery_pct', 0) / 100)
    
    # 4. Recency Bonus
    recency = 1.0
    
    # Final Formula from V7 Prompt
    priority = (vol_spike * 40) + (s2_score * 25) + (deliv * 20) + (recency * 15)
    return round(priority, 2)

def get_top_100_candidates(df):
    """
    Sorts and caps the list to exactly 100 symbols (Section 0C)
    """
    if df.empty:
        return df

    # Calculate scores for all
    df['priority_score'] = df.apply(calculate_priority_score, axis=1)
    
    # Sort by Priority Score DESC (Section 0C)
    df = df.sort_values(by='priority_score', ascending=False)
    
    # CAP MANAGEMENT: Take top 100 
    top_100 = df.head(100)
    
    print(f"✅ Stage 3 Complete: Selected exactly {len(top_100)} stocks for Gemini Analysis.")
    return top_100