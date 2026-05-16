#!/usr/bin/env python3
"""
v16.5 one-time cleanup — reset the false KOVAI SL_HIT closed entry.

CONTEXT
═══════
On the v16.4 pipeline run, the v15.0 trailing-stop logic (break-even
activation at just +5% peak gain) falsely closed KOVAI:
    - KOVAI ran to +5.4% peak
    - trailing stop ratcheted to break-even (= entry price 5391.5)
    - a normal pullback to +0.7% touched break-even
    - tracker recorded SL_HIT @ 5391.5, P&L +0.0%, days_to_outcome 3

This is a FALSE CLOSE. KOVAI never hit its real stop loss (4791.14).
The v16.5 recalibration (break-even now activates at +10%, not +5%)
prevents this going forward, but the bad row is already persisted in
`gold_outcomes` and must be reset so v16.5's tracker can re-walk KOVAI
correctly as an OPEN position.

WHAT THIS SCRIPT DOES
═════════════════════
1. Connects to market_data.db (read the path from argv or default)
2. Shows the current KOVAI gold_outcomes row(s) for confirmation
3. Resets ONLY rows that match the false-close signature:
       symbol = 'KOVAI'
       AND outcome_type = 'SL_HIT'
       AND ABS(outcome_price - <entry>) < 0.01   (closed at entry = false)
       AND current_pnl_pct BETWEEN -0.5 AND 0.5  (≈ 0% P&L = false)
   back to outcome_type='OPEN' with cleared outcome fields, so the
   next `python track_outcomes.py` run re-evaluates it from scratch
   under the v16.5 trailing logic.
4. Prints a before/after diff and commits.

SAFETY
══════
- DRY-RUN by default. Pass --commit to actually write.
- Only touches rows matching the precise false-close signature above —
  a genuine KOVAI SL_HIT (real loss, P&L ≈ -11%) would NOT match and is
  left untouched.
- Idempotent: running twice is harmless (after reset the row is OPEN
  and no longer matches the SL_HIT filter).
- Does NOT touch gold_recommendations (the immutable audit trail) —
  only the gold_outcomes tracking row.

USAGE
═════
    # Dry run (default) — shows what WOULD change, writes nothing:
    python v16_5_cleanup_false_kovai.py

    # Actually apply the reset:
    python v16_5_cleanup_false_kovai.py --commit

    # Custom DB path:
    python v16_5_cleanup_false_kovai.py --db /path/to/market_data.db --commit
"""
import sys
import os
import sqlite3
import argparse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="market_data.db",
                    help="path to market_data.db (default: ./market_data.db)")
    ap.add_argument("--commit", action="store_true",
                    help="actually write changes (default is dry-run)")
    ap.add_argument("--symbol", default="KOVAI",
                    help="symbol to clean (default: KOVAI)")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"❌ DB not found: {args.db}")
        print("   Pass the correct path with --db")
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 1. Show current state
    print("=" * 78)
    print(f"  v16.5 false-close cleanup — {args.symbol}")
    print(f"  DB: {args.db}")
    print(f"  Mode: {'COMMIT (will write)' if args.commit else 'DRY-RUN (no writes)'}")
    print("=" * 78)

    rows = c.execute(
        "SELECT recommendation_date, symbol, outcome_type, outcome_date, "
        "outcome_price, days_to_outcome, current_pnl_pct, current_price "
        "FROM gold_outcomes WHERE symbol = ?",
        (args.symbol,),
    ).fetchall()

    if not rows:
        print(f"\n  No gold_outcomes rows for {args.symbol}. Nothing to do.")
        conn.close()
        return

    print(f"\n  Current gold_outcomes rows for {args.symbol}:")
    for r in rows:
        print(f"    rec_date={r['recommendation_date']}  "
              f"outcome={r['outcome_type']}  "
              f"out_price={r['outcome_price']}  "
              f"pnl={r['current_pnl_pct']}%  "
              f"days={r['days_to_outcome']}")

    # 2. Identify false-close rows.
    # The false signature: SL_HIT where the exit price ≈ entry price and
    # P&L ≈ 0 (break-even close, not a real -11% stop-loss hit). We compare
    # outcome_price to the recorded cmp_at_recommendation in
    # gold_recommendations to be precise.
    false_rows = []
    for r in rows:
        if r["outcome_type"] != "SL_HIT":
            continue
        # Pull the entry price from the immutable recommendations table
        rec = c.execute(
            "SELECT cmp_at_recommendation FROM gold_recommendations "
            "WHERE symbol = ? AND recommendation_date = ?",
            (r["symbol"], r["recommendation_date"]),
        ).fetchone()
        entry = float(rec["cmp_at_recommendation"]) if rec else None
        pnl = float(r["current_pnl_pct"] or 0)
        out_price = float(r["outcome_price"] or 0)

        is_false = (
            entry is not None
            and abs(out_price - entry) < 0.01      # closed AT entry price
            and -0.5 <= pnl <= 0.5                  # ≈ 0% P&L
        )
        if is_false:
            false_rows.append((r, entry))

    if not false_rows:
        print(f"\n  ✓ No false-close rows detected for {args.symbol}. "
              f"Nothing to reset (a genuine SL_HIT with a real loss would "
              f"NOT match this filter — that's intentional).")
        conn.close()
        return

    # 3. Reset the false rows back to OPEN
    print(f"\n  ⚠ Detected {len(false_rows)} FALSE-close row(s) to reset:")
    for r, entry in false_rows:
        print(f"    {r['symbol']} (rec {r['recommendation_date']}): "
              f"SL_HIT @ {r['outcome_price']} (entry {entry}, "
              f"pnl {r['current_pnl_pct']}%) → will reset to OPEN")

    if not args.commit:
        print("\n  DRY-RUN — no changes written.")
        print("  Re-run with --commit to apply the reset.")
        conn.close()
        return

    for r, entry in false_rows:
        c.execute(
            "UPDATE gold_outcomes SET "
            "  outcome_type = 'OPEN', "
            "  outcome_date = '', "
            "  outcome_price = 0, "
            "  days_to_outcome = 0, "
            "  trailing_sl_pct = 0, "
            "  trailing_sl_price = 0, "
            "  last_checked_date = '' "
            "WHERE symbol = ? AND recommendation_date = ?",
            (r["symbol"], r["recommendation_date"]),
        )
    conn.commit()

    # 4. Show after-state
    print(f"\n  ✓ Reset {len(false_rows)} row(s). New state:")
    rows2 = c.execute(
        "SELECT recommendation_date, symbol, outcome_type, outcome_price, "
        "current_pnl_pct FROM gold_outcomes WHERE symbol = ?",
        (args.symbol,),
    ).fetchall()
    for r in rows2:
        print(f"    rec_date={r['recommendation_date']}  "
              f"outcome={r['outcome_type']}  "
              f"out_price={r['outcome_price']}  "
              f"pnl={r['current_pnl_pct']}%")

    conn.close()
    print("\n  ✅ Done. Next steps:")
    print("     1. Deploy the v16.5 code files (track_outcomes.py etc.)")
    print("     2. Run: python track_outcomes.py")
    print("        → KOVAI will be re-walked under v16.5 logic and will")
    print("          correctly stay OPEN (peak +5.4% < +10% threshold,")
    print("          so the trailing stop never activates).")


if __name__ == "__main__":
    main()