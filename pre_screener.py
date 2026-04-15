import pandas as pd
from forensics_engine import ForensicsEngine

def stage_1_filter(all_stocks):
    """
    Input: A list of stock dictionaries from today's Bhavcopy
    Output: ~400-600 candidates (Section 0A)
    """
    candidates = []
    
    for stock in all_stocks:
        # V4: CMP >= ₹2 (Exclude penny stocks) 
        if stock.get('CLOSE', 0) < 2:
            continue
            
        # V3: Delivery % >= 40% (Conviction check) 
        if stock.get('DELIVERY_PCT', 0) < 40:
            continue
            
        # V5: Market Cap >= ₹50Cr 
        if stock.get('MCAP', 0) < 50:
            continue
            
        # If it passes all checks, it's a candidate!
        candidates.append(stock)
        
    print(f"Stage 1 Complete: Filtered {len(all_stocks)} down to {len(candidates)}")
    return candidates



def stage_2_fundamental_scorer(df):
    """
    Implements Section 0B: 6 binary criteria (5 pts each)
    Input: Stage 1 candidates | Output: Top 150-200 qualified symbols
    """
    qualified_stocks = []

    for index, row in df.iterrows():
        score = 0
        
        # F1: PAT positive (Basic check - assumes 'PAT' column exists in your DB)
        # Note: If data is missing, we give benefit of doubt or 0 based on Section 3G
        if row.get('NET_PROFIT', 0) > 0: score += 5
        
        # F2: Revenue YoY growth > 0%
        if row.get('REV_GROWTH', 0) > 0: score += 5
        
        # F3: Debt/Equity < 1.5
        if row.get('DEBT_EQUITY', 2) < 1.5: score += 5
        
        # F4: Promoter holding > 25%
        if row.get('PROMOTER_HOLDING', 0) > 25: score += 5
        
        # F5: P/E < 80 or N/A
        pe = row.get('PE', 0)
        if pe < 80 or pe == 0: score += 5
        
        # F6: No Fraud/SEBI flags (Defaulting to 5 for now)
        score += 5

        # REJECTION RULE (Section 0B): Drop if score < 10
        if score >= 10:
            row_dict = row.to_dict()
            row_dict['stage2_score'] = score
            qualified_stocks.append(row_dict)

    print(f"✅ Stage 2 Complete: {len(qualified_stocks)} stocks qualified for Priority Ranking.")
    return pd.DataFrame(qualified_stocks)

def apply_anti_trigger_guard(symbol, volume_spike_detected, forensics_data):
    """
    Section 3H: Anti-Trigger Guard
    Prevents buying 'Value Traps' or 'Operator Pumps'.
    """
    z_score = ForensicsEngine.calculate_altman_z(forensics_data)
    m_status = ForensicsEngine.calculate_beneish_m(forensics_data)
    
    # RULE: If Z-score is in 'Distress' (<1.81), ignore any Volume Spike
    if z_score < 1.81:
        return "GUARD_REJECT: FINANCIAL_DISTRESS"
    
    # RULE: If M-Score suggests manipulation, ignore signal
    if m_status == "MANIPULATION_RISK":
        return "GUARD_REJECT: PROFIT_MANIPULATION"
        
    return "GUARD_PASS"