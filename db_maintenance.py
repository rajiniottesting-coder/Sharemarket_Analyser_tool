"""
db_maintenance.py
Section 12: Circular queue enforcement — keeps last 3 years, prunes older data.
Uses correct table names and date column matching the live pipeline schema.
"""
import sqlite3
import datetime


def enforce_circular_queue(db_path: str, keep_days: int = 1095) -> None:
    """
    Prunes rows older than keep_days (default 3 years = 1095 days) from all
    time-series tables, then VACUUMs to reclaim disk space.

    Table → date column mapping (matches initialize_v7_tables schema):
      daily_prices          → date
      fo_participant_data   → date
      bulk_deals            → date
      insider_trades        → date
      run_stats             → run_date
      latest_analysis_results → date
    """
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=keep_days)
              ).strftime("%Y-%m-%d")

    print(f"--- DB Maintenance: Pruning data older than {cutoff} ---")

    # (table_name, date_column)
    TABLES = [
        ("daily_prices",           "date"),
        ("fo_participant_data",    "date"),
        ("bulk_deals",             "date"),
        ("insider_trades",         "date"),
        ("run_stats",              "run_date"),
        ("latest_analysis_results","date"),
    ]

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    total_pruned = 0
    for table, date_col in TABLES:
        try:
            cursor.execute(
                f"DELETE FROM {table} WHERE {date_col} < ?", (cutoff,)
            )
            pruned = cursor.rowcount
            total_pruned += pruned
            if pruned > 0:
                print(f"  Pruned {pruned:,} rows from {table}")
        except sqlite3.OperationalError as e:
            # Table doesn't exist yet — safe to skip
            pass

    conn.commit()

    # Log DB size before VACUUM
    import os
    size_before = os.path.getsize(db_path) / 1_048_576
    total_rows = cursor.execute(
        "SELECT COUNT(*) FROM daily_prices"
    ).fetchone()[0]

    # VACUUM reclaims space after deletions
    print("  Compressing database (VACUUM)...")
    conn.execute("VACUUM")
    conn.commit()
    conn.close()

    size_after = os.path.getsize(db_path) / 1_048_576
    print(f"  daily_prices: {total_rows:,} rows remaining")
    print(f"  DB size: {size_before:.2f} MB → {size_after:.2f} MB")
    if total_pruned > 0:
        print(f"  Total pruned: {total_pruned:,} rows")
    print("--- DB Maintenance Complete ---")


if __name__ == "__main__":
    enforce_circular_queue("market_data.db")