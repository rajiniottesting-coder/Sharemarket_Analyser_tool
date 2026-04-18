"""
db_maintenance.py
Section 12: Smart rolling-window DB maintenance for free-tier GitHub Actions.

Strategy: Keep 400 trading days (≈16 months) rolling window.
  - 200-day SMA:        needs 200 days  ✅
  - 52-week high/low:   needs 250 days  ✅
  - 8-week momentum:    needs 41 days   ✅
  - All indicators work with 400 days   ✅
  - 3-year CAGR comes from fundamentals (v7_intelligence), NOT price DB

Size at 400 days: ~700 MB  (vs 1.3 GB for 3 years)
GitHub Actions runner disk: 14 GB — always safe.
"""

import sqlite3
import datetime
import os


# Rolling window: 400 calendar days ≈ 275 trading days > 250 needed for 52wk
KEEP_DAYS = 400


def enforce_circular_queue(db_path: str, keep_days: int = KEEP_DAYS) -> None:
    """
    Prunes rows older than keep_days from all time-series tables,
    then VACUUMs to physically reclaim disk space.

    Table → date column (matches initialize_v7_tables schema exactly):
      daily_prices           → date
      fo_participant_data    → date
      bulk_deals             → date
      insider_trades         → date
      run_stats              → run_date
      latest_analysis_results→ date
    """
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=keep_days)
              ).strftime("%Y-%m-%d")

    print(f"\n--- DB Maintenance: Rolling {keep_days}-day window ---")
    print(f"    Pruning data older than: {cutoff}")

    TABLES = [
        ("daily_prices",            "date"),
        ("fo_participant_data",     "date"),
        ("bulk_deals",              "date"),
        ("insider_trades",          "date"),
        ("run_stats",               "run_date"),
        ("latest_analysis_results", "date"),
    ]

    size_before = _db_size_mb(db_path)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    total_pruned = 0

    for table, date_col in TABLES:
        try:
            c.execute(f"DELETE FROM {table} WHERE {date_col} < ?", (cutoff,))
            n = c.rowcount
            if n > 0:
                total_pruned += n
                print(f"    Pruned {n:>8,} rows  ← {table}")
        except sqlite3.OperationalError:
            pass  # table doesn't exist yet — safe to skip

    conn.commit()

    # Count remaining rows for reporting
    try:
        price_rows = c.execute(
            "SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        date_range = c.execute(
            "SELECT MIN(date), MAX(date) FROM daily_prices").fetchone()
    except Exception:
        price_rows = 0
        date_range = ("—", "—")

    conn.close()

    # VACUUM reclaims space after bulk deletes — essential for free-tier
    conn2 = sqlite3.connect(db_path)
    print("    Compressing (VACUUM)...", end=" ", flush=True)
    conn2.execute("VACUUM")
    conn2.close()

    size_after = _db_size_mb(db_path)
    saved = size_before - size_after

    print(f"done")
    print(f"    daily_prices: {price_rows:,} rows | {date_range[0]} → {date_range[1]}")
    print(f"    DB size:      {size_before:.1f} MB → {size_after:.1f} MB "
          f"({'−' if saved >= 0 else '+'}{abs(saved):.1f} MB)")
    if total_pruned:
        print(f"    Total pruned: {total_pruned:,} rows")
    print(f"--- DB Maintenance Complete ---\n")


def _db_size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / 1_048_576
    except Exception:
        return 0.0


def get_db_stats(db_path: str = "market_data.db") -> dict:
    """Returns a dict of DB stats — useful for pipeline logging."""
    stats = {"size_mb": _db_size_mb(db_path), "price_rows": 0,
             "date_min": "—", "date_max": "—"}
    try:
        conn = sqlite3.connect(db_path)
        r = conn.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM daily_prices"
        ).fetchone()
        if r:
            stats["price_rows"] = r[0] or 0
            stats["date_min"]   = r[1] or "—"
            stats["date_max"]   = r[2] or "—"
        conn.close()
    except Exception:
        pass
    return stats


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "market_data.db"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else KEEP_DAYS
    if not os.path.exists(db):
        print(f"❌ DB not found: {db}")
    else:
        stats = get_db_stats(db)
        print(f"📦 Current: {stats['size_mb']:.1f} MB | "
              f"{stats['price_rows']:,} rows | "
              f"{stats['date_min']} → {stats['date_max']}")
        enforce_circular_queue(db, keep_days=days)