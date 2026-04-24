# ownership_tracker.py
# ────────────────────────────────────────────────────────────────────────
# SECTION 3F: SHAREHOLDING PATTERN (TRENDS)
# SECTION 3K: PROMOTER & INSIDER INTELLIGENCE
# ────────────────────────────────────────────────────────────────────────

def analyze_ownership_trends(current_row, hist_q1=None, hist_q2=None):
    """
    Session 16: key-name fixes for cross-engine compatibility.
      • current_row stores FII holding under 'fii_pct' (not 'fii_holding')
      • current_row doesn't carry 'insider_buy_qty'; insider signal comes
        from 'insider_buy_alert' (set via SAST filings in master_funnel).
      • Retained old keys as fallbacks so any legacy caller keeps working.
    """
    results = {}

    # --- SUBSECTION 3F: SHAREHOLDING TRENDS (3Q Direction) ---
    # Prefer 'fii_pct' (master_funnel's canonical key); fall back to
    # 'fii_holding' for any legacy data source.
    curr_fii = current_row.get('fii_pct', current_row.get('fii_holding', 0)) or 0
    prev_fii = (hist_q1.get('fii_pct', hist_q1.get('fii_holding', 0))
                if hist_q1 else curr_fii) or 0
    prev_fii_2 = (hist_q2.get('fii_pct', hist_q2.get('fii_holding', 0))
                  if hist_q2 else prev_fii) or 0

    # Flag 3-quarter rising streak (strict monotonic)
    results['fii_3q_trend'] = "UP" if curr_fii > prev_fii > prev_fii_2 else "NEUTRAL"

    # --- SUBSECTION 3K: PLEDGE INTELLIGENCE (Direction) ---
    # pledge_pct key already matches master_funnel.
    # v10.15: pledge_pct may be "—" (honest unknown display). Coerce to 0.
    def _pledge_num(v):
        try:
            return float(str(v or 0).replace("—", "0") or 0)
        except (ValueError, TypeError):
            return 0.0
    curr_pledge = _pledge_num(current_row.get('pledge_pct'))
    prev_pledge = _pledge_num(hist_q1.get('pledge_pct') if hist_q1 else None)

    if curr_pledge < prev_pledge:
        results['pledge_signal'] = "PLEDGE FALLING (Green Tag)"
    elif curr_pledge > prev_pledge:
        results['pledge_signal'] = "PLEDGE RISING (Red Tag)"
    else:
        results['pledge_signal'] = "STABLE"

    # --- SUBSECTION 3K: INSIDER CONVICTION ---
    # Prefer 'insider_buy_alert' (YES/NO string from SAST filings).
    # Fall back to legacy 'insider_buy_qty' numeric field.
    _insider_alert = str(current_row.get('insider_buy_alert', 'NO') or 'NO').upper()
    _insider_qty   = current_row.get('insider_buy_qty', 0) or 0
    results['insider_conviction'] = (_insider_alert == 'YES') or (_insider_qty > 0)

    return results