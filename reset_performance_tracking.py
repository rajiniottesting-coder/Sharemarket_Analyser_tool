#!/usr/bin/env python3
"""v17.3 — ONE-SHOT PERFORMANCE TRACKING RESET

WHAT THIS DOES
Empties the three Gold-pick outcome tables so performance measurement
restarts from scratch under current logic:

    gold_recommendations   — the pick log (entry / SL / T1 / T2 / T3 / expiry)
    gold_outcomes          — one row per pick, walked forward by the tracker
    gold_continuation      — v17.3 post-T1 shadow walk

Nothing else is touched. daily_prices, symbol_master, fundamental_metrics,
technical_indicators, shareholding and every other table are left alone —
price history is what MAKES tracking possible and must survive the reset.

WHY A RESET WAS JUSTIFIED
The 31 closed positions were measured under three mutually incompatible
rule sets, so aggregating them into one "hit rate 41.9%" was misleading:

  · v17.1 horizon-key bug — `stock["horizon"]` was written but the code read
    `stock.get("time_horizon")`, so EVERY pick silently defaulted to
    POSITIONAL from v14.6 onward. All horizon-specific SL/T1/T3 logic was
    inert. SHORT TERM picks carried a 15% stop-loss; current code caps at 7%.
  · v16.5 → v17.0 trailing-stop recalibration — break-even activation moved
    +5% → +10% → +12% with a 10-day minimum hold. The 4 TRAIL_SL rows were
    produced under thresholds that no longer exist.
  · v17.0 gates — market-regime, 3d-ROC momentum and sector-cycle gates now
    filter Gold entry. Positions logged before them were never subject to
    the entry criteria the system currently enforces.

DANGER — READ BEFORE RUNNING
`market_data.db` exists ONLY as a GitHub Actions artifact with 30-day
retention. There is no local copy, no git history, no backup. This deletion
is IRREVERSIBLE once the prior artifact ages out. That is why this script:

  1. Runs in DRY-RUN mode by default and changes nothing.
  2. Requires BOTH --commit AND --confirm RESET-PERFORMANCE to delete.
  3. Prints exact row counts before and after, and aborts loudly if the
     post-delete counts are not zero.
  4. Refuses to run if the expected tables are missing, rather than
     silently "succeeding" against an empty or wrong database.

USAGE
    python3 reset_performance_tracking.py                      # dry run
    python3 reset_performance_tracking.py --commit \
            --confirm RESET-PERFORMANCE                        # real delete
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.environ.get("MARKET_DB_PATH", "market_data.db")

# The ONLY tables this script is permitted to empty. Anything not in this
# tuple is out of scope by construction — there is no code path that can
# delete from another table.
#
# COMPLETENESS VERIFIED against reporting/excel_generator.py::_performance_sheet.
# That method has exactly two data sources and no others:
#     get_outcome_stats()      -> gold_recommendations JOIN gold_outcomes
#     get_continuation_stats() -> gold_continuation
# Every section of the Performance sheet (headline metrics, speed, score-band
# / archetype / sector / horizon breakdowns, closed positions, risk-adjusted
# returns, open positions, missed-runup diagnostic, survivorship audit and
# the v17.3 continuation audit) is derived from those two calls. Emptying
# these three tables therefore blanks the sheet completely.
#
# DELIBERATELY NOT TOUCHED:
#   daily_prices          — price history is what MAKES tracking possible.
#                           Deleting it would break the tracker outright and
#                           force a 400-day re-backfill.
#   symbol_master, fundamental_metrics, technical_indicators, shareholding,
#   weekly_momentum, latest_analysis_results, run_stats, market_holidays
#                         — none feed the Performance sheet. latest_analysis_
#                           results backs the Alert Log's previous-score
#                           comparison, which is unrelated to outcome tracking.
TARGET_TABLES = ("gold_recommendations", "gold_outcomes", "gold_continuation")

CONFIRM_PHRASE = "RESET-PERFORMANCE"


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _counts(conn) -> dict:
    out = {}
    for t in TARGET_TABLES:
        if _table_exists(conn, t):
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        else:
            out[t] = None          # None = table absent (distinct from 0 rows)
    return out


def _outcome_breakdown(conn) -> list:
    """Pre-delete snapshot of what is about to be destroyed, so the run log
    is a permanent record even though the rows themselves are not."""
    if not _table_exists(conn, "gold_outcomes"):
        return []
    return list(conn.execute(
        "SELECT outcome_type, COUNT(*) FROM gold_outcomes "
        "GROUP BY outcome_type ORDER BY COUNT(*) DESC"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="One-shot reset of Gold-pick performance tracking tables.")
    ap.add_argument("--commit", action="store_true",
                    help="actually delete (default is a dry run)")
    ap.add_argument("--confirm", default="",
                    help=f"must be exactly '{CONFIRM_PHRASE}' alongside --commit")
    args = ap.parse_args()

    print("=" * 72)
    print("v17.3 PERFORMANCE TRACKING RESET")
    print(f"DB: {DB_PATH}")
    print(f"Mode: {'COMMIT (irreversible)' if args.commit else 'DRY RUN (no changes)'}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    if not os.path.exists(DB_PATH):
        print(f"❌ ABORT: {DB_PATH} not found.")
        print("   This script must run INSIDE the workflow, after the")
        print("   'Restore market_data.db from artifact' step. The 641 MB DB")
        print("   never exists locally.")
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        # ── Guard 0: NEVER run on the scheduled daily pipeline ────────────
        # The workflow `if:` condition already prevents this, because
        # `schedule` supplies no inputs. This is a second, independent layer
        # that lives with the code rather than the YAML — if the step is ever
        # copied, edited, or its condition dropped, the script still refuses.
        # Belt and braces on an irreversible operation.
        _event = os.environ.get("GITHUB_EVENT_NAME", "")
        if args.commit and _event == "schedule":
            print("❌ ABORT: refusing to reset on a scheduled run.")
            print("   This is a MANUAL, one-shot operation. It must be triggered")
            print("   via workflow_dispatch with the confirmation phrase, never")
            print("   by the 23:00 UTC cron.")
            return 1

        # ── Guard 1: the tables we expect must exist ──────────────────────
        missing = [t for t in TARGET_TABLES if not _table_exists(conn, t)]
        if "gold_recommendations" in missing or "gold_outcomes" in missing:
            print(f"❌ ABORT: core tracking tables missing: {missing}")
            print("   Refusing to run against an unexpected database.")
            return 1
        if "gold_continuation" in missing:
            print("ℹ️  gold_continuation absent (pre-v17.3 DB) — it will be")
            print("   created by initialize_v7_tables() on the next run.")

        before = _counts(conn)
        breakdown = _outcome_breakdown(conn)

        print("\nBEFORE:")
        for t, n in before.items():
            print(f"  {t:<24} {'(absent)' if n is None else f'{n:>6} rows'}")

        if breakdown:
            print("\n  Outcome breakdown about to be destroyed:")
            for oc, n in breakdown:
                print(f"    {oc:<12} {n:>4}")

        total = sum(n for n in before.values() if n)
        if total == 0:
            print("\n✅ Already empty — nothing to do.")
            return 0

        # ── Guard 2: dry run stops here ──────────────────────────────────
        if not args.commit:
            print(f"\n🔍 DRY RUN — {total} rows would be deleted. Nothing changed.")
            print(f"   To execute: --commit --confirm {CONFIRM_PHRASE}")
            return 0

        # ── Guard 3: explicit confirmation phrase ────────────────────────
        if args.confirm != CONFIRM_PHRASE:
            print(f"\n❌ ABORT: --commit requires --confirm {CONFIRM_PHRASE}")
            print(f"   Got: '{args.confirm}'")
            return 1

        # ── Execute ──────────────────────────────────────────────────────
        print(f"\n⚠️  DELETING {total} rows across {len(TARGET_TABLES)} tables...")
        for t in TARGET_TABLES:
            if before[t] is None:
                continue
            conn.execute(f"DELETE FROM {t}")
            print(f"   cleared {t}")
        conn.commit()

        # ── Guard 4: verify the delete actually took ─────────────────────
        after = _counts(conn)
        print("\nAFTER:")
        for t, n in after.items():
            print(f"  {t:<24} {'(absent)' if n is None else f'{n:>6} rows'}")

        leftover = {t: n for t, n in after.items() if n}
        if leftover:
            print(f"\n❌ FAILED: rows remain after delete: {leftover}")
            return 1

        # Reclaim the freed pages. The DB is ~641 MB and lives as an artifact,
        # so shrinking it directly reduces upload/download time every run.
        print("\nRunning VACUUM to reclaim space...")
        conn.execute("VACUUM")

        print("\n" + "=" * 72)
        print("✅ RESET COMPLETE — performance tracking restarts from today.")
        print("   The Performance sheet will show the empty-state banner until")
        print("   the first Gold pick is logged, then the <30-closed sample-size")
        print("   guard until roughly 30 positions have closed under current logic.")
        print("=" * 72)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())