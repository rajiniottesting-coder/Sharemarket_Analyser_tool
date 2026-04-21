import math


def _sf(val, default=0.0):
    if val is None or val == "" or str(val) in ("—", "--", "N/A"):
        return float(default)
    try:
        return float(val)
    except (ValueError, TypeError):
        return float(default)


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
        
        # M1: DCF (3-Stage) — Stage 1 capped at 25%
        # Session 19 fix: two guards to prevent M1 from producing absurd
        # fair-value multiples (10×+ CMP) on low-beta stocks.
        #
        #   Guard 1 — WACC floor at 10%:
        #     With gsec=6.8% and equity-risk-premium=5.5%, a stock with
        #     beta=0.2 (like SBIN) gets WACC = 6.8 + 0.2×5.5 = 7.9%.
        #     Terminal growth is 4.5%, so the denominator (WACC-gt) becomes
        #     just 3.4%, and dividing terminal value by 3.4% produces
        #     mathematically correct but practically absurd fair values
        #     (SBIN came out at ₹12,126 vs CMP ₹1,108 = 10.9× CMP).
        #     Indian equity discount rates of less than 10% are unrealistic
        #     given a ~6.8% risk-free rate and the true equity risk premium
        #     in Indian markets (typically 6-8%, not 5.5%).
        #
        #   Guard 2 — cap final DCF at 4× CMP:
        #     Even with WACC floored, an EPS-based DCF with aggressive
        #     growth assumptions can produce fair values many multiples of
        #     price for outliers. A 4× cap means "deeply undervalued" but
        #     not absurd — anything beyond should skip this model rather
        #     than distort the composite.
        _raw_wacc = (self.gsec + beta * 5.5) / 100
        wacc = max(_raw_wacc, 0.10)             # floor WACC at 10%
        gt = 0.045
        cmp_m1 = _sf(data.get('close', 0))
        if eps > 0 and wacc > gt:               # WACC must exceed terminal growth rate
            g1 = min(growth_3yr / 100, 0.25)
            g2 = g1 / 2
            dcf = sum(eps * (1+g1)**y / (1+wacc)**y for y in range(1,6))
            dcf += sum(eps * (1+g1)**5 * (1+g2)**(y-5) / (1+wacc)**y for y in range(6,11))
            terminal = (eps * (1+g1)**5 * (1+g2)**5 * (1+gt)) / (wacc - gt) / (1+wacc)**10
            _m1 = dcf + terminal
            # Guard 2: cap at 4× CMP (or skip if we can't compare to price)
            if cmp_m1 > 0 and _m1 > cmp_m1 * 4:
                _m1 = cmp_m1 * 4
            models['M1_DCF'] = round(_m1, 2)
        else:
            models['M1_DCF'] = 0

        # M2: Graham Number (Skip if negative EPS)
        # M2: Graham Number — derive bvps from pb × close if not in data
        _bvps = bv
        if (not _bvps or _bvps == 0) and data.get('pb') and _sf(data.get('pb')) > 0:
            _close = _sf(data.get('close', 0))
            _pb    = _sf(data.get('pb', 0))
            if _close > 0 and _pb > 0:
                _bvps = round(_close / _pb, 2)
        models['M2_Graham'] = round(math.sqrt(22.5 * eps * _bvps), 2) if eps > 0 and _bvps > 0 else 0
        
        # M4: Price-to-Book Fair Value = BVPS × sector_median_PB
        _pb_v = _sf(data.get('pb'))
        # Session 15: the old check `'_bvps' in dir() and _bvps` was a no-op —
        # dir() always contained '_bvps' after the M2 block. Simplified to a
        # direct truthiness check with the fallback derivation preserved.
        if _bvps and _bvps > 0:
            _bvps4 = _bvps
        elif _pb_v > 0:
            _bvps4 = round(float(data.get('close', 0) or 0) / _pb_v, 2)
        else:
            _bvps4 = 0
        # Sector median PB benchmarks
        _sec_pb = {"Banks": 2.0, "IT": 6.0, "Pharma": 3.5, "FMCG": 8.0,
                   "Auto": 3.0, "Metals": 1.5, "Energy": 1.8}.get(
            str(data.get('sector','')).split()[0] if data.get('sector') else '', 3.0)
        models['M4_PB'] = round(_bvps4 * _sec_pb, 2) if _bvps4 > 0 else 0

        # M3: PE Mean Reversion — sector-appropriate benchmark PE
        _sec_parts = str(data.get('sector', '') or '').split()
        _sec_word = _sec_parts[0] if _sec_parts else ''
        _sec_pe_map = {
            "IT": 30, "Technology": 30, "Software": 28,
            "Banks": 18, "Banking": 18, "NBFC": 20, "Financial": 20,
            "Pharma": 30, "Healthcare": 28,
            "FMCG": 45, "Consumer": 40,
            "Auto": 25, "Automobiles": 25,
            "Metals": 12, "Steel": 10,
            "Energy": 15, "Oil": 12, "Power": 20,
            "Infra": 22, "Defence": 40,
            "Chemical": 28,
        }
        _fair_pe = _sec_pe_map.get(_sec_word, _sf(data.get('sector_pe_5yr', 0), 0) or 25)
        # Session 15: guard against negative EPS — negative fair value is nonsensical
        # and would distort composite FV; Graham / DCF already do this guard.
        models['M3_PE'] = round(eps * _fair_pe, 2) if eps > 0 else 0
        
        # M7: PEG-Adjusted (Growth capped at 30%)
        # Session 15: gate on positive EPS + positive growth; round for display
        # consistency with other models. Previously returned negative FV for
        # loss-making or declining-growth stocks (dimensionally wrong).
        adj_growth = min(growth_3yr, 30)
        if eps > 0 and adj_growth > 0:
            models['M7_PEG'] = round(eps * adj_growth, 2)
        else:
            models['M7_PEG'] = 0

        # M5: EV/EBITDA-based Fair Value
        # EV FV = EBITDA × sector_median_EV_multiple / shares_proxy
        # Use ps (P/S) and margins to derive: EV FV ≈ CMP × (sector_ev_mult / current_ev_ebitda)
        ev_ebitda_curr = _sf(data.get('ev_ebitda', 0))
        sector_ev_mult = {"IT": 20, "Banks": 12, "Pharma": 18, "FMCG": 30,
                          "Auto": 10, "Metals": 6, "Energy": 8}.get(
            str(data.get('sector','')).split()[0] if data.get('sector') else '', 15)
        cmp_m5 = _sf(data.get('close', 0))
        if ev_ebitda_curr > 0 and cmp_m5 > 0:
            models['M5_EV'] = round(cmp_m5 * sector_ev_mult / ev_ebitda_curr, 2)
        else:
            models['M5_EV'] = 0

        # M6: DDM (Dividend Discount Model) — Gordon Growth Model
        # FV = D1 / (r - g)  where D1 = DPS × (1+g)
        # Only valid for genuine dividend-paying stocks (0.1% < yield < 15%)
        # Yields > 15% indicate bad data (unit mismatch) — skip DDM
        div_yield_pct = _sf(data.get('div_yield', 0))
        if 0.1 < div_yield_pct < 15.0 and cmp_m5 > 0:
            dps        = cmp_m5 * div_yield_pct / 100   # annual DPS in ₹
            # Conservative growth: min(pat_yoy/2, GDP_nominal=10%) capped at 6%
            _pat_g = _sf(data.get('pat_yoy', 0), 0)
            div_growth = min(max(_pat_g / 200, 0.02), 0.06)
            req_return = (self.gsec + 4.5) / 100         # risk-free + equity premium
            if req_return > div_growth and dps > 0:
                d1 = dps * (1 + div_growth)
                models['M6_DDM'] = round(d1 / (req_return - div_growth), 2)
            else:
                models['M6_DDM'] = 0
        else:
            models['M6_DDM'] = 0

        return models

    def get_composite_fair_value(self, models, cmp):
        """
        Step 2 & 3: Composite weighting and Margin of Safety (MoS).
        Uses all available (non-zero) models with normalized base weights.
        """
        base_weights = {
            "M1_DCF":    0.30,
            "M2_Graham": 0.15,
            "M3_PE":     0.20,
            "M4_PB":     0.15,
            "M5_EV":     0.10,
            "M6_DDM":    0.05,
            "M7_PEG":    0.05,
        }

        # Include only non-zero models; normalize weights so they sum to 1
        available = {k: v for k, v in models.items()
                     if isinstance(v, (int, float)) and v > 0}
        total_w = sum(base_weights.get(k, 0.10) for k in available)

        if total_w > 0 and available:
            cfv = sum(v * base_weights.get(k, 0.10) for k, v in available.items()) / total_w
        elif available:
            cfv = sum(available.values()) / len(available)  # equal-weight fallback
        else:
            cfv = 0

        # Session 19 safety net: cap composite CFV at 3× CMP.
        # Even with M1 guarded, other models can occasionally spike (e.g., a
        # Graham number from a high-EPS + high-BVPS stock, or an EV/EBITDA
        # output when the sector multiple is far above current). A 3× cap
        # corresponds to 200% MoS — already the extreme edge of plausible
        # value. Anything beyond should be treated as a data-quality signal,
        # not a buy signal. This is belt-and-suspenders with the M1 4× cap.
        if cmp > 0 and cfv > cmp * 3:
            cfv = cmp * 3

        cfv = round(cfv, 2)

        # Step 3: Margin of Safety (MoS %)
        # MoS = how much cheaper CMP is vs fair value (as % of CMP)
        # Positive = stock is below fair value (good), Negative = above (overvalued)
        mos = round(((cfv - cmp) / cmp * 100), 2) if cmp > 0 else 0

        # Step 4: CFV Score Adjustment (based on corrected MoS %)
        # MoS > 30% = meaningful undervaluation → strong BUY signal bonus
        # MoS < -20% = overvalued → penalise score
        score_adj = 0
        if   mos > 40:         score_adj = 12   # deeply undervalued
        elif mos > 25:         score_adj = 8    # significantly undervalued
        elif mos > 10:         score_adj = 4    # mildly undervalued
        elif mos < -30:        score_adj = -10  # significantly overvalued
        elif mos < -15:        score_adj = -5   # mildly overvalued

        # Upside — floor at -100% to prevent absurd display values
        upside = round(((cfv - cmp) / cmp * 100), 2) if cmp > 0 else -100
        upside = max(upside, -100)

        # MoS label
        if   mos > 40:  mos_lbl = "EXCEPTIONAL VALUE"
        elif mos > 25:  mos_lbl = "STRONG VALUE"
        elif mos > 10:  mos_lbl = "GOOD VALUE"
        elif mos > 0:   mos_lbl = "FAIR VALUE"
        elif mos > -15: mos_lbl = "SLIGHT PREMIUM"
        elif mos > -30: mos_lbl = "OVERVALUED"
        else:           mos_lbl = "SIGNIFICANTLY OVERVALUED"

        return {
            "cfv":              cfv,
            "cfv_low":          round(cfv * 0.85, 2) if cfv > 0 else 0,
            "cfv_high":         round(cfv * 1.15, 2) if cfv > 0 else 0,
            "mos_label":        mos_lbl,
            "mos_pct":          mos,
            "score_adjustment": score_adj,
            "upside":           upside,
        }