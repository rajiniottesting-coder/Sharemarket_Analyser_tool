import pandas as pd
from datetime import datetime

class DailyReportGenerator:
    # All columns this generator accesses — filled with safe defaults if absent
    REQUIRED_COLS = {
        'early_entry_score': 0, 'spike_count': 0, 'mos_pct': 0.0,
        'composite_score': 0, 'rotation_stage': 'NEUTRAL',
        'smart_money_signals': '', 'vol_ratio': 1.0,
        'spike_triggers': '', 'sector': 'General',
        'verdict': 'WATCHLIST', 'exchange_tag': 'NSE',
        'guard_reasons': '', 'symbol': '',
    }

    def __init__(self, data, market_stats):
        self.df = pd.DataFrame(data) if data else pd.DataFrame()
        self.mkt = market_stats
        self.today_str = datetime.now().strftime('%Y-%m-%d')
        # Ensure all required columns exist
        for col, default in self.REQUIRED_COLS.items():
            if col not in self.df.columns:
                self.df[col] = default

    def generate_research_report(self):
        """Implements SECTION 9: Mandatory Daily Report Format."""
        report = []

        # HEADER
        nifty  = self.mkt.get('nifty_close', 0)
        sma200 = self.mkt.get('nifty_200d', 0)
        mood   = "BULLISH" if nifty > sma200 else "BEARISH"
        header = (
            f"HEADER: NSE/BSE Research | {self.today_str} | {mood}\n"
            f"Nifty: {nifty} | Sensex: {self.mkt.get('sensex_close', 0)} | "
            f"VIX: {self.mkt.get('vix', 0)} | FII: ₹{self.mkt.get('fii_net', 0)}Cr\n"
        )
        report.append(header + "=" * 60 + "\n")

        if self.df.empty:
            report.append("No stocks analysed today.")
            return "\n".join(report)

        # SECTION A: EARLY MOVERS
        early_movers = self.df[self.df['early_entry_score'] >= 50].sort_values(
            by='early_entry_score', ascending=False)
        report.append("SECTION A — EARLY MOVERS TODAY")
        report.append(self._format_list(early_movers, ['symbol', 'early_entry_score', 'sector']))

        # SECTION B: TOP 5 BUY CANDIDATES
        top_buys = self.df.sort_values(
            by=['spike_count', 'mos_pct'], ascending=[False, False]).head(5)
        report.append("SECTION B — TOP 5 BUY CANDIDATES")
        report.append(self._format_list(top_buys, ['symbol', 'verdict', 'mos_pct']))

        # SECTION C: SPIKE ALERTS
        spikes = self.df[self.df['spike_count'] >= 1]
        report.append("SECTION C — ACTIVE SPIKE ALERTS")
        report.append(self._format_list(spikes, ['symbol', 'spike_triggers']))

        # SECTION D: SECTOR ROTATION
        # rotation_stage stores strings like "STAGE 4 — DISTRIBUTION", not ints.
        # Match by stage label substring to roll up sectors per stage correctly.
        report.append("SECTION D — SECTOR ROTATION UPDATE")
        for stage_num, stage_label in [(4, 'STAGE 4'), (3, 'STAGE 3'), (2, 'STAGE 2'), (1, 'STAGE 1')]:
            mask = self.df['rotation_stage'].astype(str).str.contains(stage_label, na=False)
            sectors = self.df.loc[mask, 'sector'].dropna().unique()[:3]
            report.append(f"Stage {stage_num}: {', '.join(str(s) for s in sectors) if len(sectors) > 0 else 'None'}")

        # SECTION E: BSE SME WATCH
        sme_watch = self.df[
            (self.df['exchange_tag'].astype(str).str.contains('SME', na=False)) &
            (self.df['vol_ratio'] > 2)]
        report.append("\nSECTION E — BSE SME WATCH")
        report.append(self._format_list(sme_watch, ['symbol', 'vol_ratio']))

        # SECTION F: EXIT ALERTS
        exits = self.df[self.df['composite_score'] < 30].head(2)
        report.append("SECTION F — 2 EXIT ALERTS")
        report.append(self._format_list(exits, ['symbol', 'verdict', 'guard_reasons']))

        # SECTION G: SMART MONEY
        report.append("SECTION G — SMART MONEY SUMMARY")
        sm = self.df[self.df['smart_money_signals'].astype(str).str.len() > 0].head(5)
        report.append(self._format_list(sm, ['symbol', 'smart_money_signals']))

        return "\n".join(report)

    def _format_list(self, sub_df, cols):
        if sub_df is None or sub_df.empty:
            return "No candidates identified today.\n"
        lines = []
        for _, row in sub_df.iterrows():
            parts = []
            for c in cols:
                val = row.get(c, "—") if hasattr(row, "get") else row[c]
                parts.append(f"{c.upper()}: {val}")
            lines.append(" | ".join(parts))
        return "\n".join(lines) + "\n"