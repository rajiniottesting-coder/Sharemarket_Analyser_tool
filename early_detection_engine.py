class EarlyDetectionEngine:
    def calculate_early_score(self, data, context):
        """
        SECTION 3I: Compute EARLY ENTRY SCORE (0-100).
        Goal: Find stocks 4-12 weeks BEFORE institutional coverage.
        """
        score = 0
        signals = []

        # Signal 2: SME-to-Mainboard Migration (8 pts)
        if data.get('exchange_tag') == 'BSE_SME' and data.get('mcap') > 240:
            score += 8
            signals.append("SME MIGRATION WATCH")

        # Signal 4: Promoter Buying Before Results (9 pts)
        if data.get('promoter_buying_30d') and data.get('days_to_results', 100) < 60:
            score += 9
            signals.append("PRE-RESULT INSIDER BUYING")

        # Signal 12: Cross-Exchange Discovery (10 pts)
        if data.get('exchange_tag') in ['BSE_ONLY', 'BSE_SME']:
            score += 10
            signals.append("CROSS-EXCHANGE DISCOVERY")

        # ... (Implementation for all 12 signals) ...

        return {
            "total_score": score,
            "badge": "EARLY MOVER" if score >= 70 else None,
            "label": self._get_label(score),
            "active_signals": signals
        }

    def _get_label(self, score):
        if score >= 80: return "EARLY MOVER — Act before the crowd"
        if score >= 60: return "AHEAD OF CONSENSUS"
        return "EMERGING"