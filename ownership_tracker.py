# ownership_tracker.py
# ────────────────────────────────────────────────────────────────────────
# SECTION 3F: SHAREHOLDING PATTERN (TRENDS)
# SECTION 3K: PROMOTER & INSIDER INTELLIGENCE
# ────────────────────────────────────────────────────────────────────────

def analyze_ownership_trends(current_row, hist_q1=None, hist_q2=None):
    results = {}

    # --- SUBSECTION 3F: SHAREHOLDING TRENDS (3Q Direction) ---
    # Logic: Direction matters more than level (Section 3F)
    curr_fii = current_row.get('fii_holding', 0)
    prev_fii = hist_q1.get('fii_holding', 0) if hist_q1 else curr_fii
    prev_fii_2 = hist_q2.get('fii_holding', 0) if hist_q2 else prev_fii

    # Flag 3-quarter rising streak
    results['fii_3q_trend'] = "UP" if curr_fii > prev_fii > prev_fii_2 else "NEUTRAL"
    
    # --- SUBSECTION 3K: PLEDGE INTELLIGENCE (Direction) ---
    # Logic: Is pledge % rising or falling? (Section 3K)
    curr_pledge = current_row.get('pledge_pct', 0)
    prev_pledge = hist_q1.get('pledge_pct', 0) if hist_q1 else curr_pledge
    
    if curr_pledge < prev_pledge:
        results['pledge_signal'] = "PLEDGE FALLING (Green Tag)"
    elif curr_pledge > prev_pledge:
        results['pledge_signal'] = "PLEDGE RISING (Red Tag)"
    else:
        results['pledge_signal'] = "STABLE"

    # --- SUBSECTION 3K: INSIDER CONVICTION ---
    # Logic: Key management buying during market weakness (Section 3K)
    results['insider_conviction'] = current_row.get('insider_buy_qty', 0) > 0
    
    return results