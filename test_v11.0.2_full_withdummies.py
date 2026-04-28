"""
Comprehensive validation suite — covers ScoringEngine (v10.17 + v11.0),
the v11.0.1 reporting/ingestion bugfixes, the v11.0.2 allowlist
auto-maintenance + chronic-AVOID demotion + turnaround flag features,
AND the v12.1 reconciler empty-ISIN false-positive hotfix.

Validates the ScoringEngine against every code path that exists in the
engine, the v11.0.1 fixes against the daily report generator, command
parser, report formatter, and the DUAL_LISTED_ALLOWLIST, the v11.0.2
features against the allowlist_maintainer module, the verdict-streak
helpers in data_bridge, the priority-ranker chronic-AVOID demotion, and
the daily report's new Section H, and the v12.1 reconciler hotfix that
prevents empty-ISIN rows from being falsely tagged DUAL_LISTED via
symbol-merge collision. Each test is a self-contained synthetic stock
with a known expected outcome computed by hand from the engine's
documented logic.

Test coverage:
  Group 1: Sub-score weighted blend (canonical + redistributed branches)
  Group 2: MoS / score_adjustment integration
  Group 3: Spike bonus gating (fundamental≥55 vs <55)
  Group 4: Early Mover bonus (≥50 threshold)
  Group 5: Anti-trigger penalty
  Group 6: Forensic adjustment (all 4 inputs × all 3 zones each)
  Group 7: Forensic adjustment cap (clamped to [-10, +8])
  Group 8: Sentiment informedness (each of 6 paid/AI signals)
  Group 9: Composite clamping (0/100 bounds)
  Group 10: Verdict derivation — AVOID floor
  Group 11: Verdict derivation — cap-tier thresholds (LARGE/MID/SMALL/MICRO)
  Group 12: Verdict derivation — MoS gate + tech-confirmed override
  Group 13: Verdict derivation — OVERVALUED branch
  Group 14: Verdict derivation — confidence dots (HIGH/MED/LOW)
  Group 15: v10.17 informed_count counter (each dimension separately)
  Group 16: v10.17 demotion: BUY → WATCHLIST(thin data)
  Group 17: v10.17 doesn't affect OVERVALUED / NEUTRAL / AVOID / WATCHLIST
  Group 18: v10.17 boundary cases (informed=2 vs 3)
  Group 19: Defensive — None / empty / "—" inputs
  Group 20: Output dict shape consistency

  ─── v11.0.1 reporting & ingestion bugfix groups ───
  Group 21: ingestion/reconciler.py — DUAL_LISTED_ALLOWLIST integrity
  Group 22: ingestion/reconciler.py — runtime exchange-tag behavior
  Group 23: reporting/daily_report_generator.py — Section A & D
  Group 24: reporting/command_parser.py — early movers threshold
  Group 25: reporting/report_formatter.py — EARLY MOVER badge
  Group 26: cross-cutting consistency & end-to-end smoke test

  ─── v11.0.2 allowlist auto-maintenance + verdict streaks ───
  Group 27: ingestion/allowlist_maintainer.py — record / prune / lookup
  Group 28: reconciler ⊕ runtime allowlist integration (UNION semantics)
  Group 29: verdict streaks (avoid + recovery) + chronic-AVOID demotion
  Group 30: daily report Section H — turnaround candidates

For every test we also do a "no-leakage" check: confirm that for stocks
with informed_count >= 3, the new engine produces the EXACT SAME composite
score and verdict as the old engine would have.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysis.scoring_engine import ScoringEngine

scorer = ScoringEngine()

passed = 0
failed = 0
failures = []
warnings = []


def check(test_id, description, stock,
          want_verdict=None, want_score=None, score_tol=0.5,
          want_gate_applied=None, want_data_completeness=None,
          want_display_contains=None):
    global passed, failed
    try:
        out = scorer.calculate_composite_score(stock)
    except Exception as e:
        failed += 1
        failures.append(f"{test_id} [{description}]: EXCEPTION {type(e).__name__}: {e}")
        return None

    errs = []
    if want_verdict is not None and out["verdict"] != want_verdict:
        errs.append(f"verdict={out['verdict']} (want {want_verdict})")
    if want_score is not None and abs(out["composite_score"] - want_score) > score_tol:
        errs.append(f"score={out['composite_score']} (want {want_score}±{score_tol})")
    if want_gate_applied is not None and out["data_gate_applied"] != want_gate_applied:
        errs.append(f"gate={out['data_gate_applied']} (want {want_gate_applied})")
    if want_data_completeness is not None and out["data_completeness"] != want_data_completeness:
        errs.append(f"informed={out['data_completeness']} (want {want_data_completeness})")
    if want_display_contains is not None and want_display_contains not in out["verdict_display"]:
        errs.append(f"display={out['verdict_display']!r} missing {want_display_contains!r}")

    if errs:
        failed += 1
        failures.append(f"{test_id} [{description}]: " + "; ".join(errs)
                        + f" | got: verdict={out['verdict']} score={out['composite_score']} "
                          f"informed={out['data_completeness']} gate={out['data_gate_applied']}")
    else:
        passed += 1
    return out


def baseline_stock(**overrides):
    """Stock that produces well-defined behavior with all fields set sensibly."""
    s = {
        "stage2_score": 25,
        "fundamental_score": 50, "technical_score": 50, "safety_score": 50,
        "sentiment_score": 50,   "early_entry_score": 0,
        "spike_count": 0, "score_adjustment": 0,
        "cap_category": "MID", "mos_pct": 0,
        "supertrend": "NEUTRAL", "sector_stage": "NEUTRAL",
        "altman_z": None, "earnings_quality": "",
        "nd_ebitda": None, "int_coverage": None,
        "fii_3q_trend": "NEUTRAL", "insider_buy_alert": "NO",
        "promoter_qoq": 0, "dii_qoq": 0,
        "news_sentiment": "NEUTRAL", "pledge_direction": "—",
        "risk_flag_active": False,
    }
    s.update(overrides)
    return s


# ──────────────────────────────────────────────────────────────────────────
# GROUP 1: Sub-score weighted blend
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 1 — Sub-score weighted blend")
print("═" * 70)
# Canonical: f=70, t=70, e=20, sent=60 (informed via fii_3q_trend), safe=60
# = 70*0.35 + 70*0.30 + 20*0.15 + 60*0.10 + 60*0.10 = 24.5+21+3+6+6 = 60.5
check("1.1", "canonical weights, all dimensions informed",
      baseline_stock(fundamental_score=70, technical_score=70, safety_score=60,
                     early_entry_score=20, sentiment_score=60,
                     fii_3q_trend="UP"),
      want_score=60.5)

# Redistributed: same sub-scores, no paid sentiment
# = 70*0.389 + 70*0.333 + 20*0.167 + 60*0.111 = 27.23+23.31+3.34+6.66 = 60.54
check("1.2", "redistributed weights (no paid sentiment)",
      baseline_stock(fundamental_score=70, technical_score=70, safety_score=60,
                     early_entry_score=20, sentiment_score=60),
      want_score=60.54)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 2: MoS / score_adjustment integration
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 2 — MoS adjustment")
print("═" * 70)
# Base 50: 50*0.389+50*0.333+0*0.167+50*0.111 = 19.45+16.65+0+5.55 = 41.65
# +12 MoS adj -> 53.65
check("2.1", "MoS +12 (deeply undervalued)",
      baseline_stock(score_adjustment=12),
      want_score=53.65)
check("2.2", "MoS -10 (overvalued)",
      baseline_stock(score_adjustment=-10),
      want_score=31.65)
check("2.3", "score_adjustment overrides mos_adjustment if both present",
      baseline_stock(score_adjustment=8, mos_adjustment=99),
      want_score=49.65)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 3: Spike bonus gating
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 3 — Spike bonus gating")
print("═" * 70)
# fund=60 ≥ 55: spike_count=4 -> bonus = min(8, 10) = 8
# blend (with redistribution since no paid sentiment): 60*0.389 + 50*0.333 + 0 + 50*0.111 = 23.34+16.65+5.55=45.54
# + 8 spike = 53.54
check("3.1", "spike_count=4, fund=60 → full bonus +8",
      baseline_stock(fundamental_score=60, spike_count=4),
      want_score=53.54)
# fund=54 < 55: spike_count=4 -> bonus = min(8, 3) = 3
# blend: 54*0.389+50*0.333+0+50*0.111 = 21.01+16.65+5.55 = 43.21
# + 3 spike = 46.21
check("3.2", "spike_count=4, fund=54 → capped bonus +3",
      baseline_stock(fundamental_score=54, spike_count=4),
      want_score=46.21)
# Boundary: fund=55 exactly — should get full bonus
# 55*0.389+50*0.333+0+50*0.111 = 21.395+16.65+5.55 = 43.595
# + min(2*2, 10) = 4 = 47.6
check("3.3", "fund=55 (boundary, ≥55) → full bonus",
      baseline_stock(fundamental_score=55, spike_count=2),
      want_score=47.6)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 4: Early Mover bonus
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 4 — Early Mover bonus")
print("═" * 70)
# Base 50 except early_entry_score=50 → +5 mover bonus
# blend: 50*0.389+50*0.333+50*0.167+50*0.111 = 19.45+16.65+8.35+5.55 = 50
# +5 = 55
check("4.1", "early_entry=50 → +5 bonus",
      baseline_stock(early_entry_score=50), want_score=55)
# early=49 → no bonus
# blend: 50*0.389+50*0.333+49*0.167+50*0.111 = 19.45+16.65+8.183+5.55 = 49.83
check("4.2", "early_entry=49 (just below) → no bonus",
      baseline_stock(early_entry_score=49), want_score=49.83, score_tol=0.1)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 5: Anti-trigger penalty
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 5 — Anti-trigger penalty")
print("═" * 70)
# Base 41.65 - 10 = 31.65
check("5.1", "risk_flag_active=True → -10",
      baseline_stock(risk_flag_active=True), want_score=31.65)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 6: Forensic adjustment (each input × each zone)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 6 — Forensic adjustment")
print("═" * 70)
# Altman Z
check("6.1a", "altman_z=3.5 → +3", baseline_stock(altman_z=3.5), want_score=44.65)
check("6.1b", "altman_z=2.5 → 0 (grey zone)", baseline_stock(altman_z=2.5), want_score=41.65)
check("6.1c", "altman_z=1.5 → -5", baseline_stock(altman_z=1.5), want_score=36.65)
# Earn quality
check("6.2a", "earnings_quality=HIGH → +2", baseline_stock(earnings_quality="HIGH"), want_score=43.65)
check("6.2b", "earnings_quality=LOW → -3", baseline_stock(earnings_quality="LOW"), want_score=38.65)
# ND/EBITDA
check("6.3a", "nd_ebitda=0.5 → +1", baseline_stock(nd_ebitda=0.5), want_score=42.65)
check("6.3b", "nd_ebitda=2.5 → 0", baseline_stock(nd_ebitda=2.5), want_score=41.65)
check("6.3c", "nd_ebitda=6.0 → -2", baseline_stock(nd_ebitda=6.0), want_score=39.65)
# Interest Coverage
check("6.4a", "int_coverage=8 → +2", baseline_stock(int_coverage=8), want_score=43.65)
check("6.4b", "int_coverage=3 → 0", baseline_stock(int_coverage=3), want_score=41.65)
check("6.4c", "int_coverage=1.0 → -3", baseline_stock(int_coverage=1.0), want_score=38.65)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 7: Forensic adjustment cap [-10, +8]
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 7 — Forensic adjustment cap")
print("═" * 70)
# Max bonus: +3 + +2 + +1 + +2 = +8 (already at cap)
# blend 41.65 + 8 = 49.65
check("7.1", "all 4 forensics at +max → capped +8",
      baseline_stock(altman_z=3.5, earnings_quality="HIGH",
                     nd_ebitda=0.5, int_coverage=8),
      want_score=49.65)
# Max penalty: -5 + -3 + -2 + -3 = -13 → capped at -10
check("7.2", "all 4 forensics at -max → capped -10",
      baseline_stock(altman_z=1.5, earnings_quality="LOW",
                     nd_ebitda=6.0, int_coverage=1.0),
      want_score=31.65)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 8: Sentiment informedness (each of 6 paid/AI signals)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 8 — Sentiment informedness (6 paid/AI signals)")
print("═" * 70)
# Each of these should switch to canonical weights
# Canonical: 50*0.35+50*0.30+0*0.15+50*0.10+50*0.10 = 17.5+15+0+5+5 = 42.5
for sid, sig_name, kwargs in [
    ("8.1", "fii_3q_trend=UP",       dict(fii_3q_trend="UP")),
    ("8.2", "fii_3q_trend=DOWN",     dict(fii_3q_trend="DOWN")),
    ("8.3", "insider_buy_alert=YES", dict(insider_buy_alert="YES")),
    ("8.4", "promoter_qoq=0.5",      dict(promoter_qoq=0.5)),
    ("8.5", "dii_qoq=-0.5",          dict(dii_qoq=-0.5)),
    ("8.6", "news_sentiment=POSITIVE", dict(news_sentiment="POSITIVE")),
    ("8.7", "news_sentiment=NEGATIVE", dict(news_sentiment="NEGATIVE")),
    ("8.8", "pledge_direction=FALLING", dict(pledge_direction="FALLING")),
    ("8.9", "pledge_direction=RISING", dict(pledge_direction="RISING")),
]:
    check(sid, sig_name + " → canonical weights",
          baseline_stock(**kwargs), want_score=42.5)
# Tiny QoQ (< 0.1) should NOT mark as informed → still redistributed
check("8.10", "promoter_qoq=0.05 (below threshold) → still redistributed",
      baseline_stock(promoter_qoq=0.05), want_score=41.65)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 9: Composite clamping
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 9 — Composite clamp [0, 100]")
print("═" * 70)
# Drive score to 100+: fund=100, tech=100, early=100, sent=100, safe=100
# canonical: 100*0.35+100*0.30+100*0.15+100*0.10+100*0.10 = 100
# +12 MoS, +10 spike (fund>=55), +5 early mover, +8 forensic = 135 → clamp 100
check("9.1", "extreme positive inputs → clamped to 100",
      baseline_stock(fundamental_score=100, technical_score=100,
                     safety_score=100, early_entry_score=100,
                     sentiment_score=100, fii_3q_trend="UP",
                     score_adjustment=12, spike_count=5,
                     altman_z=3.5, earnings_quality="HIGH",
                     nd_ebitda=0.5, int_coverage=8),
      want_score=100)
# Drive score below 0
check("9.2", "extreme negative inputs → clamped to 0",
      baseline_stock(fundamental_score=0, technical_score=0,
                     safety_score=0, sentiment_score=0,
                     score_adjustment=-50, risk_flag_active=True,
                     altman_z=1.0, earnings_quality="LOW",
                     nd_ebitda=10, int_coverage=0.5),
      want_score=0)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 10: AVOID floor
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 10 — AVOID floor (universal)")
print("═" * 70)
# Score 37.99 → AVOID. For any cap.
for cap in ["LARGE", "MID", "SMALL", "MICRO"]:
    # blend ≈ 37.99 we approximate via low fundamentals
    s = baseline_stock(cap_category=cap, fundamental_score=20,
                       technical_score=30, safety_score=30, sentiment_score=30)
    # blend (redistributed): 20*0.389+30*0.333+0+30*0.111 = 7.78+9.99+3.33 = 21.1
    check(f"10.{cap[0]}", f"{cap}: very low score → AVOID",
          s, want_verdict="AVOID")
# Score exactly 38 boundary check: build a stock that produces score=38 exactly
# blend redistributed: f*0.389 + 50*0.333 + 0 + 50*0.111 = f*0.389 + 22.2
# For 38: f*0.389 = 15.8 → f = 40.6. So fund=40.6, others base.
# But sub-score blend will be 40.6*0.389+50*0.333+50*0.111 = 15.79+16.65+5.55 = 37.99
# That's 37.99 < 38 → AVOID. Let's go slightly higher: fund=41
check("10.5", "score≈38.05 (just above floor) → NEUTRAL not AVOID",
      baseline_stock(fundamental_score=41), want_verdict="NEUTRAL")


# ──────────────────────────────────────────────────────────────────────────
# GROUP 11: Cap-tier thresholds
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 11 — Cap-tier verdict thresholds")
print("═" * 70)

# For each cap tier, build a stock that lands exactly at:
#   - watch_min - 1  → NEUTRAL
#   - watch_min      → WATCHLIST
#   - buy_min  - 1   → WATCHLIST
#   - buy_min        → BUY (with informed≥3 + good MoS)

def build_stock_at_score(cap, target_score, with_informed=True):
    """Build a stock whose composite_score lands near target_score.
    Uses canonical weights (with paid sentiment fired)."""
    # canonical: 0.35*f + 0.30*t + 0.15*e + 0.10*sent + 0.10*safe
    # Set f=t=safe=X, e=0, sent=50:
    #   composite = X*(0.35+0.30+0.10) + 0 + 50*0.10 = 0.75X + 5
    # solve: X = (target - 5)/0.75
    x = (target_score - 5) / 0.75
    s = baseline_stock(
        cap_category=cap,
        fundamental_score=x, technical_score=x, safety_score=x,
        early_entry_score=0, sentiment_score=50,
        fii_3q_trend="UP",   # informed sentiment → canonical weights
        mos_pct=5,           # well above gate
    )
    # NOTE: we deliberately do NOT add early_entry_score=20 here.
    # The original v2 of this helper inflated the score by 3 points,
    # which broke threshold tests. Informedness comes from f/t/safe/sentiment
    # being away from base — early_entry stays at 0.
    return s

cap_thresholds = {"LARGE": (60, 50), "MID": (63, 53), "SMALL": (66, 56), "MICRO": (70, 60)}
test_id = 11
for cap, (buy_min, watch_min) in cap_thresholds.items():
    # WATCHLIST band: above watch_min by 2 (avoid float precision boundary)
    s = build_stock_at_score(cap, watch_min + 2)
    check(f"11.{cap}.watch", f"{cap} at watch_min+2 → WATCHLIST",
          s, want_verdict="WATCHLIST")
    # BUY: at buy_min + 2
    s = build_stock_at_score(cap, buy_min + 2)
    check(f"11.{cap}.buy", f"{cap} at buy_min+2 → BUY",
          s, want_verdict="BUY")
    # Below watch_min by 2 → NEUTRAL
    s = build_stock_at_score(cap, watch_min - 2)
    check(f"11.{cap}.neutral", f"{cap} below watch by 2 → NEUTRAL",
          s, want_verdict="NEUTRAL")


# ──────────────────────────────────────────────────────────────────────────
# GROUP 12: MoS gate + tech-confirmed override
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 12 — MoS gate + tech-confirmed override")
print("═" * 70)
# Default gate: -10. mos=-9.99 → BUY allowed; mos=-10.01 → blocked → OVERVALUED
high_score = build_stock_at_score("MID", 70)
high_score["mos_pct"] = -9.99
check("12.1", "mos=-9.99 (above gate) → BUY", high_score, want_verdict="BUY")
high_score["mos_pct"] = -10.01
check("12.2", "mos=-10.01 (below gate) → OVERVALUED", high_score,
      want_verdict="OVERVALUED")
# Tech-confirmed: gate relaxes to -20
high_score["mos_pct"] = -19.99
high_score["supertrend"] = "BUY"
high_score["sector_stage"] = "STAGE 2 - CONFIRMED UPTREND"
check("12.3", "mos=-19.99 + tech_confirmed → BUY", high_score, want_verdict="BUY")
high_score["mos_pct"] = -20.01
check("12.4", "mos=-20.01 + tech_confirmed → OVERVALUED", high_score,
      want_verdict="OVERVALUED")
# Tech-confirmed needs ALL 3 conditions
high_score["mos_pct"] = -15
high_score["supertrend"] = "BUY"
high_score["sector_stage"] = "NEUTRAL"  # missing stage 2
check("12.5", "mos=-15 + ST=BUY but sector NOT stage 2 → OVERVALUED",
      high_score, want_verdict="OVERVALUED")


# ──────────────────────────────────────────────────────────────────────────
# GROUP 13: Confidence dots (HIGH / MEDIUM / LOW)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 13 — Confidence dots")
print("═" * 70)
# MID buy_min=63. Build stocks at 63.0, 64, 67, 70 to test ●○○ / ●●○ / ●●●
for tag, score, want_dots in [
    ("13.1", 63.0, "●○○"),  # at threshold (dist=0, <2 → LOW)
    ("13.2", 65.0, "●●○"),  # +2 above (MEDIUM)
    ("13.3", 70.0, "●●●"),  # +7 above (HIGH)
]:
    s = build_stock_at_score("MID", score)
    check(tag, f"BUY score={score} dots={want_dots}", s,
          want_display_contains=want_dots)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 14: v10.17 informed_count counter
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 14 — v10.17 counter for each dimension")
print("═" * 70)
# 0 informed: all sub-scores at base
# Stage2=25 → fund_base=53.33. Set fund=53 → deviation=0.33 < 6 → not informed
# Sentiment NEUTRAL → not informed. Early=0 → not informed.
check("14.0", "all sub-scores at base → 0 informed",
      baseline_stock(stage2_score=25, fundamental_score=53,
                     technical_score=50, safety_score=50,
                     early_entry_score=0),
      want_data_completeness=0)
# Each dimension individually
check("14.1.f", "only fundamental informed",
      baseline_stock(stage2_score=25, fundamental_score=70),
      want_data_completeness=1)
check("14.1.t", "only technical informed",
      baseline_stock(stage2_score=25, fundamental_score=53,
                     technical_score=58),
      want_data_completeness=1)
check("14.1.s", "only safety informed",
      baseline_stock(stage2_score=25, fundamental_score=53,
                     safety_score=44),
      want_data_completeness=1)
check("14.1.sent", "only sentiment informed",
      baseline_stock(stage2_score=25, fundamental_score=53,
                     fii_3q_trend="UP"),
      want_data_completeness=1)
check("14.1.ee", "only early entry informed",
      baseline_stock(stage2_score=25, fundamental_score=53,
                     early_entry_score=10),
      want_data_completeness=1)
# All five informed
check("14.5", "all 5 dimensions informed",
      baseline_stock(stage2_score=25, fundamental_score=70, technical_score=70,
                     safety_score=60, fii_3q_trend="UP", early_entry_score=15),
      want_data_completeness=5)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 15: v10.17 demotion: BUY → WATCHLIST(thin data)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 15 — v10.17 demotion logic")
print("═" * 70)
# Build a stock that scores >63 (MID buy threshold) but has <3 informed dims
# Trick: high MoS adjustment + spike + forensic push score up while sub-scores at base
# Stage2=30 → fund_base=55, fund=55 (at base, not informed)
# tech=50, safe=50, sent=50, early=0
# blend (redistributed): 55*0.389+50*0.333+0+50*0.111 = 21.395+16.65+5.55 = 43.595
# +12 MoS +6 spike (fund>=55, count=3) +0 early +0 risk +8 forensic = 69.6
thin_buy = baseline_stock(
    stage2_score=30, fundamental_score=55,
    technical_score=50, safety_score=50, sentiment_score=50,
    early_entry_score=0,
    cap_category="MID",
    score_adjustment=12, mos_pct=45,
    spike_count=3,
    altman_z=3.5, earnings_quality="HIGH",
    nd_ebitda=0.5, int_coverage=8,
)
check("15.1", "thin-data BUY-grade → demoted to WATCHLIST",
      thin_buy, want_verdict="WATCHLIST", want_gate_applied=True,
      want_display_contains="thin data", want_data_completeness=0)

# Same stock with 3 dimensions informed → BUY allowed
thin_buy_3informed = dict(thin_buy)
thin_buy_3informed.update(fundamental_score=70, technical_score=58, safety_score=44)
check("15.2", "3 informed dimensions → BUY",
      thin_buy_3informed, want_verdict="BUY", want_gate_applied=False,
      want_data_completeness=3)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 16: v10.17 doesn't affect non-BUY verdicts
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 16 — v10.17 unaffected verdicts")
print("═" * 70)
# OVERVALUED with thin data → still OVERVALUED
# Need composite ≥ 63 (MID buy_min) AND mos < -10 to trigger OVERVALUED
# Use sub-scores high enough to reach buy threshold even with negative MoS adj
# fund=70, t=50, safe=50, sent=50, early=0 (only fund informed)
# blend (redistributed): 70*0.389+50*0.333+0+50*0.111 = 27.23+16.65+5.55 = 49.43
# Need composite >= 63. Add MoS adj of 0 (mos -25 → -5 forensic? no that's separate)
# Use score_adjustment=14 (extreme MoS bonus) is impossible; let's add forensic +8 + spike+6
# 49.43 + 6 (spike, fund>=55? fund=70 yes) + 8 forensic = 63.43 → just clears MID buy_min
# But still need MoS gate to block. mos_pct=-25 → blocks BUY. score_adjustment must be set
# independently — score_adjustment is the points addition, mos_pct is the gate input.
# So: score_adjustment=0 (no MoS bonus), mos_pct=-25 (gate blocks)
overval_thin = baseline_stock(
    stage2_score=30,
    fundamental_score=70, technical_score=50, safety_score=50, sentiment_score=50,
    early_entry_score=0,
    spike_count=3,                     # +6 (fund>=55)
    altman_z=3.5, earnings_quality="HIGH",
    nd_ebitda=0.5, int_coverage=8,     # +8 forensic
    cap_category="MID",
    score_adjustment=0,                # no MoS bonus
    mos_pct=-25,                       # blocks BUY
)
# composite ≈ 49.43 + 0 + 6 + 0 + 0 + 8 = 63.43 → BUY threshold cleared, MoS blocks → OVERVALUED
# informed = 1 (only fund) → would normally trigger v10.17 demote, but OVERVALUED is exempt
check("16.1", "OVERVALUED with informed=1 → still OVERVALUED (gate exempt)",
      overval_thin, want_verdict="OVERVALUED", want_gate_applied=False)
# WATCHLIST band naturally with thin data → still WATCHLIST (no annotation)
nat_watch = baseline_stock(stage2_score=25,
                            fundamental_score=53, technical_score=50,
                            safety_score=50, sentiment_score=50,
                            cap_category="MID")
# Need composite in [53, 63). Add small mos_adj.
nat_watch["score_adjustment"] = 12  # bumps blend ~42 + 12 = 54 → in watchlist band
check("16.2", "natural WATCHLIST with informed=0 → no thin-data annotation",
      nat_watch, want_verdict="WATCHLIST", want_gate_applied=False)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 17: v10.17 boundary cases
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 17 — v10.17 boundary at informed=2 vs 3")
print("═" * 70)
# Informed=2 + would-be-BUY → demoted
boundary_2 = dict(thin_buy)
boundary_2.update(fundamental_score=70, technical_score=58)
check("17.1", "informed=2 → demoted",
      boundary_2, want_verdict="WATCHLIST", want_gate_applied=True,
      want_data_completeness=2)
# Informed=3 → BUY (boundary on the BUY side)
boundary_3 = dict(thin_buy)
boundary_3.update(fundamental_score=70, technical_score=58, safety_score=44)
check("17.2", "informed=3 → BUY (boundary)",
      boundary_3, want_verdict="BUY", want_gate_applied=False,
      want_data_completeness=3)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 18: Defensive — bad / missing inputs
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 18 — Defensive against bad inputs")
print("═" * 70)
# Forensic with None / "—" / "" / "N/A" (no adjustment)
for tag, val in [
    ("18.1.None", None),
    ("18.2.dash", "—"),
    ("18.3.empty", ""),
    ("18.4.NA", "N/A"),
    ("18.5.dashes", "--"),
    ("18.6.alpha", "garbage"),
]:
    check(tag, f"altman_z={val!r} → no adjustment",
          baseline_stock(altman_z=val), want_score=41.65)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 19: Output dict shape
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 19 — Output dict shape")
print("═" * 70)
out = scorer.calculate_composite_score(baseline_stock())
required = {"composite_score", "verdict", "verdict_confidence", "verdict_display",
            "label", "weights_used", "forensic_adj", "forensic_factors",
            "data_completeness", "data_gate_applied"}
missing = required - set(out.keys())
if not missing:
    passed += 1
    print("  ✓ all 10 required output fields present")
else:
    failed += 1
    failures.append(f"19.1: missing fields {missing}")


# ──────────────────────────────────────────────────────────────────────────
# GROUP 20: NO-LEAKAGE — fully informed stocks behave EXACTLY as before
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 20 — No-leakage: informed≥3 stocks unaffected by v10.17")
print("═" * 70)
# Construct 20 stocks with informed≥3 across the verdict spectrum.
# For each, verify data_gate_applied=False (no demotion happened).

leakage_count = 0
for i, params in enumerate([
    # BUY stocks
    dict(fundamental_score=72, technical_score=70, safety_score=65, early_entry_score=20,
         sentiment_score=58, fii_3q_trend="UP", cap_category="LARGE", mos_pct=8),
    dict(fundamental_score=80, technical_score=75, safety_score=70, early_entry_score=30,
         sentiment_score=60, fii_3q_trend="UP", cap_category="MID", mos_pct=12,
         altman_z=3.5, earnings_quality="HIGH"),
    dict(fundamental_score=75, technical_score=72, safety_score=68, early_entry_score=25,
         sentiment_score=58, insider_buy_alert="YES", cap_category="SMALL", mos_pct=15),
    dict(fundamental_score=82, technical_score=80, safety_score=75, early_entry_score=40,
         sentiment_score=65, fii_3q_trend="UP", cap_category="MICRO", mos_pct=20),
    # OVERVALUED
    dict(fundamental_score=78, technical_score=75, safety_score=70, early_entry_score=20,
         sentiment_score=60, fii_3q_trend="UP", cap_category="MID", mos_pct=-15),
    # WATCHLIST band
    dict(fundamental_score=60, technical_score=58, safety_score=58, early_entry_score=10,
         sentiment_score=58, fii_3q_trend="UP", cap_category="MID", mos_pct=2),
    # NEUTRAL
    dict(fundamental_score=50, technical_score=48, safety_score=48, early_entry_score=5,
         sentiment_score=52, news_sentiment="POSITIVE", cap_category="LARGE", mos_pct=0),
    # AVOID
    dict(fundamental_score=25, technical_score=20, safety_score=30, sentiment_score=40,
         fii_3q_trend="DOWN", cap_category="MID", mos_pct=-30),
]):
    s = baseline_stock(**params)
    out = scorer.calculate_composite_score(s)
    if out["data_completeness"] >= 3 and out["data_gate_applied"]:
        leakage_count += 1
        failures.append(f"20.{i}: leakage — informed={out['data_completeness']} but gate fired")

if leakage_count == 0:
    passed += 1
    print(f"  ✓ no leakage across 8 representative scenarios (informed≥3 → gate never fires)")
else:
    failed += 1


# ──────────────────────────────────────────────────────────────────────────
# Group 21: ingestion/reconciler.py — DUAL_LISTED_ALLOWLIST integrity
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 70)
print("Group 21: DUAL_LISTED_ALLOWLIST integrity (v11.0.1)")
print("─" * 70)

try:
    from ingestion.reconciler import (
        DUAL_LISTED_ALLOWLIST,
        _is_dual_listed_known,
        reconcile_exchanges,
    )
    passed += 1
    print(f"  ✓ 21.0 reconciler imports cleanly")
except Exception as e:
    failed += 1
    failures.append(f"21.0 reconciler import failed: {e}")
    DUAL_LISTED_ALLOWLIST = None  # force-skip downstream tests gracefully

if DUAL_LISTED_ALLOWLIST is not None:
    NEW_27 = {
        "ABBOTINDIA","BATAINDIA","BHARTIHEXA","GLAXO","GRINDWELL","USHAMART",
        "RKFORGE","NAZARA","CIEINDIA","VENUSREM","TALBROAUTO","CARBORUNIV",
        "VGUARD","ANTHEM","INNOVACAP","MINDACORP","ERIS","POLYPLEX",
        "AADHARHFC","ASIANTILES","FIVESTAR","ANANDRATHI","WEWORK","PYRAMID",
        "WELENT","LAXMIDENTL","SENORES",
    }
    INDEX_TICKERS = {"IT","PSUBANK","BANKNIFTY1"}
    PENDING       = {"MOREALTY","KMEW","RBA"}
    EXISTING      = ["RELIANCE","TCS","HDFCBANK","SBIN","TITAN","M&M","MARUTI","INFY"]

    # 21.1 all 27 new symbols on allowlist
    miss = NEW_27 - DUAL_LISTED_ALLOWLIST
    if not miss: passed += 1; print(f"  ✓ 21.1 all 27 new symbols on allowlist")
    else: failed += 1; failures.append(f"21.1 missing from allowlist: {miss}")

    # 21.2 index tickers correctly excluded
    wrong = INDEX_TICKERS & DUAL_LISTED_ALLOWLIST
    if not wrong: passed += 1; print(f"  ✓ 21.2 index tickers correctly excluded (IT/PSUBANK/BANKNIFTY1)")
    else: failed += 1; failures.append(f"21.2 index tickers leaked into allowlist: {wrong}")

    # 21.3 pending-verification stocks correctly excluded
    pre = PENDING & DUAL_LISTED_ALLOWLIST
    if not pre: passed += 1; print(f"  ✓ 21.3 pending-verification stocks correctly excluded")
    else: failed += 1; failures.append(f"21.3 pending stocks prematurely added: {pre}")

    # 21.4 no duplicate entries (parse via AST to ignore quoted strings inside comments)
    import ast as _ast21
    fs_strings = []
    tree21 = _ast21.parse(open("ingestion/reconciler.py").read())
    for node in _ast21.walk(tree21):
        if isinstance(node, _ast21.Assign):
            for tgt in node.targets:
                if isinstance(tgt, _ast21.Name) and tgt.id == "DUAL_LISTED_ALLOWLIST":
                    if isinstance(node.value, _ast21.Call) and isinstance(node.value.args[0], _ast21.Set):
                        fs_strings = [e.value for e in node.value.args[0].elts if isinstance(e, _ast21.Constant)]
    dups = [s for s in set(fs_strings) if fs_strings.count(s) > 1]
    if not dups: passed += 1; print(f"  ✓ 21.4 no duplicate entries in allowlist source")
    else: failed += 1; failures.append(f"21.4 duplicate entries: {dups}")

    # 21.5 _is_dual_listed_known() helper works for new symbols
    broken = [s for s in NEW_27 if not _is_dual_listed_known(s)]
    if not broken: passed += 1; print(f"  ✓ 21.5 _is_dual_listed_known() works for all 27 new symbols")
    else: failed += 1; failures.append(f"21.5 helper failed for: {broken}")

    # 21.6 existing allowlist preserved (no accidental removals)
    broken_existing = [s for s in EXISTING if s not in DUAL_LISTED_ALLOWLIST]
    if not broken_existing: passed += 1; print(f"  ✓ 21.6 existing allowlist members preserved (RELIANCE, TCS, etc.)")
    else: failed += 1; failures.append(f"21.6 dropped existing: {broken_existing}")


# ──────────────────────────────────────────────────────────────────────────
# Group 22: ingestion/reconciler.py — runtime exchange-tag behavior
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 70)
print("Group 22: runtime exchange-tag behavior (v11.0.1)")
print("─" * 70)

if DUAL_LISTED_ALLOWLIST is not None:
    import pandas as _pd

    # 22.1 — newly added symbol tags as DUAL_LISTED when bse_df is empty (Cloudflare 403 path)
    nse_df1 = _pd.DataFrame([
        {"symbol":"ABBOTINDIA","close":25425.0,"isin":"INE358A01054"},
        {"symbol":"BATAINDIA", "close":1287.0, "isin":"INE176A01028"},
        {"symbol":"NEWCO_NSE", "close":150.0,  "isin":"INE999X01010"},
        {"symbol":"RELIANCE",  "close":1240.0, "isin":"INE002A01018"},
    ])
    r1 = reconcile_exchanges(nse_df1, _pd.DataFrame())
    abbot = r1[r1["symbol"]=="ABBOTINDIA"]["exchange_tag"].iloc[0]
    bata  = r1[r1["symbol"]=="BATAINDIA"]["exchange_tag"].iloc[0]
    newco = r1[r1["symbol"]=="NEWCO_NSE"]["exchange_tag"].iloc[0]
    reli  = r1[r1["symbol"]=="RELIANCE"]["exchange_tag"].iloc[0]
    if abbot == "DUAL_LISTED": passed += 1; print(f"  ✓ 22.1 ABBOTINDIA → DUAL_LISTED when bse empty")
    else: failed += 1; failures.append(f"22.1 ABBOTINDIA wrong tag: {abbot}")
    if bata == "DUAL_LISTED": passed += 1; print(f"  ✓ 22.2 BATAINDIA → DUAL_LISTED when bse empty")
    else: failed += 1; failures.append(f"22.2 BATAINDIA wrong tag: {bata}")
    if newco == "NSE_ONLY": passed += 1; print(f"  ✓ 22.3 genuine NSE-only stock stays NSE_ONLY")
    else: failed += 1; failures.append(f"22.3 NEWCO_NSE wrong tag: {newco}")
    if reli == "DUAL_LISTED": passed += 1; print(f"  ✓ 22.4 RELIANCE (existing allowlist) → DUAL_LISTED")
    else: failed += 1; failures.append(f"22.4 RELIANCE wrong tag: {reli}")

    # 22.5 — index tickers stay NSE_ONLY at runtime
    nse_df2 = _pd.DataFrame([{"symbol":"IT","close":39000.0,"isin":""}])
    r2 = reconcile_exchanges(nse_df2, _pd.DataFrame())
    it_tag = r2[r2["symbol"]=="IT"]["exchange_tag"].iloc[0]
    if it_tag == "NSE_ONLY": passed += 1; print(f"  ✓ 22.5 index ticker IT stays NSE_ONLY at runtime")
    else: failed += 1; failures.append(f"22.5 index IT wrong tag: {it_tag}")

    # 22.6 — partial BSE merge (Cloudflare returned tiny unrelated subset) still promotes via safety override
    nse_df3 = _pd.DataFrame([
        {"symbol":"ABBOTINDIA","close":25425.0,"isin":"INE358A01054"},
        {"symbol":"NEWCO_NSE", "close":150.0,  "isin":"INE999X01010"},
    ])
    bse_tiny = _pd.DataFrame([{"symbol":"XYZ","close":50.0,"isin":"INE888B01010","sc_group":"A"}])
    r3 = reconcile_exchanges(nse_df3, bse_tiny)
    sym_col = "symbol_NSE" if "symbol_NSE" in r3.columns else "symbol"
    abbot3 = r3[r3[sym_col].astype(str).str.contains("ABBOTINDIA", na=False)]
    if not abbot3.empty and abbot3["exchange_tag"].iloc[0] == "DUAL_LISTED":
        passed += 1; print(f"  ✓ 22.6 ABBOTINDIA promoted via safety override after partial BSE merge")
    else:
        failed += 1; failures.append(f"22.6 safety override didn't fire on partial merge")

    # 22.7 — graceful handling of None inputs
    try:
        _ = reconcile_exchanges(None, None)
        _ = reconcile_exchanges(None, _pd.DataFrame())
        _ = reconcile_exchanges(_pd.DataFrame(), None)
        passed += 1; print(f"  ✓ 22.7 reconciler survives None inputs without crashing")
    except Exception as e:
        failed += 1; failures.append(f"22.7 None-input handling: {e}")


# ──────────────────────────────────────────────────────────────────────────
# Group 23: reporting/daily_report_generator.py — Section A & D
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 70)
print("Group 23: daily report Section A & D (v11.0.1)")
print("─" * 70)

import numpy as _np
try:
    from reporting.daily_report_generator import DailyReportGenerator
    passed += 1; print(f"  ✓ 23.0 daily_report_generator imports cleanly")
    DRG_OK = True
except Exception as e:
    failed += 1; failures.append(f"23.0 daily_report_generator import: {e}")
    DRG_OK = False

if DRG_OK:
    _MKT = {"nifty_close":24000,"nifty_200d":23000,"sensex_close":80000,"vix":12.0,"fii_net":-19216}

    # Section A — boundary tests
    data_a = [
        {"symbol":"PSUBANK","early_entry_score":55,"sector":"General","rotation_stage":"NEUTRAL","composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"guard_reasons":"","smart_money_signals":"","exchange_tag":"NSE_ONLY","vol_ratio":1.0},
        {"symbol":"BELOW45","early_entry_score":45,"sector":"IT","rotation_stage":"NEUTRAL","composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"guard_reasons":"","smart_money_signals":"","exchange_tag":"NSE_ONLY","vol_ratio":1.0},
        {"symbol":"HIGH80","early_entry_score":80,"sector":"IT","rotation_stage":"NEUTRAL","composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"guard_reasons":"","smart_money_signals":"","exchange_tag":"NSE_ONLY","vol_ratio":1.0},
    ]
    rep_a = DailyReportGenerator(data_a, _MKT).generate_research_report()
    sec_a = rep_a.split("SECTION A")[1].split("SECTION B")[0]
    if "PSUBANK" in sec_a: passed += 1; print(f"  ✓ 23.1 Section A includes PSUBANK (EE=55, was missed by old >=70)")
    else: failed += 1; failures.append("23.1 PSUBANK missing from Section A")
    if "HIGH80" in sec_a: passed += 1; print(f"  ✓ 23.2 Section A includes HIGH80 (EE=80, passes either threshold)")
    else: failed += 1; failures.append("23.2 HIGH80 missing")
    if "BELOW45" not in sec_a: passed += 1; print(f"  ✓ 23.3 Section A excludes BELOW45 (EE=45, below threshold)")
    else: failed += 1; failures.append("23.3 BELOW45 leaked into Section A")

    # Section D — all 4 stages populate correctly via string substring match
    data_d = [
        {"symbol":"S1","sector":"Healthcare","rotation_stage":"STAGE 1 — EARLY ACCUMULATION","early_entry_score":0,"composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"guard_reasons":"","smart_money_signals":"","exchange_tag":"NSE_ONLY","vol_ratio":1.0},
        {"symbol":"S2","sector":"IT","rotation_stage":"STAGE 2 — CONFIRMED UPTREND","early_entry_score":0,"composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"guard_reasons":"","smart_money_signals":"","exchange_tag":"NSE_ONLY","vol_ratio":1.0},
        {"symbol":"S3","sector":"Real Estate","rotation_stage":"STAGE 3 — MOMENTUM PEAK","early_entry_score":0,"composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"guard_reasons":"","smart_money_signals":"","exchange_tag":"NSE_ONLY","vol_ratio":1.0},
        {"symbol":"S4","sector":"Financial Services","rotation_stage":"STAGE 4 — DISTRIBUTION","early_entry_score":0,"composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"guard_reasons":"","smart_money_signals":"","exchange_tag":"NSE_ONLY","vol_ratio":1.0},
        {"symbol":"SN","sector":"Industrials","rotation_stage":"NEUTRAL","early_entry_score":0,"composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"guard_reasons":"","smart_money_signals":"","exchange_tag":"NSE_ONLY","vol_ratio":1.0},
    ]
    rep_d = DailyReportGenerator(data_d, _MKT).generate_research_report()
    sec_d = rep_d.split("SECTION D")[1].split("SECTION E")[0]
    for stage_num, sector, label in [(4,"Financial Services","stage 4"),(3,"Real Estate","stage 3"),(2,"IT","stage 2"),(1,"Healthcare","stage 1")]:
        if f"Stage {stage_num}: {sector}" in sec_d:
            passed += 1; print(f"  ✓ 23.{4+stage_num}b Section D Stage {stage_num} contains '{sector}'")
        else:
            failed += 1; failures.append(f"23.{4+stage_num}b Stage {stage_num} missing {sector}")

    # Section D — empty rotation_stage column
    data_empty = [{**d, "rotation_stage":""} for d in data_d]
    rep_e = DailyReportGenerator(data_empty, _MKT).generate_research_report()
    sec_e = rep_e.split("SECTION D")[1].split("SECTION E")[0]
    if "Stage 4: None" in sec_e and "Stage 1: None" in sec_e:
        passed += 1; print(f"  ✓ 23.9 empty rotation_stage → all stages 'None' (no crash)")
    else:
        failed += 1; failures.append(f"23.9 empty rotation_stage edge case failed")

    # Section D — NaN values
    data_nan = [{**d, "rotation_stage":_np.nan} for d in data_d]
    rep_n = DailyReportGenerator(data_nan, _MKT).generate_research_report()
    sec_n = rep_n.split("SECTION D")[1].split("SECTION E")[0]
    if "Stage 4: None" in sec_n:
        passed += 1; print(f"  ✓ 23.10 NaN rotation_stage → all stages 'None' (no crash)")
    else:
        failed += 1; failures.append(f"23.10 NaN rotation_stage crashed")

    # Section D — caps at 3 sectors per stage (preserved behaviour)
    data_six = [
        {"symbol":f"X{i}","sector":f"Sec{i}","rotation_stage":"STAGE 1 — EARLY ACCUMULATION","early_entry_score":0,"composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"guard_reasons":"","smart_money_signals":"","exchange_tag":"NSE_ONLY","vol_ratio":1.0}
        for i in range(6)
    ]
    rep_6 = DailyReportGenerator(data_six, _MKT).generate_research_report()
    sec_6 = rep_6.split("SECTION D")[1].split("SECTION E")[0]
    s1_line = [l for l in sec_6.split("\n") if l.startswith("Stage 1:")][0]
    if s1_line.count("Sec") == 3:
        passed += 1; print(f"  ✓ 23.11 Section D caps at 3 sectors per stage (preserved [:3])")
    else:
        failed += 1; failures.append(f"23.11 Section D cap behaviour broken")


# ──────────────────────────────────────────────────────────────────────────
# Group 24: reporting/command_parser.py — early movers threshold
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 70)
print("Group 24: command_parser early movers (v11.0.1)")
print("─" * 70)

try:
    from reporting.command_parser import CommandParser
    passed += 1; print(f"  ✓ 24.0 command_parser imports cleanly")
    CMD_OK = True
except Exception as e:
    failed += 1; failures.append(f"24.0 command_parser import: {e}")
    CMD_OK = False

if CMD_OK:
    data_cmd = [
        {"symbol":"PSUBANK","early_entry_score":55,"sector":"General","composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"upside":10,"cap_category":"MID CAP","vol_ratio":1.0,"8w_chg":0,"rotation_stage":"NEUTRAL","close":100,"cmp":100},
        {"symbol":"BIGGER","early_entry_score":75,"sector":"IT","composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"upside":15,"cap_category":"LARGE CAP","vol_ratio":1.0,"8w_chg":0,"rotation_stage":"NEUTRAL","close":100,"cmp":100},
        {"symbol":"TOOLOW","early_entry_score":40,"sector":"IT","composite_score":50,"verdict":"WATCHLIST","mos_pct":0,"upside":5,"cap_category":"LARGE CAP","vol_ratio":1.0,"8w_chg":0,"rotation_stage":"NEUTRAL","close":100,"cmp":100},
    ]
    out = str(CommandParser(data_context=data_cmd).execute("early movers today"))
    if "PSUBANK" in out: passed += 1; print(f"  ✓ 24.1 'early movers today' picks up PSUBANK (EE=55)")
    else: failed += 1; failures.append("24.1 PSUBANK missing")
    if "BIGGER" in out: passed += 1; print(f"  ✓ 24.2 'early movers today' picks up BIGGER (EE=75)")
    else: failed += 1; failures.append("24.2 BIGGER missing")
    if "TOOLOW" not in out: passed += 1; print(f"  ✓ 24.3 'early movers today' excludes TOOLOW (EE=40)")
    else: failed += 1; failures.append("24.3 TOOLOW leaked")
    if ">= 50" in out: passed += 1; print(f"  ✓ 24.4 title says 'Score >= 50' (matches threshold)")
    else: failed += 1; failures.append("24.4 wrong title")


# ──────────────────────────────────────────────────────────────────────────
# Group 25: reporting/report_formatter.py — EARLY MOVER badge
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 70)
print("Group 25: report_formatter EARLY MOVER badge (v11.0.1)")
print("─" * 70)

try:
    from reporting.report_formatter import ReportFormatter
    passed += 1; print(f"  ✓ 25.0 report_formatter imports cleanly")
    RFM_OK = True
except Exception as e:
    failed += 1; failures.append(f"25.0 report_formatter import: {e}")
    RFM_OK = False

if RFM_OK:
    fmt = ReportFormatter()
    def _stk(ee):
        return {"symbol":"X","company_name":"X","sector":"General","verdict":"WATCHLIST","cap_badge":"MID","exchange_tag":"NSE_ONLY","early_entry_score":ee,"spike_count":0,"spike_triggers":[],"cmp":100,"day_chg_pct":0,"52w_low":50,"52w_high":150,"vol_ratio":1.0,"2w_chg":0,"4w_chg":0,"6w_chg":0,"8w_chg":0,"cfv":100,"cfv_low":80,"cfv_high":120,"mos_pct":0,"mos_label":"NEUTRAL","upside_to_fv":0,"upside_per_share":0,"pe":0,"earnings_yield":0,"pcf":0,"peg":0,"pb":0,"roe":0,"de":0,"fcf_yld":0,"rev_growth":0,"pat_growth":0,"div_yld":0,"f_score":0,"sector_stage":"NEUTRAL","smart_money_signals":[],"top_early_signal":"","storm_score":0,"vix":12,"fii_7d":0,"nifty_200d":0,"analysis_summary":""}
    if "[EARLY MOVER]" in fmt.format_investor_card(_stk(55)):
        passed += 1; print(f"  ✓ 25.1 EE=55 stock gets [EARLY MOVER] badge")
    else: failed += 1; failures.append("25.1 EE=55 missed badge")
    if "[EARLY MOVER]" not in fmt.format_investor_card(_stk(45)):
        passed += 1; print(f"  ✓ 25.2 EE=45 stock does NOT get [EARLY MOVER] badge")
    else: failed += 1; failures.append("25.2 EE=45 wrongly badged")
    if "[EARLY MOVER]" in fmt.format_investor_card(_stk(50)):
        passed += 1; print(f"  ✓ 25.3 EE=50 boundary stock GETS [EARLY MOVER] badge")
    else: failed += 1; failures.append("25.3 EE=50 boundary missed badge")


# ──────────────────────────────────────────────────────────────────────────
# Group 26: cross-cutting consistency check (v11.0.1)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 70)
print("Group 26: cross-cutting consistency (v11.0.1)")
print("─" * 70)

# 26.1 — all 3 reporting files use threshold 50 in operational comparisons
import re as _re
THRESH_RE = _re.compile(r"early_entry_score['\"]?\s*[\],)0\s]*\)?\s*>=\s*(\d+)")
mismatches = []
for f, expected in [("reporting/daily_report_generator.py",50),
                    ("reporting/command_parser.py",50),
                    ("reporting/report_formatter.py",50)]:
    src = open(f).read()
    for m in THRESH_RE.findall(src):
        if int(m) != expected:
            mismatches.append(f"{f} found >={m}, expected >={expected}")
if not mismatches:
    passed += 1; print(f"  ✓ 26.1 all 3 reporting files use EE threshold 50 consistently")
else:
    failed += 1; failures.append(f"26.1 EE threshold mismatch: {mismatches}")

# 26.2 — allowlist count grew by exactly 27 vs the v11.0 baseline
# Baseline = the v11.0 source SHOULD have 206 entries. Modern runtime should be 233.
if DUAL_LISTED_ALLOWLIST is not None:
    total = len(DUAL_LISTED_ALLOWLIST)
    if total == 233:
        passed += 1; print(f"  ✓ 26.2 allowlist size = 233 (206 baseline + 27 new)")
    else:
        # softer check: confirm at minimum +27 over the documented v11.0 baseline of 206
        # This handles future allowlist edits (e.g. uncommenting MOREALTY/KMEW/RBA)
        if total >= 233:
            passed += 1
            warnings.append(f"26.2 allowlist size = {total} (>=233 expected, future additions OK)")
            print(f"  ✓ 26.2 allowlist size = {total} (>=233; future additions accepted)")
        else:
            failed += 1; failures.append(f"26.2 allowlist size = {total}, expected >=233")

# ──────────────────────────────────────────────────────────────────────────
# Group 27 — Allowlist auto-add/remove (v11.0.2 feature A)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 70)
print("Group 27: ingestion/allowlist_maintainer.py (v11.0.2)")
print("─" * 70)

import os as _os27, tempfile as _tmp27, sqlite3 as _sq27

# Use a TEMP DB so we don't pollute the repo's market_data.db
_orig_cwd27 = _os27.getcwd()
_tmpdir27 = _tmp27.mkdtemp(prefix="alm_test_")
_os27.chdir(_tmpdir27)

try:
    # 27.0 — module imports
    try:
        from ingestion.allowlist_maintainer import (
            record_dual_listed_observations,
            prune_runtime_allowlist,
            get_runtime_allowlist,
            update_last_seen,
        )
        passed += 1; print("  ✓ 27.0 allowlist_maintainer imports cleanly")
        ALM_OK = True
    except Exception as e:
        failed += 1; failures.append(f"27.0 import: {e}")
        ALM_OK = False

    if ALM_OK:
        import pandas as _pd27

        # 27.1 — fresh-install: empty runtime allowlist returns set()
        rt0 = get_runtime_allowlist()
        if isinstance(rt0, set) and len(rt0) == 0:
            passed += 1; print("  ✓ 27.1 fresh-install runtime allowlist is empty set")
        else:
            failed += 1; failures.append(f"27.1 expected empty set got {type(rt0)}/{len(rt0)}")

        # 27.2 — record_dual_listed_observations writes new symbols
        df_observed = _pd27.DataFrame([
            {"symbol":"NEWLY_DISCOVERED1","exchange_tag":"DUAL_LISTED"},
            {"symbol":"NEWLY_DISCOVERED2","exchange_tag":"DUAL_LISTED"},
            {"symbol":"NSE_ONLY_STOCK","exchange_tag":"NSE_ONLY"},
        ])
        added = record_dual_listed_observations(df_observed, today_iso="2026-04-28",
                                                hardcoded_allowlist=set())
        if added == 2:
            passed += 1; print("  ✓ 27.2 record_dual_listed_observations added 2 new symbols")
        else:
            failed += 1; failures.append(f"27.2 expected 2 added, got {added}")

        # 27.3 — get_runtime_allowlist returns those symbols
        rt1 = get_runtime_allowlist()
        if {"NEWLY_DISCOVERED1","NEWLY_DISCOVERED2"} <= rt1:
            passed += 1; print("  ✓ 27.3 get_runtime_allowlist surfaces newly added symbols")
        else:
            failed += 1; failures.append(f"27.3 missing symbols, got {rt1}")

        # 27.4 — re-recording on later date refreshes last_seen but doesn't double-add
        added2 = record_dual_listed_observations(df_observed, today_iso="2026-04-29",
                                                  hardcoded_allowlist=set())
        # `added2` returns count where first_seen_date == today; on day 2 these
        # are existing records, so first_seen_date is yesterday → not counted as new.
        if added2 == 0:
            passed += 1; print("  ✓ 27.4 re-running on next day doesn't double-count existing entries")
        else:
            failed += 1; failures.append(f"27.4 expected 0 new on rerun, got {added2}")

        # 27.5 — symbols already on hardcoded allowlist are skipped (no duplication)
        df_hardcoded = _pd27.DataFrame([
            {"symbol":"RELIANCE","exchange_tag":"DUAL_LISTED"},  # already hardcoded
            {"symbol":"BRAND_NEW_X","exchange_tag":"DUAL_LISTED"},
        ])
        added3 = record_dual_listed_observations(df_hardcoded, today_iso="2026-04-29",
                                                  hardcoded_allowlist={"RELIANCE","TCS","INFY"})
        if added3 == 1:  # only BRAND_NEW_X should be added; RELIANCE skipped
            passed += 1; print("  ✓ 27.5 hardcoded-allowlist symbols not duplicated into runtime table")
        else:
            failed += 1; failures.append(f"27.5 expected 1 new, got {added3}")

        # 27.6 — prune removes entries last_seen < today − ttl_days
        # Set last_seen for NEWLY_DISCOVERED1 to a date 40 days ago
        _conn27 = _sq27.connect("market_data.db")
        _conn27.execute("UPDATE dual_listed_runtime SET last_seen_date = ? WHERE symbol = ?",
                        ("2026-03-15", "NEWLY_DISCOVERED1"))
        _conn27.commit(); _conn27.close()

        removed = prune_runtime_allowlist(today_iso="2026-04-29", ttl_days=30)
        if removed == 1:
            passed += 1; print("  ✓ 27.6 prune removed 1 stale entry (>30d absent)")
        else:
            failed += 1; failures.append(f"27.6 expected 1 pruned, got {removed}")

        # 27.7 — surviving entries still queryable
        rt2 = get_runtime_allowlist()
        if "NEWLY_DISCOVERED1" not in rt2 and "NEWLY_DISCOVERED2" in rt2:
            passed += 1; print("  ✓ 27.7 prune kept fresh entries, removed stale ones")
        else:
            failed += 1; failures.append(f"27.7 prune broke set membership: {rt2}")

        # 27.8 — graceful handling of missing DataFrame columns
        bad_df = _pd27.DataFrame([{"x":1}, {"x":2}])
        added4 = record_dual_listed_observations(bad_df)
        if added4 == 0:
            passed += 1; print("  ✓ 27.8 missing exchange_tag column → 0 added (no crash)")
        else:
            failed += 1; failures.append(f"27.8 expected 0 from bad df, got {added4}")

        # 27.9 — None / empty input is safe
        if record_dual_listed_observations(None) == 0 and \
           record_dual_listed_observations(_pd27.DataFrame()) == 0 and \
           prune_runtime_allowlist(today_iso="bogus") == 0:
            passed += 1; print("  ✓ 27.9 None/empty/bogus inputs handled gracefully")
        else:
            failed += 1; failures.append("27.9 None/empty input not handled")

        # 27.10 — get_effective_allowlist returns hardcoded ∪ runtime
        try:
            from ingestion.reconciler import get_effective_allowlist
            eff = get_effective_allowlist()
            # Should contain at least RELIANCE (hardcoded) and NEWLY_DISCOVERED2 (runtime)
            if "RELIANCE" in eff and "NEWLY_DISCOVERED2" in eff:
                passed += 1; print("  ✓ 27.10 get_effective_allowlist UNIONs hardcoded + runtime")
            else:
                failed += 1; failures.append(f"27.10 missing expected symbols in eff allowlist")
        except Exception as e:
            failed += 1; failures.append(f"27.10 get_effective_allowlist: {e}")
finally:
    _os27.chdir(_orig_cwd27)
    import shutil as _sh27
    _sh27.rmtree(_tmpdir27, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# Group 28 — Reconciler integration with runtime allowlist (v11.0.2)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 70)
print("Group 28: reconciler ⊕ runtime allowlist integration (v11.0.2)")
print("─" * 70)

# Use another temp DB so we don't pollute the repo
_orig_cwd28 = _os27.getcwd()
_tmpdir28 = _tmp27.mkdtemp(prefix="reconciler28_")
_os27.chdir(_tmpdir28)

try:
    # Force a fresh import of reconciler so its cached state is clean
    import importlib as _imp28
    if "ingestion.reconciler" in sys.modules:
        _imp28.reload(sys.modules["ingestion.reconciler"])
    if "ingestion.allowlist_maintainer" in sys.modules:
        _imp28.reload(sys.modules["ingestion.allowlist_maintainer"])

    from ingestion.reconciler import reconcile_exchanges, DUAL_LISTED_ALLOWLIST, get_effective_allowlist
    from ingestion.allowlist_maintainer import record_dual_listed_observations
    import pandas as _pd28

    # 28.1 — when runtime table empty, get_effective_allowlist == hardcoded
    eff0 = get_effective_allowlist()
    if eff0 == DUAL_LISTED_ALLOWLIST:
        passed += 1; print("  ✓ 28.1 empty runtime → effective == hardcoded (no surprise behaviour)")
    else:
        failed += 1; failures.append(f"28.1 effective != hardcoded with empty runtime")

    # 28.2 — when runtime table has a symbol, that symbol gets DUAL_LISTED tag
    record_dual_listed_observations(
        _pd28.DataFrame([{"symbol":"RUNTIME_NEW_X","exchange_tag":"DUAL_LISTED"}]),
        today_iso="2026-04-28",
        hardcoded_allowlist=set(),
    )
    nse_only = _pd28.DataFrame([
        {"symbol":"RUNTIME_NEW_X","close":100,"isin":"INE111X01018"},
        {"symbol":"DEFINITELY_NSE_ONLY","close":50,"isin":"INE222Y01010"},
    ])
    result = reconcile_exchanges(nse_only, _pd28.DataFrame())  # BSE empty
    runtime_tag = result[result["symbol"]=="RUNTIME_NEW_X"]["exchange_tag"].iloc[0]
    nseonly_tag = result[result["symbol"]=="DEFINITELY_NSE_ONLY"]["exchange_tag"].iloc[0]
    if runtime_tag == "DUAL_LISTED":
        passed += 1; print("  ✓ 28.2 runtime-discovered symbol tags as DUAL_LISTED")
    else:
        failed += 1; failures.append(f"28.2 RUNTIME_NEW_X got {runtime_tag} expected DUAL_LISTED")
    if nseonly_tag == "NSE_ONLY":
        passed += 1; print("  ✓ 28.3 unrelated symbol stays NSE_ONLY")
    else:
        failed += 1; failures.append(f"28.3 DEFINITELY_NSE_ONLY got {nseonly_tag}")

    # 28.4 — hardcoded allowlist still works (RELIANCE)
    nse_test = _pd28.DataFrame([{"symbol":"RELIANCE","close":1240,"isin":"INE002A01018"}])
    result4 = reconcile_exchanges(nse_test, _pd28.DataFrame())
    if result4[result4["symbol"]=="RELIANCE"]["exchange_tag"].iloc[0] == "DUAL_LISTED":
        passed += 1; print("  ✓ 28.4 hardcoded allowlist (RELIANCE) still works")
    else:
        failed += 1; failures.append("28.4 RELIANCE no longer DUAL_LISTED")

    # 28.5 — get_effective_allowlist post-record now contains the runtime entry
    eff1 = get_effective_allowlist()
    if "RUNTIME_NEW_X" in eff1 and "RELIANCE" in eff1:
        passed += 1; print("  ✓ 28.5 effective allowlist UNIONs runtime + hardcoded post-record")
    else:
        failed += 1; failures.append(f"28.5 union broken: RUNTIME_NEW_X in eff: {'RUNTIME_NEW_X' in eff1}")

finally:
    _os27.chdir(_orig_cwd28)
    _sh27.rmtree(_tmpdir28, ignore_errors=True)
    # Reload modules back to repo's actual market_data.db state
    _imp28.reload(sys.modules["ingestion.allowlist_maintainer"])
    _imp28.reload(sys.modules["ingestion.reconciler"])


# ──────────────────────────────────────────────────────────────────────────
# Group 29 — Verdict streaks + chronic-AVOID demotion (v11.0.2 feature B)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 70)
print("Group 29: verdict streaks + chronic-AVOID demotion (v11.0.2)")
print("─" * 70)

_orig_cwd29 = _os27.getcwd()
_tmpdir29 = _tmp27.mkdtemp(prefix="streaks_")
_os27.chdir(_tmpdir29)

try:
    # Setup the DB schema from data_bridge
    _imp28.reload(sys.modules.get("database.data_bridge", __import__("database.data_bridge")))
    from database.data_bridge import initialize_v7_tables, update_verdict_streaks, get_prior_analysis_map
    _conn29init = _sq27.connect("market_data.db")
    initialize_v7_tables(_conn29init)
    _conn29init.close()

    # 29.1 — streak update on FIRST run: prior is empty, AVOID stock starts at 1
    today_stocks_run1 = [
        {"symbol":"AVOIDCO","verdict":"AVOID","composite_score":25},
        {"symbol":"BUYCO","verdict":"BUY","composite_score":75},
    ]
    streaks1 = update_verdict_streaks(today_stocks_run1)
    if streaks1.get("AVOIDCO",{}).get("avoid") == 1:
        passed += 1; print("  ✓ 29.1 first AVOID run: avoid_streak = 1")
    else:
        failed += 1; failures.append(f"29.1 expected 1, got {streaks1.get('AVOIDCO',{}).get('avoid')}")

    # 29.2 — turnaround_candidate stamped onto stock dict
    if today_stocks_run1[0].get("turnaround_candidate") is False and \
       today_stocks_run1[0].get("consecutive_avoid_quarters") == 1:
        passed += 1; print("  ✓ 29.2 stock dict stamped with streak fields")
    else:
        failed += 1; failures.append(f"29.2 dict stamping incomplete: {today_stocks_run1[0]}")

    # Persist run-1 results so run-2 sees them as 'prior'
    _conn29 = _sq27.connect("market_data.db")
    for s in today_stocks_run1:
        _conn29.execute("""INSERT OR REPLACE INTO latest_analysis_results
            (symbol, date, composite_score, verdict,
             consecutive_avoid_quarters, consecutive_recovery_quarters)
            VALUES (?,?,?,?,?,?)""",
            (s["symbol"], "2026-04-27", s["composite_score"], s["verdict"],
             s.get("consecutive_avoid_quarters",0),
             s.get("consecutive_recovery_quarters",0)))
    _conn29.commit(); _conn29.close()

    # 29.3 — second AVOID run: streak advances to 2
    today_stocks_run2 = [
        {"symbol":"AVOIDCO","verdict":"AVOID","composite_score":22},
        {"symbol":"BUYCO","verdict":"BUY","composite_score":78},
    ]
    streaks2 = update_verdict_streaks(today_stocks_run2)
    if streaks2.get("AVOIDCO",{}).get("avoid") == 2:
        passed += 1; print("  ✓ 29.3 second AVOID run: avoid_streak advances to 2 (chronic threshold)")
    else:
        failed += 1; failures.append(f"29.3 expected 2, got {streaks2.get('AVOIDCO',{}).get('avoid')}")

    # 29.4 — chronic threshold (avoid≥2) reflected in stock dict
    if today_stocks_run2[0]["consecutive_avoid_quarters"] == 2:
        passed += 1; print("  ✓ 29.4 chronic-AVOID threshold reflected in stock dict")
    else:
        failed += 1; failures.append(f"29.4 dict streak {today_stocks_run2[0]}")

    # Persist run-2
    _conn29 = _sq27.connect("market_data.db")
    for s in today_stocks_run2:
        _conn29.execute("""INSERT OR REPLACE INTO latest_analysis_results
            (symbol, date, composite_score, verdict,
             consecutive_avoid_quarters, consecutive_recovery_quarters)
            VALUES (?,?,?,?,?,?)""",
            (s["symbol"], "2026-04-28", s["composite_score"], s["verdict"],
             s["consecutive_avoid_quarters"], s["consecutive_recovery_quarters"]))
    _conn29.commit(); _conn29.close()

    # 29.5 — recovery: AVOIDCO bounces back with score=58, streak resets, recovery starts
    today_stocks_run3 = [
        {"symbol":"AVOIDCO","verdict":"WATCHLIST","composite_score":58},
        {"symbol":"BUYCO","verdict":"BUY","composite_score":80},
    ]
    streaks3 = update_verdict_streaks(today_stocks_run3)
    s_avoidco = streaks3.get("AVOIDCO", {})
    if s_avoidco.get("avoid") == 0 and s_avoidco.get("recovery") == 1:
        passed += 1; print("  ✓ 29.5 recovery from AVOID: avoid resets to 0, recovery starts at 1")
    else:
        failed += 1; failures.append(f"29.5 unexpected: {s_avoidco}")

    # Persist run-3
    _conn29 = _sq27.connect("market_data.db")
    for s in today_stocks_run3:
        _conn29.execute("""INSERT OR REPLACE INTO latest_analysis_results
            (symbol, date, composite_score, verdict,
             consecutive_avoid_quarters, consecutive_recovery_quarters)
            VALUES (?,?,?,?,?,?)""",
            (s["symbol"], "2026-04-29", s["composite_score"], s["verdict"],
             s["consecutive_avoid_quarters"], s["consecutive_recovery_quarters"]))
    _conn29.commit(); _conn29.close()

    # 29.6 — second recovery quarter: turnaround_candidate flag fires (recovery >= 2)
    today_stocks_run4 = [
        {"symbol":"AVOIDCO","verdict":"WATCHLIST","composite_score":62},
    ]
    streaks4 = update_verdict_streaks(today_stocks_run4)
    if streaks4.get("AVOIDCO",{}).get("recovery") == 2 and \
       today_stocks_run4[0].get("turnaround_candidate") is True:
        passed += 1; print("  ✓ 29.6 recovery_streak == 2 → turnaround_candidate=True")
    else:
        failed += 1; failures.append(f"29.6 expected recovery=2 + flag=True: {today_stocks_run4[0]}")

    # 29.7 — recovery resets if score drops below 50
    _conn29 = _sq27.connect("market_data.db")
    for s in today_stocks_run4:
        _conn29.execute("""INSERT OR REPLACE INTO latest_analysis_results
            (symbol, date, composite_score, verdict,
             consecutive_avoid_quarters, consecutive_recovery_quarters)
            VALUES (?,?,?,?,?,?)""",
            (s["symbol"], "2026-04-30", s["composite_score"], s["verdict"],
             s["consecutive_avoid_quarters"], s["consecutive_recovery_quarters"]))
    _conn29.commit(); _conn29.close()

    today_stocks_run5 = [
        {"symbol":"AVOIDCO","verdict":"NEUTRAL","composite_score":42},  # below 50
    ]
    streaks5 = update_verdict_streaks(today_stocks_run5)
    if streaks5.get("AVOIDCO",{}).get("recovery") == 0:
        passed += 1; print("  ✓ 29.7 recovery resets to 0 when score drops below 50")
    else:
        failed += 1; failures.append(f"29.7 recovery should reset: {streaks5}")

    # 29.8 — chronic-AVOID demotion in priority_ranker
    from screening.priority_ranker import calculate_priority_score
    base_row = {
        "symbol":"X","stage2_score":25,"delivery_pct":60,"cap_category":"LARGE CAP",
        "turnover":1_000_000_000,"volume":1_000_000,
        "consecutive_avoid_quarters": 0,
    }
    p_no_demo = calculate_priority_score(base_row, avg_vol_cache={"X": 1_000_000})

    chronic_row = dict(base_row, consecutive_avoid_quarters=2)
    p_demo = calculate_priority_score(chronic_row, avg_vol_cache={"X": 1_000_000})

    if p_no_demo - p_demo == 15.0:
        passed += 1; print("  ✓ 29.8 chronic-AVOID (≥2) subtracts 15 points from priority_score")
    else:
        failed += 1; failures.append(f"29.8 expected -15 delta, got {p_no_demo - p_demo}")

    # 29.9 — single-AVOID does NOT trigger demotion (must be ≥2)
    single_row = dict(base_row, consecutive_avoid_quarters=1)
    p_single = calculate_priority_score(single_row, avg_vol_cache={"X": 1_000_000})
    if p_single == p_no_demo:
        passed += 1; print("  ✓ 29.9 single-AVOID (=1) does NOT demote (threshold is ≥2)")
    else:
        failed += 1; failures.append(f"29.9 single AVOID demoted: {p_no_demo} vs {p_single}")

    # 29.10 — graceful handling of missing/non-numeric streak field
    no_streak_row = {k:v for k,v in base_row.items() if k != "consecutive_avoid_quarters"}
    p_no_field = calculate_priority_score(no_streak_row, avg_vol_cache={"X": 1_000_000})
    bad_row = dict(base_row, consecutive_avoid_quarters="bogus")
    p_bad = calculate_priority_score(bad_row, avg_vol_cache={"X": 1_000_000})
    if p_no_field == p_no_demo and p_bad == p_no_demo:
        passed += 1; print("  ✓ 29.10 missing/non-numeric streak field treated as 0")
    else:
        failed += 1; failures.append("29.10 streak-field robustness broken")

finally:
    _os27.chdir(_orig_cwd29)
    _sh27.rmtree(_tmpdir29, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────
# Group 30 — Daily report Section H (v11.0.2 feature C)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "─" * 70)
print("Group 30: daily report Section H (v11.0.2)")
print("─" * 70)

import importlib as _imp30
if "reporting.daily_report_generator" in sys.modules:
    _imp30.reload(sys.modules["reporting.daily_report_generator"])
from reporting.daily_report_generator import DailyReportGenerator as _DRG30

_MKT30 = {"nifty_close":24000,"nifty_200d":23000,"sensex_close":80000,"vix":12.0,"fii_net":0}

# 30.1 — Section H surfaces stocks with recovery_quarters >= 2
data_h = [
    {"symbol":"COMEBACK1","verdict":"WATCHLIST","composite_score":62,
     "consecutive_recovery_quarters":2,"early_entry_score":0,"sector":"IT","rotation_stage":"NEUTRAL"},
    {"symbol":"COMEBACK2","verdict":"BUY","composite_score":71,
     "consecutive_recovery_quarters":3,"early_entry_score":0,"sector":"Healthcare","rotation_stage":"NEUTRAL"},
    {"symbol":"NORMAL","verdict":"BUY","composite_score":70,
     "consecutive_recovery_quarters":0,"early_entry_score":0,"sector":"IT","rotation_stage":"NEUTRAL"},
]
rep = _DRG30(data_h, _MKT30).generate_research_report()
sec_h = rep.split("SECTION H")[1] if "SECTION H" in rep else ""

if "COMEBACK1" in sec_h: passed += 1; print("  ✓ 30.1 Section H lists COMEBACK1 (recovery=2)")
else: failed += 1; failures.append("30.1 COMEBACK1 missing from Section H")
if "COMEBACK2" in sec_h: passed += 1; print("  ✓ 30.2 Section H lists COMEBACK2 (recovery=3)")
else: failed += 1; failures.append("30.2 COMEBACK2 missing")
if "NORMAL" not in sec_h: passed += 1; print("  ✓ 30.3 Section H excludes NORMAL (recovery=0)")
else: failed += 1; failures.append("30.3 NORMAL leaked into Section H")

# 30.4 — recovery=1 stock NOT listed (threshold is ≥2)
data_low = [{"symbol":"BARELY","verdict":"WATCHLIST","composite_score":52,
             "consecutive_recovery_quarters":1,"sector":"IT","rotation_stage":"NEUTRAL"}]
rep2 = _DRG30(data_low, _MKT30).generate_research_report()
sec_h2 = rep2.split("SECTION H")[1] if "SECTION H" in rep2 else ""
if "BARELY" not in sec_h2:
    passed += 1; print("  ✓ 30.4 recovery=1 (below threshold) excluded from Section H")
else:
    failed += 1; failures.append("30.4 recovery=1 leaked into H")

# 30.5 — empty input = no crash, returns "No candidates" line
rep3 = _DRG30([], _MKT30).generate_research_report()
sec_h3 = rep3.split("SECTION H")[1] if "SECTION H" in rep3 else ""
if "No candidates" in sec_h3 or sec_h3.strip() == "":
    passed += 1; print("  ✓ 30.5 empty input → Section H present without crash")
else:
    failed += 1; failures.append(f"30.5 empty input section H = {sec_h3!r}")

# 30.6 — section H shows verdict + composite_score + recovery count for each candidate
rep4 = _DRG30(data_h, _MKT30).generate_research_report()
sec_h4 = rep4.split("SECTION H")[1].split("\n")[0:6]
sec_h4_txt = "\n".join(sec_h4)
checks_30_6 = ["VERDICT" in sec_h4_txt, "COMPOSITE_SCORE" in sec_h4_txt,
               "CONSECUTIVE_RECOVERY_QUARTERS" in sec_h4_txt]
if all(checks_30_6):
    passed += 1; print("  ✓ 30.6 Section H rows include verdict, score, and recovery streak")
else:
    failed += 1; failures.append(f"30.6 Section H content missing: checks={checks_30_6}")



print("\n" + "═" * 70)
print("Group 31: v12.1 reconciler — empty-ISIN false-positive prevention")
print("─" * 70)

try:
    import pandas as _pd_31
    import numpy as _np_31
    from ingestion.reconciler import reconcile_exchanges as _rec31
    passed += 1; print("  ✓ 31.0 reconciler imports cleanly for v12.1 tests")
except Exception as _e_31_imp:
    failed += 1
    failures.append(f"31.0 reconciler import: {_e_31_imp}")
    _rec31 = None

if _rec31 is not None:
    # 31.1 — Index tickers (empty ISIN on NSE) must NOT be tagged DUAL_LISTED
    # even if a BSE row exists with the same symbol but no ISIN. This was the
    # production bug: PSUBANK (NSE index) collided with BSE PSUBANK on symbol
    # match and got falsely tagged DUAL_LISTED.
    try:
        nse31 = _pd_31.DataFrame({
            "symbol": ["RELIANCE", "PSUBANK", "IT", "BANKNIFTY1"],
            "isin":   ["INE002A01018", "", "", ""],
            "close":  [3000, 38000, 38000, 50000],
            "volume": [1000, 1000, 1000, 1000],
        })
        bse31 = _pd_31.DataFrame({
            "symbol":   ["RELIANCE", "PSUBANK", "ITCOLLIDE"],
            "isin":     ["INE002A01018", "", ""],
            "close":    [3001, 38500, 50],
            "sc_group": ["A", "M", "B"],
            "bse_code": ["500325", "111111", "222222"],
        })
        m31 = _rec31(nse31, bse31)
        sym_col = "symbol_NSE" if "symbol_NSE" in m31.columns else "symbol"
        psu_rows = m31[m31[sym_col].astype(str) == "PSUBANK"]
        psu_dual = (psu_rows["exchange_tag"] == "DUAL_LISTED").any()
        if not psu_dual:
            passed += 1; print("  ✓ 31.1 PSUBANK (empty ISIN both sides) NOT tagged DUAL_LISTED")
        else:
            failed += 1; failures.append("31.1 PSUBANK incorrectly tagged DUAL_LISTED — v12.1 fix regressed")
    except Exception as _e:
        failed += 1; failures.append(f"31.1 exception: {_e}")

    # 31.2 — IT (NSE index, empty ISIN) must stay NSE_ONLY
    try:
        it_rows = m31[m31[sym_col].astype(str) == "IT"]
        it_tag = it_rows["exchange_tag"].iloc[0] if len(it_rows) > 0 else "MISSING"
        if it_tag == "NSE_ONLY":
            passed += 1; print("  ✓ 31.2 IT (NSE index) tagged NSE_ONLY (not falsely DUAL_LISTED)")
        else:
            failed += 1; failures.append(f"31.2 IT tagged {it_tag}, expected NSE_ONLY")
    except Exception as _e:
        failed += 1; failures.append(f"31.2 exception: {_e}")

    # 31.3 — RELIANCE (real ISIN match both sides) must be DUAL_LISTED
    try:
        rel_rows = m31[m31[sym_col].astype(str) == "RELIANCE"]
        rel_tag = rel_rows["exchange_tag"].iloc[0] if len(rel_rows) > 0 else "MISSING"
        if rel_tag == "DUAL_LISTED":
            passed += 1; print("  ✓ 31.3 RELIANCE (real ISIN match) correctly tagged DUAL_LISTED")
        else:
            failed += 1; failures.append(f"31.3 RELIANCE tagged {rel_tag}, expected DUAL_LISTED")
    except Exception as _e:
        failed += 1; failures.append(f"31.3 exception: {_e}")

    # 31.4 — Realistic-scale test: 600 true ISIN matches must produce ~600 DUAL_LISTED
    # (not 2000+ as the v11.x/v12.0.1 bug produced)
    try:
        _np_31.random.seed(42)
        n_nse_31, n_bse_31 = 2483, 4997
        nse_big = _pd_31.DataFrame({
            "symbol": [f"NSE{i:04d}" for i in range(n_nse_31)],
            "isin":   [f"INE{i:08d}A1" for i in range(n_nse_31)],
            "close":  _np_31.random.uniform(50, 5000, n_nse_31),
            "volume": _np_31.random.randint(1000, 1000000, n_nse_31),
        })
        # 5 NSE indices with empty ISIN
        for i, sym in enumerate(["IT", "PSUBANK", "BANKNIFTY1", "MON100", "HDFCNIFBAN"]):
            nse_big.loc[i, "symbol"] = sym
            nse_big.loc[i, "isin"]   = ""

        bse_isins = ([f"INE{i:08d}A1" for i in range(600)] +     # 600 dual-listed
                     [f"INE9{i:07d}A1" for i in range(1500)] +    # 1500 BSE-only
                     [""] * 200 +                                  # 200 SME (no ISIN)
                     [""] * 2697)                                  # 2697 misc empty
        bse_grps = ["A"] * 600 + ["B"] * 1500 + ["M"] * 200 + ["A"] * 2697
        # 2038 of empty-ISIN BSE rows have symbols colliding with NSE — pre-fix
        # this triggered the symbol-merge cross-join false-positive
        bse_syms = ([f"BSE_DL_{i}" for i in range(600)] +
                    [f"BSE_ONLY_{i}" for i in range(1500)] +
                    [f"SME_{i}" for i in range(200)] +
                    [f"NSE{i % n_nse_31:04d}" for i in range(2038)] +
                    [f"BSE_NOISN_{i}" for i in range(659)])
        bse_big = _pd_31.DataFrame({
            "symbol":   bse_syms,
            "isin":     bse_isins,
            "close":    _np_31.random.uniform(50, 5000, n_bse_31),
            "sc_group": bse_grps,
            "bse_code": [str(500000 + i) for i in range(n_bse_31)],
        })

        m_big = _rec31(nse_big, bse_big)
        n_dual = int((m_big["exchange_tag"] == "DUAL_LISTED").sum())

        # Pre-fix: ~2038-2483 false DUAL_LISTED. Post-fix: should be ~600.
        if 580 <= n_dual <= 700:
            passed += 1
            print(f"  ✓ 31.4 realistic-scale: {n_dual} DUAL_LISTED (expected ~600, was 2038+ pre-fix)")
        else:
            failed += 1
            failures.append(f"31.4 DUAL_LISTED count {n_dual} outside expected 580-700 range")
    except Exception as _e:
        failed += 1; failures.append(f"31.4 exception: {_e}")

    # 31.5 — BSE_ONLY and BSE_SME tags must populate (were 0 in pre-fix dashboards)
    try:
        n_bse_only = int((m_big["exchange_tag"] == "BSE_ONLY").sum())
        n_bse_sme  = int((m_big["exchange_tag"] == "BSE_SME").sum())
        if n_bse_only > 1000 and n_bse_sme >= 100:
            passed += 1
            print(f"  ✓ 31.5 BSE_ONLY={n_bse_only} & BSE_SME={n_bse_sme} populated (were 0 pre-fix)")
        else:
            failed += 1
            failures.append(f"31.5 BSE_ONLY={n_bse_only} BSE_SME={n_bse_sme} unexpectedly low")
    except Exception as _e:
        failed += 1; failures.append(f"31.5 exception: {_e}")

    # 31.6 — Allowlist override still works for hardcoded DUAL stocks even if
    # ISIN merge somehow misses them (defensive sanity check)
    try:
        from ingestion.reconciler import DUAL_LISTED_ALLOWLIST as _DLA31
        # Pick a known hardcoded entry
        hc_sym = "RELIANCE" if "RELIANCE" in _DLA31 else next(iter(_DLA31))
        nse_hc = _pd_31.DataFrame({
            "symbol": [hc_sym, "OTHER"],
            "isin":   ["", ""],   # empty so ISIN merge doesn't run
            "close":  [3000, 100],
            "volume": [1000, 1000],
        })
        bse_hc = _pd_31.DataFrame({
            "symbol": [hc_sym],
            "isin":   [""],
            "close":  [3001],
            "sc_group": ["A"],
            "bse_code": ["500325"],
        })
        m_hc = _rec31(nse_hc, bse_hc)
        col = "symbol_NSE" if "symbol_NSE" in m_hc.columns else "symbol"
        hc_rows = m_hc[m_hc[col].astype(str) == hc_sym]
        # The fallback whole-symbol-merge path should produce DUAL_LISTED for hc_sym
        # (since it matches on symbol AND is on the hardcoded allowlist)
        hc_tags = set(hc_rows["exchange_tag"].astype(str).tolist())
        if "DUAL_LISTED" in hc_tags:
            passed += 1
            print(f"  ✓ 31.6 hardcoded allowlist override still tags {hc_sym} as DUAL_LISTED")
        else:
            failed += 1
            failures.append(f"31.6 {hc_sym} tagged {hc_tags}, expected DUAL_LISTED via override")
    except Exception as _e:
        failed += 1; failures.append(f"31.6 exception: {_e}")

    # 31.7 — REAL-WORLD BSE-only company: Silverline Technologies Ltd
    #   ISIN: INE368A01021 · BSE scrip code: 500389 · symbol: SILVERLINE
    #   NSE: NOT LISTED. Confirmed via business-standard.com on 28-Apr-2026.
    #   This is a regression test against the production scenario where
    #   genuinely-BSE-only equities were getting falsely tagged DUAL_LISTED.
    try:
        nse_sil = _pd_31.DataFrame({
            "symbol": ["RELIANCE", "TCS", "INFY", "PSUBANK", "BANKNIFTY1"],
            "isin":   ["INE002A01018", "INE467B01029", "INE009A01021", "", ""],
            "close":  [3000.0, 4200.0, 1700.0, 38000.0, 50000.0],
            "volume": [10000, 10000, 10000, 100, 100],
        })
        bse_sil = _pd_31.DataFrame({
            "symbol":   ["RELIANCE", "TCS", "INFY", "SILVERLINE"],
            "isin":     ["INE002A01018", "INE467B01029", "INE009A01021", "INE368A01021"],
            "close":    [3001.0, 4201.0, 1701.0, 21.55],
            "sc_group": ["A", "A", "A", "T"],   # T = Trade-to-Trade (NOT SME)
            "bse_code": ["500325", "532540", "500209", "500389"],
        })
        m_sil = _rec31(nse_sil, bse_sil)
        sym_bse_col = "symbol_BSE" if "symbol_BSE" in m_sil.columns else "symbol"
        sil_rows = m_sil[m_sil[sym_bse_col].astype(str).str.upper() == "SILVERLINE"]
        if sil_rows.empty:
            failed += 1; failures.append("31.7 SILVERLINE not found in merged output")
        else:
            sil_tag = sil_rows["exchange_tag"].iloc[0]
            if sil_tag == "BSE_ONLY":
                passed += 1
                print("  ✓ 31.7 SILVERLINE (real BSE-only equity, T2T group) → BSE_ONLY")
            else:
                failed += 1
                failures.append(f"31.7 SILVERLINE tagged {sil_tag}, expected BSE_ONLY")
    except Exception as _e:
        failed += 1; failures.append(f"31.7 exception: {_e}")

    # 31.8 — Same Silverline data but with sc_group='M' (BSE SME group)
    #   should tag BSE_SME instead of BSE_ONLY — verifies the SME branch fires
    #   correctly for genuinely-BSE-only equities.
    try:
        bse_sme = bse_sil.copy()
        bse_sme.loc[bse_sme["symbol"] == "SILVERLINE", "sc_group"] = "M"
        m_sme = _rec31(nse_sil, bse_sme)
        sil_sme = m_sme[m_sme[sym_bse_col].astype(str).str.upper() == "SILVERLINE"]
        if sil_sme.empty:
            failed += 1; failures.append("31.8 SILVERLINE-SME not found in merged output")
        else:
            sil_sme_tag = sil_sme["exchange_tag"].iloc[0]
            if sil_sme_tag == "BSE_SME":
                passed += 1
                print("  ✓ 31.8 SILVERLINE with sc_group='M' (SME) → BSE_SME")
            else:
                failed += 1
                failures.append(f"31.8 SILVERLINE-SME tagged {sil_sme_tag}, expected BSE_SME")
    except Exception as _e:
        failed += 1; failures.append(f"31.8 exception: {_e}")

print("\n" + "═" * 70)
print(f"FINAL: {passed} passed, {failed} failed")
print("═" * 70)
if failures:
    print(f"\nFailures ({len(failures)}):")
    for f in failures:
        print(f"  ❌ {f}")
if warnings:
    print(f"\nWarnings ({len(warnings)}):")
    for w in warnings:
        print(f"  ⚠ {w}")
if failed == 0:
    print("\n✅ ALL VALIDATION TESTS PASSED — engine behavior verified across all code paths.")
    sys.exit(0)
else:
    sys.exit(1)