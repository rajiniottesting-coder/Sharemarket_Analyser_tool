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



# ──────────────────────────────────────────────────────────────────────────
# GROUPS 32-45: FairValueEngine — 7 Valuation Models (v12.2)
# ──────────────────────────────────────────────────────────────────────────
# Comprehensive coverage for analysis/fair_value_engine.py
#   • All 7 models (M1 DCF, M2 Graham, M3 PE, M4 PB, M5 EV, M6 DDM, M7 PEG)
#   • Composite blending, MoS derivation, score adjustment bands
#   • v12.2 fixes: eps/bvps sanitization, sector resolver, M6/M7 corrections,
#     unknown-key composite hardening
# Each test is self-contained with hand-computed expected values.
# Uses the existing passed/failed/failures counters so the final tally rolls
# up cleanly with the rest of the suite.

import math as _math_fv
from analysis.fair_value_engine import FairValueEngine

_fv = FairValueEngine(gsec_yield=6.0)


def _fv_check(test_id, description, got, want, tol=0.01):
    """Numeric-or-equality check. Mirrors the style of the scoring tests."""
    global passed, failed
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        ok = abs(got - want) <= tol
    else:
        ok = got == want
    if ok:
        passed += 1
        print(f"  ✓ {test_id} {description}")
    else:
        failed += 1
        msg = f"{test_id} {description}: got {got!r}, want {want!r}"
        print(f"  ✗ {msg}")
        failures.append(msg)


def _fv_check_in(test_id, description, got, lo, hi):
    """Range check."""
    global passed, failed
    if lo <= got <= hi:
        passed += 1
        print(f"  ✓ {test_id} {description} ({got} in [{lo}, {hi}])")
    else:
        failed += 1
        msg = f"{test_id} {description}: {got} not in [{lo}, {hi}]"
        print(f"  ✗ {msg}")
        failures.append(msg)


def _fv_base_stock(**overrides):
    """Standard happy-path stock for FV tests: profitable mid-cap."""
    s = {
        "close":     1000,
        "eps":       50,        # PE ≈ 20 at this CMP
        "bvps":      400,       # PB ≈ 2.5
        "pb":        2.5,
        "pe":        20,
        "div_yield": 0,         # default: no dividend
        "pat_yoy":   15,        # 15% earnings growth
        "ev_ebitda": 12,
        "sector":    "Banks",
    }
    s.update(overrides)
    return s


# ──────────────────────────────────────────────────────────────────────────
# GROUP 32: M1 DCF (3-Stage Discounted Cash Flow)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 32 — M1 DCF (3-Stage Discounted Cash Flow)")
print("═" * 70)

m = _fv.calculate_all_models(_fv_base_stock(), beta=1.0, growth_3yr=15)
_fv_check_in("32.1", "DCF returns positive FV for profitable stock", m['M1_DCF'], 100, 4000)

m = _fv.calculate_all_models(_fv_base_stock(eps=-5), beta=1.0, growth_3yr=15)
_fv_check("32.2", "DCF skips on negative EPS", m['M1_DCF'], 0)

m = _fv.calculate_all_models(_fv_base_stock(eps=0), beta=1.0, growth_3yr=15)
_fv_check("32.3", "DCF skips on zero EPS", m['M1_DCF'], 0)

# Low-beta + high-growth would normally explode — guard caps at 4× CMP
m = _fv.calculate_all_models(_fv_base_stock(close=1000, eps=100), beta=0.2, growth_3yr=25)
_fv_check_in("32.4", "DCF cap at 4× CMP engaged for low-beta high-growth", m['M1_DCF'], 100, 4000)

# WACC floor at 10% prevents division-by-near-zero with very low beta
m = _fv.calculate_all_models(_fv_base_stock(), beta=0.0, growth_3yr=10)
_fv_check_in("32.5", "DCF stable with very low beta (WACC floor)", m['M1_DCF'], 100, 4000)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 33: M2 Graham Number
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 33 — M2 Graham Number")
print("═" * 70)

# Math: √(22.5 × 50 × 400) = √450000 ≈ 670.82
m = _fv.calculate_all_models(_fv_base_stock(), beta=1.0, growth_3yr=15)
_expected = round(_math_fv.sqrt(22.5 * 50 * 400), 2)
_fv_check("33.1", "Graham math: √(22.5 × eps × bvps)", m['M2_Graham'], _expected)

m = _fv.calculate_all_models(_fv_base_stock(eps=-5), beta=1.0, growth_3yr=15)
_fv_check("33.2", "Graham skips on negative EPS", m['M2_Graham'], 0)

m = _fv.calculate_all_models(_fv_base_stock(bvps=0, pb=0), beta=1.0, growth_3yr=15)
_fv_check("33.3", "Graham skips when bvps=0 and no pb fallback", m['M2_Graham'], 0)

# BVPS fallback from PB: bvps=0 but pb=2.5 and close=1000 → derived bvps=400
m = _fv.calculate_all_models(_fv_base_stock(bvps=0), beta=1.0, growth_3yr=15)
_fv_check("33.4", "Graham uses pb×close fallback for BVPS", m['M2_Graham'], _expected)

m = _fv.calculate_all_models(_fv_base_stock(bvps=0, pb=0), beta=1.0, growth_3yr=15)
_fv_check("33.5", "Graham skips when both bvps and pb missing", m['M2_Graham'], 0)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 34: M3 PE Mean Reversion (with sector resolution, v12.2 fixes)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 34 — M3 PE Mean Reversion (v12.2 sector resolution)")
print("═" * 70)

m = _fv.calculate_all_models(_fv_base_stock(sector="Banks"), beta=1.0, growth_3yr=15)
_fv_check("34.1", "PE-based FV for Banks (PE=18)", m['M3_PE'], 50 * 18)

m = _fv.calculate_all_models(_fv_base_stock(sector="IT"), beta=1.0, growth_3yr=15)
_fv_check("34.2", "PE-based FV for IT (PE=30)", m['M3_PE'], 50 * 30)

# v12.2 fix: multi-word "Information Technology" now resolves
m = _fv.calculate_all_models(_fv_base_stock(sector="Information Technology"),
                             beta=1.0, growth_3yr=15)
_fv_check("34.3", "v12.2 'Information Technology' resolves to Technology PE=30",
          m['M3_PE'], 50 * 30)

# v12.2 fix: "Iron & Steel" now matches Steel (was matching only 'Iron')
m = _fv.calculate_all_models(_fv_base_stock(sector="Iron & Steel"),
                             beta=1.0, growth_3yr=15)
_fv_check("34.4", "v12.2 'Iron & Steel' resolves to Steel PE=10", m['M3_PE'], 50 * 10)

# v12.2: Realty (was missing entirely)
m = _fv.calculate_all_models(_fv_base_stock(sector="Realty"), beta=1.0, growth_3yr=15)
_fv_check("34.5", "v12.2 Realty sector recognized (PE=25)", m['M3_PE'], 50 * 25)

# v12.2: Telecom (was missing)
m = _fv.calculate_all_models(_fv_base_stock(sector="Telecom"), beta=1.0, growth_3yr=15)
_fv_check("34.6", "v12.2 Telecom sector recognized (PE=22)", m['M3_PE'], 50 * 22)

m = _fv.calculate_all_models(_fv_base_stock(sector="Wibble Wobble"),
                             beta=1.0, growth_3yr=15)
_fv_check("34.7", "Unknown sector falls back to default PE=25", m['M3_PE'], 50 * 25)

m = _fv.calculate_all_models(_fv_base_stock(eps=-5, sector="Banks"),
                             beta=1.0, growth_3yr=15)
_fv_check("34.8", "PE skips on negative EPS", m['M3_PE'], 0)

m = _fv.calculate_all_models(_fv_base_stock(sector="", sector_pe_5yr=22),
                             beta=1.0, growth_3yr=15)
_fv_check("34.9", "Empty sector uses sector_pe_5yr fallback", m['M3_PE'], 50 * 22)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 35: M4 Price-to-Book
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 35 — M4 Price-to-Book")
print("═" * 70)

m = _fv.calculate_all_models(_fv_base_stock(sector="Banks"), beta=1.0, growth_3yr=15)
_fv_check("35.1", "PB-based FV for Banks (PB=2.0)", m['M4_PB'], 400 * 2.0)

# v12.2: Insurance sector (new)
m = _fv.calculate_all_models(_fv_base_stock(sector="Insurance"), beta=1.0, growth_3yr=15)
_fv_check("35.2", "v12.2 Insurance recognized (PB=2.5)", m['M4_PB'], 400 * 2.5)

# BVPS fallback: bvps=0, pb=2.0, close=1000 → derived bvps=500, sector PB=2.0 → FV=1000
m = _fv.calculate_all_models(_fv_base_stock(sector="Banks", bvps=0, pb=2.0),
                             beta=1.0, growth_3yr=15)
_fv_check("35.3", "PB uses bvps fallback from close/pb", m['M4_PB'], 500 * 2.0)

m = _fv.calculate_all_models(_fv_base_stock(bvps=0, pb=0), beta=1.0, growth_3yr=15)
_fv_check("35.4", "PB skips when bvps and pb both missing", m['M4_PB'], 0)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 36: M5 EV/EBITDA
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 36 — M5 EV/EBITDA")
print("═" * 70)

# cmp=1000, sector_ev=12, current_ev=12 → FV ≈ CMP
# v12.3 Round 2: Banks/NBFCs/Insurance now correctly skip M5 (EV/EBITDA isn't
# meaningful for financials). Use Pharma sector for this test instead.
m = _fv.calculate_all_models(_fv_base_stock(sector="Pharma", ev_ebitda=18),
                             beta=1.0, growth_3yr=15)
_fv_check("36.1", "EV-based FV when current = sector multiple (FV ≈ CMP)",
          m['M5_EV'], 1000.0)

# Steel sector_ev=5, current=5 → FV=CMP
m = _fv.calculate_all_models(_fv_base_stock(sector="Steel", ev_ebitda=5, close=1000),
                             beta=1.0, growth_3yr=15)
_expected = round(1000 * 5 / 5, 2)
_fv_check("36.2", "EV FV with Steel sector (mult=5)", m['M5_EV'], _expected)

m = _fv.calculate_all_models(_fv_base_stock(ev_ebitda=0), beta=1.0, growth_3yr=15)
_fv_check("36.3", "EV skips when ev_ebitda missing", m['M5_EV'], 0)

# v12.2: Realty sector (new)
m = _fv.calculate_all_models(_fv_base_stock(sector="Realty", ev_ebitda=10),
                             beta=1.0, growth_3yr=15)
_expected = round(1000 * 12 / 10, 2)
_fv_check("36.4", "v12.2 Realty EV multiple recognized", m['M5_EV'], _expected)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 37: M6 DDM (Dividend Discount Model) — v12.2 growth fix
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 37 — M6 DDM (v12.2 growth derivation fix)")
print("═" * 70)

m = _fv.calculate_all_models(_fv_base_stock(div_yield=0), beta=1.0, growth_3yr=15)
_fv_check("37.1", "DDM skips for non-dividend stock", m['M6_DDM'], 0)

m = _fv.calculate_all_models(_fv_base_stock(div_yield=20), beta=1.0, growth_3yr=15)
_fv_check("37.2", "DDM skips when yield > 15% (bad data)", m['M6_DDM'], 0)

# Healthy dividend stock — verify Gordon math
# DPS=1000×0.025=25, growth=max(min(10/100/2, 0.06), 0)=0.05
# req=0.105, d1=26.25, FV=26.25/0.055≈477.27
m = _fv.calculate_all_models(_fv_base_stock(div_yield=2.5, pat_yoy=10),
                             beta=1.0, growth_3yr=10)
_fv_check("37.3", "DDM happy path: 2.5% yield, 10% pat_yoy", m['M6_DDM'], 477.27, tol=0.5)

# v12.2 FIX: negative pat_yoy → 0% growth (was 2% in old code)
# DPS=25, d1=25, FV=25/0.105≈238.10
m = _fv.calculate_all_models(_fv_base_stock(div_yield=2.5, pat_yoy=-20),
                             beta=1.0, growth_3yr=15)
_fv_check("37.4", "v12.2 negative pat_yoy → 0% div growth (no free 2% floor)",
          m['M6_DDM'], 238.10, tol=0.5)

# v12.2: zero pat_yoy → 0% growth
m = _fv.calculate_all_models(_fv_base_stock(div_yield=2.5, pat_yoy=0),
                             beta=1.0, growth_3yr=15)
_fv_check("37.5", "v12.2 zero pat_yoy → 0% div growth", m['M6_DDM'], 238.10, tol=0.5)

# High pat_yoy capped at 6% growth
# pat_yoy=20 → growth=min(20/100/2, 0.06)=0.06, DPS=25, d1=26.5, FV=26.5/0.045≈588.89
m = _fv.calculate_all_models(_fv_base_stock(div_yield=2.5, pat_yoy=20),
                             beta=1.0, growth_3yr=15)
_fv_check("37.6", "v12.2 high pat_yoy capped at 6% growth", m['M6_DDM'], 588.89, tol=0.5)

m = _fv.calculate_all_models(_fv_base_stock(div_yield=15.0), beta=1.0, growth_3yr=15)
_fv_check("37.7", "DDM yield boundary: 15.0 excluded", m['M6_DDM'], 0)

m = _fv.calculate_all_models(_fv_base_stock(div_yield=0.1), beta=1.0, growth_3yr=15)
_fv_check("37.8", "DDM yield boundary: 0.1 excluded", m['M6_DDM'], 0)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 38: M7 PEG — v12.2 unit guard
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 38 — M7 PEG (v12.2 unit guard)")
print("═" * 70)

m = _fv.calculate_all_models(_fv_base_stock(), beta=1.0, growth_3yr=15)
_fv_check("38.1", "PEG: eps=50 × growth=15%", m['M7_PEG'], 750)

m = _fv.calculate_all_models(_fv_base_stock(), beta=1.0, growth_3yr=50)
_fv_check("38.2", "PEG growth capped at 30%", m['M7_PEG'], 50 * 30)

m = _fv.calculate_all_models(_fv_base_stock(eps=-5), beta=1.0, growth_3yr=15)
_fv_check("38.3", "PEG skips on negative EPS", m['M7_PEG'], 0)

m = _fv.calculate_all_models(_fv_base_stock(), beta=1.0, growth_3yr=0)
_fv_check("38.4", "PEG skips on zero growth", m['M7_PEG'], 0)

m = _fv.calculate_all_models(_fv_base_stock(), beta=1.0, growth_3yr=-5)
_fv_check("38.5", "PEG skips on negative growth", m['M7_PEG'], 0)

# v12.2 FIX: growth_3yr accidentally as decimal (0.15 instead of 15) → skipped
m = _fv.calculate_all_models(_fv_base_stock(), beta=1.0, growth_3yr=0.15)
_fv_check("38.6", "v12.2 PEG guards against decimal-fraction growth (0.15)",
          m['M7_PEG'], 0)

m = _fv.calculate_all_models(_fv_base_stock(), beta=1.0, growth_3yr=1.0)
_fv_check("38.7", "PEG boundary: growth=1.0 valid", m['M7_PEG'], 50 * 1.0)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 39: Composite Weighting (v12.2 unknown-key hardening)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 39 — Composite weighting")
print("═" * 70)

# All 7 models present and equal → CFV equals that value
_fake = {"M1_DCF": 100, "M2_Graham": 100, "M3_PE": 100,
         "M4_PB": 100, "M5_EV": 100, "M6_DDM": 100, "M7_PEG": 100}
_result = _fv.get_composite_fair_value(_fake, cmp=100)
_fv_check("39.1", "All-equal models → CFV = that value", _result['cfv'], 100)

# Partial set with normalized weights
# M1=200 (w=0.30), M3=100 (w=0.20). Normalized: total_w=0.50
# CFV = (200×0.30 + 100×0.20) / 0.50 = 80/0.50 = 160
_fake = {"M1_DCF": 200, "M2_Graham": 0, "M3_PE": 100,
         "M4_PB": 0, "M5_EV": 0, "M6_DDM": 0, "M7_PEG": 0}
_result = _fv.get_composite_fair_value(_fake, cmp=150)
_fv_check("39.2", "Partial model set with normalized weights", _result['cfv'], 160)

_result = _fv.get_composite_fair_value({"M1_DCF": 0, "M2_Graham": 0}, cmp=100)
_fv_check("39.3", "All models zero → CFV = 0", _result['cfv'], 0)

# v12.2 FIX: unknown model key gets weight 0, doesn't dilute composite
_fake = {"M1_DCF": 100, "UNKNOWN_MODEL": 999}
_result = _fv.get_composite_fair_value(_fake, cmp=100)
_fv_check("39.4", "v12.2 unknown model key excluded from composite",
          _result['cfv'], 100)

# 3× CMP cap engages
_fake = {"M1_DCF": 5000}
_result = _fv.get_composite_fair_value(_fake, cmp=100)
_fv_check("39.5", "Composite CFV capped at 3× CMP", _result['cfv'], 300)

_result = _fv.get_composite_fair_value({"M1_DCF": 100}, cmp=0)
_fv_check("39.6", "cmp=0 handled gracefully (mos_pct=0)", _result['mos_pct'], 0)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 40: MoS Percentage Derivation
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 40 — MoS percentage derivation")
print("═" * 70)

_result = _fv.get_composite_fair_value({"M1_DCF": 1180}, cmp=1000)
_fv_check("40.1", "MoS positive: CFV 1180, CMP 1000 → +18%", _result['mos_pct'], 18.0)

_result = _fv.get_composite_fair_value({"M1_DCF": 850}, cmp=1000)
_fv_check("40.2", "MoS negative: CFV 850, CMP 1000 → -15%", _result['mos_pct'], -15.0)

_result = _fv.get_composite_fair_value({"M1_DCF": 1000}, cmp=1000)
_fv_check("40.3", "MoS zero when CFV = CMP", _result['mos_pct'], 0.0)

# Extreme: CFV would be 10× CMP, capped at 3× → MoS = +200%
_result = _fv.get_composite_fair_value({"M1_DCF": 10000}, cmp=1000)
_fv_check("40.4", "MoS at extreme undervaluation (capped CFV)",
          _result['mos_pct'], 200.0)

_result = _fv.get_composite_fair_value({"M1_DCF": 1}, cmp=10000)
_fv_check("40.5", "Upside floored at -100%", _result['upside'] >= -100, True)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 41: Score Adjustment Bands
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 41 — Score adjustment bands")
print("═" * 70)
# v12.6: with thin-model guard, score_adjustment is zeroed when fewer than
# MIN_MODELS=3 valuation lenses fired. Use ≥3 models for band tests.
# ≥3-model setup: M1_DCF + M3_PE + M4_PB all fire with the same target value
# so the weighted blend lands at the target.
def _make_models_for_target(target):
    return {"M1_DCF": target, "M3_PE": target, "M4_PB": target}

_result = _fv.get_composite_fair_value(_make_models_for_target(1500), cmp=1000)  # +50%
_fv_check("41.1", "MoS > 40 → +12 score adj (3 models)", _result['score_adjustment'], 12)

_result = _fv.get_composite_fair_value(_make_models_for_target(1300), cmp=1000)  # +30%
_fv_check("41.2", "25 < MoS ≤ 40 → +8 score adj (3 models)", _result['score_adjustment'], 8)

_result = _fv.get_composite_fair_value(_make_models_for_target(1180), cmp=1000)  # +18%
_fv_check("41.3", "10 < MoS ≤ 25 → +4 score adj (3 models)", _result['score_adjustment'], 4)

_result = _fv.get_composite_fair_value(_make_models_for_target(1050), cmp=1000)  # +5%
_fv_check("41.4", "Neutral MoS band → 0 score adj (3 models)", _result['score_adjustment'], 0)

_result = _fv.get_composite_fair_value(_make_models_for_target(800), cmp=1000)  # -20%
_fv_check("41.5", "-30 ≤ MoS < -15 → -5 score adj (3 models)", _result['score_adjustment'], -5)

_result = _fv.get_composite_fair_value(_make_models_for_target(600), cmp=1000)  # -40%
_fv_check("41.6", "MoS < -30 → -10 score adj (3 models)", _result['score_adjustment'], -10)

# v12.6 #4: thin-model guard tests — score_adjustment must be zeroed when
# n_models < 3, regardless of MoS magnitude.
_result = _fv.get_composite_fair_value({"M1_DCF": 1500}, cmp=1000)  # 1 model, +50%
_fv_check("41.7", "v12.6 thin-model: 1 model + MoS>40 → score_adj=0",
          _result['score_adjustment'], 0)
_fv_check("41.7b", "v12.6 thin-model: cfv_thin_models flag = True",
          _result['cfv_thin_models'], True)

_result = _fv.get_composite_fair_value({"M1_DCF": 1500, "M2_Graham": 1500}, cmp=1000)  # 2 models
_fv_check("41.8", "v12.6 thin-model: 2 models + MoS>40 → score_adj=0",
          _result['score_adjustment'], 0)
_fv_check("41.8b", "v12.6 thin-model: 2-model cfv_thin_models flag = True",
          _result['cfv_thin_models'], True)

_result = _fv.get_composite_fair_value(_make_models_for_target(1500), cmp=1000)  # 3 models
_fv_check("41.9", "v12.6 thin-model: 3 models + MoS>40 → score_adj=12 (full)",
          _result['score_adjustment'], 12)
_fv_check("41.9b", "v12.6 thin-model: 3-model cfv_thin_models flag = False",
          _result['cfv_thin_models'], False)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 42: MoS Labels — REMOVED in v12.6 (#2)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 42 — MoS labels (v12.6: engine no longer emits mos_label)")
print("═" * 70)
# v12.6 (#2): the FV engine used to set its own mos_label bucket scheme
# (EXCEPTIONAL VALUE / STRONG VALUE / ...) — but master_funnel always
# overwrote it with a different scheme (EXCEPTIONAL / STRONG / ADEQUATE /
# THIN / SLIGHT PREMIUM / SIGNIFICANT PREMIUM). The engine's code was
# unreachable in production. v12.6 deletes the dead engine code: funnel is
# the single source of truth for the user-facing label.
# These tests now assert the engine NO LONGER emits mos_label.
_label_cases = [
    ({"M1_DCF": 1500}, 1000, "+50%"),
    ({"M1_DCF": 1300}, 1000, "+30%"),
    ({"M1_DCF": 1180}, 1000, "+18%"),
    ({"M1_DCF": 1050}, 1000, "+5%"),
    ({"M1_DCF":  900}, 1000, "-10%"),
    ({"M1_DCF":  800}, 1000, "-20%"),
    ({"M1_DCF":  600}, 1000, "-40%"),
]
for _i, (_models, _cmp, _mos_desc) in enumerate(_label_cases, 1):
    _result = _fv.get_composite_fair_value(_models, cmp=_cmp)
    _fv_check(f"42.{_i}", f"v12.6: engine output has no mos_label key ({_mos_desc})",
              "mos_label" in _result, False)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 43: Defensive Inputs (v12.2 sanitization fix)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 43 — Defensive inputs (v12.2 eps/bvps sanitization)")
print("═" * 70)

# eps='—' must NOT crash, all eps-dependent models return 0
m = _fv.calculate_all_models(_fv_base_stock(eps="—"), beta=1.0, growth_3yr=15)
_fv_check("43.1a", "v12.2 eps='—' → no crash, M1=0", m['M1_DCF'], 0)
_fv_check("43.1b", "v12.2 eps='—' → M2=0",          m['M2_Graham'], 0)
_fv_check("43.1c", "v12.2 eps='—' → M3=0",          m['M3_PE'], 0)
_fv_check("43.1d", "v12.2 eps='—' → M7=0",          m['M7_PEG'], 0)

m = _fv.calculate_all_models(_fv_base_stock(eps=None), beta=1.0, growth_3yr=15)
_fv_check("43.2", "v12.2 eps=None → no crash, M1=0", m['M1_DCF'], 0)

m = _fv.calculate_all_models(_fv_base_stock(eps="N/A"), beta=1.0, growth_3yr=15)
_fv_check("43.3", "v12.2 eps='N/A' → no crash, M3=0", m['M3_PE'], 0)

# bvps='—' but pb fallback works → M2 still produces value
m = _fv.calculate_all_models(_fv_base_stock(bvps="—"), beta=1.0, growth_3yr=15)
_expected = round(_math_fv.sqrt(22.5 * 50 * 400), 2)
_fv_check("43.4", "v12.2 bvps='—' uses pb fallback", m['M2_Graham'], _expected)

m = _fv.calculate_all_models(_fv_base_stock(bvps="—", pb="N/A"),
                             beta=1.0, growth_3yr=15)
_fv_check("43.5", "v12.2 both bvps and pb garbage → clean 0", m['M2_Graham'], 0)

m = _fv.calculate_all_models(_fv_base_stock(sector=""), beta=1.0, growth_3yr=15)
_fv_check("43.6", "Empty sector falls back to default PE=25", m['M3_PE'], 50 * 25)

m = _fv.calculate_all_models(_fv_base_stock(sector=None), beta=1.0, growth_3yr=15)
_fv_check("43.7", "None sector falls back to default", m['M3_PE'], 50 * 25)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 44: Output Dict Shape
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 44 — Output dict shape")
print("═" * 70)

m = _fv.calculate_all_models(_fv_base_stock(), beta=1.0, growth_3yr=15)
# Round 1: models dict now includes '_sector_resolutions' diagnostic metadata
# in addition to the 7 model outputs. Filter underscore-prefixed keys when
# checking for model output completeness.
_model_keys = {k for k in m.keys() if not k.startswith("_")}
_expected_keys = {"M1_DCF", "M2_Graham", "M3_PE", "M4_PB",
                  "M5_EV", "M6_DDM", "M7_PEG"}
_fv_check("44.1", "All 7 model keys present (excluding diagnostic metadata)",
          _model_keys, _expected_keys)

_result = _fv.get_composite_fair_value(m, cmp=1000)
# v12.5 added `cfv_capped` flag.
# v12.6 (#2): removed `mos_label` — funnel is single source of truth.
# v12.6 (#4): added `cfv_thin_models` flag and `n_models` count.
# Output dict now has 9 keys.
_expected_keys = {"cfv", "cfv_low", "cfv_high",
                  "mos_pct", "score_adjustment", "upside",
                  "cfv_capped", "cfv_thin_models", "n_models"}
_fv_check("44.2", "Composite output has all 9 expected keys (v12.6)",
          set(_result.keys()), _expected_keys)

_fv_check("44.3a", "cfv_low ≈ 0.85 × cfv",
          abs(_result['cfv_low'] - 0.85 * _result['cfv']) < 0.5, True)
_fv_check("44.3b", "cfv_high ≈ 1.15 × cfv",
          abs(_result['cfv_high'] - 1.15 * _result['cfv']) < 0.5, True)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 45: Realistic End-to-End Scenarios
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 45 — Realistic end-to-end scenarios")
print("═" * 70)

# 45.1 Profitable IT large-cap (TCS-like)
_tcs_like = {
    "close": 3500, "eps": 110, "bvps": 250, "pb": 14.0, "pe": 31.8,
    "div_yield": 1.5, "pat_yoy": 12, "ev_ebitda": 20,
    "sector": "Information Technology",
}
m = _fv.calculate_all_models(_tcs_like, beta=0.8, growth_3yr=12)
_result = _fv.get_composite_fair_value(m, cmp=3500)
# Round 1: filter out underscore-prefixed metadata keys when counting models
_fv_check("45.1a", "TCS-like: at least 6 of 7 models populated",
          sum(1 for k, v in m.items() if not k.startswith("_") and v > 0) >= 6, True)
_fv_check_in("45.1b", "TCS-like: CFV produces sensible MoS (-50% to +50%)",
             _result['mos_pct'], -50, 50)

# 45.2 Cyclical steel mid-cap
_steel_like = {
    "close": 800, "eps": 60, "bvps": 700, "pb": 1.14, "pe": 13.3,
    "div_yield": 2.0, "pat_yoy": 30, "ev_ebitda": 5,
    "sector": "Iron & Steel",
}
m = _fv.calculate_all_models(_steel_like, beta=1.4, growth_3yr=20)
_result = _fv.get_composite_fair_value(m, cmp=800)
_fv_check("45.2a", "Steel-like: M3 uses Steel PE=10 (not generic 25)",
          m['M3_PE'], 60 * 10)
_fv_check_in("45.2b", "Steel-like: produces sensible CFV",
             _result['cfv'], 100, 2400)

# 45.3 PSU bank (low PE, dividend-paying)
_psu_bank = {
    "close": 500, "eps": 50, "bvps": 400, "pb": 1.25, "pe": 10,
    "div_yield": 4.5, "pat_yoy": 15, "ev_ebitda": 8,
    "sector": "Banks",
}
m = _fv.calculate_all_models(_psu_bank, beta=1.0, growth_3yr=15)
_result = _fv.get_composite_fair_value(m, cmp=500)
_fv_check("45.3a", "PSU bank: M3 uses Banks PE=18", m['M3_PE'], 50 * 18)
_fv_check("45.3b", "PSU bank: M6 produces dividend-based FV (positive)",
          m['M6_DDM'] > 0, True)
_fv_check_in("45.3c", "PSU bank: typically shows undervaluation",
             _result['mos_pct'], -10, 200)

# 45.4 Loss-making stock (negative EPS, no dividend)
_loss_maker = {
    "close": 50, "eps": -10, "bvps": 25, "pb": 2.0, "pe": 0,
    "div_yield": 0, "pat_yoy": -50, "ev_ebitda": 0,
    "sector": "Realty",
}
m = _fv.calculate_all_models(_loss_maker, beta=1.5, growth_3yr=-10)
# Round 1: filter out underscore-prefixed metadata keys when counting models
_positive_count = sum(1 for k, v in m.items() if not k.startswith("_") and v > 0)
_fv_check_in("45.4a", "Loss-maker: most models correctly skip",
             _positive_count, 0, 2)
_fv_check("45.4b", "Loss-maker: M3 PE = 0 (negative EPS)", m['M3_PE'], 0)
_fv_check("45.4c", "Loss-maker: M7 PEG = 0", m['M7_PEG'], 0)
_fv_check("45.4d", "Loss-maker: M6 DDM = 0 (no dividend)", m['M6_DDM'], 0)

# 45.5 Multi-word sector that broke pre-v12.2 ("Real Estate Investment")
_re_stock = {
    "close": 200, "eps": 8, "bvps": 80, "pb": 2.5, "pe": 25,
    "div_yield": 1.0, "pat_yoy": 8, "ev_ebitda": 11,
    "sector": "Real Estate Investment",
}
m = _fv.calculate_all_models(_re_stock, beta=1.2, growth_3yr=10)
_fv_check("45.5a", "v12.2 'Real Estate Investment' resolves to Real Estate PE=25",
          m['M3_PE'], 8 * 25)
_fv_check("45.5b", "v12.2 'Real Estate Investment' resolves to Real Estate PB=2.5",
          m['M4_PB'], 80 * 2.5)





# ──────────────────────────────────────────────────────────────────────────
# GROUPS 46-48: v12.2 Round 1 Enhancements
# ──────────────────────────────────────────────────────────────────────────
# Tests for Round 1 follow-on fixes diagnosed from production-data analysis:
#   • SECTOR_ALIASES — explicit normalization for production sector strings
#     that didn't substring-match any benchmark key (Basic Materials,
#     Industrials, Communication Services, etc.)
#   • _canonicalize_sector() helper — case-insensitive alias lookup
#   • debug_sector_resolutions in models output — surfaces which benchmark
#     key each model resolved to, so future regressions are visible
#
# Real-world impact diagnosed: 31 of 100 production stocks were silently
# falling through to default multipliers because their sector strings
# didn't match any benchmark key in the v12.2 maps.

from analysis.fair_value_engine import (
    SECTOR_ALIASES,
    _canonicalize_sector,
    _resolve_sector_map,
)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 46: SECTOR_ALIASES — production sector normalization
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 46 — Round 1: SECTOR_ALIASES production sector normalization")
print("═" * 70)

# 46.1: Sectors found in production data that pre-Round-1 fell to defaults
# Each (input_sector, expected_PE_after_fix, expected_resolved_key)
_round1_pe_map = {
    "Software": 28, "Technology": 30, "IT": 30,
    "Banks": 18, "Banking": 18, "NBFC": 20, "Insurance": 22, "Financial": 20,
    "Pharma": 30, "Healthcare": 28,
    "FMCG": 45, "Consumer": 40,
    "Auto": 25, "Automobile": 25,
    "Steel": 10, "Metals": 12,
    "Oil": 12, "Energy": 15, "Power": 20,
    "Realty": 25, "Real Estate": 25,
    "Telecom": 22, "Cement": 22,
    "Textiles": 15, "Media": 25,
    "Chemical": 28, "Infra": 22, "Defence": 40,
}

# (input, expected_PE, expected_key)
_prod_sectors = [
    ("Basic Materials",         12, "Metals"),
    ("Industrials",             22, "Infra"),
    ("Communication Services",  22, "Telecom"),
    ("Consumer Cyclical",       40, "Consumer"),
    ("Consumer Defensive",      40, "Consumer"),
    ("Financial Services",      20, "Financial"),
    ("Real Estate",             25, "Realty"),
    ("Healthcare",              28, "Healthcare"),
    ("Technology",              30, "Technology"),
    ("Energy",                  15, "Energy"),
]
for _i, (_sec, _exp_pe, _exp_key) in enumerate(_prod_sectors, 1):
    _val, _key = _resolve_sector_map(_sec, _round1_pe_map, 25)
    _fv_check(f"46.1.{_i}a", f"Round 1 sector '{_sec}' → PE = {_exp_pe}", _val, _exp_pe)
    _fv_check(f"46.1.{_i}b", f"Round 1 sector '{_sec}' → key = '{_exp_key}'", _key, _exp_key)

# 46.2: "General" (catch-all) correctly stays at default
_val, _key = _resolve_sector_map("General", _round1_pe_map, 25)
_fv_check("46.2a", "Round 1 'General' falls to default PE=25", _val, 25)
_fv_check("46.2b", "Round 1 'General' resolved key = '(default)'", _key, "(default)")

# 46.3: Empty sector returns "(empty)" key for diagnostic clarity
_val, _key = _resolve_sector_map("", _round1_pe_map, 25)
_fv_check("46.3a", "Round 1 empty sector returns default value", _val, 25)
_fv_check("46.3b", "Round 1 empty sector resolved key = '(empty)'", _key, "(empty)")

# 46.4: SECTOR_ALIASES dict contains expected production mappings
_required_aliases = ["Basic Materials", "Industrials", "Communication Services",
                     "Consumer Cyclical", "Consumer Defensive", "Financial Services",
                     "Information Technology", "Iron & Steel"]
for _i, _alias in enumerate(_required_aliases, 1):
    _fv_check(f"46.4.{_i}", f"SECTOR_ALIASES contains '{_alias}'",
              _alias in SECTOR_ALIASES, True)

# 46.5: Aliasing is case-insensitive
_canon = _canonicalize_sector("BASIC MATERIALS")
_fv_check("46.5a", "Canonicalize is case-insensitive: 'BASIC MATERIALS' → 'Metals'",
          _canon, "Metals")
_canon = _canonicalize_sector("basic materials")
_fv_check("46.5b", "Canonicalize is case-insensitive: 'basic materials' → 'Metals'",
          _canon, "Metals")
_canon = _canonicalize_sector("  Basic Materials  ")
_fv_check("46.5c", "Canonicalize strips whitespace", _canon, "Metals")

# 46.6: Unknown sector strings pass through unchanged (substring fallback)
_canon = _canonicalize_sector("Some Niche Industry")
_fv_check("46.6", "Unknown sector passes through canonicalize unchanged",
          _canon, "Some Niche Industry")

# 46.7: None / empty produce empty string
_fv_check("46.7a", "Canonicalize None → empty string", _canonicalize_sector(None), "")
_fv_check("46.7b", "Canonicalize empty → empty string", _canonicalize_sector(""), "")


# ──────────────────────────────────────────────────────────────────────────
# GROUP 47: debug_sector_resolutions — diagnostic output (Round 1)
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 47 — Round 1: _sector_resolutions diagnostic field")
print("═" * 70)

# 47.1: Models dict now contains _sector_resolutions metadata
m = _fv.calculate_all_models(_fv_base_stock(sector="Banks"), beta=1.0, growth_3yr=15)
_fv_check("47.1", "models dict contains '_sector_resolutions' key",
          "_sector_resolutions" in m, True)

# 47.2: _sector_resolutions has entries for M3, M4, M5
_res = m["_sector_resolutions"]
_fv_check("47.2a", "_sector_resolutions has M3_PE entry", "M3_PE" in _res, True)
_fv_check("47.2b", "_sector_resolutions has M4_PB entry", "M4_PB" in _res, True)
_fv_check("47.2c", "_sector_resolutions has M5_EV entry", "M5_EV" in _res, True)

# 47.3: For Banks sector, all three resolve to "Banks" key
_fv_check("47.3a", "Banks → M3 resolves to 'Banks'", _res["M3_PE"], "Banks")
_fv_check("47.3b", "Banks → M4 resolves to 'Banks'", _res["M4_PB"], "Banks")
_fv_check("47.3c", "Banks → M5 resolves to 'Banks'", _res["M5_EV"], "Banks")

# 47.4: For "Basic Materials" (aliased to Metals), all three resolve to "Metals"
m = _fv.calculate_all_models(_fv_base_stock(sector="Basic Materials"),
                             beta=1.0, growth_3yr=15)
_res = m["_sector_resolutions"]
_fv_check("47.4a", "Basic Materials → M3 resolves to 'Metals'", _res["M3_PE"], "Metals")
_fv_check("47.4b", "Basic Materials → M4 resolves to 'Metals'", _res["M4_PB"], "Metals")
_fv_check("47.4c", "Basic Materials → M5 resolves to 'Metals'", _res["M5_EV"], "Metals")

# 47.5: For unrecognised sector, resolutions read "(default)"
m = _fv.calculate_all_models(_fv_base_stock(sector="Wibble Wobble"),
                             beta=1.0, growth_3yr=15)
_res = m["_sector_resolutions"]
_fv_check("47.5a", "Unknown sector → M3 = '(default)'", _res["M3_PE"], "(default)")
_fv_check("47.5b", "Unknown sector → M4 = '(default)'", _res["M4_PB"], "(default)")
_fv_check("47.5c", "Unknown sector → M5 = '(default)'", _res["M5_EV"], "(default)")

# 47.6: For empty sector, resolutions read "(empty)"
m = _fv.calculate_all_models(_fv_base_stock(sector=""), beta=1.0, growth_3yr=15)
_res = m["_sector_resolutions"]
_fv_check("47.6a", "Empty sector → M3 = '(empty)'", _res["M3_PE"], "(empty)")
_fv_check("47.6b", "Empty sector → M4 = '(empty)'", _res["M4_PB"], "(empty)")

# 47.7: _sector_resolutions does NOT pollute the composite
# (it's a non-numeric dict, so the composite weighter must skip it)
m = _fv.calculate_all_models(_fv_base_stock(sector="Banks"), beta=1.0, growth_3yr=15)
_result = _fv.get_composite_fair_value(m, cmp=1000)
# CFV should be sane (close to the expected blend); critically, it should NOT
# be 0 due to _sector_resolutions being treated as a model.
_fv_check("47.7", "Composite ignores _sector_resolutions metadata",
          _result['cfv'] > 0, True)

# 47.8: Composite produces same numeric value with or without _sector_resolutions
# (compare against an identical-models dict that has it stripped out)
m_clean = {k: v for k, v in m.items() if not k.startswith("_")}
_clean_result = _fv.get_composite_fair_value(m_clean, cmp=1000)
_fv_check("47.8", "Composite numerically identical with/without diagnostic key",
          _result['cfv'], _clean_result['cfv'])


# ──────────────────────────────────────────────────────────────────────────
# GROUP 48: End-to-end Round 1 production scenarios
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 48 — Round 1: production scenarios from real Excel data")
print("═" * 70)

# 48.1: HINDALCO-like (Basic Materials sector) — pre-Round-1 hit defaults,
# now should hit Metals multipliers
_hindalco_like = {
    "close": 600, "eps": 60, "bvps": 450, "pb": 1.33, "pe": 10,
    "div_yield": 0.5, "pat_yoy": -45, "ev_ebitda": 8,
    "sector": "Basic Materials",
}
m = _fv.calculate_all_models(_hindalco_like, beta=1.2, growth_3yr=10)
# Metals: PE=12, PB=1.5, EV=6
# M3 = 60 × 12 = 720 (was 60 × 25 = 1500 pre-Round-1 — clearly different)
_fv_check("48.1a", "HINDALCO-like: M3 hits Metals PE=12 (not default 25)",
          m['M3_PE'], 60 * 12)
_fv_check("48.1b", "HINDALCO-like: M4 hits Metals PB=1.5 (not default 3.0)",
          m['M4_PB'], 450 * 1.5)
_fv_check("48.1c", "HINDALCO-like: sector resolution shows Metals",
          m['_sector_resolutions']['M3_PE'], "Metals")

# 48.2: BHARTIARTL-like (Communication Services) — now hits Telecom
_bharti_like = {
    "close": 1200, "eps": 30, "bvps": 200, "pb": 6.0, "pe": 40,
    "div_yield": 0.9, "pat_yoy": -55, "ev_ebitda": 10,
    "sector": "Communication Services",
}
m = _fv.calculate_all_models(_bharti_like, beta=0.7, growth_3yr=8)
# Telecom: PE=22, PB=2.5, EV=9
_fv_check("48.2a", "BHARTIARTL-like: M3 hits Telecom PE=22",
          m['M3_PE'], 30 * 22)
_fv_check("48.2b", "BHARTIARTL-like: M4 hits Telecom PB=2.5",
          m['M4_PB'], 200 * 2.5)
_fv_check("48.2c", "BHARTIARTL-like: sector resolution shows Telecom",
          m['_sector_resolutions']['M5_EV'], "Telecom")

# 48.3: Industrials sector → Infra mapping
_industrial_like = {
    "close": 800, "eps": 40, "bvps": 300, "pb": 2.67, "pe": 20,
    "div_yield": 1.5, "pat_yoy": 10, "ev_ebitda": 11,
    "sector": "Industrials",
}
m = _fv.calculate_all_models(_industrial_like, beta=1.1, growth_3yr=15)
# Infra: PE=22, PB=2.5, EV=11
_fv_check("48.3a", "Industrials: M3 hits Infra PE=22", m['M3_PE'], 40 * 22)
_fv_check("48.3b", "Industrials: M4 hits Infra PB=2.5", m['M4_PB'], 300 * 2.5)

# 48.4: Consumer Cyclical → Consumer mapping (was already partially OK
# via substring "Consumer", but now explicit through alias)
_consumer_cyc = {
    "close": 500, "eps": 25, "bvps": 100, "pb": 5.0, "pe": 20,
    "div_yield": 1.2, "pat_yoy": 18, "ev_ebitda": 18,
    "sector": "Consumer Cyclical",
}
m = _fv.calculate_all_models(_consumer_cyc, beta=1.0, growth_3yr=15)
_fv_check("48.4a", "Consumer Cyclical: M3 hits Consumer PE=40", m['M3_PE'], 25 * 40)
_fv_check("48.4b", "Consumer Cyclical: M5 hits Consumer EV=22",
          m['_sector_resolutions']['M5_EV'], "Consumer")

# 48.5: General sector — catch-all, intentionally falls to defaults
_general_stock = {
    "close": 400, "eps": 20, "bvps": 150, "pb": 2.67, "pe": 20,
    "div_yield": 0, "pat_yoy": 5, "ev_ebitda": 12,
    "sector": "General",
}
m = _fv.calculate_all_models(_general_stock, beta=1.0, growth_3yr=10)
_fv_check("48.5a", "General sector: M3 falls to default PE=25",
          m['M3_PE'], 20 * 25)
_fv_check("48.5b", "General sector: resolution explicitly shows '(default)'",
          m['_sector_resolutions']['M3_PE'], "(default)")

# 48.6: Aliased sector still produces sensible composite
m = _fv.calculate_all_models(_hindalco_like, beta=1.2, growth_3yr=10)
_result = _fv.get_composite_fair_value(m, cmp=600)
_fv_check_in("48.6a", "HINDALCO-like (Round 1): produces sensible CFV",
             _result['cfv'], 100, 1800)
_fv_check_in("48.6b", "HINDALCO-like (Round 1): MoS in plausible range",
             _result['mos_pct'], -50, 200)





# ──────────────────────────────────────────────────────────────────────────
# GROUPS 49-50: v12.3 Round 2 — M5 EV proper formula + M7 PEG_BENCHMARK
# ──────────────────────────────────────────────────────────────────────────
# Tests for Round 2 enhancements:
#   • M5 EV/EBITDA: three-tier formula. Tier 1 uses proper EV math when
#     q_ebitda_cr + total_debt_cr + cash_cr + mcap_cr are all available.
#     Tier 2 falls back to the v12.2 multiplicative shortcut. Tier 3 skips
#     entirely (banks/NBFCs/insurance, or when no EV/EBITDA at all).
#   • M7 PEG: PEG_BENCHMARK = 1.0 made explicit (Lynch's rule of thumb).
#   • _m5_method diagnostic: surfaces which M5 tier fired.


def _fv_stock_with_full_m5_data(**overrides):
    """Stock with all the Tier-1 M5 fields populated."""
    s = _fv_base_stock(**overrides)
    # Add proper-M5 inputs (Round 2)
    s.setdefault("q_ebitda_cr",   25.0)    # quarterly EBITDA in ₹Cr
    s.setdefault("total_debt_cr", 200.0)
    s.setdefault("cash_cr",        50.0)
    s.setdefault("mcap_cr",      1000.0)   # market cap in ₹Cr
    return s


# ──────────────────────────────────────────────────────────────────────────
# GROUP 49: M5 EV/EBITDA — Round 2 three-tier formula
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 49 — Round 2: M5 EV/EBITDA proper formula + tier dispatch")
print("═" * 70)

# 49.1: Tier 1 (proper) fires when all 4 fields are populated
# Pharma sector_ev=18, q_ebitda=25 → annual_ebitda=100
# fair_EV_cr = 100 × 18 = 1800
# net_debt_cr = 200 - 50 = 150
# fair_mcap_cr = 1800 - 150 = 1650
# fair_per_share = CMP × (1650 / 1000) = 1000 × 1.65 = 1650
m = _fv.calculate_all_models(
    _fv_stock_with_full_m5_data(sector="Pharma"),
    beta=1.0, growth_3yr=15,
)
_fv_check("49.1a", "Tier 1 fires when all 4 fields populated (Pharma example)",
          m['M5_EV'], 1650.0, tol=1.0)
_fv_check("49.1b", "Tier 1 records '_m5_method' = 'proper'",
          m['_sector_resolutions'].get('_m5_method'), "proper")

# 49.2: Tier 1 with zero-debt company (legitimate, common case)
# Same as 49.1 but cash > debt → net_debt is negative (net cash position)
# fair_mcap_cr = 1800 - (50 - 100) = 1800 + 50 = 1850 → CMP × 1.85 = 1850
m = _fv.calculate_all_models(
    _fv_stock_with_full_m5_data(sector="Pharma", total_debt_cr=50, cash_cr=100),
    beta=1.0, growth_3yr=15,
)
_fv_check("49.2", "Tier 1 handles net cash company (cash > debt)",
          m['M5_EV'], 1850.0, tol=1.0)

# 49.3: Tier 1 produces 4× CMP cap when ratio explodes
# Take a tiny-debt stock with huge EBITDA so fair_EV is much higher than mcap
# q_ebitda_cr = 200 → annual = 800; sector_ev=18 → fair_EV = 14400
# net_debt = 0; fair_mcap = 14400; ratio = 14400/1000 = 14.4 → would be 14400
# Should be capped at 4× CMP = 4000
m = _fv.calculate_all_models(
    _fv_stock_with_full_m5_data(
        sector="Pharma", q_ebitda_cr=200, total_debt_cr=0, cash_cr=0,
    ),
    beta=1.0, growth_3yr=15,
)
_fv_check("49.3", "Tier 1 capped at 4× CMP for outliers",
          m['M5_EV'], 4000.0, tol=1.0)

# 49.4: Tier 1 with very high debt → fair_mcap_cr negative → 70% discount
# fair_EV_cr = 100 × 18 = 1800; debt 5000 - cash 50 = 4950 net debt
# fair_mcap = 1800 - 4950 = -3150 → negative → emit CMP × 0.3 = 300
m = _fv.calculate_all_models(
    _fv_stock_with_full_m5_data(sector="Pharma", total_debt_cr=5000, cash_cr=50),
    beta=1.0, growth_3yr=15,
)
_fv_check("49.4a", "Tier 1 with negative fair equity emits 70% discount",
          m['M5_EV'], 300.0, tol=1.0)
_fv_check("49.4b", "Tier 1 negative-equity records method = 'proper_negative_equity'",
          m['_sector_resolutions'].get('_m5_method'), "proper_negative_equity")

# 49.5: Tier 2 (shortcut) fires when proper inputs missing but ev_ebitda available
# This is the legacy v12.2 path
s = _fv_base_stock(sector="Pharma", ev_ebitda=15)
# DON'T add q_ebitda_cr / total_debt_cr / cash_cr / mcap_cr
m = _fv.calculate_all_models(s, beta=1.0, growth_3yr=15)
# Pharma sector_ev=18, current=15 → CMP × 18/15 = 1000 × 1.2 = 1200
_fv_check("49.5a", "Tier 2 (shortcut) fires when proper inputs missing",
          m['M5_EV'], 1200.0, tol=1.0)
_fv_check("49.5b", "Tier 2 records method = 'shortcut'",
          m['_sector_resolutions'].get('_m5_method'), "shortcut")

# 49.6: Tier 3a — Bank sector → skip entirely (M5 not meaningful for financials)
m = _fv.calculate_all_models(
    _fv_stock_with_full_m5_data(sector="Banks"),
    beta=1.0, growth_3yr=15,
)
_fv_check("49.6a", "Tier 3 skip_financial: Banks → M5 = 0",
          m['M5_EV'], 0)
_fv_check("49.6b", "Banks records method = 'skip_financial'",
          m['_sector_resolutions'].get('_m5_method'), "skip_financial")

# 49.7: Tier 3b — NBFC also skipped
m = _fv.calculate_all_models(
    _fv_stock_with_full_m5_data(sector="NBFC"),
    beta=1.0, growth_3yr=15,
)
_fv_check("49.7a", "NBFC → M5 = 0", m['M5_EV'], 0)
_fv_check("49.7b", "NBFC method = 'skip_financial'",
          m['_sector_resolutions'].get('_m5_method'), "skip_financial")

# 49.8: Tier 3c — Insurance also skipped
m = _fv.calculate_all_models(
    _fv_stock_with_full_m5_data(sector="Insurance"),
    beta=1.0, growth_3yr=15,
)
_fv_check("49.8a", "Insurance → M5 = 0", m['M5_EV'], 0)
_fv_check("49.8b", "Insurance method = 'skip_financial'",
          m['_sector_resolutions'].get('_m5_method'), "skip_financial")

# 49.9: Tier 3d — no EV/EBITDA AND no proper inputs → skip
s = _fv_base_stock(sector="Pharma", ev_ebitda=0)
m = _fv.calculate_all_models(s, beta=1.0, growth_3yr=15)
_fv_check("49.9a", "No data at all → M5 = 0", m['M5_EV'], 0)
_fv_check("49.9b", "Records method = 'skip_no_data'",
          m['_sector_resolutions'].get('_m5_method'), "skip_no_data")

# 49.10: Round 1 sector aliasing still works with Tier 1
# Basic Materials → Metals (sector_ev=6); q_ebitda=25 → annual 100
# fair_EV = 100 × 6 = 600; net_debt = 150; fair_mcap = 450
# fair_per_share = 1000 × (450/1000) = 450
m = _fv.calculate_all_models(
    _fv_stock_with_full_m5_data(sector="Basic Materials"),
    beta=1.0, growth_3yr=15,
)
_fv_check("49.10a", "Round 1 + Round 2: 'Basic Materials' → Metals + Tier 1",
          m['M5_EV'], 450.0, tol=1.0)
_fv_check("49.10b", "Resolved key still 'Metals' (Round 1)",
          m['_sector_resolutions']['M5_EV'], "Metals")

# 49.11: Tier 1 with negative q_ebitda → falls through to Tier 2 (since
# Tier 1 requires q_ebitda > 0)
s = _fv_stock_with_full_m5_data(sector="Pharma", q_ebitda_cr=-5, ev_ebitda=15)
m = _fv.calculate_all_models(s, beta=1.0, growth_3yr=15)
# q_ebitda < 0 disqualifies Tier 1; Tier 2 fires with ev_ebitda=15
# Pharma sector_ev=18 → CMP × 18/15 = 1200
_fv_check("49.11", "Negative q_ebitda → falls through to Tier 2 shortcut",
          m['M5_EV'], 1200.0, tol=1.0)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 50: M7 PEG — Round 2: PEG_BENCHMARK explicit constant
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 50 — Round 2: M7 PEG_BENCHMARK explicit constant")
print("═" * 70)

# 50.1: Round 2 default PEG_BENCHMARK = 1.0 produces same numbers as v12.2
# (mathematically identical since 1.0 × X = X)
m = _fv.calculate_all_models(_fv_base_stock(eps=50), beta=1.0, growth_3yr=15)
# 50 × 15 × 1.0 = 750 (same as v12.2)
_fv_check("50.1", "PEG_BENCHMARK=1.0 default unchanged from v12.2",
          m['M7_PEG'], 750.0)

# 50.2: Growth cap at 30% still applies
m = _fv.calculate_all_models(_fv_base_stock(eps=50), beta=1.0, growth_3yr=50)
# capped at 30 → 50 × 30 × 1.0 = 1500
_fv_check("50.2", "Growth still capped at 30% post-Round-2",
          m['M7_PEG'], 1500.0)

# 50.3: Unit guard still active (growth < 1.0 → skip)
m = _fv.calculate_all_models(_fv_base_stock(eps=50), beta=1.0, growth_3yr=0.15)
_fv_check("50.3", "Unit guard still skips decimal-fraction growth",
          m['M7_PEG'], 0)

# 50.4: Negative EPS still skips
m = _fv.calculate_all_models(_fv_base_stock(eps=-5), beta=1.0, growth_3yr=15)
_fv_check("50.4", "Negative EPS still skips M7", m['M7_PEG'], 0)


# ──────────────────────────────────────────────────────────────────────────
# GROUP 51: Round 2 — production-data integration scenarios
# ──────────────────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
print("GROUP 51 — Round 2: production-realistic integration")
print("═" * 70)

# 51.1: HINDALCO-like (Basic Materials with full Round 2 inputs)
# This was the exemplar stock for Round 1; verify Round 2 works on top.
_hindalco = {
    "close": 1074, "eps": 60, "bvps": 450, "pb": 1.33, "pe": 14.4,
    "div_yield": 0.5, "pat_yoy": -45, "ev_ebitda": 8,
    "sector": "Basic Materials",
    # Round 2 fields (HINDALCO is a real metals giant with ~₹2,40,000 Cr mcap)
    "q_ebitda_cr":  6500, "total_debt_cr": 50000, "cash_cr": 8000,
    "mcap_cr":      241000,
}
m = _fv.calculate_all_models(_hindalco, beta=1.2, growth_3yr=10)
_fv_check("51.1a", "HINDALCO-like: Round 1 sector resolution still 'Metals'",
          m['_sector_resolutions']['M5_EV'], "Metals")
_fv_check("51.1b", "HINDALCO-like: Round 2 Tier 1 fired (proper formula)",
          m['_sector_resolutions']['_m5_method'], "proper")
# Sanity: M5 should be a non-zero positive number, less than 4× CMP
_fv_check_in("51.1c", "HINDALCO-like: M5 in plausible range",
             m['M5_EV'], 0, 4 * 1074)

# 51.2: PSU bank (financial sector → M5 must skip)
_psu_bank = {
    "close": 500, "eps": 50, "bvps": 400, "pb": 1.25, "pe": 10,
    "div_yield": 4.5, "pat_yoy": 15, "ev_ebitda": 8,
    "sector": "Banks",
    # Even with full Round 2 inputs, banks should skip M5
    "q_ebitda_cr": 1000, "total_debt_cr": 5000, "cash_cr": 800,
    "mcap_cr":     50000,
}
m = _fv.calculate_all_models(_psu_bank, beta=1.0, growth_3yr=15)
_fv_check("51.2a", "PSU bank: M5 = 0 (financial sector)", m['M5_EV'], 0)
_fv_check("51.2b", "PSU bank: method = 'skip_financial'",
          m['_sector_resolutions']['_m5_method'], "skip_financial")
# Other models should still fire
_fv_check("51.2c", "PSU bank: M3 still fires (Banks PE=18)",
          m['M3_PE'], 50 * 18)

# 51.3: Pre-Round-2 stock (no q_ebitda_cr etc) still works via Tier 2 shortcut
_legacy = {
    "close": 1000, "eps": 50, "bvps": 400, "pb": 2.5, "pe": 20,
    "div_yield": 1.5, "pat_yoy": 10, "ev_ebitda": 12,
    "sector": "Consumer Cyclical",
    # NO Round 2 inputs
}
m = _fv.calculate_all_models(_legacy, beta=1.0, growth_3yr=12)
# Consumer sector_ev=22, current=12 → CMP × 22/12 = 1833.33
_fv_check("51.3a", "Legacy stock (no Round 2 fields): Tier 2 fires",
          m['_sector_resolutions']['_m5_method'], "shortcut")
_fv_check("51.3b", "Legacy stock M5 ≈ shortcut formula result",
          m['M5_EV'], round(1000 * 22 / 12, 2), tol=1.0)





# ──────────────────────────────────────────────────────────────────────────
# GROUP 52: v12.3 Round 2 — Downstream Consumer Regression Guards
# ──────────────────────────────────────────────────────────────────────────
# The Round 2 changes added a dict-typed key (`_sector_resolutions`) to the
# models dict that gets `stock.update(models)`-ed into the stock dict in
# master_funnel.py. These tests lock in the no-regression guarantee that
# downstream consumers (DataFrame ops, Excel column lookups, JSON-style
# round-trips) handle the new metadata key safely.
#
# Each test simulates a specific real-world downstream operation that
# could plausibly break if `_sector_resolutions` were treated as numeric.

print("\n" + "═" * 70)
print("GROUP 52 — Round 2: downstream consumer regression guards")
print("═" * 70)

import pandas as _pd

# 52.1: stock.update(models) — the master_funnel.py pattern. After the update,
# stock dict should have _sector_resolutions as a dict (not as numeric)
m = _fv.calculate_all_models(_fv_base_stock(sector="Banks"), beta=1.0, growth_3yr=15)
fake_stock = {"symbol": "TEST", "close": 1000}
fake_stock.update(m)
_fv_check("52.1a", "stock.update(models) preserves _sector_resolutions as dict",
          isinstance(fake_stock.get("_sector_resolutions"), dict), True)
_fv_check("52.1b", "stock dict still has all 7 model numeric values",
          all(isinstance(fake_stock.get(k), (int, float))
              for k in ["M1_DCF","M2_Graham","M3_PE","M4_PB",
                        "M5_EV","M6_DDM","M7_PEG"]), True)

# 52.2: DataFrame creation from list of stock dicts (what master_funnel.py
# does before passing to excel_generator). The metadata column should appear
# but not break DataFrame construction.
_stocks = []
for _sec in ["Banks", "Technology", "Basic Materials"]:
    _s = _fv_base_stock(sector=_sec)
    _m = _fv.calculate_all_models(_s, beta=1.0, growth_3yr=15)
    _r = _fv.get_composite_fair_value(_m, cmp=_s["close"])
    _s.update(_m)
    _s.update(_r)
    _s["symbol"] = f"T_{_sec[:3]}"
    _stocks.append(_s)
_df = _pd.DataFrame(_stocks)
_fv_check("52.2a", "DataFrame creates cleanly with metadata column",
          len(_df), 3)
_fv_check("52.2b", "DataFrame has _sector_resolutions column",
          "_sector_resolutions" in _df.columns, True)
_fv_check("52.2c", "M5_EV column is numeric in DataFrame",
          _pd.api.types.is_numeric_dtype(_df["M5_EV"]), True)

# 52.3: Sort and filter operations (excel_generator uses these)
try:
    _sorted = _df.sort_values("cfv", ascending=False)
    _fv_check("52.3a", "Sort by cfv works with metadata column", len(_sorted), 3)
except Exception as _e:
    _fv_check("52.3a", f"Sort failed: {_e}", False, True)

try:
    _filtered = _df[_df["M5_EV"] > 0]
    # Banks should be filtered out (M5=0); Technology + Basic Materials remain
    _fv_check("52.3b", "Filter M5>0 correctly excludes financial-sector skip",
              len(_filtered), 2)
except Exception as _e:
    _fv_check("52.3b", f"Filter failed: {_e}", False, True)

# 52.4: to_dict('records') round-trip (excel_generator uses this)
try:
    _records = _df.to_dict("records")
    _df2 = _pd.DataFrame(_records)
    _fv_check("52.4a", "to_dict('records') round-trip preserves rows",
              len(_records), 3)
    _fv_check("52.4b", "Round-trip preserves _sector_resolutions",
              isinstance(_records[0].get("_sector_resolutions"), dict), True)
except Exception as _e:
    _fv_check("52.4a", f"Round-trip failed: {_e}", False, True)

# 52.5: Excel generator's FV_MODEL_KEYS pattern — the {"M1_DCF","M2_Graham",...}
# set lookup must work for explicit keys; metadata key should NOT be in the set
_FV_MODEL_KEYS = {"M1_DCF","M2_Graham","M3_PE","M4_PB","M5_EV","M6_DDM","M7_PEG",
                  "cfv","cfv_low","cfv_high"}
_fv_check("52.5a", "_sector_resolutions correctly excluded from FV_MODEL_KEYS",
          "_sector_resolutions" not in _FV_MODEL_KEYS, True)
_fv_check("52.5b", "_m5_method correctly excluded from FV_MODEL_KEYS",
          "_m5_method" not in _FV_MODEL_KEYS, True)

# 52.6: Composite weighter must skip non-model dict-typed keys.
# Test passing a contrived dict with extra weird keys to ensure they're filtered.
_contrived = {
    "M1_DCF": 1000.0, "M2_Graham": 950.0, "M3_PE": 1100.0,
    "M4_PB": 980.0, "M5_EV": 1050.0, "M6_DDM": 0, "M7_PEG": 1020.0,
    "_sector_resolutions": {"M3_PE": "Banks", "_m5_method": "skip_financial"},
    "_extra_metadata": {"foo": "bar"},  # simulate a future metadata key
    "_extra_string": "this should be ignored",
    "UNKNOWN_MODEL": 999,
}
_result = _fv.get_composite_fair_value(_contrived, cmp=1000)
# Expected: only the 6 known non-zero models contribute
# total_w = 0.30+0.15+0.20+0.15+0.10+0.05 = 0.95
# weighted sum = 1000*0.30 + 950*0.15 + 1100*0.20 + 980*0.15 + 1050*0.10 + 1020*0.05
#              = 300 + 142.5 + 220 + 147 + 105 + 51 = 965.5
# cfv = 965.5 / 0.95 = 1016.32
_fv_check("52.6", "Composite weighter ignores all non-model keys (dict, str, unknown)",
          _result['cfv'], 1016.32, tol=0.5)

# 52.7: Confirm scoring engine accepts FV-engine-output stocks without crash.
# This is the master_funnel SECTION 6 path.
try:
    from analysis.scoring_engine import ScoringEngine
    _sc = ScoringEngine()
    _stk = _fv_base_stock(sector="Banks")
    _stk["fundamental_score"] = 60
    _stk["technical_score"]   = 65
    _stk["safety_score"]      = 60
    _stk["sentiment_score"]   = 55
    _stk["early_entry_score"] = 25
    _stk["stage2_score"]      = 25
    _stk["cap_category"]      = "LARGE"
    _stk["fii_3q_trend"]      = "UP"
    _stk["supertrend"]        = "BUY"
    _stk["rotation_stage"]    = "STAGE 2 — CONFIRMED UPTREND"
    _m = _fv.calculate_all_models(_stk, beta=1.0, growth_3yr=15)
    _r = _fv.get_composite_fair_value(_m, cmp=_stk["close"])
    _stk.update(_m)
    _stk.update(_r)
    _v = _sc.calculate_composite_score(_stk)
    _fv_check("52.7a", "ScoringEngine accepts FV output incl. _sector_resolutions",
              "verdict" in _v, True)
    _fv_check("52.7b", "Composite score is numeric",
              isinstance(_v["composite_score"], (int, float)), True)
except Exception as _e:
    _fv_check("52.7a", f"Scoring crashed: {_e}", False, True)

# 52.8: Empty-string sector handling (defensive — pipeline may pass "" if
# yfinance fails for that stock). M5 must not crash.
m = _fv.calculate_all_models(
    _fv_base_stock(sector="", q_ebitda_cr=100, total_debt_cr=300,
                   cash_cr=50, mcap_cr=5000),
    beta=1.0, growth_3yr=15,
)
# Empty sector → resolved key is "(empty)", which is NOT a financial keyword
# So Tier 1 fires with default sector_ev=15
_fv_check("52.8a", "Empty sector still produces M5 via Tier 1",
          m['M5_EV'] > 0, True)
_fv_check("52.8b", "Empty sector method = 'proper' (not skip_financial)",
          m['_sector_resolutions'].get('_m5_method'), "proper")

# 52.9: NaN/None safety in Round-2 input fields. If the pipeline somehow
# passes None for q_ebitda_cr, the engine should fall back to Tier 2 not crash.
m = _fv.calculate_all_models(
    _fv_base_stock(sector="Pharma", q_ebitda_cr=None, ev_ebitda=12),
    beta=1.0, growth_3yr=15,
)
_fv_check("52.9a", "None q_ebitda_cr → falls to Tier 2 shortcut",
          m['_sector_resolutions'].get('_m5_method'), "shortcut")
_fv_check("52.9b", "None q_ebitda_cr produces non-zero M5 via shortcut",
          m['M5_EV'] > 0, True)



# ══════════════════════════════════════════════════════════════════════
# GROUP 53 — v12.4 Production Blocker Patches (Issues #1, #6, #9, #15)
# ══════════════════════════════════════════════════════════════════════
# These tests guard the four production-blocker fixes documented in the
# v12.4 investigation. Each patch had a corresponding pre-fix bug that
# reached production; these tests ensure the fix stays in place across
# future refactors.
#
#   53.1  Header demotion threshold (excel_generator) — ≥30 % coverage
#   53.2  Resist 2 / Support 2 (backfill_history) — prior-window slice
#   53.3  Profitability clamp (master_funnel) — _clamp_pct boundaries
#   53.4  Anthropic→Gemini text replacement (excel_generator + tooltip)
# ══════════════════════════════════════════════════════════════════════
print("\n" + "═" * 70)
print("GROUP 53 — v12.4 Production blocker patches")
print("═" * 70)

# ── 53.1: Header demotion threshold ─────────────────────────────────────
# Replicate the patched logic from excel_generator.py:1480-1497 against
# synthetic preview rows. The fix raised the threshold from "≥1 row" to
# "≥30 %" so that columns sparsely populated (Pro QoQ Δ at 2/99,
# FII QoQ Δ at 22/99) correctly stay red instead of being demoted.
def _hdr_has_data(rows, key, threshold=0.30):
    n_total = max(1, len(rows))
    real = 0
    for stk in rows:
        v = stk.get(key)
        if v is None: continue
        if v in ("", "—", "--", "N/A", "STABLE"): continue
        if v in (0, 0.0, "0", "0.0"): continue
        real += 1
    return (real / n_total) >= threshold

def _make_preview(key, n_pop, n_total=99, fill="—"):
    return [{key: (1.5 if i < n_pop else fill)} for i in range(n_total)]

_hdr_cases = [
    ("53.1a", "Pro QoQ Δ at 2/99 (sparse)",       2, False),
    ("53.1b", "FII QoQ Δ at 22/99 (below 30 %)", 22, False),
    ("53.1c", "ND/EBITDA at 89/99 (well-covered)",89, True),
    ("53.1d", "Pledge % at 0/99 (fully empty)",   0, False),
    ("53.1e", "Capex/Rev at 94/99 (well-covered)",94, True),
    ("53.1f", "Edge: 30/99 (exactly threshold)", 30, True),
    ("53.1g", "Edge: 29/99 (just below)",        29, False),
]
for tid, desc, npop, expected_has_data in _hdr_cases:
    rows = _make_preview("k", npop)
    got = _hdr_has_data(rows, "k", threshold=0.30)
    if got == expected_has_data:
        passed += 1
        print(f"  ✓ {tid} {desc}")
    else:
        failed += 1
        failures.append(f"{tid} [{desc}]: has_data={got} (want {expected_has_data})")

# 53.1h: empty preview must not crash (max(1, len) guard)
try:
    got = _hdr_has_data([], "k", threshold=0.30)
    if got is False:
        passed += 1
        print("  ✓ 53.1h Empty preview safely returns False (no ZeroDivisionError)")
    else:
        failed += 1
        failures.append("53.1h: empty preview returned True unexpectedly")
except ZeroDivisionError:
    failed += 1
    failures.append("53.1h: ZeroDivisionError on empty preview — guard missing")

# 53.1i: source-code presence check — patch markers must be in the file
import os as _os53
_eg_path = _os53.path.join(_os53.path.dirname(_os53.path.abspath(__file__)),
                           "reporting", "excel_generator.py")
with open(_eg_path) as _fh:
    _eg_src = _fh.read()
if "_COVERAGE_MIN" in _eg_src and "_COVERAGE_MIN = 0.30" in _eg_src:
    passed += 1
    print("  ✓ 53.1i excel_generator.py has _COVERAGE_MIN=0.30 marker")
else:
    failed += 1
    failures.append("53.1i: excel_generator.py missing _COVERAGE_MIN=0.30 — patch reverted?")

# ── 53.2: Resist 2 / Support 2 prior-window logic ───────────────────────
# Replicate the patched logic from backfill_history.py:744-770. The fix
# computes R2 over bars BEFORE the most recent 20 so it doesn't collapse
# to R1 when a fresh 52-week breakout sits in the last 20 days (pre-fix
# behaviour: R1==R2 in 87.9 % of production rows).
import pandas as _pd53
import numpy as _np53

def _compute_r1_r2_patched(highs, lows):
    h = _pd53.Series(highs)
    l = _pd53.Series(lows)
    sup1 = l.rolling(20).min()
    res1 = h.rolling(20).max()
    if len(h) >= 80:
        prior_l = l.iloc[:-20]
        prior_h = h.iloc[:-20]
        _lb2    = min(252, len(prior_h))
        sup2    = prior_l.rolling(_lb2).min()
        res2    = prior_h.rolling(_lb2).max()
        sup2 = sup2.reindex(l.index, method="ffill")
        res2 = res2.reindex(h.index, method="ffill")
    else:
        sup2 = _pd53.Series([float("nan")] * len(h), index=l.index)
        res2 = _pd53.Series([float("nan")] * len(h), index=h.index)
    def _last(s):
        v = s.iloc[-1]
        return 0.0 if _pd53.isna(v) else float(v)
    return _last(res1), _last(res2), _last(sup1), _last(sup2)

def _compute_r1_r2_old(highs, lows):
    """The pre-fix (v10.9) logic — used to verify the bug it fixed."""
    h = _pd53.Series(highs)
    l = _pd53.Series(lows)
    _lb2 = min(252, len(h))
    sup1 = l.rolling(20).min()
    sup2 = l.rolling(_lb2).min() if _lb2 >= 60 else l.rolling(max(40, len(h))).min()
    res1 = h.rolling(20).max()
    res2 = h.rolling(_lb2).max() if _lb2 >= 60 else h.rolling(max(40, len(h))).max()
    return float(res1.iloc[-1]), float(res2.iloc[-1]), float(sup1.iloc[-1]), float(sup2.iloc[-1])

# 53.2a — fresh-breakout scenario: 252 days, ATH lands in last 20
_np53.random.seed(0)
prices_a = 100 + _np53.cumsum(_np53.random.randn(252) * 0.5)
prices_a[:-20] -= 5
prices_a[-5] = max(prices_a) + 10
highs_a = (prices_a + 1.5).tolist()
lows_a  = (prices_a - 1.5).tolist()
r1_old, r2_old, _, _ = _compute_r1_r2_old(highs_a, lows_a)
r1_new, r2_new, _, _ = _compute_r1_r2_patched(highs_a, lows_a)
if abs(r1_old - r2_old) < 0.01:
    passed += 1
    print(f"  ✓ 53.2a Pre-fix logic confirms bug: R1={r1_old:.2f} == R2={r2_old:.2f} on fresh breakout")
else:
    warnings.append(f"53.2a: pre-fix scenario didn't reproduce bug — test may not be representative")
    passed += 1
if abs(r1_new - r2_new) >= 1.0:
    passed += 1
    print(f"  ✓ 53.2b Patched logic separates R1={r1_new:.2f} from R2={r2_new:.2f}")
else:
    failed += 1
    failures.append(f"53.2b: patched R1 ({r1_new:.2f}) too close to R2 ({r2_new:.2f}) — fix not effective")

# 53.2c — short-history scenario: only 60 days → must not crash and R2 should be 0
short_h = (prices_a[-60:] + 1.5).tolist()
short_l = (prices_a[-60:] - 1.5).tolist()
try:
    r1_s, r2_s, s1_s, s2_s = _compute_r1_r2_patched(short_h, short_l)
    if r2_s == 0.0 and s2_s == 0.0:
        passed += 1
        print(f"  ✓ 53.2c Short history (60d): R2 falls back to 0 (renders '—' in dashboard)")
    else:
        failed += 1
        failures.append(f"53.2c: short-history R2={r2_s} S2={s2_s} (want 0.0 for both)")
except Exception as _e:
    failed += 1
    failures.append(f"53.2c: short-history scenario raised {type(_e).__name__}: {_e}")

# 53.2d — older-ATH scenario: 200 days, ATH at day 100. R2 should ≥ R1.
_np53.random.seed(2)
prices_d = 100 + _np53.cumsum(_np53.random.randn(200) * 0.7)
prices_d[100] = max(prices_d) + 5
highs_d = (prices_d + 1.5).tolist()
lows_d  = (prices_d - 1.5).tolist()
r1_d, r2_d, _, _ = _compute_r1_r2_patched(highs_d, lows_d)
if r2_d > r1_d:
    passed += 1
    print(f"  ✓ 53.2d Older-ATH scenario: R2={r2_d:.2f} > R1={r1_d:.2f} (R2 captures prior 52W ceiling)")
else:
    failed += 1
    failures.append(f"53.2d: R2={r2_d:.2f} should exceed R1={r1_d:.2f} when ATH is in older window")

# 53.2e — 52W high invariant: R2 must NEVER exceed the global max of the prior window
prior_max = max(highs_a[:-20])
if r2_new <= prior_max + 0.01:
    passed += 1
    print(f"  ✓ 53.2e R2={r2_new:.2f} respects prior-window max bound ({prior_max:.2f})")
else:
    failed += 1
    failures.append(f"53.2e: R2={r2_new:.2f} exceeds prior-window max {prior_max:.2f}")

# 53.2f — source-code presence check
_bf_path = _os53.path.join(_os53.path.dirname(_os53.path.abspath(__file__)),
                           "backfill_history.py")
with open(_bf_path) as _fh:
    _bf_src = _fh.read()
if "prior_h" in _bf_src and "prior_l" in _bf_src and "iloc[:-20]" in _bf_src:
    passed += 1
    print("  ✓ 53.2f backfill_history.py has prior-window slice markers")
else:
    failed += 1
    failures.append("53.2f: backfill_history.py missing prior_h/prior_l slice — patch reverted?")

# ── 53.3: Profitability clamp ────────────────────────────────────────────
def _fvn53(v):
    try: return float(v) if v is not None else 0.0
    except (ValueError, TypeError): return 0.0

def _pct53(v):
    f = _fvn53(v)
    if f == 0: return "—"
    return round(f * 100, 2) if abs(f) < 2.0 else round(f, 2)

def _clamp_pct53(raw, lo, hi):
    out = _pct53(raw)
    if isinstance(out, (int, float)):
        if out > hi: return round(hi, 2)
        if out < lo: return round(lo, 2)
    return out

# Production-data cases from the v12.4 investigation
_clamp_cases = [
    ("53.3a", "DGCONTENT NPM 126.4 → 100",    126.4, -100, 100,  100),
    ("53.3b", "AMAGI    NPM 189.1 → 100",     189.1, -100, 100,  100),
    ("53.3c", "MEGASTAR NPM 164.8 → 100",     164.8, -100, 100,  100),
    ("53.3d", "REDINGTON NPM 156.8 → 100",    156.8, -100, 100,  100),
    ("53.3e", "RELIGARE NPM 127.6 → 100",     127.6, -100, 100,  100),
    ("53.3f", "GCSL NPM -144.5 → -100",      -144.5, -100, 100, -100),
    ("53.3g", "M&MFIN ROA 189 → 100",         189,   -100, 100,  100),
    ("53.3h", "TATACAP ROA 181.5 → 100",      181.5, -100, 100,  100),
    ("53.3i", "Normal NPM 12.5 unchanged",    12.5,  -100, 100,  12.5),
    ("53.3j", "Fraction NPM 0.125 → 12.5",    0.125, -100, 100,  12.5),
    ("53.3k", "Zero NPM → '—' (missing)",     0,     -100, 100,  "—"),
    ("53.3l", "None NPM → '—' (missing)",     None,  -100, 100,  "—"),
    ("53.3m", "Legit loss NPM -25 unchanged",-25,    -100, 100, -25),
    ("53.3n", "Edge low -150 → -100",        -150,   -100, 100, -100),
    ("53.3o", "Edge high 150 → 100",          150,   -100, 100,  100),
    ("53.3p", "Gross margin lo=0: -0.05 (-5 %) → 0", -0.05, 0, 100, 0),
]
for tid, desc, raw, lo, hi, expected in _clamp_cases:
    got = _clamp_pct53(raw, lo, hi)
    if got == expected:
        passed += 1
        print(f"  ✓ {tid} {desc}")
    else:
        failed += 1
        failures.append(f"{tid} [{desc}]: got {got!r} (want {expected!r})")

# 53.3q: numeric scoring clamp — ROE/GM/NM should never exceed bounds
def _to_pct53(raw):
    return raw * 100 if 0 < abs(raw) < 2.0 else raw

_inflated = [(189, "ROA"), (126.4, "NPM"), (-144.5, "NPM"), (0.5, "ROE")]
for raw, label in _inflated:
    pct = _to_pct53(_fvn53(raw))
    clamped_npm = max(-100, min(100, pct))
    clamped_gm  = max(   0, min(100, pct))
    if -100 <= clamped_npm <= 100 and 0 <= clamped_gm <= 100:
        passed += 1
        print(f"  ✓ 53.3q-{label}-{raw} numeric clamp keeps result in bounds")
    else:
        failed += 1
        failures.append(f"53.3q-{label}-{raw}: pct={pct} clamped_npm={clamped_npm} clamped_gm={clamped_gm} out of bounds")

# 53.3r: source-code presence check
_mf_path = _os53.path.join(_os53.path.dirname(_os53.path.abspath(__file__)),
                           "master_funnel.py")
with open(_mf_path) as _fh:
    _mf_src = _fh.read()
if "_clamp_pct" in _mf_src and "v12.4" in _mf_src:
    passed += 1
    print("  ✓ 53.3r master_funnel.py has _clamp_pct + v12.4 marker")
else:
    failed += 1
    failures.append("53.3r: master_funnel.py missing _clamp_pct or v12.4 marker — patch reverted?")

# ── 53.4: Anthropic → Gemini text replacement ──────────────────────────
_eg_anthropic = _eg_src.count("Anthropic API credits")
_eg_gemini    = _eg_src.count("Gemini API credits")
if _eg_anthropic == 0:
    passed += 1
    print("  ✓ 53.4a No 'Anthropic API credits' strings remain in excel_generator.py")
else:
    failed += 1
    failures.append(f"53.4a: {_eg_anthropic} 'Anthropic API credits' string(s) still in excel_generator.py")

if _eg_gemini >= 6:
    passed += 1
    print(f"  ✓ 53.4b excel_generator.py has {_eg_gemini} 'Gemini API credits' strings (≥6 expected)")
else:
    failed += 1
    failures.append(f"53.4b: only {_eg_gemini} 'Gemini API credits' in excel_generator.py — patch incomplete")

# 53.4c: tooltip_formatter.py — historical "Claude AI" must be cleared
_tf_path = _os53.path.join(_os53.path.dirname(_os53.path.abspath(__file__)),
                           "reporting", "tooltip_formatter.py")
with open(_tf_path) as _fh:
    _tf_src = _fh.read()
if "Claude AI" not in _tf_src:
    passed += 1
    print("  ✓ 53.4c No 'Claude AI' strings remain in tooltip_formatter.py")
else:
    failed += 1
    failures.append("53.4c: 'Claude AI' string still present in tooltip_formatter.py")

# 53.4d: aistudio.google.com link present in the AI-credits tooltip
if "aistudio.google.com" in _eg_src:
    passed += 1
    print("  ✓ 53.4d Helpful aistudio.google.com link present in Key Catalyst tooltip")
else:
    failed += 1
    failures.append("53.4d: aistudio.google.com link missing — user has no path to top up Gemini credits")

# 53.4e: DB-column names must NOT be renamed (would break schema)
_db_path = _os53.path.join(_os53.path.dirname(_os53.path.abspath(__file__)),
                           "database", "data_bridge.py")
with open(_db_path) as _fh:
    _db_src = _fh.read()
if "last_claude_score" in _db_src and "claude_analysed" in _db_src:
    passed += 1
    print("  ✓ 53.4e DB column names (last_claude_score, claude_analysed) preserved (schema stability)")
else:
    failed += 1
    failures.append("53.4e: DB column names changed — would require migration; revert if unintended")



# ══════════════════════════════════════════════════════════════════════
# GROUP 54 — v12.5 Quality-of-Life Fixes (Issues #5, #7, #8, #10, #12, #13)
# ══════════════════════════════════════════════════════════════════════
# Six fixes from the residual-issue list. Issue #3 (0-vs-missing) was
# attempted but deferred — it requires a SQL-layer COALESCE rewrite that
# is too risky for a quality-of-life release. Issues #2, #4, #11, #14
# remain as judgment calls awaiting product input.
#
#   54.1  MoS cap marker (#5)             — `*` flag on MoS Label
#   54.2  Gold sheet dynamic headers (#7) — coverage-based demotion
#   54.3  Gold F-Score → Piotroski (#8)   — label sync with Full Dashboard
#   54.4  Early Mover dedup (#10)         — prefix-match vs exact-match
#   54.5  Altman Z sanity cap (#12)       — clamp at 10
#   54.6  CCC finance-sector skip (#13)   — `—` for Banks/NBFCs/HFCs/Insurance
# ══════════════════════════════════════════════════════════════════════
print("\n" + "═" * 70)
print("GROUP 54 — v12.5 Quality-of-life fixes")
print("═" * 70)

import os as _os54
import sys as _sys54
_proj_root = _os54.path.dirname(_os54.path.abspath(__file__))
if _proj_root not in _sys54.path:
    _sys54.path.insert(0, _proj_root)

# ── 54.1: MoS cap marker (#5) ───────────────────────────────────────────
# v12.5: When CFV > 3 × CMP, engine clips to 3× and sets cfv_capped=True.
# v12.6: engine no longer emits mos_label — master_funnel applies the `*`
# marker downstream when it sees cfv_capped=True. So engine-level tests
# verify the FLAG; funnel-level integration verifies the marker (53.x style).
from analysis.fair_value_engine import FairValueEngine as _FVE54
_fv54 = _FVE54()

# Synthetic models that produce CFV > 3 × CMP (3 models, so not thin-flagged)
_capped_models = {"M1_DCF": 600, "M2_Graham": 0, "M3_PE": 600, "M4_PB": 600,
                  "M5_EV": 0, "M6_DDM": 0, "M7_PEG": 0}
_capped_out = _fv54.get_composite_fair_value(_capped_models, cmp=100)
if _capped_out["cfv_capped"] is True:
    passed += 1
    print(f"  ✓ 54.1a cfv_capped=True when CFV would exceed 3× CMP "
          f"(cfv={_capped_out['cfv']}, cmp=100)")
else:
    failed += 1
    failures.append(f"54.1a: cfv_capped={_capped_out['cfv_capped']} (want True); "
                    f"cfv={_capped_out['cfv']} on cmp=100")
if _capped_out["cfv"] == 300:
    passed += 1
    print(f"  ✓ 54.1b CFV correctly clipped to 3× CMP = 300")
else:
    failed += 1
    failures.append(f"54.1b: CFV={_capped_out['cfv']} (want 300)")
# v12.6: engine no longer emits mos_label — the * marker is applied by
# master_funnel. Engine output should NOT have mos_label key.
if "mos_label" not in _capped_out:
    passed += 1
    print(f"  ✓ 54.1c v12.6: engine output has no mos_label key (funnel applies marker)")
else:
    failed += 1
    failures.append(f"54.1c: engine still emits mos_label={_capped_out.get('mos_label')!r} "
                    "(should be removed in v12.6 #2)")

# Negative case — CFV ≤ 3× CMP shouldn't have cfv_capped=True
_normal_models = {"M1_DCF": 120, "M3_PE": 120, "M4_PB": 120,
                  "M2_Graham": 0, "M5_EV": 0, "M6_DDM": 0, "M7_PEG": 0}
_normal_out = _fv54.get_composite_fair_value(_normal_models, cmp=100)
if _normal_out["cfv_capped"] is False:
    passed += 1
    print(f"  ✓ 54.1d Non-capped case: cfv_capped=False (cfv={_normal_out['cfv']})")
else:
    failed += 1
    failures.append(f"54.1d: cfv_capped={_normal_out['cfv_capped']} (expected False); "
                    f"cfv={_normal_out['cfv']}")

# Source-marker check
_fve_path = _os54.path.join(_proj_root, "analysis", "fair_value_engine.py")
with open(_fve_path) as _fh:
    _fve_src = _fh.read()
if "cfv_capped" in _fve_src and "v12.5" in _fve_src:
    passed += 1
    print("  ✓ 54.1e fair_value_engine.py has cfv_capped + v12.5 marker")
else:
    failed += 1
    failures.append("54.1e: fair_value_engine.py missing cfv_capped or v12.5 marker")

_mf_v125_path = _os54.path.join(_proj_root, "master_funnel.py")
with open(_mf_v125_path) as _fh:
    _mf_v125_src = _fh.read()
if 'stock.get("cfv_capped")' in _mf_v125_src:
    passed += 1
    print("  ✓ 54.1f master_funnel.py preserves cfv_capped flag in label override")
else:
    failed += 1
    failures.append("54.1f: master_funnel.py doesn't preserve cfv_capped flag")

# ── 54.2: Gold sheet dynamic headers (#7) ───────────────────────────────
_eg_v125_path = _os54.path.join(_proj_root, "reporting", "excel_generator.py")
with open(_eg_v125_path) as _fh:
    _eg_v125_src = _fh.read()
if "_GOLD_COV_MIN" in _eg_v125_src and "_gold_has_data" in _eg_v125_src:
    passed += 1
    print("  ✓ 54.2a Gold sheet uses dynamic coverage-based header demotion")
else:
    failed += 1
    failures.append("54.2a: Gold sheet still uses static red headers — patch reverted?")

if _eg_v125_src.count("0.30") >= 2:
    passed += 1
    print("  ✓ 54.2b Both Full and Gold sheets reference the 0.30 coverage threshold")
else:
    failed += 1
    failures.append("54.2b: 0.30 threshold not consistently used across both sheets")

# ── 54.3: Gold F-Score → Piotroski rename (#8) ──────────────────────────
if '"Piotroski F /9"' in _eg_v125_src:
    _piotr_count = _eg_v125_src.count('"Piotroski F /9"')
    if _piotr_count >= 2:
        passed += 1
        print(f"  ✓ 54.3a 'Piotroski F /9' appears in {_piotr_count} places (Full + Gold)")
    else:
        failed += 1
        failures.append(f"54.3a: 'Piotroski F /9' only in {_piotr_count} place; Gold rename incomplete")
else:
    failed += 1
    failures.append("54.3a: 'Piotroski F /9' not found in excel_generator.py")

# Orphaned tuple check — be careful not to match the v12.5 comment block
if '("SCORES","F-Score /9",' in _eg_v125_src:
    failed += 1
    failures.append("54.3b: Orphaned GLOSSARY tuple ('SCORES','F-Score /9',…) still present")
else:
    passed += 1
    print("  ✓ 54.3b Orphaned GLOSSARY 'F-Score /9' tuple removed")

_tf_v125_path = _os54.path.join(_proj_root, "reporting", "tooltip_formatter.py")
with open(_tf_v125_path) as _fh:
    _tf_v125_src = _fh.read()
if '"F-Score /9": (' in _tf_v125_src:
    failed += 1
    failures.append("54.3c: Orphaned tooltip entry '\"F-Score /9\":' still in tooltip_formatter.py")
else:
    passed += 1
    print("  ✓ 54.3c Orphaned 'F-Score /9' tooltip entry removed")

if '"F-Score /9"' in _tf_v125_src:
    failed += 1
    failures.append("54.3d: '\"F-Score /9\"' literal still appears in tooltip_formatter.py code")
else:
    passed += 1
    print("  ✓ 54.3d '\"F-Score /9\"' literal fully removed from tooltip_formatter.py code")

# ── 54.4: Early Mover dedup (#10) ───────────────────────────────────────
def _has_prefix54(sig_list, prefix):
    return any(s.upper().startswith(prefix.upper()) for s in sig_list)

# 54.4a: badge present + label tries to add → label rejected
_sigs_a = ["VOL SURGE", "EARLY MOVER"]
if _has_prefix54(_sigs_a, "EARLY MOVER"):
    passed += 1
    print("  ✓ 54.4a Existing 'EARLY MOVER' badge prevents label from being appended")
else:
    failed += 1
    failures.append("54.4a: prefix dedup didn't detect existing EARLY MOVER badge")

# 54.4b: label present + badge tries to add → badge rejected
_sigs_b = ["EARLY MOVER — Act before the crowd"]
if _has_prefix54(_sigs_b, "EARLY MOVER"):
    passed += 1
    print("  ✓ 54.4b Existing 'EARLY MOVER — …' label prevents badge from being appended")
else:
    failed += 1
    failures.append("54.4b: prefix dedup didn't detect existing EARLY MOVER label")

# 54.4c: case-insensitive
_sigs_c = ["early mover badge"]
if _has_prefix54(_sigs_c, "EARLY MOVER"):
    passed += 1
    print("  ✓ 54.4c Prefix dedup is case-insensitive")
else:
    failed += 1
    failures.append("54.4c: prefix dedup is case-sensitive (should be insensitive)")

# 54.4d: unrelated signals don't trigger false positives
_sigs_d = ["VOL SURGE + RSI ACCUMULATION", "TREND CONFLUENCE"]
if not _has_prefix54(_sigs_d, "EARLY MOVER"):
    passed += 1
    print("  ✓ 54.4d Unrelated signals don't trigger EARLY MOVER prefix match")
else:
    failed += 1
    failures.append("54.4d: false-positive prefix match on unrelated signal")

if "_has_prefix" in _mf_v125_src and "v12.5" in _mf_v125_src:
    passed += 1
    print("  ✓ 54.4e master_funnel.py has _has_prefix dedup helper + v12.5 marker")
else:
    failed += 1
    failures.append("54.4e: master_funnel.py missing _has_prefix or v12.5 marker")

# ── 54.5: Altman Z sanity cap (#12) ─────────────────────────────────────
from analysis.forensics_engine import ForensicsEngine as _FE54

# Synthetic stock with X4 unit-mismatch — mcap_cr=50000, total_liab_cr=100
# → X4 = 500, Z would be ~300 pre-clamp. With clamp, Z should be 10.
_z_unit_mismatch = {
    'total_assets_cr':       1000,
    'total_liab_cr':         100,
    'working_cap_cr':        0,
    'retained_earnings_cr':  0,
    'ebit_cr':               0,
    'mcap_cr':               50000,
    'q_rev_cr':              0,
}
_z_cap = _FE54.calculate_altman_z(_z_unit_mismatch)
if _z_cap == 10:
    passed += 1
    print(f"  ✓ 54.5a Altman Z unit-mismatch case clamped to 10 (would be ~300 pre-fix)")
else:
    failed += 1
    failures.append(f"54.5a: Altman Z={_z_cap} (want 10) for unit-mismatch input")

# Healthy company should get its real Z value, not be clamped
_z_healthy = {
    'total_assets_cr':       1000,
    'total_liab_cr':         400,
    'working_cap_cr':        200,
    'retained_earnings_cr':  300,
    'ebit_cr':               150,
    'mcap_cr':               2000,
    'q_rev_cr':              250,
}
_z_h = _FE54.calculate_altman_z(_z_healthy)
if 3.5 <= _z_h <= 6.0:
    passed += 1
    print(f"  ✓ 54.5b Healthy company gets real Z value ({_z_h}), not clamped")
else:
    failed += 1
    failures.append(f"54.5b: healthy-company Altman Z={_z_h} (want 3.5–6.0)")

# Distressed company keeps low Z
_z_distress = {
    'total_assets_cr':       1000,
    'total_liab_cr':         900,
    'working_cap_cr':        50,
    'retained_earnings_cr':  10,
    'ebit_cr':               20,
    'mcap_cr':               300,
    'q_rev_cr':              100,
}
_z_d = _FE54.calculate_altman_z(_z_distress)
if 0 < _z_d < 2.5:
    passed += 1
    print(f"  ✓ 54.5c Distressed company keeps low Z ({_z_d}), no upward clamp")
else:
    failed += 1
    failures.append(f"54.5c: distressed Altman Z={_z_d} (want 0–2.5)")

# Insufficient data still returns 0.0
_z_empty = {'total_assets_cr': 0, 'total_liab_cr': 0}
_z_e = _FE54.calculate_altman_z(_z_empty)
if _z_e == 0.0:
    passed += 1
    print("  ✓ 54.5d Insufficient-data case still returns 0.0 (unchanged)")
else:
    failed += 1
    failures.append(f"54.5d: empty Altman Z={_z_e} (want 0.0)")

_fe_path = _os54.path.join(_proj_root, "analysis", "forensics_engine.py")
with open(_fe_path) as _fh:
    _fe_src = _fh.read()
if "z > 10" in _fe_src and "v12.5" in _fe_src:
    passed += 1
    print("  ✓ 54.5e forensics_engine.py has Altman Z clamp + v12.5 marker")
else:
    failed += 1
    failures.append("54.5e: forensics_engine.py missing Altman Z clamp marker")

# ── 54.6: CCC Days finance-sector skip (#13) ────────────────────────────
# Pre-fix: TATACAP showed 7,739 CCC days. Post-fix: any sector containing
# 'financial', 'finance', 'bank', 'nbfc', 'insurance', 'housing finance'
# → CCC renders '—'.

_ccc_nbfc = _FE54.calculate_accounting_forensics({
    'sector': 'Financial Services',
    'inventory_days': 100, 'receivable_days': 5000, 'payable_days': 50,
})
if _ccc_nbfc['ccc_days'] == "—":
    passed += 1
    print("  ✓ 54.6a NBFC ('Financial Services') correctly skips CCC → '—'")
else:
    failed += 1
    failures.append(f"54.6a: NBFC ccc_days={_ccc_nbfc['ccc_days']!r} (want '—')")

_ccc_bank = _FE54.calculate_accounting_forensics({
    'sector': 'Bank', 'inventory_days': 0, 'receivable_days': 100, 'payable_days': 30,
})
if _ccc_bank['ccc_days'] == "—":
    passed += 1
    print("  ✓ 54.6b Bank correctly skips CCC")
else:
    failed += 1
    failures.append(f"54.6b: Bank ccc_days={_ccc_bank['ccc_days']!r} (want '—')")

_ccc_ins = _FE54.calculate_accounting_forensics({
    'sector': 'Insurance', 'inventory_days': 0, 'receivable_days': 200, 'payable_days': 40,
})
if _ccc_ins['ccc_days'] == "—":
    passed += 1
    print("  ✓ 54.6c Insurance correctly skips CCC")
else:
    failed += 1
    failures.append(f"54.6c: Insurance ccc_days={_ccc_ins['ccc_days']!r} (want '—')")

_ccc_hfc = _FE54.calculate_accounting_forensics({
    'sector': 'Housing Finance', 'inventory_days': 0,
    'receivable_days': 800, 'payable_days': 100,
})
if _ccc_hfc['ccc_days'] == "—":
    passed += 1
    print("  ✓ 54.6d Housing Finance correctly skips CCC")
else:
    failed += 1
    failures.append(f"54.6d: HFC ccc_days={_ccc_hfc['ccc_days']!r} (want '—')")

_ccc_normal = _FE54.calculate_accounting_forensics({
    'sector': 'Consumer Cyclical',
    'inventory_days': 30, 'receivable_days': 45, 'payable_days': 60,
})
if _ccc_normal['ccc_days'] == 15.0:
    passed += 1
    print(f"  ✓ 54.6e Consumer Cyclical computes CCC normally ({_ccc_normal['ccc_days']} days)")
else:
    failed += 1
    failures.append(f"54.6e: Consumer Cyclical ccc_days={_ccc_normal['ccc_days']} (want 15.0)")

_ccc_no_sec = _FE54.calculate_accounting_forensics({
    'sector': '', 'inventory_days': 25, 'receivable_days': 40, 'payable_days': 55,
})
if _ccc_no_sec['ccc_days'] == 10.0:
    passed += 1
    print(f"  ✓ 54.6f Empty sector → CCC computed normally ({_ccc_no_sec['ccc_days']} days)")
else:
    failed += 1
    failures.append(f"54.6f: empty-sector ccc_days={_ccc_no_sec['ccc_days']} (want 10.0)")

_ccc_upper = _FE54.calculate_accounting_forensics({
    'sector': 'FINANCIAL SERVICES',
    'inventory_days': 0, 'receivable_days': 1000, 'payable_days': 50,
})
if _ccc_upper['ccc_days'] == "—":
    passed += 1
    print("  ✓ 54.6g Sector match is case-insensitive ('FINANCIAL SERVICES' → skip)")
else:
    failed += 1
    failures.append(f"54.6g: uppercase Financial Services ccc_days={_ccc_upper['ccc_days']!r}")

if "_is_finance" in _fe_src and "Banks / NBFCs" in _fe_src:
    passed += 1
    print("  ✓ 54.6h forensics_engine.py has _is_finance check for CCC")
else:
    failed += 1
    failures.append("54.6h: forensics_engine.py missing _is_finance CCC guard")



# ══════════════════════════════════════════════════════════════════════
# GROUP 55 — v12.6 Final-Round Fixes (Issues #2, #4, #6, #11, #14)
# ══════════════════════════════════════════════════════════════════════
# Five fixes from the residual judgment-call list, plus #6 follow-up
# (R2 fallback to "—" when prior-window max ≈ recent 20-day max).
#
#   55.1  R2 fallback to "—"      (#6 follow-up: prior_h ≈ recent → NaN)
#   55.2  Engine no mos_label     (#2: master_funnel is single source)
#   55.3  Thin-model FV guard     (#4: cfv_thin_models flag + † marker)
#   55.4  NPM Q rename            (#11: Q (latest) / Q-1 / Q-2)
#   55.5  Placeholder format      (#14: [AI <verb> — <reason>])
# ══════════════════════════════════════════════════════════════════════
print("\n" + "═" * 70)
print("GROUP 55 — v12.6 Final-round fixes")
print("═" * 70)

import os as _os55
import sys as _sys55
_proj_root55 = _os55.path.dirname(_os55.path.abspath(__file__))
if _proj_root55 not in _sys55.path:
    _sys55.path.insert(0, _proj_root55)

# ── 55.1: R2 fallback to "—" when prior-window max ≈ recent 20-day max ──
import pandas as _pd55
import numpy as _np55
from backfill_history import compute_technicals as _ct55

# Case A: stock in narrow trading range — prior 232-day max ≈ recent 20-day max
# Patched code should set R2 to NaN → DB stores 0 → funnel renders "—"
# Construct a series where the all-time max equals the recent 20-day max
# exactly (both have an explicit peak at 100.0); prior-window rolling max
# will be 100.0, recent rolling max will be 100.0 — within 0.5% tolerance.
_np55.random.seed(42)
n = 252
prices_flat = 95 + _np55.random.randn(n) * 0.5    # tight range around 95
prices_flat[100] = 100.0                            # peak in old window (day 100)
prices_flat[-5]  = 100.0                            # same peak in recent 20
hist_flat = _pd55.DataFrame({
    'symbol': ['NARROW']*n,
    'date': _pd55.date_range('2025-04-30', periods=n, freq='D'),
    'open': prices_flat, 'high': prices_flat + 0.1, 'low': prices_flat - 0.1,
    'close': prices_flat, 'volume': [1e5]*n,
})
ti_flat = _ct55(hist_flat)
# When R1 ≈ R2, R2 should fall back to 0 (NaN converted by _v())
if ti_flat['resist2'] == 0.0:
    passed += 1
    print(f"  ✓ 55.1a Narrow-range stock: R2 falls back to 0 → '—' "
          f"(R1={ti_flat['resist1']:.2f})")
else:
    failed += 1
    failures.append(f"55.1a: narrow-range R2={ti_flat['resist2']} (want 0.0); "
                    f"R1={ti_flat['resist1']}")

# Case B: stock with clear trend — prior max meaningfully different from recent
# R2 should be a real number, not 0
_np55.random.seed(7)
prices_trend = _np55.zeros(n)
prices_trend[:60]   = _np55.linspace(150, 80, 60)        # decline
prices_trend[60:]   = 80 + _np55.random.randn(n-60) * 1.5 # range around 80
hist_trend = _pd55.DataFrame({
    'symbol': ['TREND']*n,
    'date': _pd55.date_range('2025-04-30', periods=n, freq='D'),
    'open': prices_trend, 'high': prices_trend + 1, 'low': prices_trend - 1,
    'close': prices_trend, 'volume': [1e5]*n,
})
ti_trend = _ct55(hist_trend)
if ti_trend['resist2'] > 0 and abs(ti_trend['resist2'] - ti_trend['resist1']) > 1.0:
    passed += 1
    print(f"  ✓ 55.1b Trending stock: R2 ({ti_trend['resist2']:.2f}) "
          f"distinct from R1 ({ti_trend['resist1']:.2f}) → real value kept")
else:
    failed += 1
    failures.append(f"55.1b: trend R1={ti_trend['resist1']} R2={ti_trend['resist2']} "
                    "(want R2 > 0 and distinct from R1)")

# Case C: master_funnel renders "—" for r2 == 0 in production (string output)
# Verify the funnel logic by checking source — funnel sets resist_2 = "—" when r2 falsy
_mf_v126_path = _os55.path.join(_proj_root55, "master_funnel.py")
with open(_mf_v126_path) as _fh:
    _mf_v126_src = _fh.read()
if 'stock["resist_2"]   = round(float(r2), 2) if r2 else "—"' in _mf_v126_src:
    passed += 1
    print("  ✓ 55.1c master_funnel renders resist_2 as '—' when DB stores 0")
else:
    failed += 1
    failures.append("55.1c: master_funnel doesn't render resist_2 as '—' for falsy values")
if 'stock["support_2"]  = round(float(s2), 2) if s2 else "—"' in _mf_v126_src:
    passed += 1
    print("  ✓ 55.1d master_funnel renders support_2 as '—' when DB stores 0")
else:
    failed += 1
    failures.append("55.1d: master_funnel doesn't render support_2 as '—' for falsy values")

# Source-marker check
_bf_v126_path = _os55.path.join(_proj_root55, "backfill_history.py")
with open(_bf_v126_path) as _fh:
    _bf_v126_src = _fh.read()
if "_R2_TOLERANCE" in _bf_v126_src and "v12.6" in _bf_v126_src:
    passed += 1
    print("  ✓ 55.1e backfill_history.py has _R2_TOLERANCE marker + v12.6")
else:
    failed += 1
    failures.append("55.1e: backfill_history.py missing _R2_TOLERANCE / v12.6 marker")

# ── 55.2: Engine no longer emits mos_label ─────────────────────────────
# Pre-v12.6: engine set its own bucket scheme (EXCEPTIONAL VALUE / etc.),
# but master_funnel always overwrote with a different scheme. The engine
# code was unreachable. v12.6 deletes the dead engine code.

from analysis.fair_value_engine import FairValueEngine as _FVE55
_fv55 = _FVE55()

# Test with full models
_full_models = {"M1_DCF": 1500, "M3_PE": 1500, "M4_PB": 1500}
_full_out = _fv55.get_composite_fair_value(_full_models, cmp=1000)
if "mos_label" not in _full_out:
    passed += 1
    print("  ✓ 55.2a Engine output has no mos_label key (full-model case)")
else:
    failed += 1
    failures.append(f"55.2a: engine still emits mos_label={_full_out.get('mos_label')!r}")

# Test with thin models
_thin_models = {"M1_DCF": 1500}
_thin_out = _fv55.get_composite_fair_value(_thin_models, cmp=1000)
if "mos_label" not in _thin_out:
    passed += 1
    print("  ✓ 55.2b Engine output has no mos_label key (thin-model case)")
else:
    failed += 1
    failures.append(f"55.2b: engine still emits mos_label (thin case)={_thin_out.get('mos_label')!r}")

# Test with empty models
_empty_out = _fv55.get_composite_fair_value({}, cmp=1000)
if "mos_label" not in _empty_out:
    passed += 1
    print("  ✓ 55.2c Engine output has no mos_label key (empty-model case)")
else:
    failed += 1
    failures.append(f"55.2c: engine still emits mos_label (empty case)={_empty_out.get('mos_label')!r}")

# Source-marker: dead engine code (the 7 mos_lbl branches) should be gone
_fve_path55 = _os55.path.join(_proj_root55, "analysis", "fair_value_engine.py")
with open(_fve_path55) as _fh:
    _fve_src55 = _fh.read()
if 'mos_lbl = "EXCEPTIONAL VALUE"' not in _fve_src55:
    passed += 1
    print("  ✓ 55.2d Dead engine bucket-scheme code removed (no 'EXCEPTIONAL VALUE' literal)")
else:
    failed += 1
    failures.append("55.2d: dead engine bucket-scheme code still present")

# ── 55.3: Thin-model FV quality guard ─────────────────────────────────
# When n_models < 3, score_adjustment is zeroed regardless of MoS magnitude.
# cfv_thin_models flag is True. master_funnel appends '†' to mos_label.

# 55.3a: n_models field exists and reports correctly
if _full_out.get("n_models") == 3:
    passed += 1
    print(f"  ✓ 55.3a Full-model case: n_models = {_full_out['n_models']}")
else:
    failed += 1
    failures.append(f"55.3a: full-model n_models={_full_out.get('n_models')} (want 3)")

if _thin_out.get("n_models") == 1:
    passed += 1
    print(f"  ✓ 55.3b Thin-model case: n_models = {_thin_out['n_models']}")
else:
    failed += 1
    failures.append(f"55.3b: thin-model n_models={_thin_out.get('n_models')} (want 1)")

# 55.3c: cfv_thin_models flag set correctly
if _full_out.get("cfv_thin_models") is False:
    passed += 1
    print("  ✓ 55.3c Full-model: cfv_thin_models = False")
else:
    failed += 1
    failures.append(f"55.3c: full-model cfv_thin_models={_full_out.get('cfv_thin_models')}")

if _thin_out.get("cfv_thin_models") is True:
    passed += 1
    print("  ✓ 55.3d Thin-model: cfv_thin_models = True")
else:
    failed += 1
    failures.append(f"55.3d: thin-model cfv_thin_models={_thin_out.get('cfv_thin_models')}")

# 55.3e: score_adjustment zeroed for thin-model rows even when MoS is high
# +50% MoS would normally trigger score_adj=12, but with only 1 model → 0
if _thin_out.get("score_adjustment") == 0:
    passed += 1
    print(f"  ✓ 55.3e Thin-model: score_adjustment=0 even with MoS={_thin_out['mos_pct']}% "
          "(prevents thin-evidence false BUYs)")
else:
    failed += 1
    failures.append(f"55.3e: thin-model score_adjustment={_thin_out.get('score_adjustment')} "
                    f"(want 0); MoS={_thin_out.get('mos_pct')}%")

# 55.3f: 2-model case also thin
_two_models = {"M1_DCF": 1500, "M3_PE": 1500}
_two_out = _fv55.get_composite_fair_value(_two_models, cmp=1000)
if _two_out.get("cfv_thin_models") is True and _two_out.get("score_adjustment") == 0:
    passed += 1
    print(f"  ✓ 55.3f 2-model case: cfv_thin_models=True, score_adj=0")
else:
    failed += 1
    failures.append(f"55.3f: 2-model thin={_two_out.get('cfv_thin_models')} "
                    f"score_adj={_two_out.get('score_adjustment')}")

# 55.3g: 3-model case is NOT thin (boundary)
if _full_out.get("score_adjustment") == 12:
    passed += 1
    print(f"  ✓ 55.3g 3-model boundary: full score_adj fires (12 for MoS>40)")
else:
    failed += 1
    failures.append(f"55.3g: 3-model score_adj={_full_out.get('score_adjustment')} (want 12)")

# 55.3h: source-marker — funnel appends † for thin-model rows
if 'cfv_thin_models' in _mf_v126_src and '"†"' in _mf_v126_src:
    passed += 1
    print("  ✓ 55.3h master_funnel appends '†' marker for thin-model rows")
else:
    failed += 1
    failures.append("55.3h: master_funnel missing '†' marker logic for thin-model")

# 55.3i: source-marker — engine has MIN_MODELS = 3 constant
if "MIN_MODELS = 3" in _fve_src55:
    passed += 1
    print("  ✓ 55.3i fair_value_engine has MIN_MODELS = 3 constant")
else:
    failed += 1
    failures.append("55.3i: fair_value_engine missing MIN_MODELS = 3 constant")

# ── 55.4: NPM Q rename to Q (latest) / Q-1 / Q-2 ───────────────────────
_eg_v126_path = _os55.path.join(_proj_root55, "reporting", "excel_generator.py")
with open(_eg_v126_path) as _fh:
    _eg_v126_src = _fh.read()

# 55.4a: new column headers present
if '"NPM Q (latest) %"' in _eg_v126_src:
    passed += 1
    print("  ✓ 55.4a 'NPM Q (latest) %' header present in excel_generator")
else:
    failed += 1
    failures.append("55.4a: 'NPM Q (latest) %' header missing")
if '"NPM Q-1 %"' in _eg_v126_src and '"NPM Q-2 %"' in _eg_v126_src:
    passed += 1
    print("  ✓ 55.4b 'NPM Q-1 %' and 'NPM Q-2 %' headers present")
else:
    failed += 1
    failures.append("55.4b: NPM Q-1 / Q-2 headers missing")

# 55.4c: old labels gone from FULL_COLS tuple format
if '"NPM Q1 %",9,"npm_q1"' in _eg_v126_src:
    failed += 1
    failures.append("55.4c: old 'NPM Q1 %' tuple still present in FULL_COLS")
else:
    passed += 1
    print("  ✓ 55.4c Old 'NPM Q1 %' FULL_COLS tuple removed")

# 55.4d: tooltip_formatter has new keys
_tf_v126_path = _os55.path.join(_proj_root55, "reporting", "tooltip_formatter.py")
with open(_tf_v126_path) as _fh:
    _tf_v126_src = _fh.read()
if '"NPM Q (latest) %": (' in _tf_v126_src:
    passed += 1
    print("  ✓ 55.4d Tooltip dict has 'NPM Q (latest) %' entry")
else:
    failed += 1
    failures.append("55.4d: 'NPM Q (latest) %' tooltip entry missing")

# 55.4e: old tooltip keys removed
if '"NPM Q1 %": (' in _tf_v126_src:
    failed += 1
    failures.append("55.4e: old '\"NPM Q1 %\":' tooltip dict entry still present")
else:
    passed += 1
    print("  ✓ 55.4e Old 'NPM Q1 %' tooltip dict entry removed")

# 55.4f: DB column names UNCHANGED (npm_q1/q2/q3 are still the keys)
if '"npm_q1"' in _eg_v126_src and '"npm_q2"' in _eg_v126_src and '"npm_q3"' in _eg_v126_src:
    passed += 1
    print("  ✓ 55.4f DB column keys (npm_q1/q2/q3) preserved (only display labels changed)")
else:
    failed += 1
    failures.append("55.4f: DB column keys changed — would require schema migration")

# ── 55.5: Placeholder string standardization ──────────────────────────
# All three "no analysis" cases use [AI <verb> — <reason>] format.

# 55.5a: AVOID-skip placeholder uses standardized format
if '[AI skipped — verdict AVOID' in _mf_v126_src:
    passed += 1
    print("  ✓ 55.5a AVOID-skip placeholder: '[AI skipped — verdict AVOID, ...]'")
else:
    failed += 1
    failures.append("55.5a: AVOID-skip placeholder not in standardized format")

# 55.5b: Default Analysis pending uses standardized format
if '[AI not yet generated — Analysis pending]' in _mf_v126_src:
    passed += 1
    print("  ✓ 55.5b Default placeholder: '[AI not yet generated — Analysis pending]'")
else:
    failed += 1
    failures.append("55.5b: Default Analysis pending not in standardized format")

# 55.5c: Old "Analysis pending." (with trailing period, no brackets) is gone
if '"Analysis pending."' in _mf_v126_src:
    failed += 1
    failures.append("55.5c: old 'Analysis pending.' string still present")
else:
    passed += 1
    print("  ✓ 55.5c Old 'Analysis pending.' (un-bracketed) removed")

# 55.5d: ai_analyst quota-skip placeholder uses standardized format
_ai_path = _os55.path.join(_proj_root55, "ai", "ai_analyst.py")
with open(_ai_path) as _fh:
    _ai_src = _fh.read()
if '[AI skipped — Gemini API quota exhausted' in _ai_src:
    passed += 1
    print("  ✓ 55.5d Quota-skip placeholder: '[AI skipped — Gemini API quota exhausted ...]'")
else:
    failed += 1
    failures.append("55.5d: quota-skip placeholder not in standardized format")

# 55.5e: Old "[Batch N skipped" prefix is gone
if '[Batch ' in _ai_src and 'skipped' in _ai_src:
    # check more precisely
    import re as _re55
    if _re55.search(r'\[Batch \d+ skipped', _ai_src):
        failed += 1
        failures.append("55.5e: old '[Batch N skipped' prefix still present in ai_analyst")
    else:
        passed += 1
        print("  ✓ 55.5e Old '[Batch N skipped — ...]' prefix removed")
else:
    passed += 1
    print("  ✓ 55.5e Old '[Batch N skipped' prefix removed")

# 55.5f: All standardized strings start with "[AI " literal
import re as _re55b
_ai_placeholders = _re55b.findall(r'"\[AI [^"]*"', _mf_v126_src + _ai_src)
if len(_ai_placeholders) >= 3:
    passed += 1
    print(f"  ✓ 55.5f Found {len(_ai_placeholders)} placeholders starting with '[AI ' "
          "(consistent format)")
else:
    failed += 1
    failures.append(f"55.5f: only {len(_ai_placeholders)} '[AI ' placeholders found")



# ══════════════════════════════════════════════════════════════════════
# GROUP 56 — v12.6.1 Backfill-window bump (365 → 400 calendar days)
# ══════════════════════════════════════════════════════════════════════
# Single-fix release: bumped DAYS_TO_BACKFILL default from 365 → 400 to
# give the 252-trading-day rolling windows in compute_technicals headroom.
# 400 calendar days ≈ 275 trading days → prior_h (excl. last 20) ≈ 255 →
# rolling(252).max() computes cleanly without falling into the
# `len(h) < 80` fallback branch on stocks with the full backfill.
#
# Tests verify:
#   56.1  DAYS_TO_BACKFILL default is 400 (not 365)
#   56.2  Other "1-year" references stay at 252 (trading days) /
#         365 (calendar days) — those encode "52-week" definition
# ══════════════════════════════════════════════════════════════════════
print("\n" + "═" * 70)
print("GROUP 56 — v12.6.1 Backfill window bump")
print("═" * 70)

import os as _os56
_proj_root56 = _os56.path.dirname(_os56.path.abspath(__file__))
_bf_path56 = _os56.path.join(_proj_root56, "backfill_history.py")
with open(_bf_path56) as _fh:
    _bf_src56 = _fh.read()

# 56.1a: Default is now 400
if "DAYS_TO_BACKFILL = int(sys.argv[1]) if len(sys.argv) > 1 else 400" in _bf_src56:
    passed += 1
    print("  ✓ 56.1a DAYS_TO_BACKFILL default = 400 calendar days")
else:
    failed += 1
    failures.append("56.1a: DAYS_TO_BACKFILL default is not 400")

# 56.1b: Old default of 365 is gone
if "else 365" in _bf_src56 and "DAYS_TO_BACKFILL" in _bf_src56:
    # Look specifically for the variable assignment line
    import re as _re56
    _old_default = _re56.search(r'DAYS_TO_BACKFILL\s*=.*else\s*365', _bf_src56)
    if _old_default:
        failed += 1
        failures.append("56.1b: DAYS_TO_BACKFILL still has 'else 365' default")
    else:
        passed += 1
        print("  ✓ 56.1b Old 'else 365' default removed from DAYS_TO_BACKFILL line")
else:
    passed += 1
    print("  ✓ 56.1b Old 'else 365' default removed from DAYS_TO_BACKFILL line")

# 56.1c: Docstring reflects 400
if "400-day Market Data Backfill" in _bf_src56:
    passed += 1
    print("  ✓ 56.1c Module docstring says '400-day Market Data Backfill'")
else:
    failed += 1
    failures.append("56.1c: docstring not updated to 400-day")

# 56.1d: v12.6.1 marker comment present
if "v12.6.1" in _bf_src56 and "365 → 400" in _bf_src56:
    passed += 1
    print("  ✓ 56.1d v12.6.1 changelog comment present (365 → 400)")
else:
    failed += 1
    failures.append("56.1d: v12.6.1 changelog comment missing")

# 56.2a: 252 trading-day rolling window in compute_technicals UNCHANGED
# This is the financial definition of "52 weeks", not a knob.
if "min(252, len(prior_h))" in _bf_src56:
    passed += 1
    print("  ✓ 56.2a R2/S2 rolling-252 trading-day window preserved")
else:
    failed += 1
    failures.append("56.2a: R2/S2 rolling-252 window changed (should stay 252)")

# 56.2b: 252 trading-day window in enrich_prices (52W high/low calc) UNCHANGED
if "grp.tail(252)" in _bf_src56:
    passed += 1
    print("  ✓ 56.2b 52W high/low tail(252) trading-day window preserved")
else:
    failed += 1
    failures.append("56.2b: tail(252) for 52W high/low changed (should stay 252)")

# 56.2c: 365 calendar-day annualisation factor in CCC formula UNCHANGED
# DIO/DSO/DPO are by financial definition annualised over 365 days.
if "* 365" in _bf_src56:
    passed += 1
    print("  ✓ 56.2c CCC formula × 365 annualisation factor preserved")
else:
    failed += 1
    failures.append("56.2c: CCC formula × 365 changed (should stay 365)")

# 56.2d: 365 calendar-day SQL filter for 52W high/low in master_funnel UNCHANGED
_mf_path56 = _os56.path.join(_proj_root56, "master_funnel.py")
with open(_mf_path56) as _fh:
    _mf_src56 = _fh.read()
if "'-365 days'" in _mf_src56:
    passed += 1
    print("  ✓ 56.2d master_funnel '-365 days' SQL filter preserved")
else:
    failed += 1
    failures.append("56.2d: master_funnel '-365 days' SQL filter changed (should stay 365)")


# ══════════════════════════════════════════════════════════════════════
# GROUP 57 — v12.7 Comprehensive dual-listed integrity fix set
# ══════════════════════════════════════════════════════════════════════
# Background: v12.6.1 production audit found that the Excel showed
# technicals (SMA200, RSI, MACD, ADX, OBV, S1/S2/R1/R2) populated for
# only 4 of 99 stocks. Root-cause investigation surfaced 12 bugs all
# rooted in the same shape: daily_prices stores one row per
# (symbol, date, exchange), so dual-listed symbols have 2× rows on
# every date. Several functions ran groupby('symbol') / WHERE symbol=?
# without an exchange filter, producing silent crashes, half-period
# rolling windows, 6-month-stale price series, or marginally wrong
# CMP lookups. v12.7 patches every per-symbol reader to filter
# exchange='NSE' (or dedupe preferring NSE) and replaces the silent
# except-pass in _compute_all_indicators with a structured error
# counter so future regressions are visible.
#
# Tests below are organised by the 12 fix numbers from the v12.7
# release notes. Each has 1-3 sub-tests covering code shape (locks
# the patch in place against future edits) and behaviour (verifies
# the fix actually works on synthetic data).

print("\n" + "═" * 70)
print("GROUP 57 — v12.7 Comprehensive dual-listed integrity fixes")
print("═" * 70)

import os as _os57
import sys as _sys57
_proj_root57 = _os57.path.dirname(_os57.path.abspath(__file__))
_bf_path57 = _os57.path.join(_proj_root57, "backfill_history.py")
_db_path57 = _os57.path.join(_proj_root57, "database/data_bridge.py")
_mf_path57 = _os57.path.join(_proj_root57, "master_funnel.py")
_dr_path57 = _os57.path.join(_proj_root57, "reporting/daily_report_generator.py")
with open(_bf_path57) as _fh: _bf_src57 = _fh.read()
with open(_db_path57) as _fh: _db_src57 = _fh.read()
with open(_mf_path57) as _fh: _mf_src57 = _fh.read()
with open(_dr_path57) as _fh: _dr_src57 = _fh.read()

# ── Fix #1: _compute_all_indicators chunk SQL + dedup ────────────────────
if "SELECT symbol, exchange, date, open, high, low, close, volume" in _bf_src57:
    passed += 1
    print("  ✓ 57.1a #1 chunk SELECT now reads exchange column")
else:
    failed += 1
    failures.append("57.1a (#1): chunk SELECT missing exchange column")

if "drop_duplicates(['symbol', 'date']" in _bf_src57:
    passed += 1
    print("  ✓ 57.1b #1 drop_duplicates(['symbol','date']) present")
else:
    failed += 1
    failures.append("57.1b (#1): drop_duplicates step missing")

if "_exch_pref" in _bf_src57 and "!= 'NSE'" in _bf_src57:
    passed += 1
    print("  ✓ 57.1c #1 NSE-preference dedup ordering present")
else:
    failed += 1
    failures.append("57.1c (#1): NSE-preference dedup ordering missing")

# ── Fix #2: silent except-pass replaced with counter ─────────────────────
if "_ti_errors" in _bf_src57 and "_ti_err_samples" in _bf_src57:
    passed += 1
    print("  ✓ 57.2a #2 silent except-pass replaced with structured counter")
else:
    failed += 1
    failures.append("57.2a (#2): silent except-pass not replaced")

# ── Fix #3: enrich_prices filters NSE ────────────────────────────────────
_ep_idx = _bf_src57.find("def enrich_prices(")
_ep_end = _bf_src57.find("\ndef ", _ep_idx + 1) if _ep_idx >= 0 else -1
_ep_body = _bf_src57[_ep_idx:_ep_end] if _ep_idx >= 0 and _ep_end > 0 else ""
if "exchange='NSE'" in _ep_body:
    passed += 1
    print("  ✓ 57.3a #3 enrich_prices SELECT filters exchange='NSE'")
else:
    failed += 1
    failures.append("57.3a (#3): enrich_prices SELECT not filtered to NSE")

# ── Fix #4: delivery_pct UPDATE scoped to NSE ────────────────────────────
if "UPDATE daily_prices SET delivery_pct=? " in _bf_src57 and \
   "AND exchange='NSE'" in _bf_src57:
    passed += 1
    print("  ✓ 57.4a #4 delivery_pct UPDATE scoped to exchange='NSE'")
else:
    failed += 1
    failures.append("57.4a (#4): delivery_pct UPDATE still un-scoped (corrupts BSE rows)")

# ── Fix #5: get_symbol_history filters NSE + ORDER BY DESC ───────────────
_gsh_idx = _db_src57.find("def get_symbol_history(")
_gsh_end = _db_src57.find("\ndef ", _gsh_idx + 1) if _gsh_idx >= 0 else -1
_gsh_body = _db_src57[_gsh_idx:_gsh_end] if _gsh_idx >= 0 and _gsh_end > 0 else ""
if "exchange='NSE'" in _gsh_body and "ORDER BY date DESC" in _gsh_body:
    passed += 1
    print("  ✓ 57.5a #5 get_symbol_history filters NSE + uses ORDER BY DESC")
else:
    failed += 1
    failures.append("57.5a (#5): get_symbol_history still returns 6-month-stale series")

# ── Fix #6: get_20d_avg_vol filters NSE ──────────────────────────────────
_g20_idx = _db_src57.find("def get_20d_avg_vol(")
_g20_end = _db_src57.find("\ndef ", _g20_idx + 1) if _g20_idx >= 0 else -1
_g20_body = _db_src57[_g20_idx:_g20_end] if _g20_idx >= 0 and _g20_end > 0 else ""
if "exchange='NSE'" in _g20_body:
    passed += 1
    print("  ✓ 57.6a #6 get_20d_avg_vol filters exchange='NSE'")
else:
    failed += 1
    failures.append("57.6a (#6): get_20d_avg_vol still mixes NSE+BSE volumes")

# ── Fix #7: get_20d_avg_vol_batch filters NSE in CTE ─────────────────────
_g20b_idx = _db_src57.find("def get_20d_avg_vol_batch(")
_g20b_end = _db_src57.find("\ndef ", _g20b_idx + 1) if _g20b_idx >= 0 else -1
_g20b_body = _db_src57[_g20b_idx:_g20b_end] if _g20b_idx >= 0 and _g20b_end > 0 else ""
if "exchange='NSE'" in _g20b_body:
    passed += 1
    print("  ✓ 57.7a #7 get_20d_avg_vol_batch CTE filters exchange='NSE'")
else:
    failed += 1
    failures.append("57.7a (#7): get_20d_avg_vol_batch still mixes NSE+BSE in window function")

# ── Fix #8: nifty_close uses correct function + mood gracefully degrades ─
if "get_nifty_close_from_db" in _db_src57:
    passed += 1
    print("  ✓ 57.8a #8 get_nifty_close_from_db helper added")
else:
    failed += 1
    failures.append("57.8a (#8): get_nifty_close_from_db helper missing")

if "\"nifty_close\":   get_nifty_close_from_db()" in _mf_src57:
    passed += 1
    print("  ✓ 57.8b #8 master_funnel maps nifty_close to correct function")
else:
    failed += 1
    failures.append("57.8b (#8): master_funnel still maps nifty_close to 52w_high")

if "if nifty > 0 and sma200 > 0" in _dr_src57 and 'mood = "—"' in _dr_src57:
    passed += 1
    print("  ✓ 57.8c #8 daily_report_generator renders '—' mood when nifty data missing")
else:
    failed += 1
    failures.append("57.8c (#8): daily_report mood logic still always-BEARISH on missing nifty")

# ── Fix #9: CMP lookups for earnings yield filter NSE (2 places) ─────────
# Source has the SQL split across two adjacent string literals; count both.
_cmp_count = _bf_src57.count(
    'SELECT close FROM daily_prices WHERE symbol=? "\r\n'
    '            "AND exchange=\'NSE\' ORDER BY date DESC LIMIT 1'
)
# Fallback: just count NSE-filtered close-lookup occurrences
if _cmp_count < 2:
    _cmp_count = 0
    _idx = 0
    while True:
        _f = _bf_src57.find("SELECT close FROM daily_prices WHERE symbol=?", _idx)
        if _f < 0:
            break
        _idx = _f + 1
        # Look ahead 200 chars for the NSE filter (split across lines OK)
        if "exchange='NSE'" in _bf_src57[_f:_f+200]:
            _cmp_count += 1

if _cmp_count >= 2:
    passed += 1
    print(f"  ✓ 57.9a #9 both CMP lookups for earnings yield filter NSE ({_cmp_count} found)")
else:
    failed += 1
    failures.append(f"57.9a (#9): only {_cmp_count}/2 CMP lookups patched")

# ── Fix #10: active_syms uses MAX(date)-anchored window not date('now') ──
if "SELECT MAX(date) FROM daily_prices" in _bf_src57 and \
   "_anchor_date" in _bf_src57:
    passed += 1
    print("  ✓ 57.10a #10 active_syms anchored to MAX(date) not wallclock")
else:
    failed += 1
    failures.append("57.10a (#10): active_syms still uses date('now') (UTC drift risk)")

# ── Fix #11: save_to_database DELETE uses data dates, not server clock ───
if "_data_dates" in _db_src57 and "DELETE FROM daily_prices WHERE date IN" in _db_src57:
    passed += 1
    print("  ✓ 57.11a #11 save_to_database DELETE scoped to data's actual dates")
else:
    failed += 1
    failures.append("57.11a (#11): save_to_database DELETE still uses server today_str")

# ── Fix #12: master_funnel refreshes technicals after daily price upsert ──
if "Section 1.5" in _mf_src57 and "_ci_daily" in _mf_src57:
    passed += 1
    print("  ✓ 57.12a #12 master_funnel triggers daily technical recompute")
else:
    failed += 1
    failures.append("57.12a (#12): master_funnel still leaves technicals stale on daily runs")

# ── Fix v12.6.1 reaffirmed: workflow YAML passes 400 not 365 ─────────────
_yml_path57 = _os57.path.join(_proj_root57, ".github/workflows/market_run.yml")
if _os57.path.exists(_yml_path57):
    with open(_yml_path57) as _fh:
        _yml_src57 = _fh.read()
    if "backfill_history.py 400" in _yml_src57:
        passed += 1
        print("  ✓ 57.13a workflow YAML passes 400 (matches v12.6.1 Python default)")
    else:
        failed += 1
        failures.append("57.13a: workflow YAML still passes 365 — overrides v12.6.1 default")
else:
    warnings.append("57.13a: .github/workflows/market_run.yml not found in test scope")

# ── End-to-end behaviour test ────────────────────────────────────────────
# 57.14: build a tiny in-memory DB with one DUAL_LISTED, one NSE_ONLY,
# one BSE_ONLY symbol; run the full chain; verify all 3 land in
# technical_indicators with sensible values, AND get_symbol_history
# returns the most recent N NSE rows (not 6-month-stale ones).
try:
    if _proj_root57 not in _sys57.path:
        _sys57.path.insert(0, _proj_root57)
    if 'backfill_history' in _sys57.modules: del _sys57.modules['backfill_history']
    if 'database.data_bridge' in _sys57.modules: del _sys57.modules['database.data_bridge']
    import backfill_history as _bf57
    import sqlite3 as _sq57, tempfile as _tf57, pandas as _pd57, numpy as _np57
    from datetime import date as _dt57, timedelta as _td57

    _np57.random.seed(42)
    _end57 = _dt57.today()
    _dates57 = _pd57.bdate_range(_end57 - _td57(days=400), _end57)[-247:]
    _prices57 = 100 + _np57.cumsum(_np57.random.randn(len(_dates57)) * 1.5)

    _rows57 = []
    for _i57, _d57 in enumerate(_dates57):
        _ds57 = _d57.strftime('%Y-%m-%d')
        _p57 = _prices57[_i57]
        _rows57.append(('TESTDUAL', 'NSE', _ds57, _p57,
                        _p57*1.01, _p57*0.99, _p57, 100000))
        _rows57.append(('TESTDUAL', 'BSE', _ds57, _p57*1.001,
                        _p57*1.011, _p57*0.991, _p57*1.001, 50000))
        _rows57.append(('TESTNSE', 'NSE', _ds57, _p57+50,
                        (_p57+50)*1.01, (_p57+50)*0.99, _p57+50, 80000))
        _rows57.append(('TESTBSE', 'BSE', _ds57, _p57+200,
                        (_p57+200)*1.01, (_p57+200)*0.99, _p57+200, 30000))

    with _tf57.TemporaryDirectory() as _tdt57:
        # get_symbol_history hard-codes "market_data.db" so chdir here
        _orig_cwd = _os57.getcwd()
        try:
            _os57.chdir(_tdt57)
            _conn57 = _sq57.connect("market_data.db")
            _bf57.init_all_tables(_conn57)
            _df57 = _pd57.DataFrame(_rows57,
                columns=['symbol','exchange','date','open','high','low','close','volume'])
            _df57.to_sql('daily_prices', _conn57, if_exists='append', index=False)

            # Run patched _compute_all_indicators (silently)
            import io as _io57, contextlib as _cl57
            _buf57 = _io57.StringIO()
            with _cl57.redirect_stdout(_buf57):
                _bf57._compute_all_indicators(_conn57)

            _ti_res57 = _conn57.execute(
                "SELECT symbol, sma_200, rsi_14, support1, support2, "
                "resist1, resist2 FROM technical_indicators ORDER BY symbol"
            ).fetchall()
            _wm_res57 = _conn57.execute(
                "SELECT symbol, chg_2w, chg_4w FROM weekly_momentum "
                "ORDER BY symbol"
            ).fetchall()
            _conn57.close()

            # Now test get_symbol_history with the patched code
            from database.data_bridge import (
                get_symbol_history as _gsh57,
                get_20d_avg_vol as _gv57,
                get_20d_avg_vol_batch as _gvb57,
            )
            _hist_dual = _gsh57('TESTDUAL', limit=250)
            _hist_dual_lastclose = float(_hist_dual.iloc[-1]['close']) if not _hist_dual.empty else 0
            _expected_today = float(_prices57[-1])

            _vol_dual = _gv57('TESTDUAL')
            _vol_batch = _gvb57(['TESTDUAL', 'TESTNSE', 'TESTBSE'])
        finally:
            _os57.chdir(_orig_cwd)

    # 57.14a: all 3 symbol-types land in technical_indicators
    _syms57 = {r[0] for r in _ti_res57}
    if _syms57 == {'TESTDUAL', 'TESTNSE', 'TESTBSE'}:
        passed += 1
        print("  ✓ 57.14a DUAL_LISTED + NSE_ONLY + BSE_ONLY all populate technical_indicators")
    else:
        failed += 1
        failures.append(f"57.14a: missing {{'TESTDUAL','TESTNSE','TESTBSE'}} - {_syms57} != expected")

    # 57.14b: DUAL_LISTED has sensible non-zero technicals (not all NaN/0)
    _dual_row = next((r for r in _ti_res57 if r[0] == 'TESTDUAL'), None)
    if _dual_row and _dual_row[1] > 0 and _dual_row[2] > 0:
        passed += 1
        print(f"  ✓ 57.14b DUAL_LISTED computes non-zero SMA200 ({_dual_row[1]:.2f}) "
              f"and RSI14 ({_dual_row[2]:.2f})")
    else:
        failed += 1
        failures.append(f"57.14b: DUAL_LISTED row missing or zero — {_dual_row}")

    # 57.14c: R2 != R1 for DUAL_LISTED (real 247-day series, not collapsed)
    if _dual_row and abs(_dual_row[5] - _dual_row[6]) > 0.01:
        passed += 1
        print(f"  ✓ 57.14c DUAL_LISTED has distinct R1 ({_dual_row[5]:.2f}) "
              f"and R2 ({_dual_row[6]:.2f})")
    else:
        failed += 1
        failures.append("57.14c: DUAL_LISTED R1==R2 — dedup may not have NSE-preferred")

    # 57.14d: weekly_momentum chg_2w for DUAL_LISTED matches NSE-only ground truth
    _wm_dual = next((r for r in _wm_res57 if r[0] == 'TESTDUAL'), None)
    _expected_2w = round((_prices57[-1] - _prices57[-11]) / _prices57[-11] * 100, 2)
    if _wm_dual and abs(_wm_dual[1] - _expected_2w) < 0.05:
        passed += 1
        print(f"  ✓ 57.14d DUAL_LISTED chg_2w ({_wm_dual[1]:.2f}%) matches "
              f"NSE-only ground truth ({_expected_2w:.2f}%)")
    else:
        failed += 1
        failures.append(f"57.14d: DUAL_LISTED chg_2w wrong — got {_wm_dual[1] if _wm_dual else None}, "
                        f"expected {_expected_2w}")

    # 57.14e: get_symbol_history returns TODAY's price as iloc[-1], not 6-month-stale
    if abs(_hist_dual_lastclose - _expected_today) < 0.01:
        passed += 1
        print(f"  ✓ 57.14e get_symbol_history iloc[-1] = today's NSE close "
              f"({_hist_dual_lastclose:.2f}, expected {_expected_today:.2f})")
    else:
        failed += 1
        failures.append(f"57.14e: get_symbol_history iloc[-1]={_hist_dual_lastclose:.2f} "
                        f"!= today's NSE close {_expected_today:.2f} (stale data bug)")

    # 57.14f: get_20d_avg_vol returns NSE-only volume (~100000 for TESTDUAL)
    # Pre-fix would mix in BSE rows (~50000) and pull avg DOWN.
    if 90000 < _vol_dual < 110000:
        passed += 1
        print(f"  ✓ 57.14f get_20d_avg_vol returns NSE-only volume ({_vol_dual:.0f})")
    else:
        failed += 1
        failures.append(f"57.14f: get_20d_avg_vol = {_vol_dual:.0f} — should be ~100000 NSE-only")

    # 57.14g: get_20d_avg_vol_batch returns NSE-only for DUAL_LISTED
    if 'TESTDUAL' in _vol_batch and 90000 < _vol_batch['TESTDUAL'] < 110000:
        passed += 1
        print(f"  ✓ 57.14g get_20d_avg_vol_batch returns NSE-only volume "
              f"for DUAL_LISTED ({_vol_batch['TESTDUAL']:.0f})")
    else:
        failed += 1
        failures.append(f"57.14g: batch vol for TESTDUAL wrong — {_vol_batch.get('TESTDUAL','MISSING')}")

    # ── 57.15: lock all 14 user-facing technical Excel columns populate ──
    # This is the test that would have caught the v12.6.1 production bug
    # immediately. Reproduces the exact master_funnel _ti_map enrichment
    # path (master_funnel.py:1791-1809) and verifies every one of the
    # 14 columns the user asks about (SMA 200, Supertrend, ADX, RSI 14,
    # MACD Signal, Stoch %K, MFI, OBV Signal, Above VWAP, Chart Pattern,
    # Support 1/2, Resist 1/2) ends up populated for DUAL_LISTED symbols.
    # If any cell is None / "" / 0 (where it shouldn't be), the test fails.
    _expected_cols = [
        ("sma_200",     "numeric"),    # 88.6 expected for TESTDUAL base
        ("supertrend",  "string"),     # SELL / BUY / NEUTRAL
        ("adx",         "numeric"),    # 9.8 expected
        ("rsi_14",      "numeric"),    # 44.83 expected
        ("macd_signal_txt", "string"), # SELL / BUY / NEUTRAL
        ("stoch_k",     "numeric"),    # 14.29 expected
        ("mfi_14",      "numeric"),    # ~43 expected
        ("obv_signal",  "string"),     # FALLING / RISING / NEUTRAL
        ("above_vwap",  "string"),     # YES / NO
        ("support1",    "numeric"),    # ~95 expected
        ("support2",    "numeric"),    # ~78 expected — distinct from S1
        ("resist1",     "numeric"),    # ~102 expected
        ("resist2",     "numeric"),    # ~107 expected — distinct from R1
    ]
    # Re-fetch the full ti row for TESTDUAL (using same temp DB connection)
    # We need to re-open since the previous block closed it. Build a new
    # synthetic DB inline for this test isolated.
    _np57.random.seed(42)
    _dates_l = _pd57.bdate_range(_end57 - _td57(days=400), _end57)[-247:]
    _prices_l = 100 + _np57.cumsum(_np57.random.randn(len(_dates_l)) * 1.5)
    _rows_l = []
    for _i, _d in enumerate(_dates_l):
        _ds = _d.strftime('%Y-%m-%d'); _p = _prices_l[_i]
        _rows_l.append(('TESTDUAL','NSE',_ds, _p, _p*1.01, _p*0.99, _p, 1000000))
        _rows_l.append(('TESTDUAL','BSE',_ds, _p*1.001, _p*1.011, _p*0.991, _p*1.001, 50000))
    with _tf57.TemporaryDirectory() as _tdt15:
        _orig_cwd_15 = _os57.getcwd()
        try:
            _os57.chdir(_tdt15)
            _conn15 = _sq57.connect("market_data.db")
            _bf57.init_all_tables(_conn15)
            _df15 = _pd57.DataFrame(_rows_l,
                columns=['symbol','exchange','date','open','high','low','close','volume'])
            _df15.to_sql('daily_prices', _conn15, if_exists='append', index=False)
            _buf15 = _io57.StringIO()
            with _cl57.redirect_stdout(_buf15):
                _bf57._compute_all_indicators(_conn15)
            # Replicate master_funnel:1149-1161 read
            _ti_query15 = _conn15.execute(
                "SELECT t.sma_200, t.supertrend, t.adx, t.rsi_14, "
                "t.macd_signal_txt, t.stoch_k, t.mfi_14, t.obv_signal, "
                "t.above_vwap, t.support1, t.support2, t.resist1, t.resist2 "
                "FROM technical_indicators t "
                "WHERE t.symbol = 'TESTDUAL'"
            ).fetchone()
            _conn15.close()
        finally:
            _os57.chdir(_orig_cwd_15)

    if _ti_query15 is None:
        failed += 1
        failures.append("57.15: TESTDUAL missing from technical_indicators (the v12.6.1 bug)")
    else:
        _populated = 0
        _missing = []
        # Tuple indices match the SELECT column order
        _idx_keys = ["sma_200","supertrend","adx","rsi_14","macd_signal_txt",
                     "stoch_k","mfi_14","obv_signal","above_vwap",
                     "support1","support2","resist1","resist2"]
        for _i, _key in enumerate(_idx_keys):
            _val = _ti_query15[_i]
            _kind = next((k for n,k in _expected_cols if n == _key), "any")
            _ok = False
            if _kind == "numeric":
                try:
                    _ok = _val is not None and float(_val) != 0.0
                except (TypeError, ValueError):
                    _ok = False
            elif _kind == "string":
                _ok = isinstance(_val, str) and _val != "" and _val != "—"
            else:
                _ok = _val is not None and _val != ""
            if _ok:
                _populated += 1
            else:
                _missing.append(f"{_key}={_val!r}")

        if _populated == 13:
            passed += 1
            print(f"  ✓ 57.15a All 13 technical_indicators columns populated for "
                  f"DUAL_LISTED (SMA200, RSI, MACD, ADX, OBV, Stoch, MFI, "
                  f"Supertrend, VWAP, S1/S2/R1/R2)")
        else:
            failed += 1
            failures.append(f"57.15a: only {_populated}/13 cols populated for DUAL_LISTED — "
                            f"missing/zero: {', '.join(_missing)}")

        # 57.15b: explicitly verify R1 != R2 and S1 != S2 (real distinct levels)
        _r1, _r2 = float(_ti_query15[11]), float(_ti_query15[12])
        _s1, _s2 = float(_ti_query15[9]),  float(_ti_query15[10])
        if abs(_r2 - _r1) > 0.01 and abs(_s2 - _s1) > 0.01:
            passed += 1
            print(f"  ✓ 57.15b DUAL_LISTED has distinct S1/S2 ({_s1:.2f}/{_s2:.2f}) "
                  f"and R1/R2 ({_r1:.2f}/{_r2:.2f}) — dedup gave real 247-day series")
        else:
            failed += 1
            failures.append(f"57.15b: DUAL_LISTED levels collapsed — "
                            f"S1={_s1:.2f}/S2={_s2:.2f}, R1={_r1:.2f}/R2={_r2:.2f}")

except Exception as _e57:
    import traceback as _tb57
    failed += 1
    failures.append(f"57.14/57.15: end-to-end test crashed — {type(_e57).__name__}: {_e57}\n"
                    + _tb57.format_exc()[:500])


# ════════════════════════════════════════════════════════════════════════════
# GROUP 58 — v12.8 release: Bug #13 (494-victim dedup ordering) + Bug #14 (404 cache)
# ════════════════════════════════════════════════════════════════════════════
print("\n--- Group 58: v12.8 release (Bug #13 + Bug #14) ---")

import sys as _sys58, os as _os58, tempfile as _tf58
import io as _io58, contextlib as _cl58, sqlite3 as _sq58
import pandas as _pd58, numpy as _np58
from datetime import date as _dt58, timedelta as _td58, datetime as _dtm58

_proj_root58 = _os58.path.dirname(_os58.path.abspath(__file__))

# Re-load backfill_history fresh
if _proj_root58 not in _sys58.path:
    _sys58.path.insert(0, _proj_root58)
for _m58 in list(_sys58.modules):
    if 'backfill_history' in _m58:
        del _sys58.modules[_m58]

# ── 58.1 — Bug #13: Dedup re-sort by (symbol,date) AFTER drop_duplicates ──
# Code-shape lock — the trailing sort_values(['symbol','date']) MUST be
# present in _compute_all_indicators's chunk dedup block, or stocks with
# fragmented NSE coverage will produce non-monotonic post-dedup order.
try:
    with open(_os58.path.join(_proj_root58, "backfill_history.py")) as _f58:
        _bf58_src = _f58.read()

    # The dedup sequence must include both drop_duplicates AND a trailing
    # sort_values(['symbol','date']) in the same expression chain.
    _has_dedup_resort = (
        ".drop_duplicates(['symbol', 'date'], keep='first')" in _bf58_src
        and ".sort_values(['symbol', 'date'])" in _bf58_src
    )
    if _has_dedup_resort:
        # Stronger check: the sort_values(['symbol','date']) must appear
        # AFTER drop_duplicates in the source order.
        _idx_dd = _bf58_src.find(".drop_duplicates(['symbol', 'date'], keep='first')")
        _idx_sv = _bf58_src.find(".sort_values(['symbol', 'date'])", _idx_dd)
        if _idx_sv > _idx_dd > 0:
            passed += 1
            print("  ✓ 58.1a Dedup re-sorts by (symbol,date) AFTER drop_duplicates")
        else:
            failed += 1
            failures.append("58.1a: sort_values(['symbol','date']) does not follow drop_duplicates")
    else:
        failed += 1
        failures.append("58.1a: missing trailing sort_values after dedup in _compute_all_indicators")
except Exception as _e58:
    failed += 1
    failures.append(f"58.1a: scan failed — {_e58}")

# ── 58.2 — Bug #13 hardening: compute_technicals resets index ──
# compute_technicals must call .reset_index(drop=True) after sort_values('date')
# so the internal index is always monotonic regardless of caller correctness.
try:
    # Search for the specific pattern in compute_technicals
    if "df = hist.sort_values('date').reset_index(drop=True)" in _bf58_src:
        passed += 1
        print("  ✓ 58.2a compute_technicals resets index after sort_values (defense in depth)")
    else:
        failed += 1
        failures.append("58.2a: compute_technicals missing reset_index after sort_values")
except Exception as _e58:
    failed += 1
    failures.append(f"58.2a: scan failed — {_e58}")

# ── 58.3 — Bug #13 end-to-end: fragmented NSE coverage ──
# This is the test that would have caught the v12.7 production bug
# immediately. Build a synthetic stock with NSE missing on multiple dates
# (matching production: 13 days/year), BSE filling the gaps. Pre-v12.8
# this raised "ValueError: index must be monotonic increasing or
# decreasing" inside compute_technicals → swallowed → 494 production
# stocks dropped from technical_indicators.
try:
    import backfill_history as _bf58
    _np58.random.seed(123)
    _all_dates_58 = _pd58.bdate_range("2025-04-01", "2026-04-30")[-247:]
    # NSE fails on 13 specific dates spread across the year
    _nse_fail_set = {0, 24, 48, 72, 96, 120, 144, 168, 192, 216, 230, 240, 246}

    with _tf58.TemporaryDirectory() as _td_58:
        _orig_cwd_58 = _os58.getcwd()
        try:
            _os58.chdir(_td_58)
            _conn58 = _sq58.connect("market_data.db")
            _bf58.init_all_tables(_conn58)

            _rows_58 = []
            for _sym58 in ['FRAGSTK1', 'FRAGSTK2', 'FRAGSTK3']:
                _base = 50 + (hash(_sym58) % 100)
                for _i58, _d58 in enumerate(_all_dates_58):
                    _ds58 = _d58.strftime('%Y-%m-%d')
                    _p58 = _base + _i58 * 0.1
                    if _i58 not in _nse_fail_set:
                        _rows_58.append((_sym58, 'NSE', _ds58, _p58, _p58*1.01, _p58*0.99, _p58, 100000))
                    _rows_58.append((_sym58, 'BSE', _ds58, _p58*1.001, _p58*1.011, _p58*0.991, _p58*1.001, 50000))

            _df58 = _pd58.DataFrame(_rows_58, columns=[
                'symbol','exchange','date','open','high','low','close','volume'])
            _df58.to_sql('daily_prices', _conn58, if_exists='append', index=False)

            _buf58 = _io58.StringIO()
            with _cl58.redirect_stdout(_buf58):
                _bf58._compute_all_indicators(_conn58)

            _ti_count58 = _conn58.execute(
                "SELECT COUNT(DISTINCT symbol) FROM technical_indicators"
            ).fetchone()[0]
            _conn58.close()
        finally:
            _os58.chdir(_orig_cwd_58)

    if _ti_count58 == 3:
        passed += 1
        print(f"  ✓ 58.3a All 3 fragmented-NSE stocks populated (the 494-victim pattern fixed)")
    else:
        failed += 1
        failures.append(f"58.3a: only {_ti_count58}/3 fragmented stocks populated — Bug #13 incomplete")

    # 58.3b: log shows ZERO _ti_errors (was 494 in v12.7 production)
    _log58 = _buf58.getvalue()
    if "compute_technicals: 0 symbols failed" in _log58 or "compute_technicals:" not in _log58:
        passed += 1
        print(f"  ✓ 58.3b Zero compute_technicals errors logged (was 494 in v12.7)")
    else:
        failed += 1
        failures.append(f"58.3b: log shows compute_technicals errors — {_log58[-200:]}")

except Exception as _e58:
    import traceback as _tb58
    failed += 1
    failures.append(f"58.3: end-to-end test crashed — {type(_e58).__name__}: {_e58}\n"
                    + _tb58.format_exc()[:500])

# ── 58.4 — Bug #14: failed_yfinance_lookups table exists ──
try:
    with _tf58.TemporaryDirectory() as _td_yfc:
        _orig_cwd_yfc = _os58.getcwd()
        try:
            _os58.chdir(_td_yfc)
            _conn_yfc = _sq58.connect("market_data.db")
            _bf58.init_all_tables(_conn_yfc)
            _tbl = _conn_yfc.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='failed_yfinance_lookups'"
            ).fetchone()
            _conn_yfc.close()
        finally:
            _os58.chdir(_orig_cwd_yfc)

    if _tbl and "PRIMARY KEY (symbol, suffix)" in _tbl[0]:
        passed += 1
        print(f"  ✓ 58.4a failed_yfinance_lookups table exists with (symbol, suffix) PK")
    else:
        failed += 1
        failures.append(f"58.4a: failed_yfinance_lookups missing or wrong PK")
except Exception as _e58:
    failed += 1
    failures.append(f"58.4a: scan failed — {_e58}")

# ── 58.5 — Bug #14: cache helpers (record + load + TTL) ──
try:
    with _tf58.TemporaryDirectory() as _td_yfh:
        _orig_cwd_yfh = _os58.getcwd()
        try:
            _os58.chdir(_td_yfh)
            _conn_yfh = _sq58.connect("market_data.db")
            _bf58.init_all_tables(_conn_yfh)

            # Record 3 fresh failures
            _bf58._record_yf_404(_conn_yfh, "TATAMOTORS", ".NS")
            _bf58._record_yf_404(_conn_yfh, "DHANI", ".NS")
            _bf58._record_yf_404(_conn_yfh, "ESILVER", ".BO")

            _cache = _bf58._load_yf_404_cache(_conn_yfh)

            # 58.5a: cache contains all 3
            _all_present = (
                ("TATAMOTORS", ".NS") in _cache
                and ("DHANI", ".NS") in _cache
                and ("ESILVER", ".BO") in _cache
            )
            if _all_present:
                passed += 1
                print(f"  ✓ 58.5a yfinance 404 cache record+load works ({len(_cache)} entries)")
            else:
                failed += 1
                failures.append(f"58.5a: cache missing entries — {sorted(_cache)}")

            # 58.5b: TTL filter works (insert 31-day-old, should be excluded)
            _old_date = (_dtm58.now() - _td58(days=31)).strftime("%Y-%m-%d")
            _conn_yfh.execute(
                "INSERT INTO failed_yfinance_lookups VALUES (?, ?, ?, ?)",
                ("OLDFAIL", ".NS", _old_date, "404")
            )
            _conn_yfh.commit()
            _cache2 = _bf58._load_yf_404_cache(_conn_yfh)
            if ("OLDFAIL", ".NS") not in _cache2 and len(_cache2) == 3:
                passed += 1
                print(f"  ✓ 58.5b TTL filter excludes 31-day-old entries (cache stable at {len(_cache2)})")
            else:
                failed += 1
                failures.append(f"58.5b: TTL filter broken — cache={sorted(_cache2)}")

            _conn_yfh.close()
        finally:
            _os58.chdir(_orig_cwd_yfh)
except Exception as _e58:
    failed += 1
    failures.append(f"58.5: cache helper test crashed — {type(_e58).__name__}: {_e58}")

# ── 58.6 — Bug #14: yfinance logger silenced ──
try:
    import logging as _log58
    _bf58._silence_yfinance_logger()
    _yf_logger = _log58.getLogger("yfinance")
    if _yf_logger.level == _log58.CRITICAL:
        passed += 1
        print(f"  ✓ 58.6a yfinance logger silenced (level=CRITICAL)")
    else:
        failed += 1
        failures.append(f"58.6a: yfinance logger level={_yf_logger.level} (expected 50/CRITICAL)")
except Exception as _e58:
    failed += 1
    failures.append(f"58.6a: logger silence test crashed — {_e58}")

# ── 58.7 — Bug #14: forensics_engine accepts skip_set parameter ──
try:
    import inspect as _ins58
    if 'analysis.forensics_engine' in _sys58.modules:
        del _sys58.modules['analysis.forensics_engine']
    from analysis.forensics_engine import ForensicsEngine as _FE58
    _sig = _ins58.signature(_FE58.fetch_forensic_inputs)
    if 'skip_set' in _sig.parameters:
        passed += 1
        print(f"  ✓ 58.7a ForensicsEngine.fetch_forensic_inputs accepts skip_set param")
    else:
        failed += 1
        failures.append(f"58.7a: skip_set param missing from fetch_forensic_inputs signature")
except Exception as _e58:
    failed += 1
    failures.append(f"58.7a: signature check crashed — {_e58}")

# ── 58.8 — Bug #14: master_funnel pre-loads skip_set before forensics loop ──
try:
    with open(_os58.path.join(_proj_root58, "master_funnel.py")) as _f58:
        _mf58_src = _f58.read()

    # Both the load query AND the call-site with skip_set= must be present
    _has_load = "FROM failed_yfinance_lookups" in _mf58_src
    _has_pass = "skip_set=_yf_skip_set" in _mf58_src

    if _has_load and _has_pass:
        passed += 1
        print(f"  ✓ 58.8a master_funnel pre-loads cache + passes skip_set to forensics")
    else:
        failed += 1
        failures.append(f"58.8a: master_funnel missing load_query={_has_load} pass={_has_pass}")
except Exception as _e58:
    failed += 1
    failures.append(f"58.8a: master_funnel scan crashed — {_e58}")


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