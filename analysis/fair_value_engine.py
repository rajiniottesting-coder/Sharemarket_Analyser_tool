import math


def _sf(val, default=0.0):
    """Safe float conversion. Returns default for None, '', '—', '--', 'N/A',
    or anything that can't be cast to float."""
    if val is None or val == "" or str(val) in ("—", "--", "N/A"):
        return float(default)
    try:
        return float(val)
    except (ValueError, TypeError):
        return float(default)


def _resolve_sector_map(sector_str, sector_map, default):
    """Match a sector string against a benchmark map.

    The previous implementation used `sector.split()[0]` which only matched
    the first whitespace-separated word. That broke multi-word sectors like
    "Information Technology" (would only match 'Information', missing
    'IT'/'Technology' keys), "Iron & Steel" (matched 'Iron', missing 'Steel'),
    and many others.

    This helper does a case-insensitive substring search across the full
    sector string. The map is iterated in insertion order — Python dicts
    preserve insertion order from 3.7+ — so callers should put more
    specific keys (e.g., 'Software') before more generic ones ('IT') if
    a string could match multiple. Returns `default` if no key matches.
    """
    if not sector_str:
        return default
    s = str(sector_str).upper()
    for key, val in sector_map.items():
        if key.upper() in s:
            return val
    return default


class FairValueEngine:
    def __init__(self, gsec_yield=6.0):
        self.gsec = gsec_yield  # Section 5B: 10Y Gsec benchmark

    def calculate_all_models(self, data, beta, growth_3yr):
        """
        Step 1: 7 Valuation Models (M1-M7)

        v12.2 fixes:
          • eps / bvps now go through _sf() — previously these were the only
            data fetches that bypassed sanitization, making the engine crash
            when upstream sent '—' / 'N/A' / None for these fields.
          • M3 / M4 / M5 sector parsing replaced with substring matching so
            multi-word sectors like 'Information Technology' resolve correctly
            (was silently falling through to default values before).
          • M3 / M4 / M5 sector maps expanded to cover Realty, Telecom,
            Cement, Textiles, Media, Insurance, NBFC explicitly.
          • M6 DDM: removed the 2% growth floor (was systematically
            over-rewarding stocks with declining earnings); added a unit-clear
            growth formula matching the documented intent (pat_yoy / 2,
            capped at GDP nominal of 6%).
          • M7 PEG: made unit-safe with explicit guard for unexpectedly small
            growth values (catches the case where growth_3yr accidentally
            arrives as a decimal fraction instead of a percentage).
        """
        models = {}

        # v12.2 fix: route through _sf() so '—' / None / '' don't blow up
        # downstream comparisons like `eps > 0`.
        eps = _sf(data.get('eps', 0), 0)
        bv  = _sf(data.get('bvps', 0), 0)

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
            _bvps4 = round(_sf(data.get('close', 0)) / _pb_v, 2)
        else:
            _bvps4 = 0
        # v12.2: expanded sector PB benchmarks. Keys ordered specific → generic
        # so 'Software' matches before 'IT' would (substring search).
        _sec_pb_map = {
            "Banks": 2.0, "Banking": 2.0, "NBFC": 2.5,
            "Insurance": 2.5, "Financial": 2.0,
            "Software": 6.0, "Technology": 5.5, "IT": 6.0,
            "Pharma": 3.5, "Healthcare": 4.0,
            "FMCG": 8.0, "Consumer": 6.0,
            "Auto": 3.0, "Automobile": 3.0,
            "Steel": 1.3, "Metals": 1.5,
            "Oil": 1.5, "Energy": 1.8, "Power": 1.5,
            "Realty": 2.5, "Real Estate": 2.5,
            "Telecom": 2.5, "Cement": 3.0,
            "Textiles": 1.8, "Media": 2.5,
            "Chemical": 3.5, "Infra": 2.5, "Defence": 5.0,
        }
        _sec_pb = _resolve_sector_map(data.get('sector', ''), _sec_pb_map, 3.0)
        models['M4_PB'] = round(_bvps4 * _sec_pb, 2) if _bvps4 > 0 else 0

        # M3: PE Mean Reversion — sector-appropriate benchmark PE
        # v12.2: substring matching (not split-first-word) + expanded map.
        _sec_pe_map = {
            "Software": 28, "Technology": 30, "IT": 30,
            "Banks": 18, "Banking": 18, "NBFC": 20, "Insurance": 22,
            "Financial": 20,
            "Pharma": 30, "Healthcare": 28,
            "FMCG": 45, "Consumer": 40,
            "Auto": 25, "Automobile": 25,
            "Steel": 10, "Metals": 12,
            "Oil": 12, "Energy": 15, "Power": 20,
            "Realty": 25, "Real Estate": 25,
            "Telecom": 22, "Cement": 22,
            "Textiles": 15, "Media": 25,
            "Chemical": 28, "Infra": 22, "Defence": 40,
        }
        _fair_pe = _resolve_sector_map(
            data.get('sector', ''), _sec_pe_map,
            _sf(data.get('sector_pe_5yr', 0), 0) or 25,
        )
        # Session 15: guard against negative EPS — negative fair value is nonsensical
        # and would distort composite FV; Graham / DCF already do this guard.
        models['M3_PE'] = round(eps * _fair_pe, 2) if eps > 0 else 0

        # M7: PEG-Adjusted (Lynch's PEG=1 → fair PE = growth rate)
        # v12.2 clarification: this is mathematically equivalent to assuming
        # PEG = 1.0 (Lynch's rule of thumb). Fair PE = growth rate (in percent),
        # so fair_value = EPS × growth_pct.
        # Guard added against unit confusion: if growth_3yr arrives as a
        # decimal (0.15 instead of 15), we'd silently produce a 100× too small
        # FV. We treat any value < 1 as suspect and skip rather than mis-price.
        adj_growth = min(growth_3yr, 30)
        if eps > 0 and adj_growth >= 1.0:        # at least 1% growth, properly in % units
            models['M7_PEG'] = round(eps * adj_growth, 2)
        else:
            models['M7_PEG'] = 0

        # M5: EV/EBITDA-based Fair Value
        # Shortcut form: fair_value ≈ CMP × (sector_ev_mult / current_ev_ebitda)
        # Note: this implicitly assumes net debt and share count remain stable
        # vs. peers. The 10% composite weight bounds the impact of this
        # assumption. v12.2: substring matching + expanded sector map.
        ev_ebitda_curr = _sf(data.get('ev_ebitda', 0))
        _sec_ev_map = {
            "Software": 22, "Technology": 22, "IT": 20,
            "Banks": 12, "Banking": 12, "NBFC": 14, "Insurance": 14,
            "Financial": 12,
            "Pharma": 18, "Healthcare": 18,
            "FMCG": 30, "Consumer": 22,
            "Auto": 10, "Automobile": 10,
            "Steel": 5, "Metals": 6,
            "Oil": 7, "Energy": 8, "Power": 9,
            "Realty": 12, "Real Estate": 12,
            "Telecom": 9, "Cement": 11,
            "Textiles": 8, "Media": 12,
            "Chemical": 14, "Infra": 11, "Defence": 18,
        }
        sector_ev_mult = _resolve_sector_map(
            data.get('sector', ''), _sec_ev_map, 15
        )
        cmp_m5 = _sf(data.get('close', 0))
        if ev_ebitda_curr > 0 and cmp_m5 > 0:
            models['M5_EV'] = round(cmp_m5 * sector_ev_mult / ev_ebitda_curr, 2)
        else:
            models['M5_EV'] = 0

        # M6: DDM (Dividend Discount Model) — Gordon Growth Model
        # FV = D1 / (r - g)  where D1 = DPS × (1+g)
        # Only valid for genuine dividend-paying stocks (0.1% < yield < 15%)
        # Yields > 15% indicate bad data (unit mismatch) — skip DDM.
        #
        # v12.2 fix: growth derivation rewritten for clarity and correctness.
        #   • OLD code: `min(max(_pat_g / 200, 0.02), 0.06)` had a 2% floor
        #     that systematically inflated FV for stocks with declining
        #     earnings — even a stock with pat_yoy=-20 got 2% growth credit.
        #   • NEW code: pat_yoy is in percent, so divide by 100 to get a
        #     decimal, then halve (conservative — comment said "min(pat/2,
        #     GDP=10%)"). Floor at 0% (stagnation), cap at 6% (matches GDP
        #     nominal anchor described in original comment). Negative-growth
        #     stocks now correctly get 0% div growth, not a free 2%.
        div_yield_pct = _sf(data.get('div_yield', 0))
        if 0.1 < div_yield_pct < 15.0 and cmp_m5 > 0:
            dps        = cmp_m5 * div_yield_pct / 100   # annual DPS in ₹
            _pat_g_pct = _sf(data.get('pat_yoy', 0), 0)
            # half of pat_yoy growth (conservative), floored at 0, capped at 6%
            div_growth = max(min(_pat_g_pct / 100 / 2, 0.06), 0.0)
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

        v12.2 fix: unknown model keys now get weight 0 (excluded) rather than
        a phantom 0.10 default — prevents accidental dilution if a future
        change adds a key to `models` that isn't in `base_weights`.
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

        # Include only known, non-zero models; normalize weights so they sum to 1
        available = {k: v for k, v in models.items()
                     if isinstance(v, (int, float)) and v > 0
                     and k in base_weights}
        total_w = sum(base_weights[k] for k in available)

        if total_w > 0 and available:
            cfv = sum(v * base_weights[k] for k, v in available.items()) / total_w
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