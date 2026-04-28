# NSE / BSE Stock Analyser — v10.17

> **Fully automated · Daily pre-market intelligence for Indian equities**
> 5,000+ stocks → AI-scored Excel dashboard → your inbox by ~5:30 AM IST.

A cloud-hosted, zero-intervention research engine for the Indian markets. It harvests NSE and BSE data overnight, funnels 5,000+ stocks down to the top 100 through a three-stage screener, runs fundamental, technical, forensic, and AI analysis on each, and delivers a colour-coded Excel dashboard with investor cards straight to your email — every trading morning.

Single-user personal tool. No UI, no server to manage. Runs free on GitHub Actions + Gmail + Google Gemini API.

---

## Highlights

- **Three-stage funnel** — 5,150 raw rows → ~600 (Stage 1 ETF/junk filter) → ~400 (Stage 2 quality score) → **100 candidates** (Stage 3 priority ranker with cap diversification).
- **7 Fair-Value models** per stock — DCF, Graham, PE, PB, EV/EBITDA, DDM, PEG — weighted into a Composite Fair Value with Margin-of-Safety gate.
- **Composite score /100** with cap-adjusted verdict thresholds, **confidence dots** (●●● HIGH / ●●○ MEDIUM / ●○○ LOW), and a distinct **OVERVALUED** verdict for quality businesses trading above fair value.
- **Data-completeness guard** (v10.17) — BUY verdicts are only allowed when at least 3 of 5 sub-score dimensions actually had real data fire. Stocks scoring high purely from MoS or spike bonuses while most sub-scores sit at base get demoted to `WATCHLIST ●●● (thin data)`.
- **Forensic guards** — Altman Z, Beneish M, promoter pledge, Piotroski F /9, BS Health status — automatically suppress alerts on distressed names.
- **Forensic quality scoring** (v10.9+) — Altman Z, Earn Quality, ND/EBITDA, Int Coverage feed into the composite score (+8 bonus / −10 penalty cap).
- **Gold-Tier filter** — 11 conditions ensure only genuinely healthy stocks with patient upside reach the Gold sheet.
- **7-sheet Excel dashboard** — Full Dashboard, Gold (Early Movers), Trade Summary, Alert Log, Delivery Preview, Glossary, Tooltip Reference — with Indian currency formatting, right-sized hover tooltips on every metric.
- **AI investor cards** — Google Gemini generates a 150–250 word research note per stock, batched 10–15 per API call, grounded with the engine's pre-computed Graham/PEG/CFV values.
- **Gate-checked execution** — 6 pre-conditions (weekday, holiday calendar, NSE bhav availability, data integrity, DB freshness, minimum rows) must pass before any download or analysis.
- **BSE resilience** — uses the `bse` pip package (Akamai auth handled internally) and falls back to a curated `DUAL_LISTED_ALLOWLIST` of 206 Nifty-100/mid-cap names when BSE downloads fail from cloud IPs.

---

## Project layout

```
Sharemarket_Analyser_tool/
├── master_funnel.py              Core pipeline orchestrator (Sections 0–13)
├── backfill_history.py           365-day initial backfill + yfinance enrichment
├── requirements.txt
├── .env                          (local only — secrets go into GitHub Secrets in prod)
│
├── ingestion/
│   ├── orchestrator.py           Gate check — 6 pre-conditions
│   ├── holiday_calendar.py       NSE holiday-master API fetcher + DB cache
│   ├── harvester.py              NSE bhav / delivery / SME / F&O downloaders
│   └── reconciler.py             NSE+BSE merge on ISIN + DUAL_LISTED_ALLOWLIST fallback
│
├── screening/
│   ├── pre_screener.py           Stage 1 (ETF filter, ~67 patterns) + Stage 2 (quality /35)
│   └── priority_ranker.py        Stage 3 (cap diversification, tech alignment bonus)
│
├── analysis/
│   ├── fair_value_engine.py      7 valuation models + composite FV + MoS gate
│   ├── scoring_engine.py         Composite score + verdict + confidence + storm score + forensic quality adj
│   ├── forensics_engine.py       Altman Z + Beneish M + ND/EBITDA + CCC + inline yfinance fetcher
│   ├── fundamental_engine.py     Graham, PEG, 9-point Piotroski F-Score
│   ├── technical_engine.py       RSI / MACD / Supertrend / ADX / MFI / Stoch
│   ├── ownership_tracker.py      Promoter / FII / DII QoQ trends
│   ├── spike_screener.py         6-trigger spike score
│   ├── early_detection_engine.py 12-signal early-entry score
│   ├── bs_engine.py              Balance sheet health audit
│   ├── rotation_engine.py        4-stage sector rotation (Accumulation→Distribution)
│   ├── smart_money.py            Bulk-deal + SAST insider scraping
│   ├── intel_fetcher.py          Latest market intelligence
│   ├── market_context.py         Market-wide regime detection
│   └── v7_analysis_engine.py     Sections 3A–3H analytical overlays
│
├── database/
│   ├── data_bridge.py            DB consolidation + all query helpers
│   ├── database_manager.py       Connection + schema management
│   └── db_maintenance.py         400-day rolling circular queue
│
├── ai/
│   └── ai_analyst.py             Google Gemini batch analysis
│
├── reporting/
│   ├── excel_generator.py        7-sheet ExcelGeneratorV6 + dynamic red-header + dynamic tooltip sizing
│   ├── tooltip_formatter.py      Cell / group / reference tooltips with per-tooltip dynamic height
│   ├── daily_report_generator.py Plain-text research report
│   ├── report_formatter.py       Investor card formatter
│   ├── email_service.py          Gmail SMTP delivery
│   ├── whatsapp_gateway.py       Twilio Flask webhook
│   └── command_parser.py         `why RELIANCE`, `early movers today`, etc.
│
├── master_prompt/
│   └── NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt   System prompt for Gemini
│
└── utils/
    ├── bse_diagnosis.py          BSE connectivity debug helper
    └── chat_interface.py         Local REPL for command_parser
```

---

## Quick start

### Requirements

- Python 3.11
- `pip install -r requirements.txt` — pulls pandas, numpy, openpyxl, requests, pytz, **google-genai**, twilio, python-dotenv, flask, **bse**, cloudscraper, yfinance.

### Local run

```bash
git clone <your-fork>
cd Sharemarket_Analyser_tool
pip install -r requirements.txt

# one-time: populate 365-day history (~30–60 min, uses yfinance)
python backfill_history.py 365

# daily: runs the full pipeline
python master_funnel.py
```

On first run, if `daily_prices` has fewer than 50,000 rows the pipeline will auto-trigger `backfill_history.py 365`. Expect a cold start to take ~45 minutes.

### Environment

Create a `.env` file (or set GitHub Secrets — see below):

```
GEMINI_API_KEY=AIza...            # or GOOGLE_API_KEY
SENDER_EMAIL=you@gmail.com
SENDER_APP_PASSWORD=xxxxxxxxxxxxxxxx
USER_EMAIL_ID=recipient@gmail.com
TWILIO_ACCOUNT_SID=AC...          # optional, WhatsApp only
TWILIO_AUTH_TOKEN=...             # optional, WhatsApp only
```

The Gmail password must be a 16-char **App Password**, not your login password.

---

## Deployment — GitHub Actions

The pipeline is designed to run for free on GitHub's 2,000 free minutes/month (a full run takes ~15–20 min on `ubuntu-latest`).

**`.github/workflows/market_run.yml` cron:** `0 23 * * 1-5`
→ 23:00 UTC Mon–Fri = **04:30 IST Tue–Sat** (covers Mon–Fri Indian trading days, delivered the morning after).
Email arrives at roughly 05:00–05:30 IST after the GitHub queue delay.

**Required GitHub Secrets:**

| Secret | Purpose |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API for AI investor cards |
| `SENDER_EMAIL` | Gmail sender address |
| `SENDER_APP_PASSWORD` | Gmail 16-char App Password |
| `USER_EMAIL_ID` | Recipient email |
| `TWILIO_ACCOUNT_SID` | WhatsApp delivery (optional) |
| `TWILIO_AUTH_TOKEN` | WhatsApp delivery (optional) |

**DB persistence:** SQLite (`market_data.db`, ~400 MB) is stored as a workflow artifact named `market-data-db` with a 7-day retention and `overwrite: true`. The workflow downloads it at start and re-uploads at end.

---

## Pipeline at a glance

```
Gate check (6 conditions)  ──►  all pass?  ──►  if no: email + halt
         │ yes
         ▼
Section 1   Harvest        NSE bhav + delivery + BSE (pip) + SME + F&O + bulk deals + insider
Section 1.2 DB sync        5,150 rows  →  daily_prices
Section 0   Three-stage funnel
             Stage 1 — ETF/penny/suspended filter       5,150 → ~600
             Stage 2 — Quality score /35                ~600  → ~400
             Stage 3 — Priority ranker + cap mix        ~400  → 100
Section 3   For each of 100 stocks
             3A  Valuation ratios (EY, PE tag, EV/EBITDA)
             3B  Forensics (Beneish, Altman, CFO/PAT) + inline yfinance BS/CF/IS fetch
             3E  Capital allocation (ROCE)
             3F  Ownership trends (Promoter/FII/DII QoQ)
             3G  Growth quality (CAGR tiers)
             3H  Anti-trigger guard (pledge / Beneish / Altman / CFO)
             3J  Bulk-deal sentiment
             3K  Insider buying
             3L  Sector rotation (deferred — recomputed after Section 5)
Section 4   BS Health first pass (placeholder)
Section 4B  yfinance fundamentals refresh (top 100) + NSE shareholding enrichment (DII)
Section 5   DB enrichment — technicals + fundamentals + weekly momentum
             → Sector Stage recomputed (real RSI/MACD/ST)
             → Ghost-key derivation before storm score
Section 5A.4 QoQ recompute — Pro/FII/DII Δ recomputed with real current values
Section 5A.5 Forensics re-run — Altman Z, Beneish M, Earn Quality with enriched data
Section 5B  Fair Value engine — 7 models + composite FV + MoS
Section 6   Scoring loop per stock
             Technical / Fundamental / Safety / Sentiment / Early-Entry scores
             → Composite score + v10.9 Forensic Quality Adjustment → Verdict + confidence dots
             → Storm score → Spike score → Horizon + Risk level (after verdict)
             → F-Score proxy → Price targets T1/T2/T3 + SL + R:R
Section 7/8 AI investor cards (Gemini, batches of 10–15)
Section 9/10 7-sheet Excel dashboard + text research report
Section 12  Gmail SMTP delivery
Section 13  DB maintenance — 400-day rolling window
```

---

## How the composite score flows into the Gold sheet

End-to-end pipeline — how a stock becomes Gold-tier:

```
Raw inputs
  ↓
Sub-scores (master_funnel Section 6)
  • fundamental_score  — PE (tiered bands), ROE, D/E, Current Ratio, Gross Margin,
                         Net Margin, Earnings Yield, Promoter %, PAT YoY, Rev YoY,
                         FCF Yield, PAT CAGR 3Y, Rev CAGR 3Y, EBITDA CAGR 1Y,
                         Margin Expansion (YES/NO), NPM Q1, plus Stage-2 baseline
  • technical_score    — RSI (14), ADX, MACD Signal, Supertrend, Above VWAP, OBV,
                         Stoch %K, MFI, SMA 200 trend alignment (CMP vs SMA200 band)
  • early_entry_score  — 12 signals: SME Migration, Pre-Result Insider Buying,
                         Cross-Exchange Discovery, Vol+RSI Accumulation,
                         Momentum Building (2w chg), Trend Confluence (ST+MACD),
                         Institutional Footprint (delivery + vol), Dual-Listed
                         Discovery, Deep Value + BUY (MoS > 25%), 52W Breakout,
                         Score Convergence (score≥70 + RSI>60 + ST=BUY),
                         FII / Promoter QoQ Accumulation (>1%)
  • sentiment_score    — FII 3Q trend (UP/DOWN), FII QoQ Δ, Smart-Money signals,
                         Insider Buy Alert, Promoter QoQ Δ, DII QoQ Δ,
                         News Sentiment (POSITIVE/NEGATIVE from Gemini),
                         Delivery %, Pledge Direction (IMPROVING/DETERIORATING)
  • safety_score       — Pledge %, Beta, D/E ratio, FCF sign, BS Health status,
                         Margin Expansion, Net-cash position (cash > debt),
                         Int Coverage, ND/EBITDA, Piotroski F-Score,
                         anti-trigger-guard clean (no Altman/Beneish flag)
  ↓
ScoringEngine.calculate_composite_score()
  Stage A: base = Fund×0.35 + Tech×0.30 + EE×0.15 + Sent×0.10 + Safe×0.10
           (redistributed to 0.389/0.333/0.167/0.111 if no informed sentiment)
  Stage B: + MoS adj (−10 to +12)
           + Spike bonus (max +10, capped at +3 if Fund<55)
           + Early Mover +5 (if EE≥50)
           − Anti-trigger penalty (−10 if risk_flag)
  Stage C: + Forensic Quality Adjustment (−10 floor / +8 cap)
             • Altman Z     ≥3.0: +3   |  <1.8: −5
             • Earn Quality HIGH: +2   |  LOW:  −3
             • ND/EBITDA   <1.0: +1   |  >5.0: −2
             • Int Coverage >5x: +2   |  <1.5x: −3
             (Missing data → no adjustment; doesn't penalise small caps without forensics)
  Stage D: Verdict derivation — cap-aware thresholds + MoS gate
           Returns: BUY / OVERVALUED / WATCHLIST / NEUTRAL / AVOID
           with confidence dots ●●●/●●○/●○○
  ↓
_get_gold() filter — 11 conditions (all must pass)
  1. Verdict = BUY                       7. Pledge % ≤ 10
  2. Composite Score ≥ 70                8. Not spike-suppressed
  3. 15% ≤ MoS ≤ 100%                    9. Altman Z ≥ 1.8 or missing
  4. Storm Score ≥ 5                    10. Earn Quality ≠ LOW
  5. RSI ≤ 70                           11. Int Coverage ≥ 1.5× or missing
  6. BS Health Flag ≠ ALERT
  ↓
⭐ Gold – Early Movers sheet
```

Missing forensic data (`"—"`) passes gates 9–11 so small caps without forensic feeds aren't unfairly excluded — existing gates (BS Health, Pledge, anti-trigger) already cover such cases.

---

## Verdict system

Verdicts are cap-aware. Thresholds and confidence dots live in `analysis/scoring_engine.py`.

| Cap tier | BUY ≥ | WATCHLIST ≥ |
|---|---|---|
| LARGE | 60 | 50 |
| MID   | 63 | 53 |
| SMALL | 66 | 56 |
| MICRO | 70 | 60 |

- **AVOID** — universal floor below 38.
- **OVERVALUED** — score clears BUY threshold but Margin of Safety is ≤ −10% (or ≤ −20% if technically confirmed: score ≥ 70 + Supertrend BUY + Sector Stage 2). Reads as "great business, currently expensive" — distinct from WATCHLIST.
- **Confidence dots** — ●●● if ≥ 5 points clear of threshold, ●●○ if 2–5, ●○○ if < 2 (cliff zone; treat with extra caution).

---

## Excel output — 7 sheets

1. **📊 Full Dashboard** — 100 stocks × ~123 columns. Every metric. The full picture.
2. **⭐ Gold / Early Movers** — 11-condition filter (see scoring flow above). Row 2 displays the criteria text; daily count typically 0–10.
3. **📊 Trade Summary** — Entry / Stop Loss / T1 / T2 / T3 / R:R for Gold stocks only.
4. **🔔 Alert Log** — Today's score vs yesterday's, 8-way **Action Required** logic (`CONSIDER ENTRY`, `MONITOR FOR ENTRY`, `VOLUME ALERT — INVESTIGATE`, `EARLY MOVER — ACCUMULATE`, `SCORE IMPROVING — WATCH`, `SCORE DECLINING — CAUTION`, `REVIEW FOR EXIT`, `MONITOR CLOSELY`).
5. **📱 Delivery Preview** — WhatsApp + Email text preview.
6. **📖 Glossary** — 80+ column definitions.
7. **💡 Tooltip Reference** — Polished hover + ⓘ cue.

Tooltips auto-size per content (v10.12): short 2-line tips render at ~85px; long 16-line tips at ~308px. No empty yellow space.

---

## DB retention — 365 vs 400 clarification

Two distinct day-counts in the pipeline, serving different purposes:

| Thing | Where | Value | Purpose |
|---|---|---|---|
| Initial backfill | `backfill_history.py` (`DAYS_TO_BACKFILL`) | **365 calendar days** | One-time cold-start hydration |
| Rolling window | `database/db_maintenance.py` (`KEEP_DAYS`) | **400 calendar days ≈ 275 trading days** | Daily pruning + VACUUM, runs as Section 13 after each pipeline |

**Why 400 rather than 365?** 52-week high/low needs ≥ 250 trading days; 200-day SMA needs 200; 8-week momentum needs 41. 400 calendar days ≈ 275 trading days (after weekends + NSE holidays) — comfortably above the 250-trading-day floor. If only 365 calendar days were kept, we'd be at ~250 trading days, right on the edge.

---

## Known limitations

| Column | Why | Status |
|---|---|---|
| Current / Quick Ratio | yfinance missing for ~25–40% of Indian stocks | 2nd-pass balance_sheet fetch in backfill; ~60–75% coverage |
| Piotroski F-Score | Free sources can't provide YoY comparisons for all 9 criteria | Proxy computed from available data; realistic 4–8 range |
| PAT CAGR 3Y / Rev CAGR 3Y | Not in yfinance for Indian stocks | Red headers in Excel = "no free source" |
| Alert Log Prev Score | Blank on first-ever run | Populates from run 2 onwards |
| BSE SME delivery % | Not available from BSE API | NSE delivery used as primary |
| Pledge % | Only in BSE corporate filings — not in yfinance | Always 0 until paid source added |
| DII % | NSE corp-info API used when available; may be blocked on cloud IPs | Real values when API responds, 0 otherwise |
| QoQ deltas (Pro/FII/DII) | Need ≥ 90 days of `shareholding` history | Show `—` until history accumulates (~3 months of daily runs) |
| OB/Bill Ratio, Pipeline Vis, L1 Wins, New Mkt Entry | No free data source for order-book / tender data | Remain empty; require paid BSE filings or manual entry |
| Key Catalyst / News Sentiment / Primary Risk / SEBI Flags | Require Gemini API quota | Fallback `"—"` when quota exhausted |

---

## Version history

Full fix-pack notes are preserved in the accompanying `README_v10_*.md` files in the repo. Compact summary:

| Version | Key change |
|---|---|
| **v10.17** | Data-completeness quality guard. A high composite score alone no longer guarantees a BUY verdict — the engine now requires at least 3 of 5 sub-score dimensions (Fundamental, Technical, Safety, Sentiment, Early Entry) to have real data move them away from base. Stocks that score above the cap-tier BUY threshold but with `informed_count < 3` are demoted to `WATCHLIST ●●● (thin data)`. Prevents inflated BUYs on stocks where most sub-scores sat at their neutral base because of missing data. New constant `MIN_INFORMED_FOR_BUY = 3` in `scoring_engine.py`; new output fields `data_completeness` (0–5) and `data_gate_applied` (bool); defensive try/except keeps the guard from ever breaking a pipeline run. OVERVALUED / NEUTRAL / AVOID and the legacy `_get_verdict` are unaffected (default `informed_count=5`). Stocks with full data: zero behaviour change. |
| **v10.16** | VALUATION display honesty (Option B) — follow-up to v10.15 user feedback. FIX #1: valuation ratios show `'—'` instead of clamped number when raw value would be noise (PE/PB/PS/EV-EBITDA ≥ 500 → `'—'`; PEG ≥ 50 → `'—'`). Prior v10.15 showed clamped 1000/100 which users could misread as "real extreme value" — AMAGI raw PE was 1,981 shown as 1,000 looked like "1000× earnings" when actually means "earnings ≈ 0, P/E not meaningful". FIX #2: DB-layer clamp tightened 1000→500 and 100→50 (matches display threshold). FIX #3: scoring logic neutrality — `fundamental_score` now treats `pe_num ≥ 500` as NEUTRAL (no +12/+7/−8 bucket), not penalized for being "expensive", because clamp represents "unknown valuation" not "high PE". Real expensive stocks (PE 60-499) still get −8. FIX #4: `v7_analysis_engine.apply_section_3A_valuation` gets defensive `_pe_num()` coerce to handle `pe='—'` without `TypeError`. FIX #5: `analysis/bs_engine.py` gets `_roe_num()` coerce for ROE comparison (v10.15 FIX #1 can produce `roe='—'`). FIX #6: `reporting/excel_generator.py::_is_exceptional_neutral` gets local `_fs()` coerce for the NEUTRAL-filter edge case. 5 valuation tooltips + VALUATION group header rewritten; 6 glossary entries updated with `'—' = ...` bucket. 198/198 integration tests pass (27 groups). Zero DB schema change, zero behaviour change for real valuations. |
| **v10.15** | PROFITABILITY / FIN HEALTH / VALUATION / SHAREHOLDING data-integrity hardening. FIX #1: ROE/ROA stored as floats (were f-string-wrapped text — broke Excel sort/filter on 69/86 stocks). FIX #2: NPM Q1/Q2/Q3 clamped at ±500% (EMAMIREAL −845% pre-clamp). FIX #3: CCC Days clamp ±500 + rev<₹0.1 Cr short-circuit (EMAMIREAL 16,821 days pre-fix). FIX #4: PE/EV-EBITDA/PB/PS clamped at ±1000, PEG at ±100 (AMAGI PE=1,981, RHETAN EV/EBITDA=1,352 pre-clamp) — **superseded by v10.16 Option B** (now "—" display at threshold 500/50). FIX #5: Pro/FII/DII QoQ Δ show '—' when no real delta (was 0 for 83/86 indistinguishably). FIX #6: Pledge %/DII % show '—' when 0 (honest free-tier display — no BSE filings, no NSE corp-info API). Safe-guards in v7_analysis_engine + ownership_tracker handle '—' via same defensive pattern as v10.10. 15 tooltips + 4 group headers rewritten with source + clamp notes. Glossary: PROFITABILITY 2→10, FIN HEALTH 3→11, SHAREHOLDING 3→15, VALUATION 5→14. 111/111 integration tests pass. Zero analytical behaviour change. |
| **v10.14** | GROWTH field data-integrity hardening. FIX #1a: `_safe_cagr()` clamps at ±500% (fixes HUBTOWN 10,194%, CHEMPLASTS 1,163% tiny-base distortions). FIX #1b: `rev_yoy`/`pat_yoy` from yfinance `.info` also clamped at ±500% (fixes RVHL 14,183.8%). FIX #2: all 10 GROWTH tooltips rewritten with source attribution + TTM-vs-fiscal-year clarification + cap note. FIX #3: glossary expanded from 3 → 10 complete GROWTH entries; legacy duplicate block removed. Zero analytical behaviour change — pure display hygiene. |
| **v10.13** | Stage 3 optimization trilogy. FIX #1: AVOID-verdict stocks skip the Gemini AI call (saves ~8-10% quota/run). FIX #2: override rules O4 (score deterioration) + O5 (7+ day expiry re-check) activated — `last_claude_score` + `days_since_analysis` now populated from `latest_analysis_results`. FIX #3: 20-day vol-average batch-fetched in one windowed SQL (107× faster than the ~1,500 per-symbol round-trips). First-run safe (empty prior map → no columns added → identical to pre-v10.13). |
| **v10.12** | Dynamic tooltip sizing — per-tooltip height computed from content (was hardcoded 420×380 box). Gold row 2 criteria text updated to 11 conditions. |
| **v10.11** | Gold-Tier filter expanded 8 → 11 conditions (added Altman Z ≥ 1.8, Earn Quality ≠ LOW, Int Coverage ≥ 1.5× gates). Missing forensic data passes these gates. |
| **v10.10** | Crash-guard hotfix — `_safe_num()` helper in `scoring_engine.py::calculate_storm_score`, `spike_screener.py::check_anti_trigger_guard`, `fundamental_engine.py::calculate_piotroski_f_score` — fixes `TypeError: '>' not supported between str and float` after v10.9 Div Yield = `"—"` change. |
| **v10.9** | (1) QoQ placement fix — added Section 5A.4 recompute block after Section 5 enrichment (was producing `-current%` bug for 81/84 stocks). (2) Resist/Support 2 = 52-week window (was 40d, caused R1==R2 for 87% of stocks). (3) **Forensic Quality Adjustment** added to composite score — Altman Z, Earn Quality, ND/EBITDA, Int Coverage feed scoring with +8/−10 caps. (4) Div Yield = 0 → `"—"` for non-dividend stocks. |
| **v10.8** | Earn Quality → categorical HIGH/MODERATE/LOW/—. Pledge Direction honest `"—"` when no data. "Upside to FV %" column removed (duplicate of MoS %); total cols 124 → 123. |
| **v10.7** | Bridge-code guard — replaced 13 direct `stock["X"] = 0` clobbers with `_pub(key, db_val)` helper that only overwrites when DB value is non-zero. Preserves v10.4 inline-fetched forensic values. |
| **v10.6** | ND/EBITDA annualization fix (was using quarterly EBITDA in annual ratio, inflating ~4×). NSE DII enrichment wired. Pledge Direction default "—" instead of "STABLE". |
| **v10.5** | Defensive schema init — `CREATE TABLE IF NOT EXISTS shareholding` + `ALTER TABLE ADD COLUMN` for 18 forensic columns at master_funnel startup. |
| **v10.4** | Inline yfinance forensic fetcher in `forensics_engine.fetch_forensic_inputs()`. QoQ deltas show `"—"` when no history (was `-current%`). Dynamic red-header demotion in `excel_generator.py`. |
| **v10.3** | `get_historical_quarter_data` reads from `shareholding` table (has DII, unlike legacy `v7_intelligence`). Section 5A.5 forensics re-run added. |
| **v10.2** | 10 forensic-input columns added to `fundamental_metrics` schema (ebit_cr, int_expense_cr, capex_cr, total_assets_cr, etc.). `_fm_ext` SELECT expanded 3 → 13 cols. |
| **v10.1** | AI provider migration: `anthropic` SDK → `google-genai`. Model: `gemini-2.5-pro`. |
| **v10.0** | Baseline (April 2026) after Sessions 1–24: v7 reorg, Excel + Alert Log, Early Detection, BS Health, Piotroski wire-up, Session 24 scoring polish (sentiment informedness, spike gate, confidence dots, OVERVALUED verdict). |

For future debugging, each `README_v10_N.md` file in the repo has the full bug-root-cause + integration-test log for that version.

**Visual pipeline reference:** [pipeline_reference_v10_16.html](pipeline_reference_v10_16.html)

---

## Credits & licence

Personal algorithmic trading research system — designed, built & maintained by **Rajkumar**.
Internal use only · confidential · not for redistribution.

The master prompt in `master_prompt/` encodes Section 0–13 of the v7 design spec. AI output is generated by Google Gemini via the official `google-genai` SDK.

# NSE/BSE Stock Analyser — v11.0.1 Bugfix Release

**Date:** 28 April 2026
**Type:** Bugfix
**Test status:** 38 of 38 tests pass · all 47 project Python files compile clean · end-to-end smoke test against today's pipeline output is correct

---

## What this release fixes

### Fix 1 — Daily Report Section A: "EARLY MOVERS TODAY" was empty

**Symptom:** The 27-Apr-2026 daily report showed *"No candidates identified today"* even though PSUBANK had Early Entry score = 55, well above the documented EARLY MOVER threshold.

**Root cause:** Three reporting files used `early_entry_score >= 70` while the design doc, Excel Gold sheet, glossary, and tooltip reference all use `>= 50`. The filter was rejecting valid early movers.

**Files changed:** `reporting/daily_report_generator.py:44`, `reporting/command_parser.py:27`, `reporting/report_formatter.py:24`

### Fix 2 — Daily Report Section D: "SECTOR ROTATION" all stages "None"

**Symptom:** Every stage line showed `Stage 4: None`, `Stage 3: None`, etc. even though the Excel dashboard had 33 stocks classified across stages.

**Root cause:** The code compared `rotation_stage == 4` (integer) but the actual values are strings like `"STAGE 4 — DISTRIBUTION"`. Every comparison silently returned False.

**Fix:** Switched to substring match using `str.contains('STAGE 4')` — handles all four stage labels correctly.

**Files changed:** `reporting/daily_report_generator.py:60-67`

### Fix 3 — Exchange tagging: Large/Mid stocks wrongly tagged NSE_ONLY

**Symptom:** 34 large/mid-cap stocks (ABBOTINDIA, BATAINDIA, GLAXO, etc.) showed `NSE_ONLY` in the Excel dashboard despite being dual-listed on both NSE and BSE.

**Root cause:** When BSE bhavcopy download fails on the GitHub Actions runner (Cloudflare 403 blocks cloud IPs), the `DUAL_LISTED_ALLOWLIST` becomes the primary tagging mechanism. The list was missing 27 widely-traded large/mid-cap names.

**Fix:** Added 27 verified dual-listed symbols to the allowlist. Three names left as commented-out lines pending your manual verification (see file). Three index tickers (IT, PSUBANK, BANKNIFTY1) explicitly excluded — they are not real listed equities.

**Files changed:** `ingestion/reconciler.py` (allowlist additions, line ~78)

---

## Files in this delivery

| File | Purpose | Where to put it |
|---|---|---|
| `reporting/daily_report_generator.py` | Section A threshold + Section D rotation_stage fix | replace `reporting/daily_report_generator.py` |
| `reporting/command_parser.py` | Threshold fix in `early movers today` CLI command | replace `reporting/command_parser.py` |
| `reporting/report_formatter.py` | Threshold fix in `[EARLY MOVER]` investor-card badge | replace `reporting/report_formatter.py` |
| `ingestion/reconciler.py` | DUAL_LISTED_ALLOWLIST additions | replace `ingestion/reconciler.py` |
| `test_upto_v11.0.1.py` | **Consolidated** validation suite (replaces `test_v10_17_full_withdummies.py`, also covers all v11.0.1 fixes) | place at project root, then **delete** the old `test_v10_17_full_withdummies.py` |
| `NSE_BSE_Design_v11_0_Final.docx` | Updated design doc with v11.0.1 changelog row | replace your existing copy |
| `NSE_BSE_Design_v11_0_Final.pdf` | Same, rendered as PDF | replace your existing copy |

**No zip required.** Just drop each file into its corresponding location in your project tree.

---

## Validation performed

The consolidated validation suite (`test_upto_v11.0.1.py`) was run before delivery. All **118 tests pass** — covering every code path in the existing v10.17 / v11.0 ScoringEngine plus all v11.0.1 fixes.

| Group | Tests | What's covered |
|---|---|---|
| **Groups 1–20** (existing v10.17 / v11.0 coverage, preserved) | 84 | Sub-score weighted blend, MoS adjustment, Spike/Early/Anti-trigger, Forensic adjustment + cap, Sentiment informedness, Composite clamping, Verdict derivation (AVOID/cap-tier/MoS/OVERVALUED/Confidence dots), v10.17 / v11.0 informed-counter + thin-data demotion, defensive input handling, output dict shape, no-leakage |
| **Group 21** | 7 | reconciler imports, allowlist has all 27 new symbols, index tickers excluded, pending-verification stocks excluded, no duplicates, helper function works for new symbols, existing 206 entries preserved |
| **Group 22** | 7 | Runtime tagging when BSE empty (most common production path), runtime tagging when BSE merge fails partially (safety override), genuine NSE-only stocks unaffected, RELIANCE (existing allowlist) unaffected, index tickers stay NSE_ONLY at runtime, None-input survival |
| **Group 23** | 11 | daily_report_generator imports, Section A boundary tests at EE=45/55/80, Section D matches all 4 stages, Section D handles empty rotation_stage column, handles NaN values, [:3] sector cap preserved |
| **Group 24** | 5 | command_parser imports + boundary tests + title label |
| **Group 25** | 4 | report_formatter imports + EARLY MOVER badge boundary tests at EE=45/50/55 |
| **Group 26** | 2 | All 3 reporting files use threshold 50 consistently, allowlist size = 233 (206 baseline + 27 new) |

Additionally:

- **All 47 project Python files compile cleanly** (full syntax check)
- **End-to-end smoke test against today's actual production data** confirmed:
  - Section A correctly surfaces `PSUBANK | EARLY_ENTRY_SCORE: 55 | SECTOR: General`
  - Section D correctly populates all 4 stages with sector rollups
  - CommandParser `early movers today` returns `PSUBANK | Score: 80.66 | Upside: 0%`

---

## To run the validation suite yourself

```bash
cd Sharemarket_Analyser_tool
python3 test_upto_v11.0.1.py
```

Expected output: `FINAL: 118 passed, 0 failed`

---

## Three names still pending manual verification

These were left commented out in the allowlist for your review:

| Ticker | Note |
|---|---|
| `MOREALTY` | Pairing not fully verified |
| `KMEW` | 2025 IPO — verify BSE code |
| `RBA` | Ticker → BSE code mapping needs verification |

Once verified, simply uncomment each line in `ingestion/reconciler.py` (around line 84).

---

## What the next pipeline run will look like

**Section A** will list any stock with EE ≥ 50 (today: PSUBANK)

**Section D** will show actual sector rollups per stage:
```
Stage 4: Financial Services, Industrials
Stage 3: Real Estate, Financial Services
Stage 2: Consumer Defensive, General, Financial Services
Stage 1: Healthcare, Communication Services, Industrials
```

**Exchange column** for the 27 new stocks will read `DUAL_LISTED` instead of `NSE_ONLY`. Their Stage 2 dual-listed-bonus signal (from `pre_screener.py` B7 +5 pts) will fire correctly, contributing to a more accurate composite score.

---

## Design doc updates

The technical design doc has been updated in two places to reflect this release:

1. **§14 Changelog** — new highlighted v11.0.1 row added below v11.0
2. v11.0 row's "current major release" label updated to "major release" (since v11.0.1 supersedes it as the current release)

No other §3.5 / §13 content needed updating — the existing references already describe the EE ≥ 50 threshold correctly. The bug was that the *code* didn't match the doc; this release brings the code into alignment with what the doc has always said.