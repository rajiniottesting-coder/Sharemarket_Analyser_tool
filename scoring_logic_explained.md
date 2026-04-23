# Scoring & Verdict Logic — Explained (Post-v10.9)

This document walks through exactly how the pipeline turns raw stock data into a
**Composite Score (0-100)** and a **Verdict (BUY / OVERVALUED / WATCHLIST /
NEUTRAL / AVOID)** after the v10.9 forensic integration.

---

## 1. The Big Picture — One Stock's Journey

For each of the top-100 stocks, `ScoringEngine.calculate_composite_score()` runs
**four stages** in this exact order:

```
Stage A: Base Weighted Score    (sub-scores × weights)        →  0-100 baseline
Stage B: Adjustments & Bonuses   (MoS, Spike, Early, Risk)    →  modifies baseline
Stage C: Forensic Quality Adj    (v10.9 NEW — Altman Z, etc.) →  ±8/−10 cap
Stage D: Verdict Derivation      (cap-aware thresholds + MoS) →  BUY / OVERVALUED / etc.
```

Final output: `{composite_score, verdict, confidence, label, forensic_adj, forensic_factors}`.

---

## 2. Stage A — Base Weighted Score

Five sub-scores are computed upstream (in `master_funnel.py` Section 6):

| Sub-score | 0-100 range | What it captures | Example inputs |
|---|---|---|---|
| **Fundamental** | `f_raw` | Business quality | PE, ROE, D/E, Margins, Growth, FCF Yield |
| **Technical** | `t_raw` | Price trend health | RSI, MACD, Supertrend, ADX, MFI, Stoch |
| **Early Entry** | `e_raw` | Momentum before consensus | 12 signals: vol spike, MACD+ST confluence, 52W breakout, FII accum |
| **Sentiment** | `sent_raw` | Informed market mood | FII 3Q trend, insider buy, news sentiment, pledge direction |
| **Safety** | `safe_raw` | Defensive quality | Beta, D/E, FCF positive, BS Health flag |

### Canonical weights (when sentiment is "informed")

```
base_score = f_raw × 0.35
           + t_raw × 0.30
           + e_raw × 0.15
           + sent_raw × 0.10
           + safe_raw × 0.10
```

### Redistributed weights (when sentiment is NOT informed)

**Why:** On free data sources (no paid FII/promoter/insider feed, no AI news),
`sent_raw` defaults to 50 ("neutral"). That would give every stock a free 5 points
(50 × 0.10) regardless of real signals. Unfair to stocks with informed bad sentiment.

**Rule:** If NONE of these signals fired, sentiment is "not informed":
- FII 3Q trend is `UP` or `DOWN`
- Insider Buy Alert is `YES`
- Promoter QoQ Δ has meaningful magnitude (>0.1 ppt)
- DII QoQ Δ has meaningful magnitude
- News Sentiment is `POSITIVE` or `NEGATIVE`
- Pledge Direction is `FALLING` or `RISING`

Redistribute sentiment's 10% across the four informed sub-scores
**proportionally to original weights:**

```
base_score = f_raw × 0.389
           + t_raw × 0.333
           + e_raw × 0.167
           + safe_raw × 0.111
```

The `weights_used` field in the output tells you which branch was taken:
`"canonical"` or `"redistributed (no paid sentiment)"`.

---

## 3. Stage B — Adjustments & Bonuses

Four modifiers applied after the base score:

| Modifier | Range | Trigger |
|---|---|---|
| **MoS Adjustment** | −10 to +12 | From `fair_value_engine.py` — based on CFV-CMP gap: `>40%: +12`, `>25%: +8`, `>10%: +4`, `<−15%: −5`, `<−30%: −10` |
| **Spike Bonus** | 0 to +10 | `spike_count × 2` (6 triggers × 2 pts each). Capped at **+3** if `f_raw < 55` — momentum can't rescue weak fundamentals |
| **Early Mover Bonus** | 0 or +5 | `+5` if `early_entry_score ≥ 50` |
| **Anti-Trigger Penalty** | 0 or −10 | `−10` if `risk_flag_active` (pledge + Beneish/Altman + CFO/PAT mismatch) |

After Stage B:
```
final_score = base_score + mos_adj + spike_bonus + early_bonus − risk_penalty
```

---

## 4. Stage C — Forensic Quality Adjustment (v10.9 NEW)

**The gap this fills:** v10.2-v10.8 populated forensic fields (Altman Z, ND/EBITDA,
Int Coverage, Earn Quality) but scoring never used them. Now they act as a
**quality gate** — max +8 bonus, −10 floor. Fundamental/technical remain primary;
forensic is the tiebreaker.

### The 4 forensic factors

| Factor | Formula | Bonus | Penalty |
|---|---|---|---|
| **Altman Z** | Composite bankruptcy predictor (5 ratios) | **+3** if ≥ 3.0 (safe zone) | **−5** if < 1.8 (distress zone) |
| **Earn Quality** | CFO / PAT categorical bucket (v10.8) | **+2** if HIGH (CFO/PAT ≥ 0.8) | **−3** if LOW (CFO/PAT < 0.5) |
| **ND / EBITDA** | (Total Debt − Cash) / annual EBITDA | **+1** if < 1.0 (strong solvency) | **−2** if > 5.0 (high leverage) |
| **Int Coverage** | EBIT / Interest Expense | **+2** if > 5× (comfortable) | **−3** if < 1.5× (distress) |

### Key rules

- **All four factors accumulate.** A stock with Altman Z ≥ 3, Earn Quality HIGH,
  ND/EBITDA < 1, and Int Coverage > 5× gets `+3 + 2 + 1 + 2 = +8` (capped at +8).
- **Missing data → no adjustment.** `"—"`, `None`, `""`, `"N/A"` all return `None`
  from `_fnum()` and contribute 0. This protects small-caps without forensic data.
- **Grey zones don't adjust.** Altman Z between 1.8-3.0, Earn Quality MODERATE,
  ND/EBITDA between 1.0-5.0, Int Coverage between 1.5-5.0 all contribute 0.
- **Caps: +8 max, −10 min.** Even if every factor fires, a stock can't gain more
  than +8 or lose more than −10 on forensic alone.

### Output

```python
{
    "forensic_adj":     3,                          # signed integer
    "forensic_factors": "AltmanZ≥3:+3|EQ=HIGH:+2|ND/EBITDA<1:+1",  # pipe-separated
}
```

Then:
```
final_score = final_score + forensic_adj     (then clamped to 0-100)
```

---

## 5. Stage D — Verdict Derivation

Cap-aware thresholds from `CAP_THRESHOLDS`:

| Cap tier | BUY ≥ | WATCHLIST ≥ | AVOID < |
|---|---|---|---|
| LARGE | 60 | 50 | 38 (universal) |
| MID   | 63 | 53 | 38 |
| SMALL | 66 | 56 | 38 |
| MICRO | 70 | 60 | 38 |

**Why cap-aware:** Small-caps are riskier → must score higher to overcome that risk.

### Decision tree

```
IF final_score < 38:
    verdict = AVOID

ELIF final_score ≥ BUY threshold for cap:
    # Passed the quantitative bar — now check MoS gate
    IF MoS% ≤ −10%:
        # Great business but overpriced
        verdict = OVERVALUED
        # Exception — "Tech Override": relax gate to MoS ≤ −20% IF:
        #   score ≥ 70 AND Supertrend=BUY AND Sector Stage 2
        #   (strong signals outweigh valuation premium)
    ELSE:
        verdict = BUY

ELIF final_score ≥ WATCHLIST threshold:
    verdict = WATCHLIST

ELSE:
    verdict = NEUTRAL
```

### Confidence dots (Session 24)

How far is the score from the decisive threshold?

| Distance | Confidence | Display |
|---|---|---|
| ≥ 5 points | HIGH | ●●● |
| 2 to 5 points | MEDIUM | ●●○ |
| 0 to 2 points | LOW | ●○○ (cliff zone — handle with care) |

The final Excel **Verdict** column shows both: `BUY ●●●` or `OVERVALUED ●●○`.

---

## 6. Worked Examples

### Example 1 — Blue-chip large-cap with strong forensics

```
Inputs:
  Fundamental = 72, Technical = 65, Early Entry = 35
  Sentiment = 55 (informed), Safety = 70
  MoS% = 18, Spike count = 2
  Altman Z = 4.5, Earn Quality = HIGH, ND/EBITDA = 0.5, Int Coverage = 12×
  Cap = LARGE

Stage A (canonical): 72×.35 + 65×.30 + 35×.15 + 55×.10 + 70×.10
                   = 25.2 + 19.5 + 5.25 + 5.5 + 7.0 = 62.45

Stage B: + MoS adj (+4 for 10-25% band) + spike (2×2=4, f≥55 so full)
         + Early (EE<50, no bonus) − Risk (no flag)
       = 62.45 + 4 + 4 + 0 − 0 = 70.45

Stage C forensic: +3 (Altman≥3) + 2 (EQ HIGH) + 1 (ND<1) + 2 (IC>5×) = +8 (capped)
       = 70.45 + 8 = 78.45

Stage D: LARGE cap, threshold 60. Score 78 ≥ 60. MoS +18 > −10 → BUY.
         Distance = 78.45 − 60 = 18.45 → HIGH confidence.

Output: "BUY ●●●" with forensic_factors="AltmanZ≥3:+3|EQ=HIGH:+2|ND/EBITDA<1:+1|IC>5x:+2"
```

### Example 2 — Expensive quality business

```
Inputs:
  Fundamental = 80, Technical = 75, Early Entry = 40, Sentiment = 60, Safety = 75
  MoS% = −25 (expensive), Spike = 3
  Altman Z = 3.2, Earn Quality = HIGH, ND/EBITDA = 0.3, Int Coverage = 15×
  Cap = MID

Stage A: 80×.35 + 75×.30 + 40×.15 + 60×.10 + 75×.10 = 28 + 22.5 + 6 + 6 + 7.5 = 70.0

Stage B: − MoS adj (−5 for −15 to −30%) + spike (3×2=6) + 0 + 0 = 71.0

Stage C forensic: +3 + 2 + 1 + 2 = +8 (capped)
       = 79.0

Stage D: MID cap threshold 63. Score 79 ≥ 63. But MoS = −25 < −10 → OVERVALUED
         (unless Tech Override fires: score ≥ 70 ✓, but need to check Supertrend=BUY
          AND Sector Stage 2 too — let's say Sector Stage = Stage 1, override doesn't fire)

Output: "OVERVALUED ●●●" — "Quality business, wait for a pullback"
```

### Example 3 — Distressed small-cap

```
Inputs:
  Fundamental = 45, Technical = 40, Early Entry = 20, Sentiment = 50, Safety = 35
  MoS% = 30, Spike = 0
  Altman Z = 1.2 (DISTRESS), Earn Quality = LOW, ND/EBITDA = 7.5, Int Coverage = 0.8
  Cap = SMALL

Stage A (redistributed — no paid sentiment): 45×.389 + 40×.333 + 20×.167 + 35×.111
       = 17.5 + 13.3 + 3.3 + 3.9 = 38.0

Stage B: + MoS (+8 for 25-40%) + spike 0 + 0 + 0 = 46.0

Stage C forensic: −5 (Altman<1.8) −3 (EQ LOW) −2 (ND>5) −3 (IC<1.5) = −13 (capped at −10)
       = 36.0

Stage D: Score 36 < 38 → AVOID.

Output: "AVOID ●○○" — forensic penalty revealed a bankruptcy risk the MoS discount
         was hiding. THIS IS WHY v10.9 FORENSIC INTEGRATION MATTERS.
```

Notice Example 3: **before v10.9**, this stock would have scored 46 → WATCHLIST.
After v10.9, forensic quality adjustment correctly flags it as AVOID.

---

## 7. Other Analysis Changes in v10.9

Beyond scoring, v10.9 also improved three other analyses:

### A. Resist 2 / Support 2 — now 52-week levels

**Before v10.9:** `res2 = rolling(40).max()` — for momentum stocks near highs,
R1 and R2 were identical (87% of stocks).

**After v10.9:** `res2 = rolling(252).max()` — 52-week high, genuinely distinct
long-term resistance. Same for `sup2` (52-week low).

**Why this matters for traders:**
- **R1 (20d high)**: nearest swing ceiling — Target 1 territory
- **R2 (52w high)**: major supply zone — Target 2/3 and breakout watch-level

### B. Pro / FII / DII QoQ Δ — placement fix

**Before v10.9:** `_qoq()` ran in Section 3 before shareholding DB enrichment in
Section 5. `stock['promoter_pct']` was still 0 at QoQ computation time, producing
`delta = 0 − 62.27 = −62.27` for 81/84 stocks.

**After v10.9:** Added a **Section 5A.4 QoQ recompute block** that runs AFTER
Section 5 enrichment. Uses real current values. Falls back to `"—"` when the
`shareholding` table lacks ≥90-day-old history (honest display).

### C. Div Yield = 0 → "—"

**Before v10.9:** Non-dividend stocks showed `Div Yield % = 0`, indistinguishable
from a rare genuine 0% yield.

**After v10.9:** Non-dividend stocks display `"—"`. The downstream failsafe at
master_funnel line ~1668 was guarded against `float("—")` crash.

---

## 8. What Hasn't Changed

These core pieces of the scoring system are unchanged from v10.0 (Session 24):

- **Canonical weights 35/30/15/10/10** — still the base
- **Sentiment informedness logic** — same redistribution rule
- **Spike bonus fundamental gate** — still +3 cap if `f_raw < 55`
- **Early Mover bonus** — still +5 at EE ≥ 50
- **Anti-trigger penalty** — still −10 on risk flag
- **MoS score adjustment** — same tiers from `fair_value_engine`
- **Cap-aware thresholds** — still LARGE 60 / MID 63 / SMALL 66 / MICRO 70
- **OVERVALUED verdict** — same MoS gate logic
- **Confidence dots** — same distance-from-threshold rule

v10.9 added **one new stage** (forensic quality adj) between the bonuses and
the verdict derivation. It didn't change any existing weights or thresholds.

---

## 9. Where to Look in the Code

| Logic | File | Function / Section |
|---|---|---|
| Composite score computation | `analysis/scoring_engine.py` | `calculate_composite_score` |
| Stage A base weights | `analysis/scoring_engine.py` | Lines 70-114 |
| Stage B adjustments | `analysis/scoring_engine.py` | Lines 116-138 |
| Stage C forensic adj (v10.9) | `analysis/scoring_engine.py` | Lines 140-205 |
| Stage D verdict derivation | `analysis/scoring_engine.py` | `_get_verdict_with_confidence` |
| Sub-score computation | `master_funnel.py` | Section 6 scoring loop |
| MoS adjustment source | `analysis/fair_value_engine.py` | `score_adjustment` in return dict |
| Altman Z calculation | `analysis/forensics_engine.py` | `calculate_altman_z` |
| Earn Quality bucketing | `analysis/forensics_engine.py` | Lines 378-388 (HIGH/LOW/MODERATE) |
| ND/EBITDA calculation | `analysis/forensics_engine.py` | Lines 340-355 |
| Int Coverage calculation | `analysis/forensics_engine.py` | Lines 357-362 |

---

## 10. Where This Is Documented for End Users

Every piece of the scoring logic is documented in multiple Excel tooltip layers:

1. **Score /100 cell tooltip** — complete forensic threshold table inline
2. **Verdict cell tooltip** — notes the forensic gate + references Score tooltip
3. **SCORES group-header tooltip** (merged cell at row 3) — mentions forensic adj
4. **Tooltip Reference sheet** (7th Excel tab) — auto-built from TIPS dict
5. **Glossary sheet** — entries for Support/Resist 1/2 explain 20d vs 52w
6. **CLAUDE.md Section 6** (AI context file) — full v10.9 specification
7. **CLAUDE.md Section 14** — all forensic constants (`FORENSIC_ALTMAN_Z_SAFE`, etc.)

A user hovering over the **Score /100** column header will see exactly the
thresholds and caps — no need to dig into source code.