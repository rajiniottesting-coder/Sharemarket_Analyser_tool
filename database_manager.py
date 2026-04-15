import sqlite3

def setup_database():
    conn = sqlite3.connect('market_data.db')
    cursor = conn.cursor()
    
    # 1. ENHANCED DAILY PRICES (Supports Stage 1 & 3)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol TEXT,
            date TEXT,
            close REAL,
            open REAL,
            high REAL,
            low REAL,
            prev_close REAL,
            volume INTEGER,
            avg_vol_20d REAL,      -- Required for Section 0A (V2)
            delivery_pct REAL,     -- Required for Section 0A (V3)
            high_52w REAL,         -- Required for Section 0A (V6)
            low_52w REAL,          -- Required for Section 0A (V6)
            mcap REAL,             -- Required for Section 0A (V5)
            exchange TEXT,         -- [NSE/BSE/BSE_SME]
            PRIMARY KEY (symbol, date)
        )
    ''')

    # 2. FUNDAMENTALS TABLE (Supports Stage 2 - Section 0B)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fundamentals (
            symbol TEXT PRIMARY KEY,
            net_profit_q1 REAL,    -- Required for F1
            net_profit_q2 REAL,    -- Required for F1
            rev_growth_yoy REAL,   -- Required for F2
            debt_equity REAL,      -- Required for F3
            promoter_holding REAL, -- Required for F4
            pe_ratio REAL,         -- Required for F5
            pledge_pct REAL,       -- Required for HD3
            last_updated TEXT
        )
    ''')

    # 3. RUN STATS LOG (Section 0E)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS run_stats (
            run_date TEXT PRIMARY KEY,
            total_universe INTEGER,
            stage1_passed INTEGER,
            stage2_passed INTEGER,
            stage3_selected INTEGER,
            gate_check_result TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fo_participant_data (
            date TEXT,
            client_type TEXT,      -- [FII, DII, Pro, Client]
            future_index_long INTEGER,
            future_index_short INTEGER,
            future_stock_long INTEGER,
            future_stock_short INTEGER,
            option_index_call_long INTEGER,
            option_index_put_long INTEGER,
            PRIMARY KEY (date, client_type)
        )
    ''')

    conn.commit()
    conn.close()
    print("✅ Database V2.0 Initialized: All Section 0 columns added.")

# def save_data_to_db(df, target_date):
#     """
#     Saves the Bhavcopy dataframe to the local SQLite database.
#     """
#     if df is None or df.empty:
#         return
        
#     conn = sqlite3.connect('market_data.db')
#     cursor = conn.cursor()
#     date_str = target_date.strftime('%Y-%m-%d')
    
#     # Prepare records for insertion
#     records = []
#     for _, row in df.iterrows():
#         records.append((
#             row.get('SYMBOL', ''),
#             date_str,
#             row.get('CLOSE', 0.0),
#             row.get('TOTTRDQTY', 0),
#             row.get('DELIVERY_PCT', 0.0), # Default to 0 if not present yet
#             row.get('MCAP', 0.0),         # Default to 0 if not present yet
#             row.get('EXCHANGE', 'NSE')
#         ))
        
#     # INSERT OR REPLACE handles duplicates if the script is run twice on the same day
#     cursor.executemany('''
#         INSERT OR REPLACE INTO daily_prices 
#         (symbol, date, close, volume, delivery_pct, mcap, exchange)
#         VALUES (?, ?, ?, ?, ?, ?, ?)
#     ''', records)
    
#     conn.commit()
#     conn.close()
#     print(f"💾 DB Update: {len(records)} records saved to market_data.db for {date_str}.")

# if __name__ == "__main__":
#     setup_database()