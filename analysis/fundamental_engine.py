import math
import numpy as np

class FundamentalEngine:
    @staticmethod
    def calculate_graham_number(eps, bvps):
        """Section 3A: Graham Number = sqrt(22.5 * EPS * BVPS)"""
        if eps <= 0 or bvps <= 0: return 0
        return round(math.sqrt(22.5 * eps * bvps), 2)

    @staticmethod
    def calculate_peg_ratio(pe, growth_rate):
        """Section 3A: PEG Ratio (Growth capped at 30% per V7 Rules)"""
        adj_growth = min(growth_rate, 30)
        if adj_growth <= 0: return 99.9 # Avoid division by zero
        return round(pe / adj_growth, 2)

    @staticmethod
    def calculate_piotroski_f_score(data):
        """
        Section 3G: Piotroski F-Score (0-9).
        Canonical 9 criteria, grouped into Profitability / Leverage-Liquidity / Efficiency.
        Each criterion scores +1 when the condition is met, else 0.
        Returns an integer in [0, 9].

        Missing / zero inputs fail their test silently (score does not advance),
        so with free data the realistic ceiling is ~6-7; full 9 requires YoY
        comparisons that need at least two reporting periods of data.
        """
        score = 0

        # ── 1. Profitability (4 criteria) ───────────────────────────────────
        # P1: Net income positive
        if data.get('net_profit', 0) > 0: score += 1
        # P2: ROA positive
        if data.get('roa', 0) > 0: score += 1
        # P3: CFO positive
        if data.get('cfo', 0) > 0: score += 1
        # P4: Accrual quality — CFO should exceed Net Income (earnings backed by cash)
        if data.get('cfo', 0) > data.get('net_profit', 0): score += 1

        # ── 2. Leverage / Liquidity / Source of Funds (3 criteria) ──────────
        # L1: Long-term debt decreased YoY (lower leverage)
        #     Accepts either explicit 'lt_debt_now/prev' or generic 'debt_current/prev'
        _dt_now  = data.get('lt_debt_now',  data.get('debt_current', 0))
        _dt_prev = data.get('lt_debt_prev', data.get('debt_prev',    0))
        if _dt_prev > 0 and _dt_now < _dt_prev: score += 1
        # L2: Current ratio improved YoY (better short-term liquidity)
        if data.get('current_ratio_now', 0) > data.get('current_ratio_prev', 0): score += 1
        # L3: No new shares issued (share count not increased)
        _sh_now  = data.get('shares_now',  0)
        _sh_prev = data.get('shares_prev', 0)
        # Tolerate 0.5% drift (buybacks/splits); fail only on real dilution
        if _sh_prev > 0 and _sh_now <= _sh_prev * 1.005: score += 1

        # ── 3. Operating Efficiency (2 criteria) ────────────────────────────
        # E1: Gross margin improved YoY
        if data.get('gross_margin_now', 0) > data.get('gross_margin_prev', 0): score += 1
        # E2: Asset turnover improved YoY
        if data.get('asset_turnover_now', 0) > data.get('asset_turnover_prev', 0): score += 1

        return score

    @staticmethod
    def calculate_altman_z(data):
        """Section 3G: Altman Z-Score for Financial Distress"""
        # Formula: 1.2A + 1.4B + 3.3C + 0.6D + 1.0E
        a = data.get('working_capital', 0) / data.get('total_assets', 1)
        b = data.get('retained_earnings', 0) / data.get('total_assets', 1)
        c = data.get('ebit', 0) / data.get('total_assets', 1)
        d = data.get('mcap', 0) / data.get('total_liabilities', 1)
        e = data.get('sales', 0) / data.get('total_assets', 1)
        return round((1.2*a + 1.4*b + 3.3*c + 0.6*d + 1.0*e), 2)
    
    @staticmethod
    def calculate_earnings_yield(eps, cmp):
        """
        Section 3A: Earnings Yield = EPS / CMP * 100
        Benchmark: > 6% (Beats 10Y Gsec) -> ATTRACTIVE
        """
        if cmp <= 0: return 0
        yield_val = (eps / cmp) * 100
        status = "ATTRACTIVE" if yield_val > 6 else ("MODERATE" if yield_val >= 4 else "LOW")
        return {"yield": round(yield_val, 2), "status": status}

    @staticmethod
    def calculate_order_book_to_bill(order_book, annual_revenue):
        """
        Section 3C: Order Book-to-Bill Ratio
        > 2.0x -> EXCEPTIONAL | < 1.0x -> DEMAND CONCERN
        """
        if annual_revenue <= 0: return 0
        ratio = order_book / annual_revenue
        
        tag = "OB/BILL 2x+" if ratio > 2.0 else ("OB/BILL STRONG" if ratio > 1.5 else None)
        status = "EXCEPTIONAL" if ratio > 2.0 else ("STRONG" if ratio > 1.5 else "LOW VISIBILITY")
        if ratio < 1.0: status = "DEMAND CONCERN"

        return {"ratio": round(ratio, 2), "tag": tag, "status": status}

    @staticmethod
    def calculate_fcf_yield(fcf, mcap):
        """Section 3D: FCF Yield: > 5% green | 3-5% good | < 2% flag"""
        if mcap <= 0: return 0
        yield_val = (fcf / mcap) * 100
        return round(yield_val, 2)
    
    def calculate_composite_fair_value(self, symbol, sector, models_data):
        """Section 5B: Weighted CFV by Sector"""
        weights = {
            "IT": {"DCF": 0.35, "PE": 0.30, "PEG": 0.20, "EV": 0.10, "G": 0.05},
            "Banks": {"PB": 0.35, "PE": 0.25, "DDM": 0.20, "DCF": 0.15, "EV": 0.05},
            "AI & Computing": {"PE": 0.35, "PEG": 0.30, "DCF": 0.25, "G": 0.10} # Special V7 Rule
        }
        
        sector_weights = weights.get(sector, weights["IT"]) # Default to IT if unknown
        cfv = 0
        for model, weight in sector_weights.items():
            cfv += models_data.get(model, 0) * weight
            
        return round(cfv, 2)