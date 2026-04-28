import re
import pandas as pd
from database.data_bridge import get_today_consolidated_data, get_symbol_history
from reporting.excel_generator import ExcelGeneratorV6
from reporting.daily_report_generator import DailyReportGenerator

class CommandParser:
    def __init__(self, data_context=None):
        # We load the latest analysis results as context
        self.ctx = data_context if data_context is not None else []
        self.df = pd.DataFrame(self.ctx)

    def execute(self, user_input):
        cmd = user_input.lower().strip()

        # --- ANALYSIS COMMANDS ---
        if cmd.startswith("analyse "):
            symbol = cmd.replace("analyse ", "").upper()
            return self._get_full_card(symbol)
        
        if cmd.startswith("why ") or cmd.startswith("explain "):
            symbol = re.sub(r'why |explain ', '', cmd).upper()
            return self._get_block_h(symbol)

        # --- SCREENING COMMANDS ---
        if "early movers today" in cmd:
            filtered = self.df[self.df['early_entry_score'] >= 50]
            return self._format_summary(filtered, "EARLY MOVERS (Score >= 50)")

        if "momentum scan" in cmd:
            filtered = self.df[(self.df['8w_chg'] > 15) & (self.df['composite_score'] > 65)]
            return self._format_summary(filtered, "MOMENTUM RADAR (8W > 15%)")

        if "deep value list" in cmd:
            filtered = self.df[(self.df['mos_pct'] > 25) & (self.df['composite_score'] > 70)]
            return self._format_summary(filtered, "DEEP VALUE (MoS > 25%)")

        # --- EXCEL COMMANDS ---
        if cmd == "generate excel":
            from datetime import datetime
            engine = ExcelGeneratorV6(self.ctx, datetime.now().strftime('%Y%m%d'))
            master, gold = engine.generate_excel_reports()
            return f"✅ Excel Files Generated: \n1. {master}\n2. {gold}"

        # --- FILTER & SORT COMMANDS ---
        if "sort by " in cmd:
            sort_key = cmd.replace("sort by ", "")
            mapping = {"early": "early_entry_score", "score": "composite_score", "mos": "mos_pct", "8week": "8w_chg"}
            col = mapping.get(sort_key, "composite_score")
            sorted_df = self.df.sort_values(by=col, ascending=False).head(10)
            return self._format_summary(sorted_df, f"TOP 10 BY {sort_key.upper()}")

        return "⚠️ Command not recognized. Use 'help' to see Section 11 triggers."

    def _get_block_h(self, symbol):
        stock = self.df[self.df['symbol'] == symbol]
        if stock.empty: return f"❌ {symbol} not found in today's analysis."
        return f"📝 **ANALYSIS SUMMARY FOR {symbol}**:\n\n{stock.iloc[0]['Analysis_Summary_Block_H']}"

    def _format_summary(self, df, title):
        if df.empty: return f"No stocks currently match the {title} criteria."
        res = [f"📊 **{title}**", "-"*30]
        for _, r in df.iterrows():
            res.append(f"{r['symbol']} | Score: {r['composite_score']} | Upside: {r['upside']}%")
        return "\n".join(res)