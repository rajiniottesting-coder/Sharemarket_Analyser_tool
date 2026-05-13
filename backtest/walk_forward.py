"""
backtest/walk_forward.py — v15.3 Phase 3

Walk-forward backtest infrastructure for empirically calibrating SL/T
multipliers from real outcome data.

STATUS: INFRASTRUCTURE ONLY
──────────────────────────
This module reads outcomes from `gold_outcomes` table and reports
hit-rate / win-rate statistics. It does NOT yet perform multiplier
calibration because that requires accumulated outcomes (60-90 days
of closed positions at minimum to be statistically meaningful).

When you have ~30+ closed positions, this module will become the
foundation for:
  - Per-cap-category multiplier sensitivity analysis
  - Per-sector tier adjustment validation
  - Regime threshold (1.20/0.80) re-calibration
  - SL_MAX/SL_MIN bound optimization

For now, the script produces a status report so you can monitor when
you're ready for empirical calibration.

DESIGN PRINCIPLES
─────────────────
- READ-ONLY: This script never modifies `gold_recommendations`,
  `gold_outcomes`, or production code. Pure analysis.
- TRANSPARENT: Reports current sample size, win rates, and what
  cohort sizes are needed for statistical significance.
- HONEST: Does NOT pretend to optimize multipliers from inadequate
  data. Refuses to calibrate below sample-size threshold.

USAGE
─────
    python -m backtest.walk_forward                    # status report
    python -m backtest.walk_forward --by-cap            # cap-category breakdown
    python -m backtest.walk_forward --by-sector-tier    # sector tier
    python -m backtest.walk_forward --by-regime         # regime impact

When ready for full calibration (60+ closed positions):
    python -m backtest.walk_forward --calibrate \
        --output multipliers_calibrated.json

(--calibrate is disabled below sample-size threshold and prints why.)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

DB_PATH = "market_data.db"
# Minimum sample size below which calibration refuses to run
MIN_SAMPLE_FOR_CALIBRATION = 30
# Strong-significance threshold
RECOMMENDED_SAMPLE = 60


def _load_closed_outcomes() -> List[Dict]:
    """Returns list of closed recommendation rows joined with outcomes.

    Returns empty list on DB error or missing tables.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.execute("""
                SELECT r.symbol, r.cap_category, r.sector, r.time_horizon,
                       r.recommendation_date, r.cmp_at_recommendation,
                       r.stop_loss, r.t1, r.t2, r.t3,
                       r.original_stop_loss, r.regime_at_rec, r.atr_at_rec,
                       o.outcome_type, o.outcome_date, o.outcome_price,
                       o.days_to_outcome, o.max_runup_pct, o.max_drawdown_pct,
                       o.current_pnl_pct
                FROM gold_recommendations r
                INNER JOIN gold_outcomes o ON r.symbol = o.symbol
                  AND r.recommendation_date = o.recommendation_date
                WHERE o.outcome_type IN ('SL_HIT','T1_HIT','T2_HIT','T3_HIT','EXPIRED')
                ORDER BY r.recommendation_date
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            return rows
        finally:
            conn.close()
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        print(f"⚠️  DB read failed: {e}")
        return []


def _classify_outcome(o: Dict) -> str:
    """Returns 'win', 'loss', or 'expired'."""
    t = o.get('outcome_type', '')
    if t in ('T1_HIT', 'T2_HIT', 'T3_HIT'):
        return 'win'
    elif t == 'SL_HIT':
        return 'loss'
    else:
        return 'expired'


def _hit_rate_summary(outcomes: List[Dict]) -> Dict:
    """Headline stats: total, wins, losses, expired, hit rate, win rate."""
    total = len(outcomes)
    if total == 0:
        return {'total': 0, 'wins': 0, 'losses': 0, 'expired': 0,
                'hit_rate_pct': 0.0, 'win_rate_pct': 0.0,
                'avg_pnl_pct': 0.0, 'avg_days_to_outcome': 0}
    wins = sum(1 for o in outcomes if _classify_outcome(o) == 'win')
    losses = sum(1 for o in outcomes if _classify_outcome(o) == 'loss')
    expired = sum(1 for o in outcomes if _classify_outcome(o) == 'expired')
    # Hit rate = wins / (wins + losses), excluding expired
    decisive = wins + losses
    hit_rate = (wins / decisive * 100.0) if decisive > 0 else 0.0
    # Win rate = wins / total, including expired
    win_rate = (wins / total * 100.0) if total > 0 else 0.0
    # Average outcomes
    pnls = [float(o.get('current_pnl_pct') or 0) for o in outcomes]
    days = [int(o.get('days_to_outcome') or 0) for o in outcomes]
    return {
        'total': total, 'wins': wins, 'losses': losses, 'expired': expired,
        'hit_rate_pct': round(hit_rate, 1),
        'win_rate_pct': round(win_rate, 1),
        'avg_pnl_pct': round(sum(pnls) / total, 2) if total else 0.0,
        'avg_days_to_outcome': int(sum(days) / total) if total else 0,
    }


def _group_by(outcomes: List[Dict], key: str) -> Dict[str, List[Dict]]:
    """Group outcomes by a field value."""
    grouped = defaultdict(list)
    for o in outcomes:
        v = str(o.get(key, '—') or '—')
        grouped[v].append(o)
    return dict(grouped)


def _print_summary_table(group: Dict[str, Dict], title: str) -> None:
    print(f"\n=== {title} ===")
    print(f"{'Bucket':<22} {'N':>5} {'Wins':>5} {'Loss':>5} {'Exp':>5} "
          f"{'Hit%':>6} {'Win%':>6} {'AvgP&L%':>8}")
    print("-" * 75)
    for bucket, stats in sorted(group.items(), key=lambda x: -x[1]['total']):
        s = stats
        print(f"  {bucket:<20} {s['total']:>5} {s['wins']:>5} {s['losses']:>5} "
              f"{s['expired']:>5} {s['hit_rate_pct']:>6.1f} {s['win_rate_pct']:>6.1f} "
              f"{s['avg_pnl_pct']:>+8.2f}")


def status_report() -> int:
    """Print sample-size status + headline stats. Returns total count."""
    outcomes = _load_closed_outcomes()
    n = len(outcomes)

    print("=" * 75)
    print("  v15.3 WALK-FORWARD BACKTEST — STATUS REPORT")
    print("=" * 75)
    print(f"\n  Closed positions accumulated: {n}")
    print(f"  Minimum for calibration:      {MIN_SAMPLE_FOR_CALIBRATION}")
    print(f"  Recommended for confidence:   {RECOMMENDED_SAMPLE}")
    print()

    if n == 0:
        print("  ℹ️  No closed positions yet. Pipeline must run for several")
        print("     weeks before walk-forward calibration is meaningful.")
        return 0

    if n < MIN_SAMPLE_FOR_CALIBRATION:
        print(f"  ⚠️  Below calibration threshold ({n} < {MIN_SAMPLE_FOR_CALIBRATION}).")
        print(f"     Statistical noise dominates with this sample size.")
        print(f"     Calibration disabled — accumulate {MIN_SAMPLE_FOR_CALIBRATION - n} more")
        print(f"     closed positions before re-running with --calibrate.")
    elif n < RECOMMENDED_SAMPLE:
        print(f"  📊  Calibration enabled but underpowered ({n} < {RECOMMENDED_SAMPLE}).")
        print(f"     Results are directional only — re-run after {RECOMMENDED_SAMPLE - n}")
        print(f"     more closed positions for high-confidence multipliers.")
    else:
        print(f"  ✅  Sample size adequate for high-confidence calibration.")

    headline = _hit_rate_summary(outcomes)
    print("\n  Overall hit rates (current v15.x parameters):")
    for k, v in headline.items():
        print(f"     {k}: {v}")

    return n


def breakdown_report(group_by_field: str, title: str) -> None:
    outcomes = _load_closed_outcomes()
    if not outcomes:
        print("⚠️  No closed positions yet.")
        return
    groups = _group_by(outcomes, group_by_field)
    summaries = {bucket: _hit_rate_summary(rows) for bucket, rows in groups.items()}
    _print_summary_table(summaries, title)


def calibrate_multipliers(out_path: str) -> int:
    """Multiplier calibration — refuses if sample too small."""
    outcomes = _load_closed_outcomes()
    n = len(outcomes)
    if n < MIN_SAMPLE_FOR_CALIBRATION:
        print(f"❌  Refusing to calibrate: only {n} closed positions "
              f"(need ≥{MIN_SAMPLE_FOR_CALIBRATION}).")
        print(f"    Calibrating on too few outcomes would produce noise,")
        print(f"    not signal. Wait for more accumulation.")
        return 1

    # Placeholder: real calibration would do gradient-descent on
    # (horizon_mult, sector_tier, regime_threshold) to maximize:
    #   - hit_rate (wins / decisive)
    #   - avg_R (avg gain on wins / avg loss on losses)
    # subject to:
    #   - SL stays in [4.5%, 15%] bounds
    #   - R:R ≥ 1.5:1 by construction
    #   - No look-ahead bias (only use data ≤ rec_date)
    print(f"📊  Sample size: {n} closed positions.")
    print(f"    Full calibration implementation deferred until production")
    print(f"    accumulates {RECOMMENDED_SAMPLE}+ outcomes. This script is")
    print(f"    the infrastructure scaffold.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Walk-forward backtest analyzer (v15.3 Phase 3)")
    p.add_argument('--by-cap', action='store_true',
                   help='Break down by cap_category')
    p.add_argument('--by-sector-tier', action='store_true',
                   help='Break down by sector tier')
    p.add_argument('--by-regime', action='store_true',
                   help='Break down by regime_at_rec')
    p.add_argument('--by-horizon', action='store_true',
                   help='Break down by time_horizon')
    p.add_argument('--calibrate', action='store_true',
                   help='Run multiplier calibration (requires N ≥ 30)')
    p.add_argument('--output', default='multipliers_calibrated.json',
                   help='Output file for calibrated multipliers')
    args = p.parse_args()

    n = status_report()
    if args.by_cap:
        breakdown_report('cap_category', 'By Cap Category')
    if args.by_sector_tier:
        breakdown_report('sector', 'By Sector')
    if args.by_regime:
        breakdown_report('regime_at_rec', 'By Regime At Recommendation')
    if args.by_horizon:
        breakdown_report('time_horizon', 'By Time Horizon')
    if args.calibrate:
        return calibrate_multipliers(args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())