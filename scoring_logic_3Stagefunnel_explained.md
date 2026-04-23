# Scoring · Verdict · Funnel — Complete Logic Explained (Post-v10.13)

This is the single source of truth for how the pipeline turns ~5,150 daily bhav rows into **100 final stocks**, each with a **Composite Score (0–100)** and a **Verdict (BUY / OVERVALUED / WATCHLIST / NEUTRAL / AVOID)**.

Read it top-to-bottom and you'll understand the entire decision chain.

---

## Table of contents

- **Part 1 — The Three-Stage Funnel** (how 5,150 stocks become 100)
  - 1.1 Stage 1: Structural eligibility filter
  - 1.2 Stage 2: Quality score /35
  - 1.3 Stage 3: Priority ranker + overrides + cap mix
  - 1.4 Worked example of a stock moving through the funnel
- **Part 2 — The Scoring Engine** (how those 100 stocks get composite scores)
  - 2.1 Big picture — one stock's journey
  - 2.2 Stage A — Base weighted score
  - 2.3 Stage B — Adjustments & bonuses
  - 2.4 Stage C — Forensic quality adjustment (v10.9)
  - 2.5 Stage D — Verdict derivation
- **Part 3 — Worked Scoring Examples**
- **Part 4 — Other v10.9 Analysis Improvements**
- **Part 5 — What Hasn't Changed**
- **Part 6 — Where to Look in the Code**
- **Part 7 — Where This Is Documented for End Users**

---

# PART 1 — The Three-Stage Funnel

Every morning, the pipeline has to decide which 100 stocks (out of ~5,150 traded on NSE+BSE) deserve the full treatment — yfinance fundamentals, forensics, technicals, scoring, Gemini AI cards. That decision is made in **three cascading stages**, from cheapest filter to most selective.

```
~5,150 raw bhav rows
      ↓  Stage 1: structural hygiene filter
~1,800 tradeable candidates
      ↓  Stage 2: tradeable-quality score /35
~1,500 quality-passing stocks
      ↓  Stage 3: priority ranker + 5 overrides + cap mix
   100 final candidates
      ↓
   Scoring + verdict + AI (Part 2)
```

The key design principle: **cheap filters first**. Stage 1 runs microseconds per stock (regex + comparisons). Stage 2 reads only bhav-copy columns (milliseconds). Stage 3 runs one batch SQL + priority scoring. Only these ~100 final stocks touch the expensive downstream layers (yfinance, forensic engine, Gemini). That's what keeps this running free-tier on GitHub Actions.

## 1.1 Stage 1 — Structural Eligibility Filter

**File:** `screening/pre_screener.py::stage_1_filter`
**Goal:** remove stocks that shouldn't be analysed at all — not because they're bad investments, but because they're **structurally ineligible** (not real equities, illiquid, or in an artificial price state).

### The filters (in execution order)

| # | Filter | Rule | What it catches |
|---|---|---|---|
| V0a | Fund series | `sc_group ∈ {"EF","MF","IF","IR","BE"}` | BSE-tagged ETFs, mutual funds, InvITs, REITs, bond ETFs |
| V0b | Symbol patterns | ~67 keywords: `LIQUIDBEES`, `GOLDBEES`, `NIFTYBEES`, `SETFNIF50`, `NETFMID`, any `*ETF`, `*BEES`, `*LIQUID`, `*ADD`, `*INDEX` | NSE-listed funds without BSE sc_group |
| V0c | Liquid-fund NAV | Close ₹995–₹1,005 AND symbol contains LIQUID/LIQ/CASH | Liquid funds trading at ₹1,000 NAV |
| V1 | No trading | `volume == 0` | Listed but quiet — no signal |
| V7 | Circuit-hit | `abs((close − prev_close) / prev_close) ≥ 19.9%` | Stock hit ±20% circuit — price is artificial |
| V4 | Penny stock | `close < ₹10` | ₹0.05 tick dominates movements; shell-company risk |
| V8 | Suspended | `suspended=True` OR `status == "SUSPENDED"` | Explicitly halted by exchange |
| V3 | Low delivery | `delivery_pct < 40%` (bypass-able via `watchlist_override`) | Intraday speculation, no real investor participation |
| V9 | Illiquid SME | `"SME" in exchange_tag` AND `turnover < ₹5L` | SME stocks where price = 1-2 trades of noise |

### What Stage 1 is NOT

**Stage 1 is hygiene, not judgment.** A fundamentally excellent stock that happens to be illiquid today will fail Stage 1 exactly like a fraud would. That's intentional — the downstream layers need volume, delivery, and price-movement signals to work. No point spending yfinance quota on a stock where the signals are noise.

### Typical result

```
✅ Stage 1 Complete: 5150 → 1834 candidates
(dropped: no_vol=0, penny=590, low_deliv=2470, circuit=9, sme_illiquid=0, suspended=0)
```

Most drops are `low_delivery` (majority of stocks are intraday-speculation-dominated) and `penny`.

## 1.2 Stage 2 — Quality Score /35

**File:** `screening/pre_screener.py::stage_2_fundamental_scorer`
**Goal:** score the Stage-1 survivors on **tradeability quality** — NOT business fundamentals. That's Part 2's job. Stage 2 asks: "is this stock liquid enough and institutionally-interesting enough that downstream expensive analysis is worth spending on it?"

### Three hard drops before scoring starts

| # | Rule | Why |
|---|---|---|
| HD1 | `0 < turnover < ₹2 Lakh` | Too illiquid to buy meaningfully — even ₹50k could move the price |
| HD2 | `0 < delivery_pct < 30%` | Pure speculation; clears Stage 1's 40% but below the *quality* bar |
| HD3 | `0 < close < ₹20` | Raises the penny floor from Stage 1's ₹10 |

### The 7-criterion score (5 points each, max = 35)

| # | Criterion | Threshold | Interpretation |
|---|---|---|---|
| B1 | Delivery ≥ 50% | +5 | Basic institutional interest |
| B2 | Delivery ≥ 65% | +5 | Strong institutional conviction |
| B3 | Turnover ≥ ₹10 L | +5 | Minimum meaningful liquidity |
| B4 | Turnover ≥ ₹50 L | +5 | Good trading liquidity |
| B5 | Price ≥ ₹50 | +5 | Avoids micro-speculative zone |
| B6 | Price ≥ ₹200 | +5 | Mid-to-large quality zone |
| B7 | Exchange = DUAL_LISTED | +5 | NSE+BSE = broader access |

**Threshold to pass Stage 2: score ≥ 15** (must satisfy at least 3 of 7).

### The cap_category estimate

At the end of Stage 2, each row gets a cap category based on today's turnover (proxy for market cap since real cap hasn't been fetched from yfinance yet):

- Turnover ≥ ₹50 Cr → `LARGE CAP`
- Turnover ≥ ₹10 Cr → `MID CAP`
- Turnover ≥ ₹1 Cr → `SMALL CAP`
- Otherwise → `MICRO CAP`

### Typical result

`Stage 2 Complete: 1594 stocks qualified (from 1834 input)` — about 87% survive.

## 1.3 Stage 3 — Priority Ranker + Overrides + Cap Mix

**File:** `screening/priority_ranker.py::get_top_100_candidates`
**Goal:** pick exactly **100 stocks** from the ~1,500 Stage-2 survivors, balancing three objectives:

1. Prefer stocks with the best **liquidity + quality signal today**
2. Force-include stocks hitting **specific catalyst triggers** (override rules)
3. Keep a balanced **cap mix** across LARGE/MID/SMALL/MICRO

### Step 1 — Batch pre-fetch (v10.13 — NEW)

At function entry, two SQL queries run ONCE for the whole df (instead of ~3,000 per-row queries):

- `_avg_vol_cache = get_20d_avg_vol_batch(all_symbols)` — 20-day volume average for every Stage-2 survivor via a single windowed SQL. **107× faster** than pre-v10.13 per-symbol calls.
- `_prior_map = get_prior_analysis_map()` — yesterday's scores and verdicts from `latest_analysis_results`. Returns `{symbol: {last_score, last_verdict, date, days_since}}`. **Empty dict on first-ever run** — first-run-safe.

If `_prior_map` is non-empty, three new columns are attached to the df:
- `last_claude_score` — last run's composite score
- `last_claude_verdict` — last run's verdict string
- `days_since_analysis` — `(today − last_analysis_date).days`

### Step 2 — The priority score formula

Every Stage-2 survivor gets a `priority_score` (0–100) computed with the **normalize-then-weight** pattern:

```
priority_score = (vol_spike / 5)    × 25     # cap spike at 5× to prevent ETF-arb dominance
               + (stage2_score / 35) × 30     # Stage 2 quality flows directly in
               + (delivery_pct / 100) × 20    # institutional participation fine-grained
               + (cap_bonus)         × 15     # LARGE=1.0, MID=0.67, SMALL=0.33, MICRO=0
               + (turnover_bonus)    × 10     # ≥₹5Cr=1.0, ≥₹1Cr=0.6, ≥₹10L=0.3
```

Weights sum to **100** — so priority_score is "percent of maximum possible signal today."

**Why those specific weights?** The v1 formula had `vol_spike = 40` which caused ETF arbitrage to dominate. v2 dropped it to 25 and added `cap_bonus` so LARGE caps don't get crushed by MICROs on vol-spike days.

**Why cap the volume spike at 5×?** ETFs and arbitrage instruments can legitimately trade 20–50× their "average" on a given day — they're plumbing, not signal. Stage 1 filters most out, but the cap prevents the few that slip through from dominating.

**Why bucket turnover (not continuous)?** Liquidity is not linear. ₹10L → ₹1Cr changes everything (actually tradeable). ₹5Cr → ₹50Cr doesn't change much for retail scale. Buckets reflect real tradeability thresholds.

### Step 3 — Five override rules (v10.13 activated O4 & O5)

Overrides force a stock into the top 100 REGARDLESS of priority_score. Rules fire in priority order `O1 > O2 > O3 > O4 > O5`, deduplicated, capped at **20 total** overrides. The remaining 80 slots fill by pure priority ranking.

| Rule | Condition | Status | Purpose |
|---|---|---|---|
| **O1** Watchlist | `watchlist_override = True` | Active | Personal follow-list |
| **O2** Announcement | `announcement_today = True` | Active but free-tier has no source | Corporate action day |
| **O3** Spike pre-trigger | `vol_ratio ≥ 3× AND delivery ≥ 60%` | Active | Unusual-activity catch |
| **O4** Score deterioration | `last_claude_score ≥ 60 AND today's stage2_score < 15` | **v10.13 activated** | Previously-good stock whose trading quality just collapsed |
| **O5** Expiry re-check | `7 ≤ days_since_analysis < 99` | **v10.13 activated** | Re-check stocks dropped off ranking every 7 days |

**Why `days_since_analysis < 99`?** On first-ever run, the table is empty and `days_since` defaults to 99 for everyone. Without the `<99` guard, every stock would flood the overrides. The guard makes O5 no-op on first run (behaves identical to pre-v10.13).

**Pre-v10.13**, O4 always returned False (column was never populated) and O5 was explicitly hardcoded to False. Effective override rules were O1 + O2 + O3. v10.13 activated O4 + O5 by populating the data these rules need from `latest_analysis_results`.

### Step 4 — Cap-tier diversification

After overrides lock in, the remaining ~80 slots fill from the priority-ranked pool with constraints:

```python
MIN_LARGE         = 20    # at least 20 LARGE CAP stocks in top 100
MIN_MID           = 15    # at least 15 MID CAP stocks
MAX_SMALL_MICRO   = 65    # at most 65 SMALL + MICRO combined
```

The filler loop walks priority-ranked non-override stocks, skipping SMALL/MICRO picks once the counter hits 65. This prevents a theme-day where 80 small-caps are all spiking from flooding the list.

### Step 5 — Selection reason + verdict-priority sort

- Each stock gets a `selection_reason` string (e.g., "Large-cap institutional quality; strong institutional delivery 72%; significant volume surge 3.2× avg") — feeds directly into the AI prompt.
- Final df is sorted by `verdict` (BUY → OVERVALUED → WATCHLIST → NEUTRAL → AVOID), then `priority_score` within tier. This determines Excel row order.

### Step 6 — AVOID-skip for AI (v10.13 FIX #1 — in master_funnel Section 7/8)

Stage 3 returns 100 stocks. Section 6 then computes composite scores and verdicts. Then Section 7/8 calls Gemini. Here's where v10.13 saves quota:

```python
for _idx, _stock in enumerate(final_100_list):
    _v = str(_stock.get("verdict", "") or "").upper()
    if _v.startswith("AVOID"):
        _avoid_indices.add(_idx)       # will get placeholder
    else:
        _ai_input_stocks.append(_stock)  # sent to Gemini
```

AVOID-verdict stocks (composite < 38) get a fixed placeholder instead of burning a Gemini call. Observed waste pre-v10.13: 8 AVOID stocks in a 88-stock run (9% of quota).

## 1.4 Worked Funnel Example — "TATAMOTORS" on a normal day

Imagine TATAMOTORS trades as follows today:

```
Price close    : ₹850
Prev close     : ₹840  (+1.19% — well below circuit)
Volume         : 3,200,000 shares (today)
20d avg volume : 2,500,000 shares
Turnover       : ₹272 Cr
Delivery %     : 64%
Exchange tag   : DUAL_LISTED
Sector         : Auto
```

### Stage 1 checks

- Not an ETF (clean Auto stock) ✓
- Volume > 0 ✓
- Day change 1.19% < 19.9% ✓
- Price ₹850 > ₹10 ✓
- Not suspended ✓
- Delivery 64% > 40% ✓
- Not SME ✓
- **Passes Stage 1** → moves to Stage 2

### Stage 2 checks

Hard drops: turnover ₹272 Cr > ₹2L ✓ · delivery 64% > 30% ✓ · price ₹850 > ₹20 ✓

Scoring:
- B1 (deliv ≥ 50%) → +5 ✓
- B2 (deliv ≥ 65%) → not quite (64%) → 0
- B3 (turnover ≥ ₹10L) → +5 ✓
- B4 (turnover ≥ ₹50L) → +5 ✓
- B5 (price ≥ ₹50) → +5 ✓
- B6 (price ≥ ₹200) → +5 ✓
- B7 (DUAL_LISTED) → +5 ✓

**stage2_score = 30**. Passes threshold (≥15). Turnover ≥ ₹50 Cr → `cap_category = LARGE CAP`.

### Stage 3 checks

**Priority score:**

| Component | Calculation | Points |
|---|---|---|
| Vol spike | `min(3.2M / 2.5M, 5) / 5 × 25 = 1.28 / 5 × 25` | 6.4 |
| Stage 2 quality | `30 / 35 × 30` | 25.7 |
| Delivery | `64 / 100 × 20` | 12.8 |
| Cap bonus | LARGE = 1.0 × 15 | 15.0 |
| Turnover bonus | ₹272 Cr ≥ ₹5 Cr → 1.0 × 10 | 10.0 |
| **TOTAL** | | **69.9** |

**Overrides:** no watchlist flag, no announcement, vol spike 1.28× is below O3's 3× threshold. Assume TATAMOTORS was analysed yesterday (days_since_analysis = 1) with last_score = 72 → O4 needs today's stage2 < 15 (stage2 is 30, doesn't fire), O5 needs days_since ≥ 7 (doesn't fire). **No override.**

**Ranking placement:** priority_score 69.9 is solid — comfortably in the top 80 ranked picks. TATAMOTORS makes the top 100 via pure priority ranking.

### Then what?

Sections 3–6 run: yfinance pulls fundamentals, forensics engine computes Altman Z / Earn Quality / ND-EBITDA / Int Coverage, technicals compute RSI / MACD / Supertrend, scoring engine computes the composite score (Part 2 below), verdict is derived. Section 7/8 sends the stock to Gemini (assuming the verdict isn't AVOID). The Excel gets generated.

---

# PART 2 — The Scoring Engine

Now that we have 100 stocks, each one goes through `ScoringEngine.calculate_composite_score()` which runs **four stages in this exact order**:

```
Stage A: Base Weighted Score    (sub-scores × weights)        →  0-100 baseline
Stage B: Adjustments & Bonuses   (MoS, Spike, Early, Risk)    →  modifies baseline
Stage C: Forensic Quality Adj    (v10.9 — Altman Z, etc.)     →  ±8/−10 cap
Stage D: Verdict Derivation      (cap-aware thresholds + MoS) →  BUY / OVERVALUED / etc.
```

Final output: `{composite_score, verdict, confidence, label, forensic_adj, forensic_factors}`.

## 2.1 Big Picture — One Stock's Journey

Every stock enters scoring with 5 sub-scores already computed upstream (in `master_funnel.py` Section 6):

| Sub-score | 0-100 range | What it captures | Example inputs |
|---|---|---|---|
| **Fundamental** | `f_raw` | Business quality | PE, ROE, D/E, Margins, Growth, FCF Yield |
| **Technical** | `t_raw` | Price trend health | RSI, MACD, Supertrend, ADX, MFI, Stoch |
| **Early Entry** | `e_raw` | Momentum before consensus | 12 signals: vol spike, MACD+ST confluence, 52W breakout, FII accum |
| **Sentiment** | `sent_raw` | Informed market mood | FII 3Q trend, insider buy, news sentiment, pledge direction |
| **Safety** | `safe_raw` | Defensive quality | Beta, D/E, FCF positive, BS Health flag |

## 2.2 Stage A — Base Weighted Score

### Canonical weights (when sentiment is "informed")

```
base_score = f_raw × 0.35
           + t_raw × 0.30
           + e_raw × 0.15
           + sent_raw × 0.10
           + safe_raw × 0.10
```

### Redistributed weights (when sentiment is NOT informed)

**Why:** On free data sources (no paid FII/promoter/insider feed, no AI news), `sent_raw` defaults to 50 ("neutral"). That would give every stock a free 5 points (50 × 0.10) regardless of real signals. Unfair to stocks with informed bad sentiment.

**Rule:** If NONE of these signals fired, sentiment is "not informed":
- FII 3Q trend is `UP` or `DOWN`
- Insider Buy Alert is `YES`
- Promoter QoQ Δ has meaningful magnitude (>0.1 ppt)
- DII QoQ Δ has meaningful magnitude
- News Sentiment is `POSITIVE` or `NEGATIVE`
- Pledge Direction is `FALLING` or `RISING`

Redistribute sentiment's 10% across the four informed sub-scores **proportionally to original weights:**

```
base_score = f_raw × 0.389
           + t_raw × 0.333
           + e_raw × 0.167
           + safe_raw × 0.111
```

The `weights_used` field in the output tells you which branch was taken: `"canonical"` or `"redistributed (no paid sentiment)"`.

## 2.3 Stage B — Adjustments & Bonuses

Four modifiers applied after the base score:

| Modifier | Range | Trigger |
|---|---|---|
| **MoS Adjustment** | −10 to +12 | From `fair_value_engine.py` based on CFV-CMP gap: `>40%: +12`, `>25%: +8`, `>10%: +4`, `<−15%: −5`, `<−30%: −10` |
| **Spike Bonus** | 0 to +10 | `spike_count × 2` (6 triggers × 2 pts each). Capped at **+3** if `f_raw < 55` — momentum can't rescue weak fundamentals |
| **Early Mover Bonus** | 0 or +5 | `+5` if `early_entry_score ≥ 50` |
| **Anti-Trigger Penalty** | 0 or −10 | `−10` if `risk_flag_active` (pledge + Beneish/Altman + CFO/PAT mismatch) |

After Stage B:

```
final_score = base_score + mos_adj + spike_bonus + early_bonus − risk_penalty
```

## 2.4 Stage C — Forensic Quality Adjustment (v10.9)

**The gap this fills:** v10.2–v10.8 populated forensic fields (Altman Z, ND/EBITDA, Int Coverage, Earn Quality) but scoring never used them. Now they act as a **quality gate** — max +8 bonus, −10 floor. Fundamental/technical remain primary; forensic is the tiebreaker.

### The 4 forensic factors

| Factor | Formula | Bonus | Penalty |
|---|---|---|---|
| **Altman Z** | Composite bankruptcy predictor (5 ratios) | **+3** if ≥ 3.0 (safe zone) | **−5** if < 1.8 (distress zone) |
| **Earn Quality** | CFO / PAT categorical bucket (v10.8) | **+2** if HIGH (CFO/PAT ≥ 0.8) | **−3** if LOW (CFO/PAT < 0.5) |
| **ND / EBITDA** | (Total Debt − Cash) / annual EBITDA | **+1** if < 1.0 (strong solvency) | **−2** if > 5.0 (high leverage) |
| **Int Coverage** | EBIT / Interest Expense | **+2** if > 5× (comfortable) | **−3** if < 1.5× (distress) |

### Key rules

- **All four factors accumulate.** A stock with Altman Z ≥ 3, Earn Quality HIGH, ND/EBITDA < 1, and Int Coverage > 5× gets `+3 + 2 + 1 + 2 = +8` (capped at +8).
- **Missing data → no adjustment.** `"—"`, `None`, `""`, `"N/A"` all return `None` from `_fnum()` and contribute 0. This protects small-caps without forensic data.
- **Grey zones don't adjust.** Altman Z between 1.8–3.0, Earn Quality MODERATE, ND/EBITDA between 1.0–5.0, Int Coverage between 1.5–5.0 all contribute 0.
- **Caps: +8 max, −10 min.** Even if every factor fires, a stock can't gain more than +8 or lose more than −10 on forensic alone.

### Output

```python
{
    "forensic_adj":     3,                                       # signed integer
    "forensic_factors": "AltmanZ≥3:+3|EQ=HIGH:+2|ND/EBITDA<1:+1",  # pipe-separated
}
```

Then:

```
final_score = final_score + forensic_adj   (then clamped to 0-100)
```

## 2.5 Stage D — Verdict Derivation

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

| Zone | Distance | Confidence | Display |
|---|---|---|---|
| BUY / OVERVALUED / WATCHLIST | ≥ 5 points above threshold | HIGH | ●●● |
| BUY / OVERVALUED / WATCHLIST | 2 to 5 points above | MEDIUM | ●●○ |
| BUY / OVERVALUED / WATCHLIST | 0 to 2 points above | LOW (cliff zone) | ●○○ |
| AVOID | > 5 points below 38 | HIGH | ●●● |
| AVOID | 2 to 5 below 38 | MEDIUM | ●●○ |
| AVOID | 0 to 2 below 38 | LOW | ●○○ |
| NEUTRAL | ≥ 8 points above 38 | HIGH | ●●● |
| NEUTRAL | 4 to 8 above 38 | MEDIUM | ●●○ |
| NEUTRAL | 0 to 4 above 38 | LOW | ●○○ |

The final Excel **Verdict** column shows both: `BUY ●●●` or `OVERVALUED ●●○`.

---

# PART 3 — Worked Scoring Examples

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

Notice Example 3: **before v10.9**, this stock would have scored 46 → WATCHLIST. After v10.9, forensic quality adjustment correctly flags it as AVOID.

### Example 4 — The end-to-end flow for a good stock (layman version)

Imagine INFY trades today with:
- Delivery 78%, turnover ₹320 Cr, price ₹1,520, volume 2.3× 20-day avg
- Cap category: LARGE
- Yesterday's score was 74 → BUY

**Stage 1:** Large-cap stock, trading normally, not an ETF, delivery well above 40% → PASSES.

**Stage 2:** Delivery ≥ 65 (+5+5), turnover ≥ ₹50 L (+5+5), price ≥ ₹200 (+5+5), dual-listed (+5) → **Stage 2 score = 35/35**. Cap → `LARGE CAP` (turnover > ₹50 Cr).

**Stage 3:**
- Priority score: vol-spike ~11.5 + stage2-quality 30 + delivery 15.6 + cap 15 + turnover 10 = **82.1** (very high)
- O5 (expiry) doesn't fire because days_since = 1 (analysed yesterday)
- No override needed — ranks in top 10 by priority
- Makes top 100 via priority ranking

**Section 6 computes:**
- fundamental_score 70, technical_score 75 (RSI 61, MACD BUY, ST BUY), early_entry 45, sentiment 55 (informed via FII 3Q trend UP), safety 72
- MoS% = +12 → +4 MoS adj
- Spike count = 2 → +4 (f_raw 70 ≥ 55, full bonus)
- EE 45 < 50 → no early bonus
- No risk flag
- Altman Z 4.1 → +3, Earn Quality HIGH → +2, ND/EBITDA 0.4 → +1, Int Coverage 8× → +2 → total +8 (capped)

**Stage A base (canonical):** 70×.35 + 75×.30 + 45×.15 + 55×.10 + 72×.10 = 24.5 + 22.5 + 6.75 + 5.5 + 7.2 = **66.45**
**+ Stage B:** 66.45 + 4 (MoS) + 4 (spike) = **74.45**
**+ Stage C forensic:** 74.45 + 8 = **82.45**
**Stage D:** LARGE threshold 60 → score 82 ≥ 60. MoS +12 > −10 → **BUY**. Distance 22.45 → **HIGH confidence**.

**Excel shows:** `BUY ●●●` with composite score 82.45.

**Section 7/8:** verdict is BUY (not AVOID), so Gemini generates the investor card for INFY. It lands in the Excel with a proper Block H analysis.

---

# PART 4 — Other v10.9 Analysis Improvements

Beyond scoring, v10.9 also improved three other analyses:

### A. Resist 2 / Support 2 — now 52-week levels

**Before v10.9:** `res2 = rolling(40).max()` — for momentum stocks near highs, R1 and R2 were identical (87% of stocks).

**After v10.9:** `res2 = rolling(252).max()` — 52-week high, genuinely distinct long-term resistance. Same for `sup2` (52-week low).

**Why this matters for traders:**
- **R1 (20d high)**: nearest swing ceiling — Target 1 territory
- **R2 (52w high)**: major supply zone — Target 2/3 and breakout watch-level

### B. Pro / FII / DII QoQ Δ — placement fix

**Before v10.9:** `_qoq()` ran in Section 3 before shareholding DB enrichment in Section 5. `stock['promoter_pct']` was still 0 at QoQ computation time, producing `delta = 0 − 62.27 = −62.27` for 81/84 stocks.

**After v10.9:** Added a **Section 5A.4 QoQ recompute block** that runs AFTER Section 5 enrichment. Uses real current values. Falls back to `"—"` when the `shareholding` table lacks ≥90-day-old history (honest display).

### C. Div Yield = 0 → "—"

**Before v10.9:** Non-dividend stocks showed `Div Yield % = 0`, indistinguishable from a rare genuine 0% yield.

**After v10.9:** Non-dividend stocks display `"—"`. The downstream failsafe at master_funnel line ~1668 was guarded against `float("—")` crash.

---

# PART 5 — What Hasn't Changed

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

### What v10.13 specifically changed (vs the funnel docs pre-v10.13)

| Aspect | Pre-v10.13 | Post-v10.13 |
|---|---|---|
| Stage 3 vol-avg lookups | ~1,500 per-row SQL round-trips | **1 batched windowed SQL** (107× faster) |
| Override rules active | O1 + O2 + O3 only | **O1 + O2 + O3 + O4 + O5** |
| Long-tail coverage | Stocks dropped off ranking were forgotten | **Re-checked ≥7 days via O5** |
| Score-deterioration detection | Dormant (column never populated) | **Active** (column populated from `latest_analysis_results`) |
| Gemini quota per run | ~100% of 100 stocks called | **~90-92%** (AVOID stocks skipped) |
| First-run behavior | — | **Identical to pre-v10.13** (empty prior map → no columns → O4/O5 no-op) |
| Stage 1/Stage 2 logic | Unchanged | Unchanged |
| Priority score formula | Unchanged | Unchanged |
| Cap mix constraints | Unchanged | Unchanged |
| Stage A/B/C/D scoring logic | Unchanged | Unchanged |

v10.9 added **one new stage** (forensic quality adj) between Stage B and Stage D. v10.13 didn't change any weights, thresholds, or formulas — only activated dormant code and batched DB calls.

---

# PART 6 — Where to Look in the Code

| Logic | File | Function / Section |
|---|---|---|
| **Stage 1 (structural filter)** | `screening/pre_screener.py` | `stage_1_filter` (lines 20–133) |
| **Stage 2 (quality score /35)** | `screening/pre_screener.py` | `stage_2_fundamental_scorer` (lines 138–228) |
| **Stage 3 (priority ranker)** | `screening/priority_ranker.py` | `calculate_priority_score` + `get_top_100_candidates` |
| **Batch vol-avg helper** (v10.13) | `database/data_bridge.py` | `get_20d_avg_vol_batch` |
| **Prior-analysis map** (v10.13) | `database/data_bridge.py` | `get_prior_analysis_map` |
| **AVOID AI-skip** (v10.13) | `master_funnel.py` | Section 7/8 pre-filter (around line 2580) |
| Composite score computation | `analysis/scoring_engine.py` | `calculate_composite_score` |
| Stage A base weights | `analysis/scoring_engine.py` | Lines 67–114 |
| Stage B adjustments | `analysis/scoring_engine.py` | Lines 116–138 |
| Stage C forensic adj (v10.9) | `analysis/scoring_engine.py` | Lines 140–205 |
| Stage D verdict derivation | `analysis/scoring_engine.py` | `_get_verdict_with_confidence` |
| Sub-score computation | `master_funnel.py` | Section 6 scoring loop |
| MoS adjustment source | `analysis/fair_value_engine.py` | `score_adjustment` in return dict (lines 200–207) |
| Altman Z calculation | `analysis/forensics_engine.py` | `calculate_altman_z` |
| Earn Quality bucketing | `analysis/forensics_engine.py` | Lines 378–388 (HIGH/LOW/MODERATE) |
| ND/EBITDA calculation | `analysis/forensics_engine.py` | Lines 340–355 |
| Int Coverage calculation | `analysis/forensics_engine.py` | Lines 357–362 |

---

# PART 7 — Where This Is Documented for End Users

Every piece of the logic above is documented in multiple user-facing layers:

1. **Score /100 cell tooltip** — complete forensic threshold table inline
2. **Verdict cell tooltip** — notes the forensic gate + references Score tooltip
3. **SCORES group-header tooltip** (merged cell at row 3) — mentions forensic adj
4. **Tooltip Reference sheet** (7th Excel tab) — auto-built from TIPS dict
5. **Glossary sheet** — entries for Support/Resist 1/2 explain 20d vs 52w
6. **CLAUDE.md Section 6** (AI context file) — full v10.9 + v10.13 specification
7. **CLAUDE.md Section 14** — all forensic constants + Stage 3 constants (`FORENSIC_ALTMAN_Z_SAFE`, `O5_DAYS_SINCE_MIN`, etc.)
8. **CLAUDE.md Section 15** — version history for v10.9, v10.11, v10.12, v10.13

A user hovering over the **Score /100** or **Verdict** column headers sees exactly the thresholds and caps — no need to dig into source code.

---

**Document version:** reflects code as of v10.13 (post-Stage-3 optimization trilogy). If you're on v10.9 / v10.10 / v10.11 / v10.12, the scoring logic (Parts 2–3) is unchanged; only Part 1's Stage 3 has v10.13-specific improvements.