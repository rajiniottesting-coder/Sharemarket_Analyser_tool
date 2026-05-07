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
        # v11.0.2 — verdict streaks (feature B + C)
        'consecutive_avoid_quarters': 0,
        'consecutive_recovery_quarters': 0,
        'turnaround_candidate': False,
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
        # v12.7 (#8 FIX): pre-fix mood was "BULLISH" if nifty > sma200 else
        # "BEARISH", but with NIFTY 50 not ingested into daily_prices both
        # values returned 0 → mood was always BEARISH (misleading). Now
        # render "—" when nifty data is unavailable so users aren't shown
        # a fabricated regime call.
        # v13.x: extend the same honesty principle to the second header line —
        # Nifty / Sensex / VIX values are static placeholders in master_funnel
        # (sensex hardcoded to 0, vix to 12.0, nifty_close not ingested). Pre-
        # v13.x they printed as "0" / "12.0" alongside a "—" mood, mixing
        # honest absence with fake numeric precision. Now render "—" for any
        # of these that are zero/missing; FII (real data from F&O participant
        # table) keeps its numeric format.
        nifty  = self.mkt.get('nifty_close', 0)
        sma200 = self.mkt.get('nifty_200d', 0)
        sensex = self.mkt.get('sensex_close', 0)
        vix    = self.mkt.get('vix', 0)
        fii    = self.mkt.get('fii_net', 0)
        if nifty > 0 and sma200 > 0:
            mood = "BULLISH" if nifty > sma200 else "BEARISH"
        else:
            mood = "—"   # no NIFTY data available

        # v13.x: "—" for unavailable market scalars, retain numeric for real data
        nifty_disp  = nifty  if (isinstance(nifty,  (int, float)) and nifty  > 0) else "—"
        sensex_disp = sensex if (isinstance(sensex, (int, float)) and sensex > 0) else "—"
        vix_disp    = vix    if (isinstance(vix,    (int, float)) and vix    > 0) else "—"
        # FII can be legitimately negative (net selling); render "—" only if 0
        # (no record in fo_participant_data) or non-numeric.
        if isinstance(fii, (int, float)) and fii != 0:
            fii_disp = f"₹{fii}Cr"
        else:
            fii_disp = "—"

        header = (
            f"HEADER: NSE/BSE Research | {self.today_str} | {mood}\n"
            f"Nifty: {nifty_disp} | Sensex: {sensex_disp} | "
            f"VIX: {vix_disp} | FII: {fii_disp}\n"
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
        # v13.x fix: previously this sort returned the global top-5 by spike
        # count regardless of verdict, so OVERVALUED / NEUTRAL stocks could
        # appear in a section labelled "BUY". Filter to BUY first, then sort.
        # Substring match ("BUY" in verdict) tolerates the dotted display
        # variants like "BUY ●●●" / "BUY ○○" emitted by ScoringEngine.
        _buy_only = self.df[self.df['verdict'].astype(str).str.contains(
            'BUY', case=False, na=False, regex=False)]
        top_buys = _buy_only.sort_values(
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
        # v13.x fix: previously filtered `composite_score < 30` and capped
        # at head(2) with a hardcoded "2 EXIT ALERTS" title. Two problems:
        # (1) the score-only filter missed AVOID-verdict stocks scoring 30-37
        # (the system's actual exit signal), and (2) the title was
        # hardcoded "2" regardless of how many stocks qualified. Aligned the
        # filter to the section's intent — list AVOID verdicts (the system's
        # explicit exit recommendation) — and made the count dynamic. Cap
        # at 5 to keep the report compact; pipeline rarely produces > 5.
        # Substring match ('AVOID' in verdict) tolerates dotted display
        # variants like 'AVOID ●●●' / 'AVOID ●○○ (thin data)'.
        _avoid_mask = self.df['verdict'].astype(str).str.contains(
            'AVOID', case=False, na=False, regex=False)
        # Sort weakest first (lowest score → most urgent to exit)
        exits = self.df[_avoid_mask].sort_values(
            by='composite_score', ascending=True).head(5)
        _exit_count = len(exits)
        if _exit_count == 0:
            report.append("\nSECTION F — EXIT ALERTS")
        else:
            report.append(f"\nSECTION F — {_exit_count} EXIT ALERT" +
                          ("S" if _exit_count != 1 else ""))
        report.append(self._format_list(exits, ['symbol', 'verdict', 'guard_reasons']))

        # SECTION G: SMART MONEY
        report.append("SECTION G — SMART MONEY SUMMARY")
        sm = self.df[self.df['smart_money_signals'].astype(str).str.len() > 0].head(5)
        report.append(self._format_list(sm, ['symbol', 'smart_money_signals']))

        # v11.0.2 — SECTION H: TURNAROUND CANDIDATES (feature C)
        # Stocks that have recovered for ≥2 consecutive quarters after an
        # AVOID streak. Surfaces stocks worth re-evaluating manually.
        report.append("\nSECTION H — TURNAROUND CANDIDATES")
        try:
            ta_mask = self.df['consecutive_recovery_quarters'].fillna(0).astype(int) >= 2
            ta_stocks = self.df[ta_mask].sort_values(
                by='consecutive_recovery_quarters', ascending=False).head(5)
        except Exception:
            ta_stocks = self.df.iloc[0:0]  # empty same shape
        report.append(self._format_list(
            ta_stocks,
            ['symbol', 'verdict', 'composite_score', 'consecutive_recovery_quarters']))

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