import pandas as pd

class ScoringEngine:
    """
    Three-verdict system with cap-category-adjusted thresholds.

    Only three verdicts are shown in the main Excel verdict column:
        BUY       — strong signal, worth acting on
        WATCHLIST — emerging signal, monitor closely
        NEUTRAL   — no clear signal (+ AVOID for very weak stocks)

    Thresholds differ by cap category because:
        LARGE CAP  → lower score needed (steadier, less volatile, lower risk)
        MID CAP    → moderate threshold
        SMALL CAP  → higher threshold (more volatile, needs stronger signal)
        MICRO CAP  → highest threshold (maximum risk, must be convincing)

    The supplementary 'label' field (from _assign_quick_pick) still carries
    DEEP VALUE, EARLY MOVER, etc. for the Gold sheet and quick-pick logic.
    """

    # Cap-tier thresholds: {cap_tier: (BUY_min, WATCHLIST_min)}
    # Any score below WATCHLIST_min → NEUTRAL  (< lowest 15% → AVOID)
    CAP_THRESHOLDS = {
        "LARGE": (60, 50),   # Lower bar — large caps are inherently safer
        "MID":   (63, 53),   # Moderate bar
        "SMALL": (66, 56),   # Higher bar — more volatility, needs conviction
        "MICRO": (70, 60),   # Highest bar — only the clearest signals qualify
    }
    AVOID_BELOW = 38         # Universal floor — below this = AVOID regardless of cap

    def __init__(self):
        pass  # Thresholds defined as class constants above

    def calculate_composite_score(self, data):
        """
        Implements Section 6: Weighted Composite Scoring (0-100).
        """
        # A. Base Weighted Scores
        f_score = data.get('fundamental_score', 50) * 0.35   # Sections 3A-3G + 4 
        t_score = data.get('technical_score', 50) * 0.30     # Section 5 
        e_score = data.get('early_entry_score', 0) * 0.15    # Section 3I 
        s_score = data.get('sentiment_score', 50) * 0.10     # Section 2 
        r_score = data.get('safety_score', 50) * 0.10        # Pledge, Debt, Beneish 
        
        base_score = f_score + t_score + e_score + s_score + r_score
        
        # B. Adjustments & Bonuses
        # MoS Adjustment from Section 5B Step 4 
        final_score = base_score + data.get('mos_adjustment', 0)
        
        # Spike Bonus: +2 per trigger (cap +10) 
        spike_bonus = min(data.get('spike_count', 0) * 2, 10) 
        final_score += spike_bonus
        
        # Early Mover Bonus (Section 6) 
        if data.get('early_entry_score', 0) >= 70:
            final_score += 5 
            
        # Anti-trigger Penalty (Section 6) 
        if data.get('risk_flag_active', False):
            final_score -= 10
            
        final_score = max(0, min(100, final_score)) # Clamp 0-100
        
        cap_cat = str(data.get("cap_category", "") or "")
        return {
            "composite_score": round(final_score, 2),
            "verdict": self._get_verdict(final_score, cap_cat),
            "label": self._assign_quick_pick(data, final_score)
        }

    def calculate_storm_score(self, data, market_vix, market_off_peak):
        """
        Implements Section 7: Volatile Market Filter (MANDATORY if VIX > 18).
        """
        if market_vix <= 18 and market_off_peak <= 5:
            return None # Not mandatory in stable markets 
            
        score = 0
        # Scoring Logic (Section 7)
        if data.get('beta', 1.0) < 0.8: score += 2 
        if data.get('debt_equity', 1.0) < 0.3: score += 2 
        if data.get('fcf_positive_4q', False): score += 2 
        if data.get('promoter_q_increase', False): score += 1
        if data.get('div_yield', 0) > 2.0: score += 1 
        if data.get('fii_buy_3q', False): score += 1 
        if data.get('rev_growth_yoy', 0) > 10.0: score += 1 
        
        # Labels 
        label = "HIGH RISK"
        if score >= 8: label = "STORM SAFE"
        elif score >= 5: label = "MODERATE"
        
        return {"storm_score": score, "storm_label": label}

    def _get_verdict(self, score, cap_category=""):
        """
        Returns one of: BUY, WATCHLIST, NEUTRAL, AVOID.
        Thresholds vary by cap category — large caps qualify with lower scores
        because they carry lower inherent risk.
        """
        # Universal floor — any stock below this is AVOID regardless of cap
        if score < self.AVOID_BELOW:
            return "AVOID"

        # Determine cap tier from cap_category string
        cap_up = str(cap_category).upper()
        if   "LARGE" in cap_up: tier = "LARGE"
        elif "MID"   in cap_up: tier = "MID"
        elif "SMALL" in cap_up: tier = "SMALL"
        else:                    tier = "MICRO"  # MICRO CAP or unknown

        buy_min, watch_min = self.CAP_THRESHOLDS[tier]

        if   score >= buy_min:   return "BUY"
        elif score >= watch_min: return "WATCHLIST"
        else:                    return "NEUTRAL"

    def _assign_quick_pick(self, data, score):
        """Implements Section 6 Quick-Pick Labels"""
        mos = data.get('mos_pct', 0)
        early = data.get('early_entry_score', 0)
        
        if mos > 25 and score > 70 and early >= 60:
            return "DEEP VALUE EARLY MOVER"
        if mos > 25 and score > 70:
            return "DEEP VALUE" 
        if early >= 70 and score > 55:
            return "EARLY MOVER"
        if score < 38 or (score < 45 and mos < -30):
            return "AVOID / EXIT"
        return "WATCHLIST"