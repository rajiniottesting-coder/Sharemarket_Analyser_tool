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
            f"{stock.get('symbol', '')} | {stock.get('company_name', '')} | {stock.get('sector', 'General')}\n"
            f"[{stock.get('verdict', 'WATCHLIST')}] [{stock.get('cap_badge', 'Unknown')}] [{stock.get('exchange_tag', 'NSE')}]"
        )
        if stock.get('early_entry_score', 0) >= 70:
            header += " [EARLY MOVER]"
        if stock.get('new_market_entry'):
            header += " [NEW MARKET]"

        # SPIKE BANNER (Section 8)
        spike_banner = ""
        if stock.get('spike_count', 0) >= 1:
            spike_banner = f"SPIKE SCORE {stock.get('spike_count', 0)}/6 : " + " ".join([f"[{t}]" for t in stock.get('spike_triggers', [])])

        # LINE 2: PRICE SNAPSHOT
        price_snapshot = (
            f"CMP: ₹{stock.get('close', 0)} (±{stock.get('day_change', 0)}% today) | "
            f"52W: ₹{stock.get('low_52w', 0)}–₹{stock.get('high_52w', 0)} | Vol: {stock.get('vol_ratio', 1.0)}x avg\n"
            f"Weekly: 2W: {stock.get('2w_chg', '—')}%  4W: {stock.get('4w_chg', '—')}%  "
            f"6W: {stock.get('6w_chg', '—')}%  8W: {stock.get('8w_chg', '—')}%"
        )

        # BLOCK A: FAIR VALUE (Boxed format)
        block_a = (
            f"┌────────────────────────────────────────────────────┐\n"
            f"│ Fair Value (CFV): ₹{stock.get('cfv', 0)}  Range: ₹{stock.get('cfv_low', 0)} – ₹{stock.get('cfv_high', 0)}     │\n"
            f"│ CMP is {abs(stock.get('mos_pct', 0))}% {'CHEAP' if stock.get('mos_pct', 0) > 0 else 'EXPENSIVE'} vs fair value       │\n"
            f"│ MoS: {stock.get('mos_pct', 0)}%  [{stock.get('mos_label', '—')}]        │\n"
            f"│ Upside to FV: +{stock.get('upside', 0)}%  (₹{stock.get('upside_rs', 0)} per share)                │\n"
            f"└────────────────────────────────────────────────────┘"
        )

        # BLOCK B: METRICS STRIP (Section 8)
        block_b = (
            f"PE: {stock.get('pe', 0)}x | Earn Yld: {stock.get('earnings_yield', 0)}% | P/CF: {stock.get('p_cf', 0)}x | PEG: {stock.get('peg', 0)}\n"
            f"PB: {stock.get('pb', 0)}x | ROE: {stock.get('roe', 0)}% | D/E: {stock.get('de', 0)} | FCF Yld: {stock.get('fcf_yield', 0)}%\n"
            f"Rev Gr: +{stock.get('rev_growth', 0)}% | PAT Gr: +{stock.get('pat_growth', 0)}% | Div Yld: {stock.get('div_yield', 0)}% | F-Score: {stock.get('f_score', 0)}/9"
        )
        # Add Order Book metrics if applicable
        if stock.get('order_book'):
            block_b += f"\nOB/Bill: {stock.get('ob_bill', 0)}x | Pipeline: {stock.get('pipeline', 0)}x | L1 Wins: {stock.get('l1_wins', 0)}"

        # BLOCK D: EARLY DETECTION PANEL (Mandatory for every card)
        block_d = (
            f"Early Entry Score: {stock.get('early_entry_score', 0)}/100  [{stock.get('early_label', 'EMERGING')}]\n"
            f"Sector: {stock.get('sector', 'General')} — Stage {stock.get('rotation_stage', 'NEUTRAL')}\n"
            f"Smart Money: {', '.join(stock.get('smart_money_signals', ['None']))}\n"
            f"Top Early Signal: {stock.get('top_early_signal', '—')}"
        )

        # BLOCK E: MARKET UNCERTAINTY (Mandatory when VIX > 18)
        block_e = ""
        if stock.get('storm_score') != "N/A":
            block_e = (
                f"Storm Score: {stock.get('storm_score', 0)}/10  [{stock.get('storm_label', 'N/A')}]\n"
                f"VIX: {stock.get('vix', 0)} | FII 7D: ₹{stock.get('fii_7d', 0)}Cr | Nifty 200D: {stock.get('nifty_200d_pos', '—')}\n"
                f"\"{stock.get('storm_comment', '—')}\""
            )

        # BLOCK G: BALANCE SHEET (Only if BS Flag != HEALTHY)
        block_g = ""
        if stock.get('bs_status') in ['WATCH', 'ALERT']:
            block_g = f"BS [{stock.get('bs_status', 'HEALTHY')}]: {stock.get('bs_output', '—')}"

        # Final Assembly
        card_output = f"{header}\n{spike_banner}\n{'-'*54}\n{price_snapshot}\n{'-'*54}\n{block_a}\n{'-'*54}\n{block_b}\n{'-'*54}\n{block_d}"
        if block_e: card_output += f"\n{'-'*54}\n{block_e}"
        if block_g: card_output += f"\n{'-'*54}\n{block_g}"
        
        return card_output