import sqlite3
import datetime

def enforce_circular_queue(db_path):
    """
    Ensures the DB acts as a circular queue, keeping only the last 3 years of data.
    Mapped to Master Prompt Section 12 logic.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Calculate the cutoff date (3 years ago from today)
    cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=1095)).strftime('%Y-%m-%d')
    
    print(f"--- DB Maintenance: Pruning data older than {cutoff_date} ---")
    
    # List of tables to prune (mapped to Sections 1A, 3J, 12B)
    tables_to_clean = ['stock_prices', 'bulk_deals', 'run_log', 'run_stats', 'stock_analyses']
    
    for table in tables_to_clean:
        try:
            cursor.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff_date,))
            print(f"Cleaned {cursor.rowcount} records from {table}")
        except sqlite3.OperationalError:
            print(f"Table {table} not found or missing timestamp column. Skipping.")

    # Rule 2: Physically shrink the file (VACUUM)
    # Essential for Git-Commit strategy to keep pushes under 100MB
    print("Compressing database file...")
    conn.execute("VACUUM")
    
    conn.commit()
    conn.close()
    print("--- DB Maintenance Complete ---")

if __name__ == "__main__":
    enforce_circular_queue('market_data.db')