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

    # v10.17 quality guard: minimum number of "informed" sub-score dimensions
    # required before a stock can carry a BUY verdict. A stock that scores
    # high purely because most sub-scores sat at their neutral base (50) and
    # got mild bonuses from one or two informed dimensions has too much
    # missing data to act on. With this guard, such a stock is demoted to
    # WATCHLIST regardless of composite score. Default 3 of 5 dimensions.
    # See ScoringEngine._count_informed_dimensions() for the counting rule.
    MIN_INFORMED_FOR_BUY = 3

    def __init__(self):
        pass  # Thresholds defined as class constants above

    # ──────────────────────────────────────────────────────────────────────
    # v10.17 — Data-completeness quality guard
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _count_informed_dimensions(data, sentiment_is_informed):
        """
        Count how many of the 5 sub-score dimensions actually had real data
        speak (rather than sitting at their neutral base / zero floor).

        Returns an integer 0–5. Used by _get_verdict_with_confidence() to
        gate the BUY verdict — see MIN_INFORMED_FOR_BUY.

        Counting rules (each adds 1 if the test passes):
          1. Fundamental  — fundamental_score deviates ≥ 6 from its base band
                            (base is 45-55 depending on Stage 2 score; deviation
                            of 6+ means at least one moderate bucket actually fired)
          2. Technical    — technical_score deviates ≥ 6 from neutral 50
                            (at least one BUY/SELL signal or a strong indicator)
          3. Safety       — safety_score deviates ≥ 6 from neutral 50
                            (real safety signal — pledge, debt, FCF, BS health, etc.)
          4. Sentiment    — sentiment_is_informed flag (already computed upstream
                            from the 6 paid/AI signal presence check)
          5. Early Entry  — early_entry_score > 0 (EE has zero base; any score
                            means at least one early-mover signal fired)

        Why ±6 and not exact-equals-base? Because cap-tier base for fundamental
        ranges 45-55. A test of 'deviation > 0' would always pass (Stage 2 score
        is non-zero by definition). 6 points means at least one moderate bucket
        bonus fired (PE in 0-20 = +12, ROE >10% = +6) or one penalty triggered.
        """
        try:
            count = 0

            # 1. Fundamental — base is 45 + (s2/30)*10, range 45-55. Compute
            #    the actual base for this stock's Stage 2 score and check
            #    deviation from it.
            try:
                s2 = float(data.get('stage2_score', 0) or 0)
            except (ValueError, TypeError):
                s2 = 0
            f_base = 45.0 + min(max(s2, 0), 30) / 30.0 * 10.0
            try:
                f_raw = float(data.get('fundamental_score', f_base) or f_base)
            except (ValueError, TypeError):
                f_raw = f_base
            if abs(f_raw - f_base) >= 6:
                count += 1

            # 2. Technical — base 50
            try:
                t_raw = float(data.get('technical_score', 50) or 50)
            except (ValueError, TypeError):
                t_raw = 50
            if abs(t_raw - 50) >= 6:
                count += 1

            # 3. Safety — base 50
            try:
                safe_raw = float(data.get('safety_score', 50) or 50)
            except (ValueError, TypeError):
                safe_raw = 50
            if abs(safe_raw - 50) >= 6:
                count += 1

            # 4. Sentiment — already computed by caller (paid/AI signal presence)
            if sentiment_is_informed:
                count += 1

            # 5. Early Entry — zero base, so any positive score counts
            try:
                e_raw = float(data.get('early_entry_score', 0) or 0)
            except (ValueError, TypeError):
                e_raw = 0
            if e_raw > 0:
                count += 1

            return count
        except Exception:
            # Defensive: if anything unexpected happens, return 5 (don't gate)
            # so the new check can never break a working pipeline run.
            return 5

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

        # v10.17: count "informed" sub-score dimensions for the data-completeness
        # gate. A stock with too few informed dimensions cannot become BUY (it
        # gets demoted to WATCHLIST inside _get_verdict_with_confidence). See
        # MIN_INFORMED_FOR_BUY and _count_informed_dimensions for the rule.
        informed_count = self._count_informed_dimensions(data, sentiment_is_informed)

        # C. Verdict derivation with confidence + OVERVALUED (fixes #4, #5)
        # ────────────────────────────────────────────────────────────────────
        cap_cat     = str(data.get("cap_category", "") or "")
        mos         = data.get("mos_pct", None)
        supertrend  = str(data.get("supertrend", "") or "").upper()
        sector_stage= str(data.get("rotation_stage", data.get("sector_stage", "")) or "").upper()

        verdict_info = self._get_verdict_with_confidence(
            final_score, cap_cat, mos,
            supertrend=supertrend, sector_stage=sector_stage,
            informed_count=informed_count,
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
            "data_completeness":  informed_count,             # v10.17 (0-5)
            "data_gate_applied":  verdict_info.get("data_gate_applied", False),  # v10.17
        }

    def calculate_storm_score(self, data, market_vix, market_off_peak):
        """
        Section 7: Defensive quality score. Always calculated; critical above VIX 18.
        v10.10: guarded against '—' string for fields that may now be non-numeric
        (div_yield non-dividend stocks, etc.) — reuse the _fnum helper pattern.
        """
        # v10.10 safe numeric coercion — returns None for '—', '', None, etc.
        def _safe_num(v, default=None):
            if v in (None, "", "—", "--", "N/A"):
                return default
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        score = 0
        # Scoring Logic (Section 7)
        _beta = _safe_num(data.get('beta'), 1.0)
        if _beta is not None and _beta < 0.8: score += 2
        # de_ratio_num is the normalised key set in master_funnel;
        # fall back to debt_equity then 1.0 so D/E<0.3 stocks correctly get +2 pts
        _de_val = _safe_num(data.get('de_ratio',
                        data.get('de_ratio_num',
                        data.get('debt_equity', 1.0))), 1.0)
        if _de_val is not None and _de_val < 0.3: score += 2
        if data.get('fcf_positive_4q', False): score += 2
        if data.get('promoter_q_increase', False): score += 1
        _dy = _safe_num(data.get('div_yield'))
        if _dy is not None and _dy > 2.0: score += 1
        if data.get('fii_buy_3q', False): score += 1
        _rg = _safe_num(data.get('rev_growth_yoy'))
        if _rg is not None and _rg > 10.0: score += 1
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
                                      supertrend="", sector_stage="",
                                      informed_count=5):
        """
        Session 24: Enriched verdict derivation.

        Returns dict with:
          verdict           — one of BUY, OVERVALUED, WATCHLIST, NEUTRAL, AVOID
          confidence        — one of HIGH, MEDIUM, LOW (based on distance from threshold)
          display           — e.g., "BUY ●●●", "OVERVALUED ●●○", "WATCHLIST ●○○"
          data_gate_applied — True if BUY was demoted to WATCHLIST by the
                              v10.17 data-completeness guard

        Verdict rules:
          AVOID       — score < 38 (universal floor)
          BUY         — score ≥ cap-adjusted BUY threshold AND MoS passes gate
                        AND informed_count ≥ MIN_INFORMED_FOR_BUY (v10.17)
          OVERVALUED  — score ≥ cap-adjusted BUY threshold BUT MoS gate blocks
                        (great business, but currently expensive)
          WATCHLIST   — score in WATCHLIST band (between BUY threshold and AVOID floor)
                        OR score above BUY threshold but data too sparse (v10.17)
          NEUTRAL     — 38 ≤ score < WATCHLIST threshold

        Confidence rules (distance from the threshold the verdict clears):
          HIGH   — ≥ 5 points above the decisive threshold
          MEDIUM — 2–5 points above the decisive threshold
          LOW    — within 2 points of the threshold (cliff zone)

        v10.17 data-completeness guard:
          If a stock would qualify for BUY (score and MoS both pass) but has
          fewer than MIN_INFORMED_FOR_BUY informed sub-score dimensions, the
          BUY is demoted to WATCHLIST. The display string is annotated with
          "(thin data)" so the user can see why. OVERVALUED is unaffected
          (a great-but-expensive call already advises waiting). NEUTRAL and
          AVOID are unaffected (they're already conservative).
        """
        # Universal AVOID floor
        if score < self.AVOID_BELOW:
            # Distance from 38 floor going downward
            dist = self.AVOID_BELOW - score
            conf = "HIGH" if dist > 5 else ("MEDIUM" if dist > 2 else "LOW")
            return {"verdict":"AVOID","confidence":conf,
                    "display":f"AVOID {self._dots(conf)}",
                    "data_gate_applied": False}

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
            # v10.17 data-completeness guard — demote to WATCHLIST if data is thin
            if informed_count < self.MIN_INFORMED_FOR_BUY:
                # Distance from buy threshold (still useful for confidence dots)
                dist = score - watch_min
                conf = "HIGH" if dist >= 5 else ("MEDIUM" if dist >= 2 else "LOW")
                return {"verdict":"WATCHLIST","confidence":conf,
                        "display":f"WATCHLIST {self._dots(conf)} (thin data)",
                        "data_gate_applied": True}
            dist = score - buy_min
            conf = "HIGH" if dist >= 5 else ("MEDIUM" if dist >= 2 else "LOW")
            return {"verdict":"BUY","confidence":conf,
                    "display":f"BUY {self._dots(conf)}",
                    "data_gate_applied": False}

        if score >= buy_min and mos_blocks_buy:
            # Session 24: new OVERVALUED verdict — distinguishes "BUY-quality
            # business, currently expensive" from WATCHLIST "weak signal".
            dist = score - buy_min
            conf = "HIGH" if dist >= 5 else ("MEDIUM" if dist >= 2 else "LOW")
            return {"verdict":"OVERVALUED","confidence":conf,
                    "display":f"OVERVALUED {self._dots(conf)}",
                    "data_gate_applied": False}

        if score >= watch_min:
            dist = score - watch_min
            conf = "HIGH" if dist >= 5 else ("MEDIUM" if dist >= 2 else "LOW")
            return {"verdict":"WATCHLIST","confidence":conf,
                    "display":f"WATCHLIST {self._dots(conf)}",
                    "data_gate_applied": False}

        # NEUTRAL — between AVOID floor and WATCHLIST threshold
        dist = score - self.AVOID_BELOW
        conf = "HIGH" if dist >= 8 else ("MEDIUM" if dist >= 4 else "LOW")
        return {"verdict":"NEUTRAL","confidence":conf,
                "display":f"NEUTRAL {self._dots(conf)}",
                "data_gate_applied": False}

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