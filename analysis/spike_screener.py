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
        """
        triggers = []
        
        # TRIGGER 1: Value Breakout
        if data.get('order_book_to_bill', 0) > 1.5 and data.get('vol_spike_50d', 0) > 2.0:
            triggers.append("VALUE BREAKOUT")
            
        # TRIGGER 3: Perfect Storm (De-leveraging + Order Book)
        if data.get('debt_change_pct', 0) < -10 and data.get('ob_change_pct', 0) > 10:
            triggers.append("PERFECT STORM")
            
        # TRIGGER 5: Institutional Accumulation
        if data.get('vol_spike_50d', 0) > 3.0 and data.get('delivery_pct', 0) > 60:
            triggers.append("INSTITUTIONAL ACCUMULATION")

        # TRIGGER 2: Technical Breakout (MACD BUY + Supertrend BUY + above-avg vol)
        _macd_sp = str(data.get('macd_signal', '')).upper()
        _st_sp   = str(data.get('supertrend', '')).upper()
        if 'BUY' in _macd_sp and 'BUY' in _st_sp and data.get('vol_spike_50d', 0) > 1.5:
            triggers.append("TECHNICAL BREAKOUT")

        # TRIGGER 4: RSI Accumulation Zone with volume surge
        _rsi_sp = float(data.get('rsi', data.get('rsi_14', 50)) or 50)
        if 45 < _rsi_sp <= 65 and data.get('vol_spike_50d', 0) > 2.0:
            triggers.append("RSI ACCUMULATION")

        return {
            "score": len(triggers),
            "tags": triggers,
            "label": self._get_label(len(triggers))
        }

    def _get_label(self, count):
        if count >= 5: return "RARE CONFLUENCE"
        if count >= 3: return "STRONG SETUP"
        return "WATCH"