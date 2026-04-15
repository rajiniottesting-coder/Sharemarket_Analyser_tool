import pandas as pd
from datetime import datetime

class DailyReportGenerator:
    def __init__(self, data, market_stats):
        self.df = pd.DataFrame(data)
        self.mkt = market_stats # VIX, Nifty, FII data
        self.today_str = datetime.now().strftime('%Y-%m-%d')

    def generate_research_report(self):
        """
        Implements SECTION 9: Mandatory Daily Report Format (20:30 IST).
        """
        report = []

        # --- HEADER (Section 9) ---
        mood = "BULLISH" if self.mkt['nifty_close'] > self.mkt['nifty_200d'] else "BEARISH"
        header = (
            f"HEADER: NSE/BSE Research | {self.today_str} | {mood}\n"
            f"Nifty: {self.mkt['nifty_close']} | Sensex: {self.mkt['sensex_close']} | "
            f"VIX: {self.mkt['vix']} | FII: ₹{self.mkt['fii_net']}Cr\n"
        )
        report.append(header + "="*60 + "\n")

        # --- SECTION A: EARLY MOVERS TODAY (Early Entry Score >= 70) ---
        early_movers = self.df[self.df['early_entry_score'] >= 70].sort_values(by='early_entry_score', ascending=False)
        report.append("SECTION A — EARLY MOVERS TODAY")
        report.append(self._format_list(early_movers, ['symbol', 'early_entry_score', 'sector']))

        # --- SECTION B: TOP 5 BUY CANDIDATES (Spike DESC, MoS DESC) ---
        top_buys = self.df.sort_values(by=['spike_count', 'mos_pct'], ascending=[False, False]).head(5)
        report.append("SECTION B — TOP 5 BUY CANDIDATES")
        report.append(self._format_list(top_buys, ['symbol', 'verdict', 'mos_pct']))

        # --- SECTION C: ACTIVE SPIKE ALERTS (fired today) ---
        spikes = self.df[self.df['spike_count'] >= 1]
        report.append("SECTION C — ACTIVE SPIKE ALERTS")
        report.append(self._format_list(spikes, ['symbol', 'spike_triggers']))

        # --- SECTION D: SECTOR ROTATION UPDATE (Stages 1–4) ---
        report.append("SECTION D — SECTOR ROTATION UPDATE")
        for stage in [4, 3, 2, 1]:
            sectors = self.df[self.df['rotation_stage'] == stage]['sector'].unique()[:3]
            report.append(f"Stage {stage}: {', '.join(sectors) if len(sectors)>0 else 'None'}")

        # --- SECTION E: BSE SME WATCH (volume awakening) ---
        sme_watch = self.df[(self.df['exchange_tag'] == 'BSE_SME') & (self.df['vol_ratio'] > 2)]
        report.append("\nSECTION E — BSE SME WATCH")
        report.append(self._format_list(sme_watch, ['symbol', 'vol_ratio']))

        # --- SECTION F: 2 EXIT ALERTS (Score dropped or SL breached) ---
        exits = self.df[self.df['composite_score'] < 30].head(2)
        report.append("SECTION F — 2 EXIT ALERTS")
        report.append(self._format_list(exits, ['symbol', 'verdict', 'guard_reasons']))

        # --- SECTION G: SMART MONEY SUMMARY (bulk/block/promoter) ---
        report.append("SECTION G — SMART MONEY SUMMARY")
        sm_signals = self.df[self.df['smart_money_signals'].str.len() > 0].head(5)
        report.append(self._format_list(sm_signals, ['symbol', 'smart_money_signals']))

        return "\n".join(report)

    def _format_list(self, sub_df, cols):
        if sub_df.empty: return "No candidates identified today.\n"
        lines = []
        for _, row in sub_df.iterrows():
            lines.append(" | ".join([f"{c.upper()}: {row[c]}" for c in cols]))
        return "\n".join(lines) + "\n"