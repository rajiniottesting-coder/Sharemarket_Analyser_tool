import pandas as pd

class ReportFormatter:
    def __init__(self):
        # Mandatory border and badge colors from Section 8
        self.colors = {
            "GOLD": "DEEP VALUE EARLY MOVER",
            "GREEN": "DEEP VALUE",
            "BLUE": "BUY / EARLY MOVER",
            "AMBER": "WATCH",
            "RED": "AVOID / EXIT"
        }

    def format_investor_card(self, stock):
        """
        Implements Section 8: Crisp Investor Card (Blocks A-G).
        Ensures strict adherence to the 'No Paragraphs' rule.
        """
        # LINE 1: IDENTITY HEADER
        header = (
            f"{stock['symbol']} | {stock['company_name']} | {stock['sector']}\n"
            f"[{stock['verdict']}] [{stock['cap_badge']}] [{stock['exchange_tag']}]"
        )
        if stock.get('early_entry_score', 0) >= 70:
            header += " [EARLY MOVER]"
        if stock.get('new_market_entry'):
            header += " [NEW MARKET]"

        # SPIKE BANNER (Section 8)
        spike_banner = ""
        if stock.get('spike_count', 0) >= 1:
            spike_banner = f"SPIKE SCORE {stock['spike_count']}/6 : " + " ".join([f"[{t}]" for t in stock.get('spike_triggers', [])])

        # LINE 2: PRICE SNAPSHOT
        price_snapshot = (
            f"CMP: ₹{stock['close']} (±{stock['day_change']}% today) | "
            f"52W: ₹{stock['low_52w']}–₹{stock['high_52w']} | Vol: {stock['vol_ratio']}x avg\n"
            f"Weekly: 2W: {stock.get('2w_chg', '—')}%  4W: {stock.get('4w_chg', '—')}%  "
            f"6W: {stock.get('6w_chg', '—')}%  8W: {stock.get('8w_chg', '—')}%"
        )

        # BLOCK A: FAIR VALUE (Boxed format)
        block_a = (
            f"┌────────────────────────────────────────────────────┐\n"
            f"│ Fair Value (CFV): ₹{stock['cfv']}  Range: ₹{stock['cfv_low']} – ₹{stock['cfv_high']}     │\n"
            f"│ CMP is {abs(stock['mos_pct'])}% {'CHEAP' if stock['mos_pct'] > 0 else 'EXPENSIVE'} vs fair value       │\n"
            f"│ MoS: {stock['mos_pct']}%  [{stock['mos_label']}]        │\n"
            f"│ Upside to FV: +{stock['upside']}%  (₹{stock['upside_rs']} per share)                │\n"
            f"└────────────────────────────────────────────────────┘"
        )

        # BLOCK B: METRICS STRIP (Section 8)
        block_b = (
            f"PE: {stock['pe']}x | Earn Yld: {stock['earnings_yield']}% | P/CF: {stock['p_cf']}x | PEG: {stock['peg']}\n"
            f"PB: {stock['pb']}x | ROE: {stock['roe']}% | D/E: {stock['de']} | FCF Yld: {stock['fcf_yield']}%\n"
            f"Rev Gr: +{stock['rev_growth']}% | PAT Gr: +{stock['pat_growth']}% | Div Yld: {stock['div_yield']}% | F-Score: {stock['f_score']}/9"
        )
        # Add Order Book metrics if applicable
        if stock.get('order_book'):
            block_b += f"\nOB/Bill: {stock['ob_bill']}x | Pipeline: {stock['pipeline']}x | L1 Wins: {stock['l1_wins']}"

        # BLOCK D: EARLY DETECTION PANEL (Mandatory for every card)
        block_d = (
            f"Early Entry Score: {stock['early_entry_score']}/100  [{stock['early_label']}]\n"
            f"Sector: {stock['sector']} — Stage {stock['rotation_stage']}\n"
            f"Smart Money: {', '.join(stock.get('smart_money_signals', ['None']))}\n"
            f"Top Early Signal: {stock.get('top_early_signal', '—')}"
        )

        # BLOCK E: MARKET UNCERTAINTY (Mandatory when VIX > 18)
        block_e = ""
        if stock.get('storm_score') != "N/A":
            block_e = (
                f"Storm Score: {stock['storm_score']}/10  [{stock['storm_label']}]\n"
                f"VIX: {stock['vix']} | FII 7D: ₹{stock['fii_7d']}Cr | Nifty 200D: {stock['nifty_200d_pos']}\n"
                f"\"{stock['storm_comment']}\""
            )

        # BLOCK G: BALANCE SHEET (Only if BS Flag != HEALTHY)
        block_g = ""
        if stock.get('bs_status') in ['WATCH', 'ALERT']:
            block_g = f"BS [{stock['bs_status']}]: {stock['bs_output']}"

        # Final Assembly
        card_output = f"{header}\n{spike_banner}\n{'-'*54}\n{price_snapshot}\n{'-'*54}\n{block_a}\n{'-'*54}\n{block_b}\n{'-'*54}\n{block_d}"
        if block_e: card_output += f"\n{'-'*54}\n{block_e}"
        if block_g: card_output += f"\n{'-'*54}\n{block_g}"
        
        return card_output