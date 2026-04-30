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


# ──────────────────────────────────────────────────────────────────────────
# Sector normalization — v12.2 Round 1
# ──────────────────────────────────────────────────────────────────────────
# Maps the sector strings that arrive from upstream (yfinance / NSE feed) to
# the canonical keys used in the per-model sector multiple maps below.
# Without this, sector strings that don't substring-match any benchmark key
# silently fall through to the default multipliers, producing the same
# "neutral" fair value as a stock whose sector we genuinely can't classify.
#
# Real-world example: production data showed 31 of 100 stocks in sectors
# whose strings ("Basic Materials", "Industrials", "Communication Services",
# "General") didn't match any benchmark key, so M3/M4/M5 always returned
# default values for them. After aliasing, these sectors map to their nearest
# benchmark equivalent (Metals, Infra, Telecom) and produce real signal.
SECTOR_ALIASES = {
    # yfinance "Sector" field (top-level GICS-style classification)
    "Basic Materials":         "Metals",       # steel, copper, chemicals, mining
    "Industrials":             "Infra",        # capital goods, construction, defense, transport
    "Communication Services":  "Telecom",      # Bharti, telecom infrastructure
    "Consumer Cyclical":       "Consumer",     # consumer discretionary
    "Consumer Defensive":      "Consumer",     # consumer staples
    "Financial Services":      "Financial",    # broad — banks/NBFCs/insurance subdivision via substring
    "Real Estate":             "Realty",
    # "Healthcare" → matches our Healthcare key directly
    # "Technology" → matches our Technology key directly
    # "Energy" → matches our Energy key directly
    # "Utilities" → matches our Power key indirectly (no entry needed)
    # "General" → no good mapping (catch-all bucket); falls through to default

    # Common legacy / industry-specific labels (Indian market data sources)
    "Information Technology":  "Technology",
    "IT Services":             "Technology",
    "Software":                "Software",
    "Iron & Steel":            "Steel",
    "Iron and Steel":          "Steel",
    "Banking and Finance":     "Banks",
    "Banks - Public Sector":   "Banks",
    "Banks - Private Sector":  "Banks",
    "Pharmaceuticals":         "Pharma",
    "Auto Components":         "Auto",
    "Automobiles":             "Auto",
    "Cement & Construction":   "Cement",
    "Real Estate Investment":  "Realty",
    "Power Generation":        "Power",
    "Oil & Gas":               "Oil",
    "Capital Goods":           "Infra",
    "Defence":                 "Defence",
    "Defense":                 "Defence",
}


def _canonicalize_sector(sector_str):
    """Apply SECTOR_ALIASES if applicable; otherwise return the original string.

    Case-insensitive on alias keys but preserves canonical-value casing.
    """
    if not sector_str:
        return ""
    s = str(sector_str).strip()
    s_lower = s.lower()
    for alias, canonical in SECTOR_ALIASES.items():
        if alias.lower() == s_lower:
            return canonical
    return s   # no alias — pass through to substring matcher


def _resolve_sector_map(sector_str, sector_map, default):
    """Match a sector string against a benchmark map.

    Two-pass resolution:
      1. Apply SECTOR_ALIASES to canonicalize known-but-unmapped strings
         (e.g., "Basic Materials" → "Metals").
      2. Substring-search the canonicalized string against the benchmark map.

    The map is iterated in insertion order (Python 3.7+), so callers should
    put more specific keys (e.g., 'Software') before generic ones ('IT') if
    a string could match multiple. Returns `default` if no key matches.

    Returns: (matched_value, matched_key) so callers can surface diagnostics.
    """
    if not sector_str:
        return default, "(empty)"
    canonical = _canonicalize_sector(sector_str)
    s = str(canonical).upper()
    for key, val in sector_map.items():
        if key.upper() in s:
            return val, key
    return default, "(default)"


class FairValueEngine:
    def __init__(self, gsec_yield=6.0):
        self.gsec = gsec_yield  # Section 5B: 10Y Gsec benchmark

    def calculate_all_models(self, data, beta, growth_3yr):
        """
        Step 1: 7 Valuation Models (M1-M7)

        v12.2 fixes (initial release):
          • eps / bvps now go through _sf() — previously these were the only
            data fetches that bypassed sanitization, making the engine crash
            when upstream sent '—' / 'N/A' / None for these fields.
          • M3 / M4 / M5 sector parsing replaced with substring matching so
            multi-word sectors like 'Information Technology' resolve correctly.
          • M3 / M4 / M5 sector maps expanded to cover Realty, Telecom,
            Cement, Textiles, Media, Insurance, NBFC explicitly.
          • M6 DDM: removed the 2% growth floor (was systematically
            over-rewarding stocks with declining earnings); added a unit-clear
            growth formula matching the documented intent.
          • M7 PEG: made unit-safe with explicit guard for unexpectedly small
            growth values (catches the case where growth_3yr accidentally
            arrives as a decimal fraction instead of a percentage).

        v12.2 Round 1 enhancements (addresses production-data findings):
          • SECTOR_ALIASES: explicit normalization map for production sector
            strings (e.g., "Basic Materials" → "Metals") that don't substring-
            match any benchmark key. Diagnosed from real Excel output where
            31 of 100 stocks were silently using default multipliers.
          • debug_sector_resolutions in output: surfaces which benchmark key
            each model resolved to, so future regressions in sector handling
            are visible without code archaeology.

        v12.3 Round 2 enhancements (M5 dimensional fix + M7 explicitness):
          • M5 EV/EBITDA: replaced the multiplicative shortcut with a proper
            EV-based formula (annual_ebitda × sector_mult − net_debt), with
            three-tier fallback. Only fires Tier 1 when q_ebitda_cr +
            total_debt_cr + cash_cr + mcap_cr are all available. Tier 2 falls
            back to the v12.2 shortcut. Banks/NBFCs/Insurance now correctly
            skip M5 entirely (EV/EBITDA isn't meaningful for financials).
          • M7 PEG: PEG_BENCHMARK = 1.0 now an explicit named constant
            (Lynch's rule of thumb), making the assumption tunable.
          • _m5_method diagnostic: surfaces which M5 tier fired ("proper" /
            "shortcut" / "skip_financial" / "skip_no_data" /
            "proper_negative_equity") so quality of M5 output is visible.
        """
        models = {}
        # Track which benchmark key each model resolved to (Round 1 diagnostic)
        sector_resolutions = {}

        # v12.2 fix: route through _sf() so '—' / None / '' don't blow up
        # downstream comparisons like `eps > 0`.
        eps = _sf(data.get('eps', 0), 0)
        bv  = _sf(data.get('bvps', 0), 0)

        # M1: DCF (3-Stage) — Stage 1 capped at 25%
        # Session 19: WACC floor at 10% + 4× CMP cap.
        _raw_wacc = (self.gsec + beta * 5.5) / 100
        wacc = max(_raw_wacc, 0.10)
        gt = 0.045
        cmp_m1 = _sf(data.get('close', 0))
        if eps > 0 and wacc > gt:
            g1 = min(growth_3yr / 100, 0.25)
            g2 = g1 / 2
            dcf = sum(eps * (1+g1)**y / (1+wacc)**y for y in range(1,6))
            dcf += sum(eps * (1+g1)**5 * (1+g2)**(y-5) / (1+wacc)**y for y in range(6,11))
            terminal = (eps * (1+g1)**5 * (1+g2)**5 * (1+gt)) / (wacc - gt) / (1+wacc)**10
            _m1 = dcf + terminal
            if cmp_m1 > 0 and _m1 > cmp_m1 * 4:
                _m1 = cmp_m1 * 4
            models['M1_DCF'] = round(_m1, 2)
        else:
            models['M1_DCF'] = 0

        # M2: Graham Number — derive bvps from pb × close if missing.
        # Note: M2 uses no sector multiplier — formula depends only on EPS
        # and BVPS, so v12.2 sector fixes don't affect this model.
        _bvps = bv
        if (not _bvps or _bvps == 0) and data.get('pb') and _sf(data.get('pb')) > 0:
            _close = _sf(data.get('close', 0))
            _pb    = _sf(data.get('pb', 0))
            if _close > 0 and _pb > 0:
                _bvps = round(_close / _pb, 2)
        models['M2_Graham'] = round(math.sqrt(22.5 * eps * _bvps), 2) if eps > 0 and _bvps > 0 else 0

        # M4: Price-to-Book Fair Value = BVPS × sector_median_PB
        _pb_v = _sf(data.get('pb'))
        if _bvps and _bvps > 0:
            _bvps4 = _bvps
        elif _pb_v > 0:
            _bvps4 = round(_sf(data.get('close', 0)) / _pb_v, 2)
        else:
            _bvps4 = 0
        # v12.2: expanded sector PB map. Round 1: aliasing handled upstream.
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
        _sec_pb, _pb_key = _resolve_sector_map(data.get('sector', ''), _sec_pb_map, 3.0)
        sector_resolutions['M4_PB'] = _pb_key
        models['M4_PB'] = round(_bvps4 * _sec_pb, 2) if _bvps4 > 0 else 0

        # M3: PE Mean Reversion — sector-appropriate benchmark PE
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
        _fair_pe, _pe_key = _resolve_sector_map(
            data.get('sector', ''), _sec_pe_map,
            _sf(data.get('sector_pe_5yr', 0), 0) or 25,
        )
        sector_resolutions['M3_PE'] = _pe_key
        models['M3_PE'] = round(eps * _fair_pe, 2) if eps > 0 else 0

        # M7: PEG-Adjusted (Lynch's PEG=1 → fair PE = growth rate)
        # v12.2 unit guard: skip if growth < 1.0 (likely arrived as decimal).
        # v12.3 Round 2: PEG_BENCHMARK constant made explicit. Lynch's classic
        # rule of thumb: a stock is fairly valued when PEG = 1.0 (so fair PE
        # equals the growth rate). The constant is named so it's tunable —
        # value-investing setups might use 0.8 (stricter), while growth-tilted
        # mandates might use 1.2 (more permissive).
        PEG_BENCHMARK = 1.0
        adj_growth = min(growth_3yr, 30)
        if eps > 0 and adj_growth >= 1.0:
            # fair_PE = growth_rate × PEG_BENCHMARK
            # fair_value = EPS × fair_PE
            models['M7_PEG'] = round(eps * adj_growth * PEG_BENCHMARK, 2)
        else:
            models['M7_PEG'] = 0

        # M5: EV/EBITDA-based Fair Value
        # v12.3 Round 2: replaces the v12.2 multiplicative shortcut with a
        # proper EV-based per-share fair value that accounts for net debt.
        #
        # Why the change: production data showed the v12.2 shortcut formula
        # (`CMP × sector_ev_mult / current_ev_ebitda`) producing aggressive
        # outputs for sectors where the new sector multiple was very different
        # from the old default. The shortcut implicitly assumed net debt and
        # share count remained stable vs peers — a fragile assumption.
        #
        # The proper formula avoids this by working in absolute ₹Cr units:
        #   fair_EV_cr     = annual_ebitda_cr × sector_ev_mult
        #   net_debt_cr    = total_debt_cr − cash_cr
        #   fair_mcap_cr   = fair_EV_cr − net_debt_cr   (debt subtracts from equity value)
        #   fair_per_share = CMP × (fair_mcap_cr / current_mcap_cr)
        #
        # The last step is the elegant trick: by ratioing the fair mcap to the
        # current mcap, we don't need to know shares_outstanding. CMP already
        # encodes that information, and (fair_mcap_cr / mcap_cr) gives the
        # mispricing as a multiplier on CMP.
        #
        # Three tiers, each falling back to the next:
        #   Tier 1 (proper):    needs annual_ebitda_cr, total_debt_cr, cash_cr, mcap_cr
        #   Tier 2 (shortcut):  needs ev_ebitda ratio + cmp + sector mult (legacy v12.2)
        #   Tier 3 (skip):      no usable data → 0
        #
        # The `_m5_method` field surfaces which tier fired, for diagnostics.
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
        sector_ev_mult, _ev_key = _resolve_sector_map(
            data.get('sector', ''), _sec_ev_map, 15
        )
        sector_resolutions['M5_EV'] = _ev_key
        cmp_m5 = _sf(data.get('close', 0))

        # Tier 1: proper EV math (uses absolute ₹Cr fields)
        # We accept either q_ebitda_cr (quarterly, multiply by 4) or annual EBITDA.
        # Banks/NBFCs use a different income structure — skip M5 entirely for them
        # since EV/EBITDA isn't meaningful for financials.
        _is_financial = any(k in str(_ev_key).upper() for k in
                            ["BANK", "NBFC", "INSURANCE", "FINANCIAL"])
        _q_ebitda    = _sf(data.get('q_ebitda_cr', 0), 0)
        _total_debt  = _sf(data.get('total_debt_cr',
                            data.get('total_debt', 0)), 0)
        _cash        = _sf(data.get('cash_cr',
                            data.get('cash', 0)), 0)
        _mcap_cr     = _sf(data.get('mcap_cr',
                            data.get('mcap', 0)), 0)

        _proper_inputs_ok = (
            not _is_financial
            and _q_ebitda > 0
            and _mcap_cr > 0
            and cmp_m5 > 0
            # debt and cash can be 0 (zero-debt company is fine), so we don't
            # require them to be positive — but they must not be negative
            and _total_debt >= 0
            and _cash >= 0
        )

        if _proper_inputs_ok:
            # Tier 1: proper formula
            _annual_ebitda_cr = _q_ebitda * 4         # annualize quarterly
            _fair_ev_cr       = _annual_ebitda_cr * sector_ev_mult
            _net_debt_cr      = _total_debt - _cash
            _fair_mcap_cr     = _fair_ev_cr - _net_debt_cr

            if _fair_mcap_cr > 0:
                _m5 = cmp_m5 * (_fair_mcap_cr / _mcap_cr)
                # Sanity: cap at 4× CMP (mirrors M1 DCF cap) to prevent
                # tiny-EBITDA + low-debt outliers producing implausible numbers
                if _m5 > cmp_m5 * 4:
                    _m5 = cmp_m5 * 4
                models['M5_EV'] = round(_m5, 2)
                sector_resolutions['_m5_method'] = "proper"
            else:
                # Negative fair equity = company's debt exceeds its EV at sector
                # multiple = severely overvalued / overlevered. Express as a
                # heavily-discounted FV rather than skipping (which would lose
                # the bearish signal).
                models['M5_EV'] = round(cmp_m5 * 0.3, 2)  # 70% discount
                sector_resolutions['_m5_method'] = "proper_negative_equity"

        elif ev_ebitda_curr > 0 and cmp_m5 > 0 and not _is_financial:
            # Tier 2: legacy shortcut (when proper inputs missing)
            models['M5_EV'] = round(cmp_m5 * sector_ev_mult / ev_ebitda_curr, 2)
            sector_resolutions['_m5_method'] = "shortcut"

        else:
            # Tier 3: skip
            models['M5_EV'] = 0
            sector_resolutions['_m5_method'] = (
                "skip_financial" if _is_financial else "skip_no_data"
            )

        # M6: DDM (Dividend Discount Model) — Gordon Growth Model
        # v12.2 fix: removed 2% growth floor; growth = max(min(pat_yoy/200, 0.06), 0)
        div_yield_pct = _sf(data.get('div_yield', 0))
        if 0.1 < div_yield_pct < 15.0 and cmp_m5 > 0:
            dps        = cmp_m5 * div_yield_pct / 100
            _pat_g_pct = _sf(data.get('pat_yoy', 0), 0)
            div_growth = max(min(_pat_g_pct / 100 / 2, 0.06), 0.0)
            req_return = (self.gsec + 4.5) / 100
            if req_return > div_growth and dps > 0:
                d1 = dps * (1 + div_growth)
                models['M6_DDM'] = round(d1 / (req_return - div_growth), 2)
            else:
                models['M6_DDM'] = 0
        else:
            models['M6_DDM'] = 0

        # Round 1: attach sector resolution diagnostics. Underscore prefix
        # signals "metadata, not a model output"; composite weighting in
        # get_composite_fair_value() filters these out via base_weights guard.
        models['_sector_resolutions'] = sector_resolutions

        return models

    def get_composite_fair_value(self, models, cmp):
        """
        Step 2 & 3: Composite weighting and Margin of Safety (MoS).
        Uses all available (non-zero) models with normalized base weights.

        v12.2: unknown model keys get weight 0 (excluded) rather than a
        phantom 0.10 default. This also means metadata keys like
        '_sector_resolutions' (Round 1) are correctly ignored.

        v12.6: emits a `cfv_thin_models` flag and zeroes `score_adjustment`
        when fewer than MIN_MODELS valuation lenses fired — prevents thin
        FV evidence (1-2 models) from driving false BUYs via score adj.
        Also stops setting `mos_label` here — master_funnel is the single
        source of truth for the user-facing label scheme (#2 deduplication).
        Engine's bucket scheme is gone; engine still emits the numeric
        `mos_pct`, `cfv`, `cfv_capped`, `cfv_thin_models`, and `score_adjustment`
        fields that downstream code reads.
        """
        MIN_MODELS = 3   # v12.6: minimum model count for full-confidence CFV

        base_weights = {
            "M1_DCF":    0.30,
            "M2_Graham": 0.15,
            "M3_PE":     0.20,
            "M4_PB":     0.15,
            "M5_EV":     0.10,
            "M6_DDM":    0.05,
            "M7_PEG":    0.05,
        }

        available = {k: v for k, v in models.items()
                     if isinstance(v, (int, float)) and v > 0
                     and k in base_weights}
        total_w = sum(base_weights[k] for k in available)
        n_models = len(available)

        if total_w > 0 and available:
            cfv = sum(v * base_weights[k] for k, v in available.items()) / total_w
        elif available:
            cfv = sum(available.values()) / len(available)
        else:
            cfv = 0

        # Session 19: cap composite CFV at 3× CMP.
        # v12.5: surface a `cfv_capped` flag so downstream display can mark
        # the MoS Label with `*` — users can tell that 200 % MoS was clipped
        # rather than the underlying model genuinely projecting 3× upside.
        cfv_capped = False
        if cmp > 0 and cfv > cmp * 3:
            cfv = cmp * 3
            cfv_capped = True

        cfv = round(cfv, 2)

        mos = round(((cfv - cmp) / cmp * 100), 2) if cmp > 0 else 0

        # v12.6: thin-model guard — fewer than MIN_MODELS valuation lenses
        # fired means the CFV is based on an unusually narrow basis. We
        # still display CFV/MoS so the user can decide, but we suppress the
        # automatic `score_adjustment` (which would otherwise drive a +12
        # bonus into composite_score on noisy 1-2-model evidence).
        cfv_thin_models = (n_models < MIN_MODELS)

        score_adj = 0
        if not cfv_thin_models:
            if   mos > 40:         score_adj = 12
            elif mos > 25:         score_adj = 8
            elif mos > 10:         score_adj = 4
            elif mos < -30:        score_adj = -10
            elif mos < -15:        score_adj = -5

        upside = round(((cfv - cmp) / cmp * 100), 2) if cmp > 0 else -100
        upside = max(upside, -100)

        # v12.6 (#2): mos_label is no longer set here — master_funnel is the
        # single source of truth for the user-facing bucket scheme. Engine
        # output drives the numeric values + flags; funnel renders the label.

        return {
            "cfv":              cfv,
            "cfv_low":          round(cfv * 0.85, 2) if cfv > 0 else 0,
            "cfv_high":         round(cfv * 1.15, 2) if cfv > 0 else 0,
            "mos_pct":          mos,
            "score_adjustment": score_adj,
            "upside":           upside,
            "cfv_capped":       cfv_capped,        # v12.5: surfaced for display
            "cfv_thin_models":  cfv_thin_models,   # v12.6: surfaced for display
            "n_models":         n_models,          # v12.6: count for diagnostics
        }