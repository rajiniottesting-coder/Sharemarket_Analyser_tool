import sqlite3
import pandas as pd

# --- SECTION 1: SCHEMA & INITIALIZATION ---

def initialize_v7_tables(conn):
    """
    SECTION 1.2: Creates all necessary V7 tables if they don't exist.
    This fixes the 'no such table' errors on fresh GitHub Action runs.
    """
    cursor = conn.cursor()
    # 1. Daily Market Data
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol TEXT, date TEXT, isin TEXT, open REAL, high REAL, 
            low REAL, close REAL, volume REAL, prev_close REAL,
            exchange_tag TEXT, PRIMARY KEY(symbol, date)
        )
    ''')
    # 2. Pipeline Execution Stats (Section 12B)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS run_stats (
            run_date TEXT PRIMARY KEY, 
            total_universe INTEGER, 
            gate_check_result TEXT
        )
    ''')
    # 3. F&O Participant Data (Section 1A)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fo_participant_data (
            date TEXT, client_type TEXT, future_index_long REAL, 
            future_index_short REAL, future_stock_long REAL, 
            future_stock_short REAL, total_long REAL, total_short REAL,
            net_value REAL
        )
    ''')
    # 4. Intelligence/Historical Baseline (Section 3 & 4)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS v7_intelligence (
            symbol TEXT, timestamp TEXT, fii_holding REAL, pledge_pct REAL,
            total_debt REAL, dio REAL, dso REAL, roe REAL, networth REAL
        )
    ''')
    # 5. Final AI Reports (Section 8)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS latest_analysis_results (
            symbol TEXT PRIMARY KEY, date TEXT, ai_card TEXT, 
            final_score REAL, allocation_tag TEXT
        )
    ''')
    conn.commit()

# --- SECTION 2: SAVING METHODS ---

def save_to_database(df):
    """
    Main bridge for daily consolidated market data.
    """
    if df is None or df.empty: return
    conn = sqlite3.connect('market_data.db')
    try:
        initialize_v7_tables(conn)
        df.to_sql('daily_prices', conn, if_exists='append', index=False)
        print(f"✅ Bridged {len(df)} records.")
        
        run_date = str(df['date'].iloc[0])
        conn.execute('INSERT OR REPLACE INTO run_stats VALUES (?, ?, ?)', 
                     (run_date, len(df), 'RUN_SUCCESS'))
        conn.commit()
    except Exception as e:
        print(f"❌ Database Bridge Error: {e}")
    finally:
        conn.close()

def save_fo_data(df, target_date):
    """Saves Section 1A F&O Participant Data"""
    if df is None or df.empty: return
    conn = sqlite3.connect('market_data.db')
    date_str = target_date.strftime('%Y-%m-%d')
    try:
        initialize_v7_tables(conn)
        df['date'] = date_str
        df.to_sql('fo_participant_data', conn, if_exists='append', index=False)
        print(f"✅ F&O Smart Money data saved for {date_str}")
    except Exception as e:
        print(f"❌ F&O DB Error: {e}")
    finally:
        conn.close()

# --- SECTION 3: CONSOLIDATION & RECONCILIATION ---

def standardize_to_v7_schema(df):
    """Helper to ensure NSE/BSE use identical keys before reconciliation."""
    if df is None or df.empty: return pd.DataFrame()
    df.columns = [c.lower().strip().replace(" ", "_") for c in df.columns]
    mapping = {
        'sc_name': 'symbol', 'security_id': 'symbol', 'scrip_id': 'symbol', 'syml': 'symbol',
        'isin_code': 'isin', 'isin': 'isin',
        'close_price': 'close', 'last_price': 'close',
        'prev_close': 'prev_close', 'prevclose': 'prev_close', 'previous_close': 'prev_close',
        'open_price': 'open', 'high_price': 'high', 'low_price': 'low',
        'tottrdqty': 'volume', 'no_of_shrs': 'volume', 'total_trd_qty': 'volume'
    }
    df = df.rename(columns={k: v for k, v in mapping.items() if k in df.columns})
    critical_cols = ['isin', 'symbol', 'close', 'prev_close', 'volume', 'open', 'high', 'low']
    for col in critical_cols:
        if col not in df.columns:
            df[col] = 0 if col not in ['symbol', 'isin'] else "UNKNOWN"
    return df[critical_cols]

def reconcile_exchanges(nse_df, bse_df):
    """Cross-Exchange Reconciliation via ISIN (Section 1B)."""
    merged = pd.merge(nse_df, bse_df, on='isin', how='outer', suffixes=('_NSE', '_BSE'))
    def tag_exchange(row):
        if pd.notnull(row['symbol_NSE']) and pd.notnull(row['symbol_BSE']): return 'DUAL_LISTED'
        elif pd.notnull(row['symbol_BSE']): return 'BSE_ONLY'
        return 'NSE_ONLY'
    merged['exchange_tag'] = merged.apply(tag_exchange, axis=1)
    return merged

def get_today_consolidated_data(target_date, nse_main, nse_sme, bse_main, bse_sme):
    """V7 Consolidation Hub."""
    print(f"🔄 [V7] Consolidating market data for {target_date.date()}...")
    n_m = standardize_to_v7_schema(nse_main)
    n_s = standardize_to_v7_schema(nse_sme)
    b_m = standardize_to_v7_schema(bse_main)
    b_s = standardize_to_v7_schema(bse_sme)
    all_nse = pd.concat([n_m, n_s], ignore_index=True) if not n_m.empty or not n_s.empty else pd.DataFrame()
    all_bse = pd.concat([b_m, b_s], ignore_index=True) if not b_m.empty or not b_s.empty else pd.DataFrame()
    consolidated_df = reconcile_exchanges(all_nse, all_bse)
    if consolidated_df is not None and not consolidated_df.empty:
        consolidated_df['date'] = pd.to_datetime(target_date).date()
        print(f"✅ V7 Consolidation Complete: {len(consolidated_df)} records.")
    return consolidated_df if consolidated_df is not None else pd.DataFrame()

# --- SECTION 4: HISTORICAL & ANALYTICAL RETRIEVAL ---

def get_historical_quarter_data(symbols):
    """SECTION 3F, 3K & 4: Quarterly Baseline Trends."""
    if not symbols: return {}
    hist_context = {}
    conn = sqlite3.connect('market_data.db')
    try:
        placeholders = ', '.join(['?'] * len(symbols))
        query = f"SELECT * FROM v7_intelligence WHERE symbol IN ({placeholders}) AND timestamp <= datetime('now', '-90 days') GROUP BY symbol"
        df_hist = pd.read_sql_query(query, conn, params=symbols)
        hist_dict = df_hist.set_index('symbol').to_dict('index')
        for s in symbols:
            if s in hist_dict:
                d = hist_dict[s]
                hist_context[s] = {"fii_holding": d.get('fii_holding', 0), "pledge_pct": d.get('pledge_pct', 0),
                                   "total_debt": d.get('total_debt', 0), "dio": d.get('dio', 0), "dso": d.get('dso', 0)}
            else: hist_context[s] = None
    except Exception: return {s: None for s in symbols}
    finally: conn.close()
    return hist_context

def get_latest_fii_net_cash():
    """SECTION 7 & 9: Latest FII Net Cash Flow."""
    conn = sqlite3.connect('market_data.db')
    try:
        query = "SELECT net_value FROM fo_participant_data WHERE client_type = 'FII' ORDER BY date DESC LIMIT 1"
        result = pd.read_sql_query(query, conn)
        return result['net_value'].iloc[0] if not result.empty else 0
    except Exception: return 0
    finally: conn.close()

def get_nifty_200_sma():
    """SECTION 9: Nifty 200 SMA Sentiment."""
    conn = sqlite3.connect('market_data.db')
    try:
        df = pd.read_sql_query("SELECT close FROM daily_prices WHERE symbol = 'NIFTY 50' ORDER BY date DESC LIMIT 200", conn)
        return df['close'].mean() if not df.empty else 0
    except Exception: return 0
    finally: conn.close()

def get_20d_avg_vol(symbol):
    conn = sqlite3.connect('market_data.db')
    try:
        query = f"SELECT AVG(volume) FROM daily_prices WHERE symbol = '{symbol}' LIMIT 20"
        avg_vol = pd.read_sql_query(query, conn).iloc[0, 0]
        return avg_vol if avg_vol else 0
    finally: conn.close()

def get_symbol_history(symbol, limit=250):
    conn = sqlite3.connect('market_data.db')
    try:
        query = "SELECT date, open, high, low, close, volume FROM daily_prices WHERE symbol = ? ORDER BY date ASC LIMIT ?"
        df = pd.read_sql_query(query, conn, params=(symbol, limit))
        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric, errors='coerce')
        return df
    finally: conn.close()

def get_nifty_52w_high_from_db():
    conn = sqlite3.connect('market_data.db')
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(close) FROM daily_prices WHERE symbol = 'NIFTY 50' AND date >= date('now', '-365 days')")
        result = cursor.fetchone()
        return result[0] if result and result[0] else 1.0
    finally: conn.close()

def load_latest_analysis_results():
    try:
        conn = sqlite3.connect('market_data.db')
        df = pd.read_sql("SELECT * FROM latest_analysis_results", conn)
        conn.close()
        return df.to_dict('records') if not df.empty else []
    except Exception: return []