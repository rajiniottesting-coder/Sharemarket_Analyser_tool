import os
import glob
import datetime
import sys
import pytz
import pandas as pd

# --- SECTION 1: SYSTEM & DATA IMPORTS ---
from orchestrator import gate_check
from harvester import (
    download_nse_bhavcopy, download_bse_bhavcopy, 
    download_nse_delivery, download_bse_delivery,
    download_nse_sme_bhavcopy, download_bse_sme_bhavcopy,
    download_nse_fo_participant_data
)
from data_bridge import (
    save_to_database, get_historical_quarter_data, 
    get_symbol_history, get_nifty_52w_high_from_db,
    get_today_consolidated_data, get_latest_fii_net_cash, get_nifty_200_sma
)

# --- SECTION 0 & 3: SCREENING & ANALYTICS ---
from pre_screener import stage_1_filter, stage_2_fundamental_scorer
from priority_ranker import get_top_100_candidates
from v7_analysis_engine import V7AnalysisEngine
from ownership_tracker import analyze_ownership_trends
from forensics_engine import ForensicsEngine
from rotation_engine import SectorRotationRadar
from db_maintenance import enforce_circular_queue
from intel_fetcher import fetch_latest_intelligence 

# # --- SECTION 7 & 8: AI & FORMATTING ---
from ai_analyst import get_ai_analysis
from report_formatter import ReportFormatter

def cleanup_temp_files():
    """Section 12: Pre-pipeline physical file cleanup."""
    patterns = ["*.zip", "*.csv", "*.DAT"]
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            try: os.remove(filepath)
            except: pass

def run_master_pipeline():
    cleanup_temp_files()

    today = datetime.datetime.now(ist)

    # 1. HARVEST (Harvester does the downloading)
    n_m = download_nse_bhavcopy(today)
    n_s = download_nse_sme_bhavcopy(today)
    b_m = download_bse_bhavcopy(today)
    b_s = download_bse_sme_bhavcopy(today)

    # 2. CONSOLIDATE (Bridge does the merging)
    all_stocks = get_today_consolidated_data(today, n_m, n_s, b_m, b_s)

    # 3. SAVE
    save_to_database(all_stocks)

    # --- SECTION 12B: INSTITUTIONAL GATEKEEPER ---
    gate_result = gate_check()
    if not gate_result["run"]:
        from email_service import send_analysis_email
        send_analysis_email(is_skip=True, skip_reason=gate_result["reason"])
        return

    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.datetime.now(ist)
    
    try:
        # --- SECTION 1: MULTI-STREAM HARVESTING ---
        print("🚀 [Section 1] Harvesting Market Streams...")
        raw_nse = download_nse_bhavcopy(today)
        raw_bse = download_bse_bhavcopy(today)
        nse_deliv = download_nse_delivery(today)
        bse_deliv = download_bse_delivery(today)
        sme_nse = download_nse_sme_bhavcopy(today)
        sme_bse = download_bse_sme_bhavcopy(today)
        fo_data = download_nse_fo_participant_data(today)

        # --- SECTION 3J & 3K: SMART MONEY HARVEST ---
        print("🕵️ [Section 3J/K] Scraping Bulk Deals & Insider Trading...")
        try:
            from smart_money import SmartMoneyScraper
            scraper = SmartMoneyScraper()
            bulk_deals_df = scraper.fetch_nse_bulk_deals()
            insider_trades_df = scraper.fetch_sast_insider_trading()
        except Exception as e:
            print(f"⚠️ Smart Money Scrape warning: {e}")

        # --- SECTION 1.2: DATABASE SYNC (Feeding the Bridge) ---
        save_to_database(
            nse_data=raw_nse, bse_data=raw_bse, 
            nse_del=nse_deliv, bse_del=bse_deliv,
            sme_nse=sme_nse, sme_bse=sme_bse,
            participant_data=fo_data)

        if not bulk_deals_df.empty:
            save_to_database(bulk_deals_df, table='bulk_deals')
        if not insider_trades_df.empty: 
            save_to_database(insider_trades_df, table='insider_trades')

        # --- SECTION 0: PRE-SCREENING FUNNEL (STAGES 1-3) ---
        print("🔍 [Section 0] Executing Funnel Stages 1-3...")
        all_stocks = get_today_consolidated_data(today)
        stage1_candidates = stage_1_filter(all_stocks.to_dict('records'))
        stage2_qualified = stage_2_fundamental_scorer(pd.DataFrame(stage1_candidates))
        final_100_df = get_top_100_candidates(stage2_qualified)
        final_100_list = final_100_df.to_dict('records')

        # --- SECTION 6: CORE ANALYTICAL ENGINES ---
        v7_engine = V7AnalysisEngine()
        forensics = ForensicsEngine()
        rotation = SectorRotationRadar()
        formatter = ReportFormatter() # Initialize Formatter
        historical_map = get_historical_quarter_data([s['symbol'] for s in final_100_list])

        for stock in final_100_list:
            # --- SECTION 2: LATEST INTELLIGENCE ---
            stock['intel_queries'] = fetch_latest_intelligence(stock['symbol'], stock.get('sector'))
            
            # --- SECTION 3J: BULK DEAL SENTIMENT ---
            if not bulk_deals_df.empty:
                deals = bulk_deals_df[bulk_deals_df['symbol'] == stock['symbol']]
                buy_vol = deals[deals['type'] == 'BUY']['quantity'].sum()
                sell_vol = deals[deals['type'] == 'SELL']['quantity'].sum()
                stock['net_inst_flow'] = buy_vol - sell_vol
                stock['smart_money_sentiment'] = "ACCUMULATION" if stock['net_inst_flow'] > 0 else "NEUTRAL"
            else:
                stock['net_inst_flow'] = 0
                stock['smart_money_sentiment'] = "NEUTRAL"

            # --- SECTION 3K: INSIDER BUYING ---
            if not insider_trades_df.empty:
                ins_buys = insider_trades_df[(insider_trades_df['symbol'] == stock['symbol']) & 
                                             (insider_trades_df['mode'] == 'Market Purchase')]
                stock['insider_buy_alert'] = "YES" if not ins_buys.empty else "NO"
            else:
                stock['insider_buy_alert'] = "NO"
            
            # --- SECTION 3A/3C: VALUATION, GROWTH ---
            stock.update(v7_engine.apply_section_3A_valuation(stock))
            stock.update(v7_engine.apply_section_3C_growth(stock))

            # --- SECTION 3B/3D/3G: DEEP FORENSICS & AUDIT ---
            stock.update(forensics.calculate_accounting_forensics(stock))

            # --- SECTION 3E: CAPITAL ALLOCATION ---
            roce, wacc = stock.get('roce', 0), 11.5
            stock['wealth_creation_spread'] = roce - wacc
            stock['allocation_tag'] = "WEALTH CREATOR" if roce > wacc else "VALUE ERODER"
            
            # --- SECTION 3F: OWNERSHIP TRENDS ---
            stock.update(analyze_ownership_trends(stock, historical_map.get(stock['symbol'])))
            
            # --- SECTION 3H: ANTI-TRIGGER SAFETY GUARD ---
            guard = v7_engine.apply_section_3H_guards(stock)
            stock['spike_suppressed'] = guard['suppressed']
            stock['guard_reasons'] = ", ".join(guard['reasons'])

            # --- SECTION 3I: EARLY DETECTION SCORING ---
            early_data = v7_engine.calculate_section_3I_early_score(stock)
            stock['early_entry_score'] = early_data['early_score']
            stock['early_mover_badge'] = early_data['badge']

            # --- SECTION 3L: SECTORAL ROTATION ---
            stock['rotation_stage'] = rotation.calculate_rotation_stage(
                stock.get('sector_return', 0), stock.get('nifty_return', 0), 'neutral'
            )

            # --- SECTION 4: BALANCE SHEET HEALTH ---
            from bs_engine import BalanceSheetEngine
            bs_report = BalanceSheetEngine().analyze_bs_health(stock, historical_map.get(stock['symbol'], {}))
            stock['bs_status'] = bs_report['status']
            stock['bs_flags'] = ", ".join(bs_report['flags'])

        # --- SECTION 5: MOMENTUM & TECHNICALS ---
        print("📈 [Section 5] Calculating Momentum Deltas...")
        for stock in final_100_list:
            history = get_symbol_history(stock['symbol'])
            if not history.empty and len(history) >= 41:
                curr = history.iloc[-1]['close']
                stock['2w_chg'] = ((curr - history.iloc[-11]['close']) / history.iloc[-11]['close']) * 100
                stock['4w_chg'] = ((curr - history.iloc[-21]['close']) / history.iloc[-21]['close']) * 100
                stock['8w_chg'] = ((curr - history.iloc[-41]['close']) / history.iloc[-41]['close']) * 100

        # --- SECTION 5B: MULTI-MODEL FAIR VALUE ---
        from fair_value_engine import FairValueEngine
        fv_engine = FairValueEngine()
        for stock in final_100_list:
            stock.update(fv_engine.calculate_all_models(stock))

        # --- SECTION 7 & 8: AI INVESTOR CARDS ---
        print("🤖 [Section 7/8] Generating AI Cards & Formatting...")
        # Get AI analysis text block
        investor_cards_text = get_ai_analysis(pd.DataFrame(final_100_list))
        
        # Apply formatting to create structured cards
        final_cards_for_display = []
        for stock in final_100_list:
            structured_card = formatter.format_investor_card(stock)
            final_cards_for_display.append(structured_card)
            # Link to Excel Block H Summary Note
            stock['Analysis_Summary_Block_H'] = stock.get('ai_summary_note', "See PDF report.")

        # --- SECTION 9 & 10: REPORTING & DELIVERY ---
        print("📝 [Section 9/10] Constructing Final Deliverables...")
        from excel_generator import ExcelGeneratorV6
        from daily_report_generator import DailyReportGenerator

        market_stats = {
            'nifty_52w_high': get_nifty_52w_high_from_db(),
            'fii_net': get_latest_fii_net_cash(),
            'nifty_200d': get_nifty_200_sma(),
            'vix': 12.0 # Standard threshold
        }

        # Generate Dashboard (Section 10)
        ExcelGeneratorV6(final_100_list, today.strftime('%Y%m%d')).generate_excel_reports()
        
        # Generate Summary (Section 9)
        report_txt = DailyReportGenerator(final_100_list, market_stats).generate_research_report()
        
        # Combine everything into the Final Daily Analysis File
        report_filename = f"Daily_Analysis_Report_{today.strftime('%Y%m%d')}.txt"
        with open(report_filename, "w", encoding="utf-8") as f:
            f.write(report_txt)
            f.write("\n\n" + "="*60 + "\n\n")
            f.write("--- QUICK INVESTOR CARDS (SECTION 8) ---\n")
            f.write("\n\n".join(final_cards_for_display))

        # --- SECTION 12: FINAL EMAIL DELIVERY ---
        from email_service import send_analysis_email
        attachments = ['Full_Dashboard.xlsx', report_filename]
        send_analysis_email(attachments=attachments)

        # --- SECTION 13: MAINTENANCE ---
        enforce_circular_queue('market_data.db')
        print(f"✅ Pipeline Execution Success.")

    except Exception as e:
        print(f"❌ CRITICAL FAILURE: {str(e)}")
        from email_service import send_analysis_email
        send_analysis_email(is_error=True, error_msg=str(e))

if __name__ == "__main__":
    run_master_pipeline()