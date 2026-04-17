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
        # M1: Simplified 3-stage DCF
        # Stage 1 (yrs 1-5): growth_3yr, Stage 2 (6-10): half, Terminal: 4.5%
        if eps > 0 and wacc > 0.04:
            g1 = min(growth_3yr / 100, 0.25)
            g2 = g1 / 2
            gt = 0.045
            dcf = sum(eps * (1+g1)**y / (1+wacc)**y for y in range(1,6))
            dcf += sum(eps * (1+g1)**5 * (1+g2)**(y-5) / (1+wacc)**y for y in range(6,11))
            terminal = (eps * (1+g1)**5 * (1+g2)**5 * (1+gt)) / (wacc - gt) / (1+wacc)**10
            models['M1_DCF'] = round(dcf + terminal, 2)
        else:
            models['M1_DCF'] = 0

        # M2: Graham Number (Skip if negative EPS)
        # M2: Graham Number — derive bvps from pb × close if not in data
        _bvps = bv
        if (not _bvps or _bvps == 0) and data.get('pb') and float(data.get('pb') or 0) > 0:
            _close = float(data.get('close', 0) or 0)
            _pb    = float(data.get('pb', 0) or 0)
            if _close > 0 and _pb > 0:
                _bvps = round(_close / _pb, 2)
        models['M2_Graham'] = round(math.sqrt(22.5 * eps * _bvps), 2) if eps > 0 and _bvps > 0 else 0
        
        # M4: Price-to-Book Fair Value = BVPS × sector_median_PB
        _pb_v = float(data.get('pb') or 0)
        _bvps4 = _bvps if '_bvps' in dir() and _bvps else (
            round(float(data.get('close',0) or 0) / _pb_v, 2) if _pb_v > 0 else 0
        )
        # Sector median PB benchmarks
        _sec_pb = {"Banks": 2.0, "IT": 6.0, "Pharma": 3.5, "FMCG": 8.0,
                   "Auto": 3.0, "Metals": 1.5, "Energy": 1.8}.get(
            str(data.get('sector','')).split()[0] if data.get('sector') else '', 3.0)
        models['M4_PB'] = round(_bvps4 * _sec_pb, 2) if _bvps4 > 0 else 0

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