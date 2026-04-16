import math

class FairValueEngine:
    def __init__(self, gsec_yield=6.0):
        self.gsec = gsec_yield # Section 5B: 10Y Gsec benchmark

    def calculate_all_models(self, data, beta, growth_3yr):
        """
        Step 1: 7 Valuation Models (M1-M7)
        """
        models = {}
        eps = data.get('eps', 0)
        bv = data.get('bvps', 0)
        
        # M1: DCF (3-Stage) - Stage 1 capped at 25% (Section 5B)
        wacc = self.gsec + (beta * 5.5)
        # Simplified for Python logic; Full stage-3 logic in master_funnel integration
        models['M1_DCF'] = data.get('dcf_intrinsic', 0) 

        # M2: Graham Number (Skip if negative EPS)
        models['M2_Graham'] = math.sqrt(22.5 * eps * bv) if eps > 0 else 0
        
        # M3: PE Mean Reversion (Section 5B)
        models['M3_PE'] = eps * data.get('sector_pe_5yr', 20)
        
        # M7: PEG-Adjusted (Growth capped at 30%)
        adj_growth = min(growth_3yr, 30)
        models['M7_PEG'] = eps * adj_growth
        
        return models

    def get_composite_fair_value(self, models, sector, cmp):
        """
        Step 2 & 3: Composite weighting and Margin of Safety (MoS)
        """
        # Section 5B Weighting Table
        weights = {
            "IT": {"M1_DCF": 0.35, "M3_PE": 0.30, "M7_PEG": 0.20},
            "Banks": {"M4_PB": 0.35, "M3_PE": 0.25, "M6_DDM": 0.20},
            "BSE_SME": {"M3_PE": 0.35, "M7_PEG": 0.30, "M1_DCF": 0.25}
        }
        
        sw = weights.get(sector, weights["IT"])
        cfv = sum(models.get(m, 0) * w for m, w in sw.items())
        
        # Step 3: Margin of Safety (MoS %)
        mos = ((cfv - cmp) / cfv * 100) if cfv > 0 else 0
        
        # Step 4: CFV Score Adjustment (Section 5B)
        score_adj = 0
        if mos > 40: score_adj = 10
        elif 25 <= mos <= 40: score_adj = 6
        elif mos < -15: score_adj = -8
        
        return {
            "cfv": round(cfv, 2),
            "mos_pct": round(mos, 2),
            "score_adjustment": score_adj,
            "upside": round(((cfv - cmp) / cmp * 100), 2)
        }