class SpikeScreener:
    def check_anti_trigger_guard(self, data):
        """
        SECTION 3H: Check FIRST - Suppress all spikes if risk flags active.
        """
        guards = []
        if data.get('pledge_pct', 0) > 20: guards.append("HIGH PLEDGE > 20%")
        if data.get('altman_z', 5) < 1.81: guards.append("ALTMAN Z-SCORE DISTRESS")
        if data.get('beneish_m', -5) > -2.22: guards.append("BENEISH M-SCORE MANIPULATION")
        if data.get('cfo_pat_ratio', 1) < 0.5: guards.append("CFO/PAT DIVERGENCE")

        return {"suppressed": len(guards) > 0, "reasons": guards}

    def calculate_spike_score(self, data, sector_data):
        """
        SECTION 3H: Calculates 0-6 Spike Score based on V7 Triggers.
        All 6 triggers use free NSE/yfinance data only.

        T1: 52W High Breakout  — CMP within 3% of 52W High + volume surge (momentum breakout)
        T2: Technical Breakout — MACD BUY + Supertrend BUY + above-avg volume
        T3: Strong Trend       — ADX > 25 (trend strength) + delivery > 60% + vol surge
        T4: RSI Accumulation   — RSI in 45-65 zone (not overbought) + volume surge
        T5: Institutional Accum— Very high volume (>3×) + high delivery (>60%) = conviction buying
        T6: Momentum Surge     — 2-week momentum > 3% and accelerating vs 4-week
        """
        triggers = []
        vol  = float(data.get('vol_spike_50d', data.get('vol_ratio', 0)) or 0)
        deliv= float(data.get('delivery_pct', 0) or 0)

        # TRIGGER 1: 52W High Breakout
        # CMP within 3% of 52W High + volume surge → stock breaking out of resistance
        _cmp   = float(data.get('close',  data.get('cmp', 0)) or 0)
        _high  = float(data.get('high_52w', data.get('52w_high', 0)) or 0)
        if _high > 0 and _cmp > 0:
            _dist_from_high = (_high - _cmp) / _high * 100
            if _dist_from_high <= 3.0 and vol > 2.0:
                triggers.append("52W BREAKOUT")

        # TRIGGER 2: Technical Breakout (MACD BUY + Supertrend BUY + above-avg vol)
        _macd = str(data.get('macd_signal', '')).upper()
        _st   = str(data.get('supertrend',  '')).upper()
        if 'BUY' in _macd and 'BUY' in _st and vol > 1.5:
            triggers.append("TECHNICAL BREAKOUT")

        # TRIGGER 3: Strong Trend (ADX > 25 = confirmed trend + delivery conviction)
        _adx = float(data.get('adx', 0) or 0)
        if _adx > 25 and deliv > 60 and vol > 1.5:
            triggers.append("STRONG TREND")

        # TRIGGER 4: RSI Accumulation Zone + volume surge
        # RSI 45-65: momentum building but not overbought = ideal entry zone
        _rsi = float(data.get('rsi', data.get('rsi_14', 50)) or 50)
        if 45 < _rsi <= 65 and vol > 2.0:
            triggers.append("RSI ACCUMULATION")

        # TRIGGER 5: Institutional Accumulation
        # Very high volume + high delivery = large players building positions
        if vol > 3.0 and deliv > 60:
            triggers.append("INSTITUTIONAL ACCUMULATION")

        # TRIGGER 6: Momentum Surge
        # 2-week return > 3% AND accelerating vs 4-week return + volume confirming
        _chg2w = float(data.get('2w_chg', data.get('chg_2w', 0)) or 0)
        _chg4w = float(data.get('4w_chg', data.get('chg_4w', 0)) or 0)
        if _chg2w > 3.0 and _chg2w > _chg4w and vol > 1.5:
            triggers.append("MOMENTUM SURGE")

        return {
            "score": len(triggers),
            "tags":  triggers,
            "label": self._get_label(len(triggers))
        }

    def _get_label(self, count):
        if count >= 5: return "RARE CONFLUENCE"
        if count >= 3: return "STRONG SETUP"
        if count >= 1: return "WATCH"
        return "NO SIGNAL"