import pandas as pd
import math

class V7AnalysisEngine:
    def __init__(self):
        self.gsec_rate = 6.0  # 10Y Gsec benchmark (Section 3A)

    # ─── SECTION 3A: VALUATION RATIOS ────────────────────────────────
    def apply_section_3A_valuation(self, row):
        """
        Calculates Earnings Yield vs Gsec and P/E historical tags.
        """
        results = {}
        # Earnings Yield = EPS / CMP * 100
        cmp = row.get('close', 1)
        eps = row.get('eps', 0)
        yield_val = (eps / cmp) * 100
        results['earnings_yield'] = round(yield_val, 2)
        
        # Benchmarking: > 6% Beats 10Y Gsec (Section 3A)
        results['yield_tag'] = "ATTRACTIVE (Green)" if yield_val > self.gsec_rate else "LOW YIELD"
        
        # HIST. CHEAP Tag: Current P/E < Own 5yr Avg * 0.85
        pe = row.get('pe', 0)
        pe_5yr = row.get('pe_5yr_avg', 0)
        if pe > 0 and pe_5yr > 0 and pe < (pe_5yr * 0.85):
            results['valuation_tag'] = "HIST. CHEAP"
            
        return results

    # ─── SECTION 3C: GROWTH & ORDER BOOK ──────────────────────────────
    def apply_section_3C_growth(self, row):
        """
        Calculates Order Book-to-Bill Ratio and Pipeline Visibility.
        Applicable to: Infra, Defence, IT, Railways, AI, Semiconductors, 
        Critical Minerals, Renewable Energy, and Pharma (Section 3C).
        """
        # Get raw data from database fields
        ob = row.get('order_book', 0)
        rev = row.get('annual_revenue', 0)
        
        # Pipeline Visibility calculation: Unbilled Revenue / Annual Revenue (Section 3C)
        unbilled = row.get('unbilled_revenue', 0)
        pipeline_vis = round(unbilled / rev, 2) if rev > 0 else 0
        
        # Order Book-to-Bill Ratio (Section 3C primary metric)
        if rev <= 0:
            return {"ob_bill_ratio": 0, "pipeline_visibility": 0, "growth_tag": None}
            
        ratio = ob / rev
        
        # V7.0 Tagging Logic
        tag = None
        if ratio > 2.0: 
            tag = "OB/BILL 2x+ (EXCEPTIONAL)"
        elif ratio > 1.5: 
            tag = "OB/BILL STRONG"
        elif ratio < 1.0: 
            tag = "DEMAND CONCERN (Red Flag)"
            
        # Add Pipeline Visibility Tag (Section 3C)
        if pipeline_vis > 2.0:
            tag = f"{tag} | 2YR+ VISIBILITY" if tag else "2YR+ VISIBILITY"

        return {
            "ob_bill_ratio": round(ratio, 2), 
            "pipeline_visibility": pipeline_vis,
            "growth_tag": tag
        }

    # ─── SECTION 3H: VALUE SPIKE SCREENER (ANTI-TRIGGER) ─────────────
    def apply_section_3H_guards(self, row):
        """
        The Gatekeeper: Suppresses all spikes if quality is poor.
        Checks Pledge, Altman Z, and Beneish M (Section 3H).
        """
        is_suppressed = False
        reasons = []

        # Rule: Promoter_Pledge > 20%
        if row.get('pledge_pct', 0) > 20:
            is_suppressed = True
            reasons.append("Pledge > 20%")
            
        # Rule: Altman_Z < 1.81 (Financial Distress)
        if row.get('altman_z', 5) < 1.81:
            is_suppressed = True
            reasons.append("Altman Z < 1.81")
            
        # Rule: Beneish_M > -2.22 (Manipulation Risk)
        if row.get('beneish_m', -5) > -2.22:
            is_suppressed = True
            reasons.append("Beneish M > -2.22")

        return {"suppressed": is_suppressed, "reasons": reasons}

    # ─── SECTION 3I: EARLY DETECTION ENGINE ──────────────────────────
    def calculate_section_3I_early_score(self, row):
        """
        Computes the proprietary 0-100 Early Entry Score.
        Assigns "EARLY MOVER" gold badge if score >= 70 (Section 3I).
        """
        score = 0
        active_signals = []

        # Signal 2: SME-to-Mainboard Migration (8 pts)
        if row.get('exchange_tag') == 'BSE_SME' and row.get('mcap', 0) > 240:
            score += 8
            active_signals.append("SME MIGRATION WATCH")

        # Signal 4: Promoter Buying before results (9 pts)
        if row.get('insider_buying_30d') and row.get('days_to_results', 100) < 60:
            score += 9
            active_signals.append("PRE-RESULT PROMOTER BUY")

        # Signal 12: Cross-Exchange Discovery (10 pts)
        if row.get('exchange_tag') in ['BSE_ONLY', 'BSE_SME']:
            score += 10
            active_signals.append("CROSS-EXCHANGE DISCOVERY")

        # Labeling (Section 3I)
        label = "EMERGING"
        if score >= 80: label = "EARLY MOVER — Act before the crowd"
        elif score >= 60: label = "AHEAD OF CONSENSUS"

        return {
            "early_score": score,
            "label": label,
            "badge": "EARLY MOVER" if score >= 70 else None,
            "signals": active_signals
        }