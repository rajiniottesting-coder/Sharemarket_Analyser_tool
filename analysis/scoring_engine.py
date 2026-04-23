import pandas as pd


def _nonzero_qoq(val) -> bool:
    """
    Session 24 helper for sentiment informedness check.
    Returns True if a QoQ shareholding delta is a meaningful signal
    (not None, not zero, not '—', and magnitude > 0.1 percentage points).
    """
    if val is None:
        return False
    try:
        v = float(str(val).replace("—", "0") or 0)
        return abs(v) > 0.1
    except (ValueError, TypeError):
        return False


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

        Session 24 refinements (five fixes, one coherent pass):
         1. Sentiment weight redistributes if sentiment is weakly-informed
            (no paid/AI inputs fired). Prevents "5 free points" for ignorance.
         2. Spike bonus gated by fundamental quality — full +10 only when
            fundamental_score ≥ 55. Momentum enhances quality; doesn't rescue junk.
         3. Confidence indicator (HIGH/MEDIUM/LOW) tells user how far from
            threshold the score is. Honest disclosure near cliffs.
         4. New OVERVALUED verdict for stocks that score BUY but MoS gate blocks.
            Distinguishes "great business, currently expensive" from "weak signal".
         5. Stage-2 inflation fix is upstream in master_funnel.py — this block
            receives the corrected fundamental_score unchanged.
        """
        # A. Base weighted sub-scores (unchanged weights for strong scores)
        # ────────────────────────────────────────────────────────────────────
        # Canonical weights: fundamental 35% / technical 30% / EE 15% / sentiment 10% / safety 10%
        f_raw = data.get('fundamental_score', 50)
        t_raw = data.get('technical_score',   50)
        e_raw = data.get('early_entry_score',  0)
        sent_raw = data.get('sentiment_score', 50)
        safe_raw = data.get('safety_score',    50)

        # Fix #1: sentiment "informedness" detection.
        # On free data, 5 of 8 sentiment inputs are paid (FII/Promoter/DII QoQ,
        # insider alert, pledge direction) and 1 is AI (news_sentiment). Only
        # smart_money_sentiment and delivery_pct are reliable free inputs.
        # If the stock has none of the paid/AI signals, sentiment_score was
        # built on weak evidence — redistribute its 10% weight instead of
        # letting a default 50 give "free 5 points".
        _has_paid_sentiment = any([
            data.get('fii_3q_trend') in ('UP', 'DOWN'),
            data.get('insider_buy_alert') == 'YES',
            _nonzero_qoq(data.get('promoter_qoq')),
            _nonzero_qoq(data.get('dii_qoq')),
            str(data.get('news_sentiment', 'NEUTRAL')).upper() in ('POSITIVE', 'NEGATIVE'),
            str(data.get('pledge_direction', '—')).upper() in ('FALLING', 'RISING'),
        ])
        sentiment_is_informed = _has_paid_sentiment

        # Compute base with canonical or redistributed weights
        if sentiment_is_informed:
            # Canonical weights as originally designed
            base_score = (
                f_raw * 0.35 + t_raw * 0.30 + e_raw * 0.15
                + sent_raw * 0.10 + safe_raw * 0.10
            )
            _weights_used = "canonical"
        else:
            # Redistribute sentiment's 10% across the 4 remaining sub-scores
            # proportionally to their original weights (sum = 0.90):
            #   fundamental: 0.35/0.90 = 0.389  → +0.0389 boost
            #   technical:   0.30/0.90 = 0.333  → +0.0333 boost
            #   early:       0.15/0.90 = 0.167  → +0.0167 boost
            #   safety:      0.10/0.90 = 0.111  → +0.0111 boost
            # Final adjusted weights:
            #   fundamental 0.389, technical 0.333, early 0.167, safety 0.111
            base_score = (
                f_raw * 0.389 + t_raw * 0.333 + e_raw * 0.167
                + safe_raw * 0.111
            )
            _weights_used = "redistributed (no paid sentiment)"

        # B. Adjustments & Bonuses (applied the same in both branches)
        # ────────────────────────────────────────────────────────────────────
        # MoS Adjustment (from fair_value_engine)
        final_score = base_score + data.get('score_adjustment',
                                   data.get('mos_adjustment', 0))

        # Fix #2: Spike bonus gated on fundamental quality
        # Full +10 only when fundamentals ≥ 55 (decent baseline). Otherwise
        # capped at +3 so momentum doesn't rescue a genuinely weak stock.
        _spk_cnt = data.get('spike_count', 0) or 0
        if f_raw >= 55:
            spike_bonus = min(_spk_cnt * 2, 10)          # full bonus
        else:
            spike_bonus = min(_spk_cnt * 2, 3)           # capped — don't mask weakness
        final_score += spike_bonus

        # Early Mover Bonus (unchanged)
        if e_raw >= 50:
            final_score += 5

        # Anti-trigger Penalty
        if data.get('risk_flag_active', False):
            final_score -= 10

        # ────────────────────────────────────────────────────────────────────
        # v10.9 FORENSIC QUALITY ADJUSTMENT
        # ────────────────────────────────────────────────────────────────────
        # The v10.2-v10.8 work populated forensic fields (Altman Z, ND/EBITDA,
        # Int Coverage, Earn Quality) but these were never used in scoring.
        # Now they act as a quality gate: +8 max bonus for genuinely safe
        # businesses, -10 max penalty for distress signals. Keeps fundamental
        # and technical as the primary drivers — forensic is the tiebreaker.
        #
        # All contributions are guarded against missing data ("—", None, "")
        # so absent forensics don't penalise a stock.
        def _fnum(v):
            try:
                if v in (None, "", "—", "--", "N/A"): return None
                return float(v)
            except (ValueError, TypeError): return None

        forensic_adj = 0
        _contributors = []

        # 1. ALTMAN Z — bankruptcy risk
        #    > 3.0: "safe zone"  (+3)
        #    1.8–3.0: "grey zone" (no adjustment)
        #    < 1.8: "distress zone" (-5)
        _alt = _fnum(data.get('altman_z'))
        if _alt is not None:
            if _alt >= 3.0:
                forensic_adj += 3; _contributors.append(f"AltmanZ≥3:+3")
            elif _alt < 1.8:
                forensic_adj -= 5; _contributors.append(f"AltmanZ<1.8:-5")

        # 2. EARN QUALITY — categorical v10.8 output
        #    HIGH: cash flow matches profits (+2)
        #    MODERATE: (no adjustment)
        #    LOW: accounting concern (-3)
        _eq = str(data.get('earnings_quality', '') or '').upper()
        if _eq == "HIGH":
            forensic_adj += 2; _contributors.append("EQ=HIGH:+2")
        elif _eq == "LOW":
            forensic_adj -= 3; _contributors.append("EQ=LOW:-3")

        # 3. ND/EBITDA — leverage solvency
        #    < 1.0: strong solvency (+1)
        #    1.0–3.0: healthy (no adjustment)
        #    > 5.0: high leverage warning (-2)
        _nde = _fnum(data.get('nd_ebitda'))
        if _nde is not None:
            if _nde < 1.0:
                forensic_adj += 1; _contributors.append("ND/EBITDA<1:+1")
            elif _nde > 5.0:
                forensic_adj -= 2; _contributors.append("ND/EBITDA>5:-2")

        # 4. INT COVERAGE — can the company service interest?
        #    > 5x: comfortable (+2)
        #    2x–5x: OK (no adjustment)
        #    < 1.5x: distress warning (-3)
        _ic = _fnum(data.get('int_coverage'))
        if _ic is not None:
            if _ic > 5.0:
                forensic_adj += 2; _contributors.append("IC>5x:+2")
            elif _ic < 1.5:
                forensic_adj -= 3; _contributors.append("IC<1.5x:-3")

        # Cap contributions: +8 max bonus, -10 max penalty
        forensic_adj = max(-10, min(8, forensic_adj))
        final_score += forensic_adj

        final_score = max(0, min(100, final_score))  # Clamp 0-100

        # C. Verdict derivation with confidence + OVERVALUED (fixes #4, #5)
        # ────────────────────────────────────────────────────────────────────
        cap_cat     = str(data.get("cap_category", "") or "")
        mos         = data.get("mos_pct", None)
        supertrend  = str(data.get("supertrend", "") or "").upper()
        sector_stage= str(data.get("rotation_stage", data.get("sector_stage", "")) or "").upper()

        verdict_info = self._get_verdict_with_confidence(
            final_score, cap_cat, mos,
            supertrend=supertrend, sector_stage=sector_stage
        )

        return {
            "composite_score":    round(final_score, 2),
            "verdict":            verdict_info["verdict"],
            "verdict_confidence": verdict_info["confidence"],
            "verdict_display":    verdict_info["display"],
            "label":              self._assign_quick_pick(data, final_score),
            "weights_used":       _weights_used,
            "forensic_adj":       forensic_adj,               # v10.9
            "forensic_factors":   "|".join(_contributors) if _contributors else "",  # v10.9
        }

    def calculate_storm_score(self, data, market_vix, market_off_peak):
        """
        Section 7: Defensive quality score. Always calculated; critical above VIX 18.
        """
        score = 0
        # Scoring Logic (Section 7)
        if data.get('beta', 1.0) < 0.8: score += 2
        # de_ratio_num is the normalised key set in master_funnel;
        # fall back to debt_equity then 1.0 so D/E<0.3 stocks correctly get +2 pts
        _de_val = float(data.get('de_ratio',
                        data.get('de_ratio_num',
                        data.get('debt_equity', 1.0))) or 1.0)
        if _de_val < 0.3: score += 2
        if data.get('fcf_positive_4q', False): score += 2 
        if data.get('promoter_q_increase', False): score += 1
        if data.get('div_yield', 0) > 2.0: score += 1 
        if data.get('fii_buy_3q', False): score += 1 
        if data.get('rev_growth_yoy', 0) > 10.0: score += 1
        # Margin expansion = earnings quality improving = more storm-resistant
        if str(data.get('margin_expansion', 'NO') or 'NO').upper() == 'YES': score += 1
        
        # Labels 
        label = "HIGH RISK"
        if score >= 8: label = "STORM SAFE"
        elif score >= 5: label = "MODERATE"
        
        return {"storm_score": score, "storm_label": label}

    def _get_verdict(self, score, cap_category="", mos_pct=None,
                     supertrend="", sector_stage=""):
        """
        Returns one of: BUY, WATCHLIST, NEUTRAL, AVOID.
        (Legacy entry point — kept for any direct callers.)
        Session 24: see _get_verdict_with_confidence() for the enriched version
        that also returns confidence + OVERVALUED distinction.
        """
        return self._get_verdict_with_confidence(
            score, cap_category, mos_pct, supertrend, sector_stage
        )["verdict"]

    def _get_verdict_with_confidence(self, score, cap_category="", mos_pct=None,
                                      supertrend="", sector_stage=""):
        """
        Session 24: Enriched verdict derivation.

        Returns dict with:
          verdict    — one of BUY, OVERVALUED, WATCHLIST, NEUTRAL, AVOID
          confidence — one of HIGH, MEDIUM, LOW (based on distance from threshold)
          display    — e.g., "BUY ●●●", "OVERVALUED ●●○", "WATCHLIST ●○○"

        Verdict rules:
          AVOID       — score < 38 (universal floor)
          BUY         — score ≥ cap-adjusted BUY threshold AND MoS passes gate
          OVERVALUED  — score ≥ cap-adjusted BUY threshold BUT MoS gate blocks
                        (great business, but currently expensive)
          WATCHLIST   — score in WATCHLIST band (between BUY threshold and AVOID floor)
          NEUTRAL     — 38 ≤ score < WATCHLIST threshold

        Confidence rules (distance from the threshold the verdict clears):
          HIGH   — ≥ 5 points above the decisive threshold
          MEDIUM — 2–5 points above the decisive threshold
          LOW    — within 2 points of the threshold (cliff zone)
        """
        # Universal AVOID floor
        if score < self.AVOID_BELOW:
            # Distance from 38 floor going downward
            dist = self.AVOID_BELOW - score
            conf = "HIGH" if dist > 5 else ("MEDIUM" if dist > 2 else "LOW")
            return {"verdict":"AVOID","confidence":conf,
                    "display":f"AVOID {self._dots(conf)}"}

        # Cap tier resolution
        cap_up = str(cap_category).upper()
        if   "LARGE" in cap_up: tier = "LARGE"
        elif "MID"   in cap_up: tier = "MID"
        elif "SMALL" in cap_up: tier = "SMALL"
        else:                    tier = "MICRO"

        buy_min, watch_min = self.CAP_THRESHOLDS[tier]

        # Technical override for MoS gate
        tech_confirmed = (
            score >= 70
            and "BUY" in str(supertrend).upper()
            and "STAGE 2" in str(sector_stage).upper()
        )
        mos_gate = -20 if tech_confirmed else -10
        mos = mos_pct if mos_pct is not None else 0
        mos_blocks_buy = mos <= mos_gate

        # Verdict + confidence
        if score >= buy_min and not mos_blocks_buy:
            dist = score - buy_min
            conf = "HIGH" if dist >= 5 else ("MEDIUM" if dist >= 2 else "LOW")
            return {"verdict":"BUY","confidence":conf,
                    "display":f"BUY {self._dots(conf)}"}

        if score >= buy_min and mos_blocks_buy:
            # Session 24: new OVERVALUED verdict — distinguishes "BUY-quality
            # business, currently expensive" from WATCHLIST "weak signal".
            dist = score - buy_min
            conf = "HIGH" if dist >= 5 else ("MEDIUM" if dist >= 2 else "LOW")
            return {"verdict":"OVERVALUED","confidence":conf,
                    "display":f"OVERVALUED {self._dots(conf)}"}

        if score >= watch_min:
            dist = score - watch_min
            conf = "HIGH" if dist >= 5 else ("MEDIUM" if dist >= 2 else "LOW")
            return {"verdict":"WATCHLIST","confidence":conf,
                    "display":f"WATCHLIST {self._dots(conf)}"}

        # NEUTRAL — between AVOID floor and WATCHLIST threshold
        dist = score - self.AVOID_BELOW
        conf = "HIGH" if dist >= 8 else ("MEDIUM" if dist >= 4 else "LOW")
        return {"verdict":"NEUTRAL","confidence":conf,
                "display":f"NEUTRAL {self._dots(conf)}"}

    @staticmethod
    def _dots(confidence: str) -> str:
        """Visual confidence indicator for the display field."""
        return {"HIGH": "●●●", "MEDIUM": "●●○", "LOW": "●○○"}.get(confidence, "")

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