import os
import glob
import datetime
import pytz
import pandas as pd

# Core Pipeline Imports
from orchestrator import gate_check
from harvester import (
    download_nse_bhavcopy, download_bse_bhavcopy, 
    download_nse_delivery, download_bse_delivery,
    download_nse_sme_bhavcopy, download_bse_sme_bhavcopy,
    download_nse_fo_participant_data
)
from data_bridge import (
    save_to_database, 
    get_historical_quarter_data, 
    get_symbol_history, 
    get_nifty_52w_high_from_db,
    get_today_consolidated_data,
    get_latest_fii_net_cash, 
    get_nifty_200_sma
)
from pre_screener import stage_1_filter, stage_2_fundamental_scorer
from priority_ranker import get_top_100_candidates
from reconciler import reconcile_exchanges

# Section 3 Engine Imports
from v7_analysis_engine import V7AnalysisEngine
from ownership_tracker import analyze_ownership_trends
from forensics_engine import ForensicsEngine
from rotation_engine import SectorRotationRadar

# Section 2 & 3I Catalyst Search
from intel_fetcher import fetch_latest_intelligence 

def cleanup_temp_files():
    """Cleans up leftover .zip, .csv, or .DAT downloads from previous runs."""
    print("🧹 Running pre-pipeline physical file cleanup...")
    patterns = ["*.zip", "*.csv", "*.DAT"]
    deleted_count = 0
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            try:
                os.remove(filepath)
                deleted_count += 1
            except Exception as e:
                print(f"⚠️ Could not delete {filepath}: {e}")
    if deleted_count > 0:
        print(f"✅ Cleanup complete: Removed {deleted_count} temporary files.")

def run_master_pipeline():
    cleanup_temp_files()

    # Step 1: Execute Section 0 / 12B Gate Check
    if not gate_check():
        print("🛑 Pipeline halted by Gatekeeper.")
        return

    # Step 2: Set target date
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.datetime.now(ist)
    
    # --- FIX: Use consolidated data fetcher to avoid redundancy ---
    all_stocks = get_today_consolidated_data(today)
    
    # Step 3.1: Download Deliveries (kept for specific processing)
    nse_deliv = download_nse_delivery(today)
    bse_deliv = download_bse_delivery(today)

    # Step 4.1: Multi-Exchange Delivery Merge
    if nse_deliv is not None:
        all_stocks = pd.merge(all_stocks, nse_deliv, on='symbol', how='left')
    if bse_deliv is not None:
        all_stocks = pd.merge(all_stocks, bse_deliv, on='bse_code', how='left', suffixes=('', '_BSE'))
        all_stocks['delivery_pct'] = all_stocks['delivery_pct'].fillna(all_stocks.get('delivery_pct_BSE', 0))

    # Data Sanitization
    num_cols = all_stocks.select_dtypes(include=['number']).columns
    all_stocks[num_cols] = all_stocks[num_cols].fillna(0)
    all_stocks.drop(columns=['delivery_pct_BSE'], errors='ignore', inplace=True)
            
    # Step 5: Save Raw Data & Smart Money (Section 3J & 3K)
    save_to_database(all_stocks)
    from smart_money import SmartMoneyScraper
    scraper = SmartMoneyScraper()
    save_to_database(scraper.fetch_nse_bulk_deals(), table='bulk_deals')
    save_to_database(scraper.fetch_sast_insider_trading(), table='insider_trades')

    # --- SECTION 0: PRE-SCREENING FUNNEL ---
    stage1_candidates = stage_1_filter(all_stocks.to_dict('records'))
    stage2_qualified = stage_2_fundamental_scorer(pd.DataFrame(stage1_candidates))
    final_100 = get_top_100_candidates(stage2_qualified)

    # ─── STEP 6.1: SECTION 2 & 3 DEEP ANALYSIS ───
    print("🧠 Executing Section 2 Intelligence & Section 3 Analytical Engines...")
    v7_engine = V7AnalysisEngine()
    forensics = ForensicsEngine()
    rotation = SectorRotationRadar()
    
    final_100_list = final_100.to_dict('records')
    historical_map = get_historical_quarter_data([s['symbol'] for s in final_100_list])
    
    save_to_database(pd.DataFrame(final_100_list), table='latest_analysis_results')
    
    for stock in final_100_list:
        # --- SECTION 2: LATEST INTELLIGENCE ---
        stock['intel_queries'] = fetch_latest_intelligence(stock['symbol'], stock.get('sector'))

        # --- SECTION 3H: Anti-Trigger Guard ---
        guard = v7_engine.apply_section_3H_guards(stock)
        stock['spike_suppressed'] = guard['suppressed']
        stock['guard_reasons'] = ", ".join(guard['reasons'])

        # --- SECTION 3A: Valuation ---
        stock.update(v7_engine.apply_section_3A_valuation(stock)) 

        # --- SECTION 3C: Growth ---
        target_growth_sectors = ['Infra', 'Defence', 'IT', 'Railways', 'AI', 'Semiconductors', 'Critical Minerals', 'Renewable Energy', 'Pharma']
        if any(s.lower() in str(stock.get('sector', '')).lower() for s in target_growth_sectors):
            stock.update(v7_engine.apply_section_3C_growth(stock))
            
        # --- SECTION 3I: Early Detection Scoring ---
        early_data = v7_engine.calculate_section_3I_early_score(stock)
        stock.update({'early_entry_score': early_data['early_score'], 'early_mover_badge': early_data['badge']})

        # --- SECTION 3B, 3D, 3G: Deep Forensics ---
        stock.update(forensics.calculate_accounting_forensics(stock))

        # --- SECTION 3E: CAPITAL ALLOCATION ---
        roce = stock.get('roce', 0)
        wacc = 11.5 
        stock['wealth_creation_spread'] = roce - wacc
        stock['allocation_tag'] = "WEALTH CREATOR" if roce > wacc else "VALUE ERODER"

        # --- SECTION 3F, 3K: Ownership & Pledge Trends ---
        stock.update(analyze_ownership_trends(stock, historical_map.get(stock['symbol'])))

        # --- SECTION 3L: Sectoral Rotation Stage ---
        stock['rotation_stage'] = rotation.calculate_rotation_stage(
            stock.get('sector_return', 0), stock.get('nifty_return', 0), stock.get('fii_sector_trend', 'neutral')
        )

        # FINAL ENFORCEMENT: Section 3H Safety Suppression Logic
        if stock['spike_suppressed'] or stock.get('earnings_manipulation_risk') or stock.get('pledge_signal') == "PLEDGE RISING (Red Tag)":
            stock['spike_suppressed'] = True
            stock['early_entry_score'] = 0 
            if "Forensic/Pledge Risk" not in stock['guard_reasons']:
                stock['guard_reasons'] += " | Forensic/Pledge Risk"

    # ─── STEP 6.12: SECTION 4 — BALANCE SHEET ANALYSIS ───
    # FIX: Consolidated for efficiency with Contingent Debt mapping
    from bs_engine import BalanceSheetEngine
    bs_analyst = BalanceSheetEngine()

    for stock in final_100_list:
        prev_bs_4q = historical_map.get(stock['symbol'], {}) 
        bs_report = bs_analyst.analyze_bs_health(stock, prev_bs_4q)
        
        stock['bs_status'] = bs_report['status'] # Mapped to Excel "BS Health Flag"
        stock['bs_flags'] = ", ".join(bs_report['flags']) # Mapped to Excel "BS Health Note"
        
        if bs_report['status'] in ['WATCH', 'ALERT']:
            stock['bs_output'] = bs_report['output_line']
            stock['guard_reasons'] += f" | BS {bs_report['status']}: " + ", ".join(bs_report['flags'])
            # Section 3H Guard Integration
            if bs_report['status'] == 'ALERT':
                stock['verdict'] = "AVOID / EXIT"
                stock['spike_suppressed'] = True
        else:
            stock['bs_output'] = None
    
    # --- MOMENTUM CALCULATION (STEP 6.14 Logic) ---
    for stock in final_100_list:
        history = get_symbol_history(stock['symbol']) # Pulls historical price data
        
        if not history.empty and len(history) >= 41:
            current_close = history.iloc[-1]['close']
            
            # Mapping to Section 10 Rule 4 & 126
            # 10 days = 2W | 20 days = 4W | 30 days = 6W | 40 days = 8W
            stock['2w_chg'] = ((current_close - history.iloc[-11]['close']) / history.iloc[-11]['close']) * 100
            stock['4w_chg'] = ((current_close - history.iloc[-21]['close']) / history.iloc[-21]['close']) * 100
            stock['6w_chg'] = ((current_close - history.iloc[-31]['close']) / history.iloc[-31]['close']) * 100
            stock['8w_chg'] = ((current_close - history.iloc[-41]['close']) / history.iloc[-41]['close']) * 100
        else:
            # Fallback for new listings or data gaps
            stock['2w_chg'] = stock['4w_chg'] = stock['6w_chg'] = stock['8w_chg'] = 0

        # ─── STEP 6.15: SECTION 5 TECHNICAL ANALYSIS ───
        print("📈 Executing Section 5 Technical Analysis Engine...")
        from technical_engine import TechnicalAnalysisEngine
        tech_engine = TechnicalAnalysisEngine()
        
        for stock in final_100_list:
            symbol_history = get_symbol_history(stock['symbol']) 
            if not symbol_history.empty:
                analyzed_history = tech_engine.calculate_indicators(
                    symbol_history, 
                    exchange_tag=stock.get('exchange_tag', 'NSE')
                )
                latest = analyzed_history.iloc[-1]
                stock['rsi_14'] = latest['RSI_14']
                stock['sma_200'] = latest['SMA_200']
                stock['vol_ratio'] = latest['vol_ratio']
                stock['vol_quality'] = latest['vol_quality']
                stock['supertrend'] = latest['ST_Direction']
                
                # WEEKLY CHANGE CALCULATION (Section 10 Requirement)
                # 10, 20, 30, 40 days for 2W, 4W, 6W, 8W
                try:
                    stock['2w_chg'] = ((latest['close'] - analyzed_history.iloc[-10]['close']) / analyzed_history.iloc[-10]['close']) * 100
                    stock['4w_chg'] = ((latest['close'] - analyzed_history.iloc[-20]['close']) / analyzed_history.iloc[-20]['close']) * 100
                    stock['8w_chg'] = ((latest['close'] - analyzed_history.iloc[-40]['close']) / analyzed_history.iloc[-40]['close']) * 100
                except:
                    stock['2w_chg'] = stock['4w_chg'] = stock['8w_chg'] = 0

        # ─── STEP 6.20: SECTION 5B FAIR VALUE ENGINE ───
        print("⚖️ Executing Section 5B Multi-Model Fair Value Engine...")
        from fair_value_engine import FairValueEngine
        fv_engine = FairValueEngine(gsec_yield=6.0)
        
        for stock in final_100_list:
            models = fv_engine.calculate_all_models(
                stock, 
                beta=stock.get('beta', 1.0), 
                growth_3yr=stock.get('pat_cagr_3y', 15)
            )
            fv_results = fv_engine.get_composite_fair_value(
                models, 
                sector=stock.get('sector', 'General'), 
                cmp=stock.get('close', 1)
            )
            stock['cfv'] = fv_results['cfv']
            stock['mos_pct'] = fv_results['mos_pct']
            stock['upside'] = fv_results['upside']
            stock['score_adjustment'] = fv_results['score_adjustment']

        # ─── STEP 6.25: SECTION 6 & 7 SCORING AND STORM ENGINE ───
        print("⚖️ Finalizing Composite Scores & Storm Filters...")
        from market_context import MarketContextPoller
        from scoring_engine import ScoringEngine
        
        poller = MarketContextPoller()
        indices_df = poller.fetch_nse_indices()
        
        v_vix = indices_df.loc[indices_df['indexSymbol'] == 'INDIA VIX', 'last'].values[0] 
        nifty_current = indices_df.loc[indices_df['indexSymbol'] == 'NIFTY 50', 'last'].values[0]
        nifty_52w_high = get_nifty_52w_high_from_db() 
        v_off_peak = ((nifty_52w_high - nifty_current) / nifty_52w_high) * 100 

        scorer = ScoringEngine()
        for stock in final_100_list:
            results = scorer.calculate_composite_score(stock)
            stock.update(results)
            
            storm_data = scorer.calculate_storm_score(stock, v_vix, v_off_peak)
            if storm_data:
                stock['storm_score'] = storm_data['storm_score']
                stock['storm_label'] = storm_data['storm_label']
            else:
                stock['storm_score'] = "N/A"

        # Step 7: F&O Context
        fo_data = download_nse_fo_participant_data(today)
        if fo_data is not None:
            save_to_database(fo_data, table='fo_positioning')

        # ─── STEP 8: AI ANALYST & CRISP CARD GENERATION ───
        from ai_analyst import get_ai_analysis
        from report_formatter import ReportFormatter
        
        run_stats = {
            'total_universe': len(all_stocks),
            'stage1_passed': len(stage1_candidates),
            'stage2_passed': len(stage2_qualified),
            'stage3_selected': len(final_100_list)
        }
        
        print(f"🤖 Triggering AI Analyst for Section 8 Cards...") 
        investor_cards_text = get_ai_analysis(pd.DataFrame(final_100_list))
        
        formatter = ReportFormatter()
        final_cards_for_display = []
        for stock in final_100_list:
            structured_card = formatter.format_investor_card(stock)
            final_cards_for_display.append(structured_card)
            # Mapping for Excel Block H Summary Note
            stock['Analysis_Summary_Block_H'] = stock.get('ai_summary_note', "Detailed analysis provided in PDF/Text report.")

        # ─── STEP 9: DAILY REPORT & EXCEL DELIVERY ───────────────────────────────
        from excel_generator import ExcelGeneratorV6 # Use your final master class
        from daily_report_generator import DailyReportGenerator 

        # NEW: Prepare Market Mood Stats for Section 9 Header
        market_stats = {
            'nifty_close': nifty_current,
            'sensex_close': indices_df.loc[indices_df['indexSymbol'] == 'S&P BSE SENSEX', 'last'].values[0] if 'S&P BSE SENSEX' in indices_df.values else 0,
            'vix': v_vix,
            'fii_net': get_latest_fii_net_cash(), 
            'nifty_200d': get_nifty_200_sma() 
        }

        # Generate Section 10 Excel Dashboards (v6.0)
        print("📊 Constructing Master Excel Dashboards...")
        excel_engine = ExcelGeneratorV6(final_100_list, today.strftime('%Y%m%d'))
        excel_engine.generate_excel_reports()

        # Generate Section 9 Research Summary
        report_engine = DailyReportGenerator(final_100_list, market_stats)
        research_summary = report_engine.generate_research_report()

        # Generate the Final Consolidated .txt Report
        report_filename = f"Daily_Analysis_Report_{today.strftime('%Y%m%d')}.txt"
        with open(report_filename, "w", encoding="utf-8") as f:
            f.write(research_summary) # Section 9 at top
            f.write("\n" + "="*60 + "\n")
            f.write(f"--- FILTER STATS ---\n")
            f.write(f"NSE/BSE Research | {today.strftime('%Y-%m-%d')} | VIX: {v_vix}\n")
            f.write(f"Screened: {run_stats['total_universe']} -> Analysed: {run_stats['stage3_selected']}\n")
            f.write("="*60 + "\n\n")
            f.write("--- DETAILED AI ANALYSIS (SECTION 0D) ---\n")
            f.write(investor_cards_text)
            f.write("\n\n" + "="*60 + "\n\n")
            f.write("--- QUICK INVESTOR CARDS (SECTION 8) ---\n")
            f.write("\n\n".join(final_cards_for_display))

        print(f"✅ Pipeline Complete. Section 9 Research Summary, Section 8 Cards & Section 10 Excel ready.")
        return final_100_list

if __name__ == "__main__":
    run_master_pipeline()