import sys
import pandas as pd
from datetime import datetime
from reporting.command_parser import CommandParser
from master_funnel import run_master_pipeline # To refresh context
from analysis.v7_analysis_engine import V7AnalysisEngine
from database.data_bridge import get_symbol_history

class StockChatAI:
    def __init__(self):
        print("🚀 Initializing Gemini V7 Stock Engine...")
        # Load existing analysis results to avoid re-running the 16-hour scan for every query
        try:
            self.context_data = pd.read_sql("SELECT * FROM latest_analysis_results", "sqlite:///market_data.db")
            self.parser = CommandParser(self.context_data.to_dict('records'))
        except:
            print("⚠️ No pre-scanned data found. Running in 'Deep-Search' mode per symbol.")
            self.context_data = pd.DataFrame()
            self.parser = CommandParser([])

    def handle_interactive_session(self):
        print(f"\n✨ System Online | Mode: Interactive Analyst | Date: {datetime.now().strftime('%Y-%m-%d')}")
        print("Type 'analyse [SYMBOL]', 'why [SYMBOL]', or 'exit' to quit.\n")
        
        while True:
            try:
                user_input = input("Rajkumar > ").strip()
                
                if user_input.lower() in ['exit', 'quit', 'bye']:
                    print("👋 Analysis session closed. Reports saved to /outputs.")
                    break
                
                if not user_input:
                    continue

                # Trigger the parser
                response = self.parser.execute(user_input)
                
                # Special Case: If user asks for a stock NOT in our Top 100 list
                if "not found" in str(response).lower() and "analyse" in user_input.lower():
                    symbol = user_input.split()[-1].upper()
                    response = self._run_on_demand_analysis(symbol)

                print(f"\nGemini > {response}\n")

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"❌ Error processing command: {e}")

    def _run_on_demand_analysis(self, symbol):
        """
        Executes the full V7 Logic chain for a single stock 
        that wasn't picked up in the daily Top 100 scan.
        """
        print(f"🔍 Stock '{symbol}' not in daily Top 100. Triggering Deep-Search Engine...")
        
        # 1. Fetch live data for this specific symbol
        history = get_symbol_history(symbol)
        if history.empty:
            return f"❌ Could not find trading data for {symbol}. Please check the ticker."

        # 2. Re-initialize V7 Engine
        engine = V7AnalysisEngine()
        
        # 3. Build a mock stock object and run engines
        # (This mimics the Step 6 logic from master_funnel for one stock)
        mock_stock = {'symbol': symbol, 'close': history.iloc[-1]['close']}
        analysis = engine.apply_section_3A_valuation(mock_stock)
        
        return (
            f"✅ **ON-DEMAND ANALYSIS: {symbol}**\n"
            f"Verdict: {analysis.get('verdict', 'NEUTRAL')} | "
            f"CFV: ₹{analysis.get('cfv', 0)} | "
            f"MoS: {analysis.get('mos_pct', 0)}%\n"
            f"Note: Run 'full report {symbol}' for deep technicals."
        )

if __name__ == "__main__":
    chat_app = StockChatAI()
    chat_app.handle_interactive_session()