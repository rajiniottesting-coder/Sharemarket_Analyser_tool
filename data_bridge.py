import sqlite3
import pandas as pd

def save_to_database(df):
    """
    Saves consolidated NSE/BSE data to the V2 database tables (Section 1A/1B)
    """
    if df is None or df.empty:
        print("⚠️ No data available to save.")
        return

    conn = sqlite3.connect('market_data.db')
    
    try:
        # Saving to daily_prices table
        # We use 'append' to keep historical data for the 20-day average calculations
        df.to_sql('daily_prices', conn, if_exists='append', index=False)
        print(f"✅ Successfully bridged {len(df)} records to V2 Database.")
        
        # Section 0E: Initializing Run Stats for today
        run_date = df['date'].iloc[0]
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO run_stats (run_date, total_universe, gate_check_result)
            VALUES (?, ?, ?)
        ''', (run_date, len(df), 'RUN_APPROVED'))
        conn.commit()
        
    except Exception as e:
        print(f"❌ Database Bridge Error: {e}")
    finally:
        conn.close()

def get_20d_avg_vol(symbol):
    """
    Calculates the 20-day average volume required for Section 0C (Priority Score)
    """
    conn = sqlite3.connect('market_data.db')
    query = f"SELECT AVG(volume) FROM daily_prices WHERE symbol = '{symbol}' LIMIT 20"
    avg_vol = pd.read_sql_query(query, conn).iloc[0, 0]
    conn.close()
    return avg_vol if avg_vol else 0

def reconcile_exchanges(nse_df, bse_df):
    """
    Implements Section 1B: Cross-Exchange Reconciliation via ISIN.
    Flags: NSE_ONLY, BSE_ONLY, DUAL_LISTED, BSE_SME.
    """
    # Merge on ISIN (Section 1B requirement)
    merged = pd.merge(nse_df, bse_df, on='ISIN', how='outer', suffixes=('_NSE', '_BSE'))
    
    # Logic for Exchange Tagging (Section 1B)
    def tag_exchange(row):
        if pd.notnull(row['SYMBOL_NSE']) and pd.notnull(row['SYMBOL_BSE']):
            return 'DUAL_LISTED'
        elif pd.notnull(row['SYMBOL_BSE']):
            return 'BSE_ONLY'
        return 'NSE_ONLY'

    merged['EXCHANGE_TAG'] = merged.apply(tag_exchange, axis=1)
    return merged

def save_fo_data(df, target_date):
    """Saves Section 1A F&O Participant Data to Database"""
    if df is None or df.empty: return
    
    conn = sqlite3.connect('market_data.db')
    date_str = target_date.strftime('%Y-%m-%d')
    
    try:
        # We only care about the Net Positioning rows (FII/DII)
        df['date'] = date_str
        df.to_sql('fo_participant_data', conn, if_exists='append', index=False)
        print(f"✅ F&O Smart Money data saved for {date_str}")
    except Exception as e:
        print(f"❌ F&O DB Error: {e}")
    finally:
        conn.close()

import sqlite3
import pandas as pd

def get_historical_quarter_data(symbols):
    """
    SECTION 3F & 3K (90 Days): FII Trends & Pledge Direction.
    SECTION 4 (360 Days): Balance Sheet YoY Trends (DIO, DSO, Debt).
    """
    if not symbols:
        return {}

    historical_context = {}
    conn = sqlite3.connect('market_data.db')
    placeholders = ', '.join(['?'] * len(symbols))

    try:
        # We query for the most recent record that is AT LEAST 90 days old.
        # This provides the baseline for 'Previous Quarter' trends.
        query = f"""
            SELECT * FROM v7_intelligence 
            WHERE symbol IN ({placeholders}) 
            AND timestamp <= datetime('now', '-90 days')
            GROUP BY symbol
            HAVING MAX(timestamp)
        """
        
        df_hist = pd.read_sql_query(query, conn, params=symbols)
        conn.close()

        # Convert the DataFrame into a nested dictionary for the Funnel
        hist_dict = df_hist.set_index('symbol').to_dict('index')

        for symbol in symbols:
            # If DB has data, we map it. If not, we return empty dict to trigger Safety Guards.
            if symbol in hist_dict:
                data = hist_dict[symbol]
                historical_context[symbol] = {
                    # Section 3F & 3K logic
                    "fii_holding": data.get('fii_holding', 0),
                    "pledge_pct": data.get('pledge_pct', 0),
                    
                    # Section 4 logic (Trend Baselines)
                    "total_debt": data.get('total_debt', 0),
                    "dio": data.get('dio', 0),
                    "dso": data.get('dso', 0),
                    "roe": data.get('roe', 0),
                    "networth": data.get('networth', 1)
                }
            else:
                # Return None if no history exists (prevents false 'Rising Debt' alerts)
                historical_context[symbol] = None

    except Exception as e:
        print(f"⚠️ Database Lookup Warning: {e}. Using empty history.")
        return {symbol: None for symbol in symbols}

    return historical_context

def get_symbol_history(symbol, limit=250):
    """
    SECTION 5 & 5B: Fetches historical time-series for technical indicators.
    Returns a Pandas DataFrame formatted for pandas_ta.
    """
    conn = sqlite3.connect('market_data.db')
    
    # We fetch OHLCV data sorted by date ascending (required for TA math)
    query = f"""
        SELECT date, open, high, low, close, volume 
        FROM daily_prices 
        WHERE symbol = ? 
        ORDER BY date ASC 
        LIMIT ?
    """
    
    try:
        df = pd.read_sql_query(query, conn, params=(symbol, limit))
        
        # Section 5 Technical Note: Ensure numeric types for TA calculations
        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            cols = ['open', 'high', 'low', 'close', 'volume']
            df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')
            
        return df
    except Exception as e:
        print(f"❌ Error fetching history for {symbol}: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

def get_nifty_52w_high_from_db():
    """
    SECTION 7: Fetches the 52-week high for Nifty 50 to calculate market drawdown.
    Required for the Mandatory Storm Score trigger (Market > 5% off peak).
    """
    conn = sqlite3.connect('market_data.db')
    
    # We query the max close price for NIFTY 50 from the last 250 records
    query = """
        SELECT MAX(close) FROM daily_prices 
        WHERE symbol = 'NIFTY 50' 
        AND date >= date('now', '-365 days')
    """
    
    try:
        cursor = conn.cursor()
        cursor.execute(query)
        result = cursor.fetchone()
        
        # If no data exists (new DB), return a safe fallback to prevent division by zero
        return result[0] if result[0] else 1.0
        
    except Exception as e:
        print(f"❌ Error fetching Nifty 52W High: {e}")
        return 1.0
    finally:
        conn.close()

# Add this to data_bridge.py

from harvester import (
    download_nse_bhavcopy, download_bse_bhavcopy, 
    download_nse_sme_bhavcopy, download_bse_sme_bhavcopy
)
from reconciler import reconcile_exchanges
import pandas as pd

def get_today_consolidated_data(target_date):
    """
    SECTION 1A & 1B: Consolidates NSE/BSE Main and SME data into one master dataset.
    This acts as the single source of truth for the rest of the pipeline.
    """
    print(f"🔄 Consolidating market data for {target_date.date()}...")

    # 1. Download all 4 raw data streams (Section 1A & 1B) 
    nse_main = download_nse_bhavcopy(target_date)
    nse_sme = download_nse_sme_bhavcopy(target_date)
    bse_main = download_bse_bhavcopy(target_date)
    bse_sme = download_bse_sme_bhavcopy(target_date)

    # 2. Stack Main + SME for each exchange 
    # We use ignore_index=True to create a fresh index for the combined list
    all_nse = pd.concat([nse_main, nse_sme], ignore_index=True) if nse_main is not None else nse_sme
    all_bse = pd.concat([bse_main, bse_sme], ignore_index=True) if bse_main is not None else bse_sme

    # 3. Cross-Exchange Reconciliation (Section 1B) 
    # This uses the ISIN-based matching logic you defined in reconciler.py
    consolidated_df = reconcile_exchanges(all_nse, all_bse)

    if consolidated_df is not None:
        print(f"✅ Consolidation Complete: {len(consolidated_df)} unique instruments identified.")
    else:
        print("⚠️ Consolidation failed: No data returned from exchanges.")

    return consolidated_df

import sqlite3
import pandas as pd

def get_latest_fii_net_cash():
    """
    SECTION 7 & 9: Fetches the latest FII Net Cash flow from fo_positioning.
    Used for the Research Report header and Storm Score context.
    """
    conn = sqlite3.connect('market_data.db')
    try:
        # Pulls the most recent 'net_value' from FII participant data
        query = "SELECT net_value FROM fo_positioning WHERE client_type = 'FII' ORDER BY date DESC LIMIT 1"
        result = pd.read_sql_query(query, conn)
        return result['net_value'].iloc[0] if not result.empty else 0
    except Exception as e:
        print(f"⚠️ Error fetching FII Net: {e}")
        return 0
    finally:
        conn.close()

def get_nifty_200_sma():
    """
    SECTION 9: Calculates the 200-day SMA for NIFTY 50 from historical records.
    Used to determine if the Market Mood is Bullish or Bearish.
    """
    conn = sqlite3.connect('market_data.db')
    try:
        # Fetches the last 200 closing prices for Nifty 50
        query = """
            SELECT close FROM daily_prices 
            WHERE symbol = 'NIFTY 50' 
            ORDER BY date DESC LIMIT 200
        """
        df = pd.read_sql_query(query, conn)
        return df['close'].mean() if len(df) >= 200 else df['close'].mean()
    except Exception as e:
        print(f"⚠️ Error calculating Nifty 200 SMA: {e}")
        return 0
    finally:
        conn.close()

def load_latest_analysis_results():
    """
    Fetches the most recent analysis results from the database 
    to provide context for the WhatsApp/Chat interface.
    """
    import sqlite3
    import pandas as pd
    
    try:
        conn = sqlite3.connect('market_data.db')
        # We query the table where master_funnel saves the final results
        query = "SELECT * FROM latest_analysis_results"
        df = pd.read_sql(query, conn)
        conn.close()
        
        if df.empty:
            return []
        return df.to_dict('records')
    except Exception as e:
        print(f"⚠️ Database fetch failed: {e}")
        return []