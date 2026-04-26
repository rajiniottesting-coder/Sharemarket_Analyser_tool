"""
seed_holidays.py — one-shot local seeder for market_holidays table.

WHY THIS EXISTS
───────────────
Production runs on GitHub Actions, where NSE's holiday-master API is often
blocked by Cloudflare. The pipeline's gate check will fail-closed (block the
run) on the first day of a new year if it can't reach NSE AND has no cached
holidays for that year.

This script is the manual escape hatch. Run it locally (your home network
isn't blocked by NSE's Cloudflare rules), and it populates market_holidays
for the requested year(s). Commit the updated market_data.db so the next
GitHub Actions run starts with a hot cache.

USAGE
─────
  # From the project root, on your local machine:
  python scripts/seed_holidays.py              # seeds current calendar year
  python scripts/seed_holidays.py 2026 2027    # seeds specific years

The script reuses ingestion/holiday_calendar.py — no logic duplication.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import sys

# Make project-root imports work when running as a script
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from ingestion.holiday_calendar import (
    _fetch_nse_holidays_from_api,
    _group_by_year,
    _sync_holidays_to_db,
    _load_holidays_from_db,
    DB_PATH,
)


def main() -> int:
    args = sys.argv[1:]
    if args:
        try:
            wanted_years = sorted({int(a) for a in args})
        except ValueError:
            print(f"Usage: python {sys.argv[0]} [YEAR ...]")
            return 2
    else:
        wanted_years = [datetime.date.today().year]

    print(f"Target years: {wanted_years}")
    print(f"DB path:      {os.path.abspath(DB_PATH)}")

    # Make sure the DB and table exist even on a fresh checkout — _sync handles
    # CREATE TABLE IF NOT EXISTS internally, but a quick ping confirms the path.
    sqlite3.connect(DB_PATH).close()

    print("\nFetching from NSE holiday-master API...")
    holidays = _fetch_nse_holidays_from_api()
    if not holidays:
        print("\n❌ NSE API fetch failed. Nothing to seed.")
        print("   If your local network is blocked too, you can manually insert")
        print("   rows into market_holidays(date, name, exchange) — see schema in")
        print("   database/data_bridge.py.")
        return 1

    grouped = _group_by_year(holidays)
    seeded_any = False
    for year in wanted_years:
        rows = grouped.get(year, {})
        if not rows:
            print(f"  · {year}: NSE API didn't return any dates "
                  f"(too early in the year? NSE often publishes mid-December).")
            continue
        _sync_holidays_to_db(year, rows)
        cached = _load_holidays_from_db(year)
        print(f"  ✓ {year}: cached {len(cached)} holidays")
        for d, n in sorted(cached.items()):
            print(f"        {d}  {n}")
        seeded_any = True

    if not seeded_any:
        print("\n⚠ No years were seeded. Check the year arguments and try again.")
        return 1

    print(f"\n✅ Done. Commit {DB_PATH} so GitHub Actions starts with a hot cache.")
    return 0


if __name__ == "__main__":
    sys.exit(main())