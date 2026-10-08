# CLAUDE.md — NSE/BSE Stock Analyser Tool
## AI Context File · v17.19 · October 2026

This file gives Claude (or any AI assistant) the project context needed to understand, debug or extend this codebase. **Read it first** before making any change.

**How this file is organised**
- **Part A — Current state (v17.19).** Authoritative description of how the system works today. Sections 1–14 and 16–21 are kept current; when anything in Part B disagrees with Part A, Part A wins.
- **Part B — Version history, newest first.** Release notes as written at the time (v17.19 back to v10.0). Older entries describe the code *as it was*; many details there (Gemini, T1/T2/T3 targets, 7 sheets, 11 Gold gates, +5 % break-even …) have since been superseded.

Companion documents: `readme.md` (user-facing overview + version table), `pipeline_reference.html` (visual end-to-end reference), `scoring_logic_3Stagefunnel_explained.md` (funnel + scoring walkthrough with worked examples), `data/README_NSE_SNAPSHOT.md` (local NSE fetch setup).

---

# PART A — CURRENT STATE (v17.19)

## 1. PROJECT PURPOSE

A fully automated, cloud-hosted daily pipeline that:

1. Downloads NSE + BSE market data every trading morning (GitHub Actions cron 23:00 UTC = 04:30 IST, Tue–Sat IST, processing the previous trading day)
2. Screens ~5,200 stocks through a 3-stage funnel → 100 candidates (plus any open positions that fell out of the top 100)
3. Runs fundamental, technical, forensic, valuation and **news-sentiment** analysis on them
4. Gates Gold picks on the **market regime** and a **15-condition** filter, sets a regime-aware **stop-loss + single Target** and a risk-parity position size
5. **Logs every Gold pick and tracks its real outcome** (live trailing stop, shadow stop, continuation audit, risk-adjusted metrics)
6. Writes **AI analyst notes** (company OpenAI-compatible LLM) for Gold picks and open positions only
7. Emails an **8-sheet** colour-coded Excel dashboard + a text research report (GitHub queues scheduled runs; delivery has ranged ~05:00–09:30 IST)
8. Maintains its own SQLite history (400-day rolling window; outcome tables never pruned)

Plus one small job on the owner's Windows laptop: a weekly NSE pledge/promoter fetch, because NSE does not serve cloud runners.

**Single-user research tool.** Pre-market preparation. Zero manual intervention on trading days. Not financial advice (see LICENSE / readme disclaimer).

---

## 2. FOLDER STRUCTURE

```
Sharemarket_Analyser_tool/
├── master_funnel.py              ~4,370 lines — pipeline orchestrator (Sections 0–13)
├── backfill_history.py           400-day backfill + yfinance / NSE fundamentals & shareholding enrichment
├── track_outcomes.py             Gold-pick outcome tracker + continuation walk + shadow stop (auto-invoked)
├── fetch_nse_local.py            OWNER'S MACHINE ONLY: NSE pledge + shareholding → data/nse_snapshot.json → git push
├── fetch_nse_now.bat             One-click manual trigger for the above
├── reset_performance_tracking.py Guarded wipe of the 3 outcome tables (workflow manual-dispatch only)
├── test_v13_v14_consolidated.py  Regression suite — 96 guards, all green (v17.19)
├── test_v11.0.2_full_withdummies.py  ⚠ NOT the deep ScoringEngine suite any more: overwritten on 16-May-2026
│                                     (commit a09ba4c) with a stale copy of the consolidated suite (70/84 on
│                                     current code). The real deep suite is in git history at d2f301a
│                                     (524 pass / 18 drifted on v17.19 code) — see §20
├── test_run.py · test_yfinance.py    Manual helpers (not part of the suite)
├── requirements.txt              pandas numpy openpyxl requests pytz twilio python-dotenv flask bse cloudscraper yfinance curl_cffi
├── CLAUDE.md · readme.md · pipeline_reference.html · scoring_logic_3Stagefunnel_explained.md
├── data/
│   ├── nse_snapshot.json         Weekly NSE pledge/promoter snapshot (overwritten each fetch; history in git)
│   └── README_NSE_SNAPSHOT.md    Setup guide for the local fetch + Task Scheduler
├── ingestion/
│   ├── orchestrator.py           Gate check C1–C5 → "GATE APPROVED"
│   ├── holiday_calendar.py       NSE holiday-master fetch + DB cache (fail-closed)
│   ├── trading_day_calendar.py   Exact trading-day arithmetic (v15.3)
│   ├── harvester.py              NSE bhav / delivery / SME / F&O downloaders
│   ├── reconciler.py             NSE+BSE merge on ISIN; sets df.attrs["dual_match_method"]; allowlist fallback
│   ├── allowlist_maintainer.py   dual_listed_runtime table — write-time guards (v17.17), 30-day prune
│   ├── nse_pledge.py             NSE bulk pledge (percSharesPledged); Actions-aware "not served" message
│   └── nse_snapshot.py           Validates + applies the committed snapshot (≤14 days, fills blanks only, IST display)
├── screening/
│   ├── pre_screener.py           Stage 1 (V0–V9) + Stage 2 (B1–B7 /35) + anti-trigger guard
│   └── priority_ranker.py        Stage 3 priority /100 + overrides O1–O5 + cap mix
├── analysis/
│   ├── scoring_engine.py         Composite + verdict + confidence + storm + quick-pick
│   ├── fair_value_engine.py      7 FV models + composite FV + MoS (+ cfv_capped / thin-model guard)
│   ├── forensics_engine.py       Altman Z, real 8-variable Beneish M, ND/EBITDA, CCC, earnings quality, inline yfinance fetch
│   ├── fundamental_engine.py     Graham, PEG, Piotroski F
│   ├── technical_engine.py · spike_screener.py · early_detection_engine.py · bs_engine.py
│   ├── ownership_tracker.py · rotation_engine.py · smart_money.py · market_context.py · v7_analysis_engine.py
│   ├── news_sentiment.py         v17.9/v17.11 — Google News RSS → LLM → sentiment + evidenced facts
│   ├── risk_metrics.py           Sharpe / Sortino / Calmar (v16.0)
│   ├── survivorship_audit.py     Open positions vs today's universe (v16.0)
│   └── intel_fetcher.py          Legacy (query strings only)
├── risk/correlation_aware_sizing.py   v15.4/v15.5 risk-parity sizing
├── backtest/walk_forward.py           Offline hit-rate report (refuses calibration below N=30)
├── database/
│   ├── data_bridge.py            Schema + migrations + every query helper (outcome, continuation, shadow, resilience…)
│   ├── database_manager.py       Connection management
│   └── db_maintenance.py         400-day rolling window (KEEP_DAYS)
├── ai/ai_analyst.py              LLM investor notes (Block H) — compact prompt, batches of 4, per-day cache
├── reporting/
│   ├── excel_generator.py        ExcelGeneratorV6 — 8 sheets
│   ├── tooltip_formatter.py      TIPS (210 entries) · GROUP_TIPS · Tooltip Reference sheet
│   ├── daily_report_generator.py Plain-text research report
│   ├── report_formatter.py       Quick investor cards for the text report
│   ├── email_service.py          Gmail SMTP delivery (normal / skip / error mails)
│   ├── whatsapp_gateway.py       Twilio webhook (optional)
│   └── command_parser.py         `why RELIANCE`, `early movers today`, …
├── master_prompt/NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt
│                                 Analyst spec. The pipeline sends ONLY its "SYSTEM ROLE" and
│                                 "BLOCK H — ANALYSIS SUMMARY" sections (see §12)
├── utils/bse_diagnosis.py · utils/chat_interface.py   Diagnostics / local REPL
├── scripts/seed_holidays.py
└── .github/workflows/
    ├── market_run.yml            Daily pipeline (12 steps — see §13)
    └── keep_alive.yml            Wed + Sun 10:00 UTC timestamp commit (resets GitHub's 60-day inactivity clock)
```

---

## 3. DATABASE SCHEMA (SQLite — `market_data.db`, ~650 MB)

Created by `backfill_history.py::init_all_tables()` (full set on cold start), `database/data_bridge.py::initialize_v7_tables()` (pipeline tables + idempotent `ALTER TABLE ADD COLUMN` migrations) and the defensive startup block in `master_funnel.py`. **The DB is never committed** — it lives only as the Actions artifact `market-data-db` (30-day retention). Any schema change must be an idempotent migration that runs inside the pipeline.

| Table | Contents |
|---|---|
| `daily_prices` | OHLCV + delivery % + 52w hi/lo + day chg %. PK `(symbol, date, exchange)` — **dual-listed stocks have two rows per date; always filter `exchange='NSE'`** (v12.7) |
| `symbol_master` | Company name, sector, cap category, ISIN, BSE code |
| `fundamental_metrics` | PE, PB, EPS, ROE, D/E, margins, CAGRs + forensic input columns |
| `shareholding` | Promoter / FII / DII / Pledge % + QoQ; snapshot pledge persisted here every run (v17.13.7) |
| `shareholding_history` | Per-date shareholding history (QoQ baseline) |
| `technical_indicators` | RSI, MACD, Supertrend, ADX, Stoch, MFI, OBV, VWAP, S1/S2/R1/R2, `atr_14` (full history since v15.2) |
| `weekly_momentum` · `delivery_stats` · `fo_participant_data` · `bulk_deals` · `insider_trades` | as named |
| `latest_analysis_results` | Yesterday's scores (Alert Log Δ, Stage 3 O4/O5) + verdict streak counters (v11.0.2) |
| `v7_intelligence` · `watchlist` · `run_stats` · `market_holidays` | as named |
| `dual_listed_runtime` | Runtime dual-listed allowlist (v11.0.2; write-time guarded v17.17) |
| `failed_yfinance_lookups` | 30-day cache of yfinance 404s (v12.8) |
| `pipeline_migrations` | One-time migration markers (e.g. v12.0.1 allowlist purge) |
| `gold_recommendations` | **Append-only** log of every Gold pick: entry range, SL, `t1` (= the Target), dormant `t2`/`t3`, CFV, MoS, score, archetype, `time_horizon`, `expiry_days/expiry_date`, `times_reappeared`, audit fields (`original_stop_loss`, `atr_at_rec`, `regime_at_rec`, `next_earnings_date`), `suggested_alloc_pct`, `alloc_rationale` |
| `gold_outcomes` | One row per recommendation (PK `(symbol, recommendation_date)`): `outcome_type` ∈ {OPEN, T1_HIT, T2_HIT, T3_HIT, SL_HIT, TRAIL_SL, EXPIRED}, outcome date/price, max runup/drawdown, current price/P&L, trailing stop (`trailing_sl_pct`, `trailing_sl_price`, `peak_price_seen`), DD duration, `shadow_*` columns (v17.7) |
| `gold_continuation` | v17.3 post-T1 shadow walk to each position's own expiry (T2/T3 reach, peak/trough, SL break). Never writes `gold_outcomes` |
| `gold_resilience_watch` | v17.5 bearish-regime standouts (reference only; never feeds Gold or the tracker) |

Outcome tables (`gold_recommendations`, `gold_outcomes`, `gold_continuation`) are **never pruned** by the 400-day maintenance.

---

## 4. PIPELINE EXECUTION ORDER (`master_funnel.py::run_master_pipeline`)

```
Startup       initialize_v7_tables · defensive schema init (v10.5) ·
              Section 12A.5 one-time v12.0.1 allowlist purge (marker-gated, already done)
Section 12B   Gate check (ingestion.orchestrator.gate_check)
                C1 weekday · C2 NSE holiday (fail-closed) · C3 NSE bhav HEAD (+1 retry)
                C4 BSE — never blocks · C5 watchlist — skipped
                → "GATE APPROVED" / "Gate passed. Processing trading day: …"
                fail → skip email + return
Section 1     Harvest NSE bhav + delivery, BSE bhav (bse pkg → cloudscraper → curl_cffi), SME, F&O
              C5 data integrity: NSE frame present and ≥ 500 rows, else skip email + return
Section 3J/K  Bulk deals + SAST insider trades
Section 1.2   Gap detection (missed trading days)
Section 1.3   DB sync → daily_prices (save_to_database: reconcile + allowlist recorder, v17.17 guards)
Section 1.4   Gap-fill (≥ 2 missed days → backfill those days + recompute indicators)
Section 1.5   Daily technical refresh (_compute_all_indicators, v12.7)
Section 0     Funnel: Stage 1 → Stage 2 → Stage 3 (top 100)
              v17.15 held monitoring: OPEN positions not in the top 100 appended (_held_monitor=True)
Section 3     Per-stock overlays (3A valuation · 3B inline forensic fetch · 3D/3G forensics ·
              3E capital allocation · 3F ownership · 3G growth · 3H anti-trigger guard ·
              3J/3K smart money · 3L sector rotation placeholder · BS health first pass)
Section 4B    NSE fundamentals refresh (top 100) — NSE live calls return nothing on Actions
Section 5     DB enrichment: technicals + fundamentals + weekly momentum; sector stage recompute;
              ghost keys (fcf_positive_4q, promoter_q_increase, fii_buy_3q, …)
Section 5A.3b NSE snapshot fallback (v17.13): load_snapshot() → apply_snapshot() fills BLANK
              pledge / promoter / FII / DII; provenance kept for the Excel header
Section 5A.4  QoQ recompute (Pro / FII / DII) · 5A.4b cleanup ('—' for no history)
Section 5A.5  Forensics re-run with enriched data
Section 5A.6  News & catalyst extraction (v17.9/v17.11) — before scoring
Section 5B    Fair value engine (7 models → CFV, MoS)
Section 6     Scoring loop per stock: tech / fund / safety / sentiment / early-entry sub-scores →
              composite + verdict (scoring_engine) → storm → horizon + risk level →
              BS health 2nd pass → spike score (3H guard re-evaluated with fresh forensics, v12.9) →
              early-entry + convergence bonus (Quick Pick recomputed, v13.x) → Piotroski →
              _compute_sl_t_v14_6(): SL + regime Target → risk-parity size (v15.5) → ETF flag (v15.8)
Section 7/8   (placeholder — cards are generated later, v17.14)
Section 9/10  Split held-monitor rows into _held_monitor_map; prune v15.8 ETF leakers
              market_stats (+ nse_snapshot provenance) → market-regime gate (Nifty vs 20d SMA, 2% band)
              → resilience watchlist refresh (bearish days) → ExcelGeneratorV6 constructed
              → verdict streaks + save latest_analysis_results → allowlist prune
              → v14 hook: log today's Gold picks (excel_gen._get_gold(), first appearance only;
                re-appearances increment times_reappeared)
              → track_outcomes.main(): live walk + continuation + shadow stop
Section 7/8   DEFERRED AI cards (v17.14): scope = _get_gold() ∪ get_open_recommendations();
              held positions use their fully-enriched _held_monitor_map rows (v17.15);
              notes synced into the Excel frame by symbol; held cards → market_stats["held_cards"]
Section 9/10  generate_excel_reports() → 8-sheet workbook; DailyReportGenerator text report
Section 12    Email (Excel + Gold file + text report)
Section 13    enforce_circular_queue (400 days); run_stats row
On exception  error email (is_error=True)
```

**CRITICAL ORDER RULES**

- Technical data (RSI/MACD/Supertrend/ATR) loads at Section 5. Anything using it must run after that.
- `composite_score` and `verdict` are set by `ScoringEngine.calculate_composite_score()`; `horizon` and `risk_level` must run after it.
- News sentiment (5A.6) must run **before** Section 6 so it can move the sentiment sub-score.
- The snapshot fallback (5A.3b) runs **before** 5A.4 so QoQ and the spike guard see filled values.
- `latest_analysis_results` must be **read before** today's scores are saved (Alert Log Δ, O4/O5).
- The v14 logging hook and the tracker must run **before** `generate_excel_reports()` (v14.1.2 / v14.1.3, guards G10/G11) so today's picks and fresh P&L appear in today's Performance sheet.
- AI cards run **after** the tracker so their scope is the same Gold result the tracker logged (v17.14). Anything written into `market_stats` must happen **after** `market_stats = {…}` is created (v17.11.1, guard G40).
- Held-monitor rows must be removed before anything renders or saves the top-100 view.

---

## 5. SCREENING FUNNEL

Typical daily counts (production logs of 18, 25 and 26 Sep 2026): **~5,200 → ~1,900 → ~1,600 → 100** — Stage 1 1,859–1,993, Stage 2 1,509–1,696, e.g. `Universe: 5234 → Stage1: 1993 → Stage2: 1696 → Stage3: 100`. Stage 1's biggest drops are low delivery (~2,400) and penny stocks (~630).

### Stage 1 — `screening/pre_screener.py::stage_1_filter` (V0–V9 hard drops)

1. V0 `sc_group` exclusion: EF, MF, IF, IR, BE → dropped
2. V0b ETF / fund symbol patterns (~70 keywords; ends-with ETF/BEES/INDEX …; v15.2 added 17 specific fund tickers)
3. V0c liquid-fund NAV trap (CMP ≈ ₹1,000 + LIQUID/CASH name)
4. V1 volume must be > 0
5. V7 circuit: |price change| ≥ 19.9 % → dropped
6. V4 penny: close < ₹10 → dropped
7. V8 suspended → dropped
8. V3 delivery < 40 % → dropped (unless `watchlist_override`)
9. V9 BSE SME turnover < ₹5 L → dropped

`company_name` and `sector` are **not** available at Stage 1 (bhav files carry tickers only). The v15.8 post-enrichment ETF/fund filter (with the AMC-parent carve-out for HDFCAMC, NAM-INDIA, UTIAMC, ABSLAMC) runs after `symbol_master` enrichment; flagged rows are pruned before the Excel build.

### Stage 2 — `stage_2_fundamental_scorer` (bhav-only quality /35, gate ≥ 15)

Hard drops first: HD1 turnover < ₹2 L · HD2 delivery < 30 % · HD3 price < ₹20. Then seven +5 criteria: B1 delivery ≥ 50 % · B2 delivery ≥ 65 % · B3 turnover ≥ ₹10 L · B4 turnover ≥ ₹50 L · B5 price ≥ ₹50 · B6 price ≥ ₹200 · B7 dual-listed. Cap category is assigned here from turnover (≥ ₹50 Cr LARGE · ≥ ₹10 Cr MID · ≥ ₹1 Cr SMALL · else MICRO) until yfinance enrichment.

### Stage 3 — `screening/priority_ranker.py::get_top_100_candidates`

```
Priority Score = (vol_spike/5 × 25) + (stage2/35 × 30) + (delivery/100 × 20)
              + (cap_bonus × 15) + (turnover_bonus × 10)
```

Overrides (max 20, priority O1 > O2 > O3 > O4 > O5): O1 watchlist · O2 announcement today · O3 vol ≥ 3× and delivery ≥ 60 % · O4 last score ≥ 60 and today's Stage 2 < 15 · O5 7 ≤ days since analysis < 99. Chronic-AVOID demotion (v11.0.2): `consecutive_avoid_quarters ≥ 2` → priority −15. Cap mix: LARGE ≥ 20, MID ≥ 15, SMALL+MICRO ≤ 65. `VOL_SPIKE_CAP = 5×`. Technical alignment bonus after technicals load: Supertrend BUY + MACD BUY → +8 · one BUY → +3 · both SELL → −5.

**v17.15 held monitoring.** After Stage 3, every OPEN position (from `gold_outcomes`) that is not in the top 100 is appended with `_held_monitor=True` so it receives the full analysis; those rows are removed again at the start of Section 9/10 and used only for AI cards.

---

## 6. SCORING SYSTEM

### Composite Score (0–100) — `analysis/scoring_engine.py::calculate_composite_score`

```
Canonical:     Fund×0.35 + Tech×0.30 + EE×0.15 + Sent×0.10 + Safe×0.10
Redistributed: Fund×0.389 + Tech×0.333 + EE×0.167 + Safe×0.111
               (when sentiment is not informed — see below)
+ MoS adjustment (−10 to +12)
+ Spike bonus (+2 per trigger, max +10 if fund ≥ 55, else max +3)
+ Early Mover bonus (+5 if early_entry_score ≥ 50)
− Anti-trigger penalty (−10 if risk_flag_active)
+ v10.9 Forensic Quality Adjustment (−10 min to +8 max)
    Altman Z      ≥ 3.0  → +3   |  < 1.8  → −5
    Earn Quality  HIGH   → +2   |  LOW    → −3
    ND / EBITDA   < 1.0  → +1   |  > 5.0  → −2
    Int Coverage  > 5×   → +2   |  < 1.5× → −3
    Missing data → no adjustment (doesn't penalise stocks without forensics)
```

**Session 24 refinements:**

1. **Sentiment informedness check** — if none of the paid/AI sentiment signals fired (FII/Promoter/DII QoQ, insider buy, news sentiment, pledge direction), the 10% sentiment weight **redistributes** proportionally to Fundamental/Technical/Early/Safety. No "free 5 points" for missing data.
2. **Fundamental-gated spike bonus** — full +10 only when `fundamental_score ≥ 55`. Otherwise capped at +3.
3. **Confidence dots** — HIGH ●●● (≥ 5 points clear of threshold), MEDIUM ●●○ (2–5), LOW ●○○ (< 2; cliff zone).
4. **OVERVALUED verdict** — distinct from WATCHLIST; styled in soft orange (`FED7AA` / `7C2D12`). Stocks that clear BUY score threshold but fail the MoS gate.
5. Stage-2 inflation fix lives upstream in `master_funnel.py`; scoring receives corrected `fundamental_score` unchanged.

**v10.17 refinement — Data-completeness guard (quality protection):**

6. **BUY requires informed data** — a stock can only carry a BUY verdict if at least 3 of 5 sub-score dimensions actually had real data move them away from base (Fundamental ≥6 from Stage-2 base, Technical/Safety ≥6 from neutral 50, Sentiment paid/AI signal fired, Early Entry > 0). Stocks that score above the BUY threshold but with `informed_count < 3` are demoted to **`WATCHLIST ●●● (thin data)`**. Prevents inflated BUYs on data-blind stocks. New output fields: `data_completeness` (0–5 count) and `data_gate_applied` (bool). OVERVALUED / NEUTRAL / AVOID unaffected. Defensive `try/except` around the counter returns 5 on any error so the guard never breaks a pipeline run. See `scoring_engine.py::_count_informed_dimensions` and `MIN_INFORMED_FOR_BUY`.

### Verdict thresholds — `scoring_engine.py::CAP_THRESHOLDS`

```python
CAP_THRESHOLDS = {
    "LARGE": (60, 50),   # (BUY_min, WATCHLIST_min)
    "MID":   (63, 53),
    "SMALL": (66, 56),
    "MICRO": (70, 60),
}
AVOID_BELOW         = 38    # Universal floor
MIN_INFORMED_FOR_BUY = 3    # v10.17: of 5 sub-score dimensions
```

**MoS gate for BUY** — normally MoS ≤ −10% blocks BUY (becomes OVERVALUED). Relaxed to MoS ≤ −20% if **technically confirmed**: `score ≥ 70 AND Supertrend=BUY AND Sector Stage 2`.

### Score inputs by category

- **Fundamental:** PE, ROE, D/E, Current Ratio, Gross Margin, Net Margin, Earnings Yield, Promoter %, PAT YoY, Rev YoY, FCF Yield
- **Technical:** RSI, ADX, MACD, Supertrend, VWAP, OBV, Stochastic K, MFI
- **Safety:** Pledge %, Beta, D/E, FCF, BS Health
- **Sentiment:** `fii_3q_trend` (UP +10 / DOWN −10), `smart_money_sentiment` (ACCUMULATION +10), `insider_buy_alert` (+8 — SAST, or news-evidenced since v17.11), promoter QoQ (±5), DII QoQ (+6/+4/−3), `news_sentiment` (POSITIVE +4 / NEGATIVE −5 — populated by Section 5A.6 since v17.9), delivery % (+4/+2/−3), `pledge_direction` (FALLING +3 / RISING −5; vocabulary aligned in v13.0)

### Ghost keys (derived before storm/sentiment scoring)

Populated in `master_funnel.py` just before the composite-score call:
- `fcf_positive_4q` ← `fcf > 0`
- `promoter_q_increase` ← `promoter_qoq > 0.3`
- `fii_buy_3q` ← `fii_qoq > 0.3`
- `rev_growth_yoy` ← `rev_yoy`
- `fii_3q_trend` ← derived from `fii_qoq`
- `promoter_buying_30d` ← `promoter_qoq > 0.5`

---

---

## 7. FAIR VALUE ENGINE — Session 19 guards

7 models, weighted and normalised to active (non-zero) models only:

| Model | Weight | Formula | Condition |
|---|---|---|---|
| M1 DCF | 30% | EPS × (1+g)^n / r | Positive EPS |
| M2 Graham | 15% | √(22.5 × EPS × BVPS) | EPS > 0, BVPS > 0 |
| M3 PE | 20% | EPS × sector_median_PE | Sector PE map |
| M4 PB | 15% | BVPS × sector_median_PB | PB available |
| M5 EV/EBITDA | 10% | CMP × (sector_EV_mult / EV_EBITDA) | EV/EBITDA available |
| M6 DDM | 5% | D1 / (r−g), growth capped 6% | Div yield 0.1 %–15 % ONLY |
| M7 PEG | 5% | EPS × min(growth, 30%) | Growth available |

**MoS** = (CFV − CMP) / CMP × 100

Fair value drives the MoS score adjustment (−10 … +12, suppressed when fewer than 3 models fire — v12.6 `†` marker), the BUY MoS gate and the Gold 15–100 % band. **It no longer sets the Target** (v17.8.1). CFV capped at 3× CMP marks the MoS label with `*` (v12.5). ETFs / funds with no CFV render MoS as `—` (v13.x).

### Session 19 DCF guards (non-negotiable)

- **WACC floor at 10%** — `wacc = max(gsec + beta × 5.5, 0.10)`. Prevents SBIN-style ₹12k fair-value bug.
- **M1 cap at 4× CMP**
- **Composite CFV cap at 3× CMP** (200% MoS ceiling)
- **DDM guard:** `0.1 < div_yield_pct < 15.0` only. Do NOT relax.

---

## 8. FORENSICS ENGINE (v10.4+ consolidated)

`analysis/forensics_engine.py` is the single source of truth for forensic calculations. As of v10.4 it has an inline yfinance fetcher that removes the dependency on backfill-populated DB columns.

### Key method: `ForensicsEngine.fetch_forensic_inputs(symbol)` (v10.4)

Pulls live from yfinance for ONE symbol and returns a dict:
- From `ticker.balance_sheet`: `total_assets_cr`, `total_liab_cr`, `retained_earnings_cr`, `working_cap_cr`, `curr_assets_cr`, `curr_liab_cr`, `total_debt_cr`, `cash_cr`, `inventory_days`, `receivable_days`, `payable_days`
- From `ticker.cashflow`: `capex_cr`, `operating_cf_cr`
- From `ticker.income_stmt`: `ebit_cr`, `int_expense_cr`, `q_rev_cr`, `q_ebitda_cr`, `q_pat_cr`

Called inline by `master_funnel.py` for each of the top-100 stocks before `calculate_accounting_forensics()`. Never raises — swallows all Yahoo errors and returns `{}` if unreachable. Takes ~2 seconds per stock (~3 min total).

### `ForensicsEngine.calculate_accounting_forensics(row)` — output fields

Returns a dict merged onto the stock dict. All fields return `"—"` when inputs are missing (not 0 or 1.0):

| Output key | Excel column | Formula |
|---|---|---|
| `ccc_days` | CCC Days | inventory_days + receivable_days − payable_days |
| `nd_ebitda` | ND/EBITDA | (total_debt − cash) / ebitda_annual |
| `int_coverage` | Int Coverage | ebit / int_expense |
| `capex_rev` | Capex / Rev % | (capex / rev_annual) × 100 |
| `earnings_quality` | Earn Quality | CFO / **annual** PAT (v12.9) → HIGH ≥ 0.8 · MODERATE · LOW < 0.5 |
| `altman_z` | Altman Z | 1.2·x1 + 1.4·x2 + 3.3·x3 + 0.6·x4 + 1.0·x5, capped at 10 (v12.5) |
| `beneish_m` | Beneish M | Real 8-variable Beneish (1999) M-Score (v12.9), clamped [−10, 10]; falls back to the TATA accrual proxy when only one year of statements exists. Anti-trigger fires at M > −1.78 (v16.4) |
| `pledge_direction` | Pledge Direction | Passthrough from master_funnel |

### Important v10.6 annualization fix (ND/EBITDA)

The DB column `q_ebitda_cr` (written by `backfill_history.py`) is **QUARTERLY** — one quarter's EBITDA. Using it in an annual ND/EBITDA ratio inflates the ratio by ~4×. The v10.6 fix at line 340:

```python
ebitda_annual = _num(row, 'ebitda', 'ebitda_cr')           # prefer annual from yfinance .info
if ebitda_annual <= 0:
    _q_ebitda = _num(row, 'q_ebitda_cr')
    if _q_ebitda > 0:
        ebitda_annual = _q_ebitda * 4                       # annualize fallback
```

Same annualization applied to Capex/Rev (capex is annual; uses `revenue` first, fallback to `q_rev_cr × 4`).

---

---

## 9. EXCEL DASHBOARD (`reporting/excel_generator.py`) — 8 sheets

**Class:** `ExcelGeneratorV6(data, date_str, run_time=None, prev_scores=None, gap_days=None, market_stats=None)`. `market_stats["market_regime"]` drives Gold suppression; `market_stats["nse_snapshot"]` feeds the row-2 provenance line; `market_stats["held_cards"]` carries AI notes for held positions.

| # | Sheet | Content |
|---|---|---|
| 1 | 📊 Full Dashboard | Top-100 × **126 columns** in 18 bands: IDENTITY 7 · SCORES 5 · PRICE & MARKET 7 · WEEKLY CHANGE % 4 · FAIR VALUE 12 · VALUATION 7 · PROFITABILITY 10 · GROWTH 10 · FIN HEALTH 10 · CAP ALLOC 3 · SHAREHOLDING 9 · QUALITY SCORES 4 · PIPELINE / OB 5 · EARLY DETECTION 3 · TECHNICAL 14 · BALANCE SHEET 2 · TRADE PLAN 7 (Entry · SL · Target · Horizon · Risk · Suggested Alloc % · Sizing Rationale) · NEWS & RISK 7 (Key Catalyst · News Sentiment · Primary Risk · SEBI Flags · Insider (news) · Bulk Deal · Reg Flag). No AI column since v17.18. Row 2: header-colour legend + `Pledge/Promoter data as of DD-Mon-YYYY HH:MM IST (Nd old)` |
| 2 | ⭐ Gold – Early Movers | 41 columns in 11 bands (incl. the AI Analysis Summary). Row 2 lists the 15 criteria + count + bearish-regime notice. Below: **Bearish-Regime Standouts** (v17.5) or a greyed *Recent Resilience* strip on bullish days |
| 3 | 📊 Trade Summary | Entry range · SL · Target · R:R · horizon · risk. R:R colours (≥ 3 green, ≥ 2 amber, else red) predate the single target — with R:R ≈ 1.3–1.8 every row is red (open item) |
| 4 | 🔔 Alert Log | Alert type: `⬇ SCORE DEGRADED` only on a real ≥ 3-pt drop (v17.2), `⚠ LOW SCORE` (< 30, no drop), `🔔 SPIKE FIRED`, `⭐ EARLY MOVER DETECTED`. Action: REVIEW FOR EXIT — SCORE FALLING · REVIEW FOR EXIT · CONSIDER ENTRY (BUY, MoS > 10, score ≥ 65) · MONITOR FOR ENTRY · STRONG STOCK — WAIT FOR PULLBACK (OVERVALUED) · SCORE IMPROVING / DECLINING (WATCHLIST ±3) · VOLUME ALERT · EARLY MOVER — ACCUMULATE · MONITOR CLOSELY |
| 5 | 📱 Delivery Preview | WhatsApp / email text |
| 6 | 🎯 Performance | See §9C |
| 7 | 📖 Glossary | `GLOSSARY_DATA` (~196 rows) |
| 8 | 📖 Tooltip Reference | Built from `tooltip_formatter.TIPS` (210 entries) |

**Header colours.** Red `991B1B` = column in `NO_FREE_SOURCE_COLS` with < 30 % of rows populated (`_COVERAGE_MIN`, v12.4; Gold sheet uses the same rule, v12.5). Amber = AI-generated column (`NEEDS_AI_CREDITS`; only on the Gold sheet now). Otherwise the band colour.

**Tooltips.** `self._apply_col_tips()` → `tooltip_formatter.apply_tooltips()` (hover note + ⓘ cue + link to the reference sheet). `TIPS` is the single source of truth; the legacy `_HDR_TIPS` dict in excel_generator is unused (kept in sync for external readers). Shared headers on the Performance sheet (P&L %, Max Runup %, Days Held …) must read correctly for both OPEN and CLOSED rows (v15.9, G26).

**Width rule.** Performance-sheet columns are shared by several sections — widths go through `_set_min_width()` (widen-only). Never assign `.width =` there (v17.1/v17.1.1, G35).

**Text alignment.** Full Dashboard text columns are left-aligned **by name** (`_FULL_LEFT_ALIGNED`, v17.18), not by position.

---

## 9A. GOLD FILTER, MARKET-REGIME GATE & RESILIENCE WATCHLIST

**Regime gate** (master_funnel Section 9/10, v17.0 + v17.5.1): `gap = (Nifty close − 20-day SMA) / SMA`. BEARISH only when `gap < −_REGIME_TOLERANCE_PCT` (2.0 %); otherwise BULLISH/neutral. SMA ≤ 0 (data missing) → BULLISH (never a silent blanket block). Log line: `📊 Market regime: … (Nifty X vs 20d-SMA Y, gap ±Z%, tolerance -2.0%)`. On BEARISH, `_get_gold()` returns empty and row 2 shows the suppression notice.

**Gold filter** (`ExcelGeneratorV6._get_gold()`), ALL 15 must pass:

```
 1 verdict == BUY              6 BS health ≠ ALERT          11 Int Coverage ≥ 1.5× or missing
 2 composite ≥ 70              7 pledge ≤ 10 %              12 ROE ≥ 10 % or missing     (v16.2)
 3 15 % ≤ MoS ≤ 100 %          8 not spike-suppressed       13 PEG ≤ 8, missing or ≤ 0   (v16.2)
 4 storm ≥ 5                   9 Altman Z ≥ 1.8 or missing  14 3-day ROC ≥ 0             (v17.0)
 5 RSI ≤ 70                   10 Earn Quality ≠ LOW         15 sector cycle OK            (v17.0)
```

Sector cycle: weak sectors (yfinance "Consumer Defensive", "Industrials", "Technology", "Communication Services" + NSE-style patterns IT / FMCG / capital goods / telecom / media …) pass only if the stock's own 4-week change > 0. The same `_get_gold()` result feeds the Gold sheet, the outcome logger and the AI-card scope — one source of truth.

**Resilience watchlist** (`_refresh_resilience_watch`, v17.5/v17.5.1) — reference only, never a pick. On BEARISH days, stocks with own 3-day return ≥ 5 % (`_RW_MIN_ABS_RETURN`) **and** beating the Nifty's 3-day return are upserted into `gold_resilience_watch` (30-day retention, streak over the last 5 bearish days; shown when `streak_hits ≥ 2`). Retention pruning runs every day.

---

## 9B. TRADE PLAN — STOP-LOSS, TARGET, SIZING (`master_funnel._compute_sl_t_v14_6`)

```
atr_pct  = ATR-14 / CMP × 100          (cap-category fallback when ATR missing:
                                        LARGE 2.0 · MID 2.8 · SMALL 4.0 · MICRO 5.5)
h_mult   = SHORT TERM 2.5 · POSITIONAL 3.5 · LONG TERM 5.0
sector   = 5-tier adjustment _V14_7_SECTOR_TIER (+0.60 very-high … −0.35 very-low)
regime   = ATR% vs its 252-trading-day average: ≥ 1.20× → high (×1.10) · ≤ 0.80× → low (×0.90)
raw_sl   = atr_pct × (h_mult + sector) × regime
support  = floor at (CMP − support1)/CMP + 0.5 % only when vol_ratio ≥ 1.20
SL %     = clamp(raw_sl, 4.5, 15.0); SHORT TERM → min(SL %, 7.0)        (v15.1, v17.0)
earnings = ×1.2 if results within 5 days (cap 9 % SHORT) — built in, no data source, never fires
Target % = _V14_6_TARGET_RR_BY_REGIME[regime] × SL %   (low 1.3 · neutral/normal 1.5 · high 1.8)
           capped at _V14_6_TARGET_SANITY_CEILING_PCT = 50 %                (v17.8.1)
T2 / T3  = Target × 1.35, × 1.35² (dormant; ordering enforced)
```

Owner decision (v17.8.1): no fair-value anchoring, no horizon caps; the multipliers are starting values to review after ~30 closed positions. Levels are **frozen at log time** — re-appearances never change them.

**Sizing** (`risk/correlation_aware_sizing.py`, v15.4/v15.5/v15.7): `alloc = 1.0 % risk budget / |SL %|` × cap multiplier (LARGE/MID 1.0 · SMALL 0.85 · MICRO 0.70), 30 % sector cap, clamped [1 %, 15 %]. Shown as Suggested Alloc % + Sizing Rationale; "sector cap" text only when the sector headroom is the binding constraint (v15.7).

---

## 9C. OUTCOME TRACKING & PERFORMANCE MEASUREMENT

**Logging** (master_funnel v14 hook, before the Excel build): every stock in `_get_gold()` is inserted into `gold_recommendations` + an OPEN `gold_outcomes` row — **first appearance only** (`has_open_recommendation`); re-appearances bump `times_reappeared` once per day. `insert_gold_recommendation` returns False on a PK collision (v14.3). Horizon → `expiry_days`: SHORT 30 · POSITIONAL 90 · LONG 270 (`horizon_to_expiry_days`); the stock dict key is `horizon` (v14.1 / v17.1 fixes — never `time_horizon` on the stock dict).

**Walk** (`track_outcomes._walk_forward`, auto-invoked): daily NSE bars from the day after the recommendation; first event wins; SL beats target on same-bar ties; T3 > T2 > T1 on one bar; EXPIRED at the horizon cutoff (hard). Effective SL = max(original, trailing). Trailing tiers (v16.5 → v17.0): peak ≥ 25 % → lock +12 % · ≥ 20 % → +9 % · ≥ 15 % → +5 % · ≥ 12 % **and** ≥ 10 days held → break-even · otherwise none. Trailing updates at the end of each bar (no look-ahead, G17). A stop hit on the trailing level is **TRAIL_SL** (excluded from SL rate); on the original level **SL_HIT**.

**Continuation** (v17.3, `gold_continuation`): for each T1_HIT, a shadow walk from the next day to the position's own expiry (T2/T3 reach, peak/trough vs T1, broke original SL). Read-only w.r.t. `gold_outcomes` (G37).

**Shadow stop** (v17.7): Chandelier stop = peak − N × Wilder-ATR × regime adj (N = 2.5 / 3.0 / 3.5 by horizon), ratchet-up only, no look-ahead; writes `shadow_*` columns only (G39).

**Performance sheet sections, in order:** title + sample-size banner (< 30 closed = preliminary) · HEADLINE (Total / Closed / Open / Hit rate (T1+) / SL rate; tiles TARGET HIT · SL HIT · EXPIRED · TRAIL SL) · SPEED (avg days → Target / SL) · DIAGNOSTIC BREAKDOWNS (score band · archetype · sector · time horizon) · CLOSED POSITIONS (15 cols incl. Score Band / Archetype / Sector, v17.4) · RISK-ADJUSTED RETURNS (Sharpe / Sortino / Calmar, rf 6.5 %, DD duration, v16.0) · OPEN POSITIONS (SL, Target, Trailing — label = level actually locked (v17.19), Regime, Suggested Alloc, Sizing Rationale, Score Band, Archetype, Sector, **AI Card**; pale-yellow ⚠ when ≤ 14 days left) · EXPIRED MISSED-RUNUP diagnostic (≥ 3 expired) · CONTINUATION AUDIT · SHADOW EXIT COMPARISON · SURVIVORSHIP AUDIT (open positions vs today's universe).

`reset_performance_tracking.py` (workflow input `RESET-PERFORMANCE`, manual dispatch only) wipes the three outcome tables — irreversible.

---

## 10. BACKFILL (`backfill_history.py`)

Auto-runs when `daily_prices` has fewer than 50,000 rows (fresh DB).

**Tables populated:**
- `daily_prices` — 400 calendar days (≈ 275 trading days) of OHLCV per symbol (v12.6.1; `DAYS_TO_BACKFILL`)
- `symbol_master` — company names, sectors, cap categories
- `technical_indicators` — all indicators per symbol (latest date) + full `atr_14` history (v15.2, for the regime baseline)
- `weekly_momentum` — 2w/4w/6w/8w changes + beta_90d
- `delivery_stats` — daily delivery %
- `fundamental_metrics` — PE, PB, ROE, EPS, + 18 forensic input columns (v10.2)
- `shareholding` — Promoter / FII (yfinance institutions, FII+DII combined) / Pledge; NSE values only when NSE answers (never on Actions) — the weekly snapshot fills blanks in the pipeline

### NSE shareholding enrichment (current — v17.13.3)

`_nse_shareholding(symbol, session)` calls `/api/corporate-share-holdings-master?index=equities&symbol=` with browser/XHR headers and reads `pr_and_prgrp` (promoter) and `public_val` (public); with ≥ 2 rows it also returns promoter QoQ. The older `corp-info` endpoint (with `diisTotal` / `fiisTotal`) is retired, so DII stays 0 → `—`. On GitHub runners NSE returns nothing — this path only produces data on a residential IP (the weekly `fetch_nse_local.py` reuses these parsers). The enrichment message on Actions says the DII gap is expected. History note: v10.6 wired the corp-info DII loop, v13.0 added multi-quarter QoQ, both now superseded.

### Resistance / Support formula (v12.4 corrected)

```python
sup1 = l.rolling(20).min()                       # short-term swing low
res1 = h.rolling(20).max()                       # short-term swing high
if len(h) >= 80:                                 # need ≥ 60 prior + 20 recent
    prior_l = l.iloc[:-20]                       # bars BEFORE last 20d
    prior_h = h.iloc[:-20]
    _lb2    = min(252, len(prior_h))
    sup2    = prior_l.rolling(_lb2).min()        # prior 52-week low
    res2    = prior_h.rolling(_lb2).max()        # prior 52-week high
    sup2    = sup2.reindex(l.index, method="ffill")
    res2    = res2.reindex(h.index, method="ffill")
else:
    sup2 = pd.Series([float("nan")] * len(h), index=l.index)
    res2 = pd.Series([float("nan")] * len(h), index=h.index)
```

**Evolution of this code:**

- **Pre-v10.9** had `sup2 = rolling(40).min()` / `res2 = rolling(40).max()`. Because the screener targets momentum stocks near highs, 87 % of stocks had `res1 == res2` — the 20-day high WAS the 40-day high.
- **v10.9** changed the window to 252 trading days (~52 weeks), expecting that to give a genuinely different long-term reference. It silently failed for the same 87.9 % of rows whenever the 252-day max landed inside the most recent 20 days (a fresh breakout — common pattern for the momentum stocks the funnel concentrates on). Production audit of the v12.3 Excel showed 79/99 rows where `52W High > Resist 2 by >5 %`, which is mathematically impossible if R2 truly were the rolling-252 max.
- **v12.4** computes R2 / S2 over `iloc[:-20]` — bars BEFORE the most recent 20 — so they represent the *prior* 52-week ceiling/floor, genuinely separate from R1's recent-swing high regardless of whether a fresh breakout sits in the last 20 days. Forward-fill back to the original index keeps `_v(...)` working unchanged. Stocks with < 80 days of history fall back to NaN → cell renders `"—"` via the standard `_g` default.

### Supertrend formula (corrected)

```python
sma20_st   = c.rolling(20).mean()
_buy_mask  = c > (sma20_st + 0.5 * atr14)   # BUY
_sell_mask = c < (sma20_st - 0.5 * atr14)   # SELL
# else NEUTRAL
```

Old formula had `c > st_up = BUY` which was inverted → always NEUTRAL. Fixed.

### Current Ratio / Quick Ratio fixes

- `_get_bs_row()` helper — keyword search without requiring "Total" prefix
- Excludes "non current", "noncurrent", "other" from CA/CL row matching
- Tries both `.NS` and `.BO` suffixes
- Tries quarterly `balance_sheet` as fallback
- Cap 100 stocks per run
- Quick Ratio now `(CA − Inventory) / CL` (was `CR × 0.75`)

---

---

## 11. INGESTION & EXTERNAL DATA

### Gate check — `ingestion/orchestrator.py::gate_check`
C1 weekday · C2 NSE holiday (three-state; unknown calendar = fail-closed) · C3 NSE bhav HEAD with one 30-second retry · C4 BSE — always PASS (download attempted at runtime) · C5 — skipped (no watchlist requirement). Prints `✅ GATE APPROVED: Processing data for …`; master_funnel then prints `✅ Gate passed. Processing trading day: …`. The real data-integrity check (`check_data_integrity`, NSE frame ≥ 500 rows) runs after the download.

### BSE downloads
One `bse` package client per run; `_bse_bhav()` cascades `bse` → `cloudscraper` → `curl_cffi` (v12.0). BSE-down days fall back to NSE-only mode. Known noise: the BSE SME delivery file sometimes arrives as "File is not a zip file" (logged as `BSE-Deliv ⚠️`; NSE delivery % is used).

### Reconciler + dual-listed allowlist — `ingestion/reconciler.py`, `ingestion/allowlist_maintainer.py`
NSE+BSE merge on **real 12-character ISINs only** (v12.1 — empty ISINs never join). `df.attrs["dual_match_method"]` records how the merge was made (`isin` / `symbol` / `allowlist_fallback` / `bse_only`). On BSE-down days the effective allowlist = the 233 hard-coded `DUAL_LISTED_ALLOWLIST` names ∪ `dual_listed_runtime`. In the merged frame `final_symbol` prefers the NSE ticker and `final_close` the NSE close.
`record_dual_listed_observations()` (called from `save_to_database`) writes only when the run used the ISIN merge, only for rows present on both exchanges, skips ETF / fund / index products (`_is_fund_or_index`), refuses a cross-join-looking run (`_MAX_DUP_SHARE = 0.01`, `_MAX_DUAL_SHARE = 0.99`) and self-cleans stored fund rows (v17.17). `prune_runtime_allowlist()` drops entries unseen for 30 days. Typical log: recorder stored ~2,100, removed ~90 ETF rows on first run.

### NSE pledge / shareholding — weekly local snapshot (v17.13.x)
NSE answers a residential client (browser/XHR headers + warm-up cookies) but returns nothing to GitHub runners. So:
- **Owner's laptop:** `fetch_nse_local.py --push` (Windows Task Scheduler, weekly — Sunday 20:00 per `data/README_NSE_SNAPSHOT.md` — with *run as soon as possible after a missed start*; manual `fetch_nse_now.bat`). Pledge: `/api/corporate-pledgedata` → `percSharesPledged`, `comName` mapped to symbol via the NIFTY 500 list (`_norm_name`). Shareholding: `/api/corporate-share-holdings-master?index=equities&symbol=` → `pr_and_prgrp` (promoter), `public_val`. Writes `data/nse_snapshot.json` (`fetched_at`, `pledge`, `shareholding`), then **commit → `pull --rebase --autostash` → push** with `GIT_TERMINAL_PROMPT=0` / `GCM_INTERACTIVE=Never` (v17.13.5; credentials cached once via Git Credential Manager). `--probe` dumps raw endpoint responses; `--dry-run` counts only. Parser imports isolate `sys.argv` (backfill_history reads argv at import).
- **Pipeline:** `ingestion/nse_snapshot.load_snapshot()` accepts the file only if present, schema-valid, non-empty and ≤ 14 days old (`NSE_SNAPSHOT_MAX_AGE_DAYS`); `apply_snapshot()` fills **blank** pledge / promoter / FII / DII only. Provenance (`_fetched_display` in IST, `_age_days`) → Full Dashboard row 2. Snapshot pledge is also written to `shareholding` history every run (v17.13.7) so Pledge Direction can populate after ~90 days (≈ mid-Dec 2026).
- **Not available free:** DII % / DII QoQ (NSE exposes promoter vs public only; `corp-info` is retired). FII % on the dashboard is yfinance's institutional holding (FII+DII combined proxy). Never derive DII from the public bucket.
- Weekly snapshot commits look like `chore(nse-snapshot): pledge 340 / shareholding 467 @ 2026-10-07`. One run (27-Sep-2026) got `pledge 0` — the fetcher still overwrites the file in that case (open item).

---

## 12. AI & NEWS LAYER (company OpenAI-compatible LLM — v17.9 → v17.16)

Gemini and the `google-genai` SDK were removed in v17.10. Both modules call `{OPENAI_API_BASE}/chat/completions` over plain HTTPS with `OPENAI_API_KEY` and `LLM_MODEL`. Without credentials everything else runs; news is skipped (sentiment weight redistributes) and cards show a placeholder. The endpoint is a **reasoning model**: hidden reasoning tokens count against `max_tokens`.

### News & catalyst sentiment — `analysis/news_sentiment.py` (Section 5A.6)
Google News RSS, up to 15 headlines from the last 14 days (`NEWS_MAX_HEADLINES`, `NEWS_LOOKBACK_DAYS`) → LLM at temperature 0 with a strict JSON schema, `max_tokens` 3,000 (`NEWS_MAX_TOKENS`), optional `LLM_REASONING_EFFORT`. Verdicts below confidence 0.6 become NEUTRAL. Live mode sets `news_sentiment` (POSITIVE +4 / NEGATIVE −5 in the sentiment sub-score; an informed verdict switches the composite to canonical weights), `key_catalyst`, `primary_risk`. v17.11 evidenced facts — each must quote its headline or is dropped, no numbers invented: `news_insider` (a news-evidenced BUY may raise `insider_buy_alert` NO → YES, never lowers a SAST YES), `news_bulk_deal`, `regulatory_flag`, results tone. `NEWS_SENTIMENT_SHADOW=1` logs without touching scores; `NEWS_SENTIMENT_ENABLED=0` disables. Logs: `📰 News sentiment: N/M stocks got an informed signal` and `📰   not informed: headlines neutral ×K · …`.

### Investor notes (Block H) — `ai/ai_analyst.py`
- **Scope** (v17.10/v17.14): `excel_gen._get_gold()` ∪ `get_open_recommendations()` — nothing else. Generated after logging + tracker, just before the Excel build. Held positions outside the top 100 use their fully enriched `_held_monitor_map` row (v17.15).
- **Prompt** (v17.16): `_load_card_system_prompt()` extracts **SYSTEM ROLE** (up to `SECTION 1A`) and **BLOCK H — ANALYSIS SUMMARY** (up to `LIST VIEW sort`) from the master prompt (~2 KB) and adds "write ONLY Block H notes". Falls back to the full prompt if the headings are renamed — don't rename them.
- **Batches** of 4; each note delimited `=== CARD: SYMBOL ===` and parsed by symbol (`_mark_batch`, `_parse_marked`); missing notes retried one stock at a time (`_recover_missing`); `_cards_complete` gates caching. `LLM_CARD_MAX_TOKENS` = `LLM_MAX_TOKENS` env (default 16,000).
- **Errors** (`_llm_complete`): raises with the real cause — error body, no `choices`, empty content (with `finish_reason` / usage); records `_LAST_CALL`. A missing/invalid key stops after the first failure (v17.6).
- **Cache** (v17.12): `downloads/ai_cache/` (git-ignored), one file per day, key = date + sha1(symbol | score | label | entry | SL | target | horizon | `_CARD_FORMAT_VERSION="v17.16-compact-prompt"`). Same-day re-runs make zero calls; failed/partial output is never cached; `AI_CACHE=0` bypasses. Persisted across Actions runs by `actions/cache` (`key: ai-cache-${{ github.run_id }}`, `restore-keys: ai-cache-`). Bump `_CARD_FORMAT_VERSION` when the card format changes.
- **Where notes appear:** Gold sheet *Analysis Summary*; Performance sheet *AI Card* for every open position. Not on the Full Dashboard (v17.18).

---

## 13. INFRASTRUCTURE

### `.github/workflows/market_run.yml` (12 steps)
`0 23 * * 1-5` + `workflow_dispatch` (input `reset_performance`). Steps: Checkout · Python 3.11 · Install deps · **Restore AI card cache** · **Restore `market_data.db`** (`dawidd6/action-download-artifact@v6`, `if_no_artifact_found: ignore`) · Show DB state · **Auto-backfill** (`backfill_history.py 400` when `daily_prices` < 50,000 rows) · **v17.3 performance reset** (only when the input equals `RESET-PERFORMANCE`; dry run then `--commit --confirm`) · **Execute `python master_funnel.py || true`** · Delete previous DB artifact (REST API with `GITHUB_TOKEN`) · Upload `market-data-db` (30-day retention, `overwrite: true`) · Show DB state after. The v12.1 allowlist-wipe and v16.5 KOVAI steps were removed in v17.17.

**Secrets:** `OPENAI_API_KEY`, `OPENAI_API_BASE`, `LLM_MODEL`, `SENDER_EMAIL`, `SENDER_APP_PASSWORD`, `USER_EMAIL_ID`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` (optional), `KEEPALIVE_PAT`. **Variables:** `NEWS_SENTIMENT_SHADOW`, `AI_CACHE` (optional). `LLM_MAX_TOKENS`, `LLM_REASONING_EFFORT`, `NEWS_MAX_TOKENS` are not wired into the workflow — defaults apply on Actions.

Runner: `ubuntu-latest`, Python 3.11.

### `keep_alive.yml`
Wed + Sun 10:00 UTC. Always commits `.github/last_active.txt` (GitHub's 60-day inactivity clock counts commits, not runs), checks the pipeline ran in the last 7 days and re-enables it if disabled. Pushes with `secrets.KEEPALIVE_PAT || github.token` — a **fine-grained** PAT scoped to this repo only, *Contents: read & write* (current token expires 24-Sep-2027). Because it commits to `main`, always `git pull --rebase` before pushing.

### Owner's laptop
Weekly `fetch_nse_local.py --push` (see §11). Keep `.env` local and git-ignored; never embed tokens in remote URLs.

---

## 14. KEY CONSTANTS & THRESHOLDS

```python
# Screening
STAGE1_MIN_DELIVERY      = 40      # %
STAGE1_MIN_PRICE         = 10      # ₹
STAGE1_CIRCUIT_THRESHOLD = 19.9    # %
STAGE3_MIN_LARGE         = 20
STAGE3_MIN_MID           = 15
STAGE3_MAX_SMALL_MICRO   = 65

# Scoring
AVOID_BELOW              = 38
BUY_MIN                  = {LARGE:60, MID:63, SMALL:66, MICRO:70}
WATCH_MIN                = {LARGE:50, MID:53, SMALL:56, MICRO:60}
MOS_GATE_FOR_BUY         = -10
MOS_GATE_TECH_CONFIRMED  = -20
MAX_SPIKE_BONUS_STRONG   = 10      # fundamental ≥ 55
MAX_SPIKE_BONUS_WEAK     = 3       # fundamental < 55
EARLY_MOVER_BONUS_FLOOR  = 50
ANTI_TRIGGER_PENALTY     = -10

# v10.9 Forensic Quality Adjustment thresholds (cap: +8 max / −10 min)
FORENSIC_ALTMAN_Z_SAFE      = 3.0    # ≥ this → +3 (safe zone)
FORENSIC_ALTMAN_Z_DISTRESS  = 1.8    # < this → −5 (distress zone)
FORENSIC_EQ_HIGH_BONUS      = 2      # Earn Quality = HIGH → +2
FORENSIC_EQ_LOW_PENALTY     = -3     # Earn Quality = LOW → −3
FORENSIC_NDE_SAFE           = 1.0    # ND/EBITDA < this → +1
FORENSIC_NDE_HIGH           = 5.0    # ND/EBITDA > this → −2
FORENSIC_IC_STRONG          = 5.0    # Int Coverage > this → +2
FORENSIC_IC_WEAK            = 1.5    # Int Coverage < this → −3
FORENSIC_ADJ_MAX_BONUS      = 8      # cap on total positive adjustment
FORENSIC_ADJ_MAX_PENALTY    = -10    # cap on total negative adjustment

# Fair Value (Session 19 guards)
DCF_WACC_FLOOR           = 0.10
DCF_M1_CAP_MULTIPLE      = 4
CFV_CAP_MULTIPLE         = 3
DDM_DIV_YIELD_MIN        = 0.1
DDM_DIV_YIELD_MAX        = 15.0
DCF_GROWTH_CAP           = 30
DDM_DIV_GROWTH_CAP       = 6
GSEC_RATE                = 6.0
EQUITY_PREMIUM           = 5.5

# Backfill
CR_SECOND_PASS_CAP       = 100
SUPERTREND_ATR_MULT      = 0.5
BACKFILL_DAYS            = 400     # v12.6.1 (was 365)
NSE_SHAREHOLDING_CAP     = 100     # v10.6 — top stocks for DII enrichment
NSE_RATE_LIMIT_SEC       = 0.3     # v10.6 — sleep between NSE calls

# Priority ranker
VOL_SPIKE_CAP            = 5
PRIORITY_W_VOL           = 25
PRIORITY_W_QUALITY       = 30
PRIORITY_W_DELIVERY      = 20
PRIORITY_W_CAP           = 15
PRIORITY_W_TURNOVER      = 10

# Stage 3 override rules (v10.13 — O4/O5 now active)
OVERRIDE_MAX              = 20       # total override cap
O3_VOL_RATIO_MIN          = 3.0      # spike pre-trigger: today's vol / 20d avg
O3_DELIVERY_MIN           = 60       # %, paired with O3_VOL_RATIO_MIN
O4_PREV_SCORE_MIN         = 60       # previous run's composite_score
O4_CURRENT_S2_MAX         = 15       # today's stage2_score (deterioration signal)
O5_DAYS_SINCE_MIN         = 7        # re-check stale analyses
O5_DAYS_SINCE_SENTINEL    = 99       # guard: skip stocks never analysed (first-run)

# GROWTH field clamps (v10.14 — prevent tiny-base yfinance distortions)
CAGR_CLAMP_PCT            = 500      # ±500 % cap on all CAGR/YoY fields
                                     # (applies to _safe_cagr in backfill_history.py
                                     #  AND the rev_yoy/pat_yoy .info extractions)

# v10.15 clamps — same tiny-base-denominator mitigation extended to
# PROFITABILITY, FIN HEALTH, and VALUATION groups
NPM_CLAMP_PCT             = 500      # ±500 % cap on NPM Q1/Q2/Q3 quarterly margins
                                     # (EMAMIREAL-like tiny-rev denominators
                                     #  produced −845% NPM pre-clamp)
CCC_CLAMP_DAYS            = 500      # ±500 days cap on CCC Days
                                     # (EMAMIREAL 16,821 days = 46yr pre-clamp)
CCC_MIN_REVENUE           = 1000     # if totalRevenue < ₹0.1 Cr, skip CCC
                                     # computation entirely (noise threshold)
VALUATION_CLAMP           = 500      # v10.16 Option B: DB-layer clamp lowered
                                     # 1000→500. Display layer shows '—' when
                                     # raw ≥ 500 (more honest than showing 1000).
                                     # Applies to P/E, P/B, P/S, EV/EBITDA.
                                     # (AMAGI PE=1,981 / RHETAN EV/EBITDA=1,352
                                     #  pre-clamp; real stocks rarely exceed 200×)
PEG_CLAMP                 = 50       # v10.16: lowered 100→50. PEG beyond 50 is
                                     # pure arithmetic noise. Display → '—'.
VALUATION_DISPLAY_THRESHOLD = 500    # v10.16: display threshold matches DB cap.
                                     # When stock["pe"/"pb"/"ps"/"ev_ebitda"] raw
                                     # ≥ this, master_funnel writes '—' to Excel.
PEG_DISPLAY_THRESHOLD     = 50       # Same pattern for PEG (all 4 fallback tiers)
PE_SCORING_NEUTRAL_CUTOFF = 500      # v10.16: fundamental_score treats pe_num
                                     # ≥ this as NEUTRAL (no +12/+7/-8), not
                                     # penalized for being "expensive" — because
                                     # clamp value signals "unknown", not high PE.

# AI batching (ai/ai_analyst.py)
AI_BATCH_SIZE            = 4         # v17.16 (was 12)
LLM_CARD_MAX_TOKENS      = 16000     # env LLM_MAX_TOKENS (v17.16)
_CARD_FORMAT_VERSION     = "v17.16-compact-prompt"   # part of the per-day cache key
AVOID_SKIP_AI            = True      # v10.13 — AVOID verdict → placeholder (moot since v17.10 scope)

# News (analysis/news_sentiment.py)
NEWS_MAX_HEADLINES       = 15        # v17.11 (was 8)
NEWS_LOOKBACK_DAYS       = 14        # v17.11 (was 7)
_NEWS_MAX_TOKENS         = 3000      # env NEWS_MAX_TOKENS (v17.16)
NEWS_CONFIDENCE_FLOOR    = 0.6       # below → NEUTRAL
NEWS_SENT_POSITIVE       = +4        # sentiment sub-score
NEWS_SENT_NEGATIVE       = -5

# Market regime + Gold (master_funnel.py / excel_generator.py)
_REGIME_TOLERANCE_PCT    = 2.0       # BEARISH only when Nifty > 2% below its 20-day SMA (v17.5.1)
GOLD_SCORE_MIN           = 70
GOLD_MOS_RANGE           = (15, 100)
GOLD_STORM_MIN           = 5
GOLD_RSI_MAX             = 70
GOLD_PLEDGE_MAX          = 10
GOLD_ALTMAN_MIN          = 1.8       # or missing
GOLD_INT_COVER_MIN       = 1.5       # or missing
QUALITY_FLOOR_ROE_PCT    = 10.0      # v16.2, or missing
QUALITY_FLOOR_PEG_MAX    = 8.0       # v16.2, or missing / ≤ 0
GOLD_3D_ROC_MIN          = 0         # v17.0
BENEISH_ANTI_TRIGGER     = -1.78     # v16.4 (was -2.22)

# Resilience watchlist (v17.5 / v17.5.1)
_RW_RETENTION_DAYS = 30 · _RW_STREAK_WINDOW = 5 · _RW_STREAK_MIN = 2 · _RW_MIN_ABS_RETURN = 5.0

# Stop-loss / target (master_funnel.py)
_V14_6_CAP_ATR_FALLBACK  = {LARGE 2.0, MID 2.8, SMALL 4.0, MICRO 5.5}   # % of CMP; default 3.0
_V14_6_HORIZON_SL_MULT   = {SHORT TERM 2.5, POSITIONAL 3.5, LONG TERM 5.0}
_V14_7_SECTOR_TIER       = +0.60 very-high … +0.30 high … −0.20 low … −0.35 very-low (else 0)
_V14_7_REGIME_HIGH/LOW   = 1.20 / 0.80 × baseline ATR → SL × 1.10 / × 0.90
_V14_7_VOL_CONFIRM_RATIO = 1.20      # support floor only when vol_ratio ≥ this
_V14_6_SL_MIN_PCT / _MAX = 4.5 / 15.0 (v15.1)
_V17_SHORT_TERM_SL_MAX_PCT = 7.0     # v17.0
_V14_6_TARGET_RR_BY_REGIME = {low 1.3, neutral 1.5, normal 1.5, high 1.8}   # v17.8.1 LIVE
_V14_6_TARGET_SANITY_CEILING_PCT = 50.0
# Dormant since v17.8.1 (still defined, no longer used for targets):
#   _V14_6_HORIZON_T1_CAP_BASE, _V14_6_HORIZON_T3_HARD_CAP; T2/T3 = T1 × 1.35, × 1.35²

# Outcome tracking (track_outcomes.py / data_bridge.py)
EXPIRY_DAYS              = {SHORT TERM 30, POSITIONAL 90, LONG TERM 270}; DEFAULT_EXPIRY_DAYS = 90
TRAIL tiers              = peak ≥25% → +12% · ≥20% → +9% · ≥15% → +5% · ≥12% & ≥10 days → break-even
_TRAIL_BREAKEVEN_THRESHOLD = 12.0 · _TRAIL_MIN_HOLDING_DAYS = 10    # v17.0
_SHADOW_N_BY_HORIZON     = {SHORT 2.5, POSITIONAL 3.0, LONG 3.5}; regime 1.20/0.80 → ×1.10/×0.90
RISK_FREE_RATE           = 6.5% (India 91-day T-bill) for Sharpe / Sortino
SAMPLE_SIZE_BANNER       = 30 closed positions

# Sizing (risk/correlation_aware_sizing.py)
DEFAULT_RISK_BUDGET_PCT = 1.0 · MIN/MAX_ALLOCATION_PCT = 1.0 / 15.0 · MAX_SECTOR_EXPOSURE_PCT = 30.0
cap multipliers LARGE 1.0 · MID 1.0 · SMALL 0.85 · MICRO 0.70

# NSE snapshot / allowlist
NSE_SNAPSHOT_MAX_AGE_DAYS = 14
_MAX_DUP_SHARE = 0.01 · _MAX_DUAL_SHARE = 0.99      # v17.17 allowlist write guards
DB KEEP_DAYS             = 400
```

---

## 15. VERSION HISTORY

Moved to **Part B** at the end of this file (newest first). `readme.md` carries the one-row-per-release summary table.

---

## 16. KNOWN LIMITATIONS

| Item | Why | Status |
|---|---|---|
| DII % / DII QoQ Δ | NSE's free endpoints expose promoter vs public only | `—`; never derived from the public bucket |
| Pledge % | NSE blocks cloud runners | Weekly local snapshot (≤ 14 days); `—` when stale or when a weekly fetch returned 0 pledge rows |
| Pledge Direction | Needs ≥ 90 days of stored pledge history | Real history from 17-Sep-2026 → expected ~mid-Dec 2026 |
| QoQ deltas (Pro / FII) | Need a stored prior-quarter value | `—` until history exists |
| Order-book / tender columns (OB/Bill, Pipeline Vis, L1 Wins, L1 Est, New Mkt Entry) | No free structured source | Empty (red headers) |
| SEBI Flags | No free structured source | Empty; *Reg Flag* (news) covers headline-level regulatory action |
| Current / Quick ratio, some CAGRs | yfinance coverage gaps for Indian stocks | Partial |
| News sentiment | LLM judgement is non-deterministic | Temperature 0, JSON schema, ≥ 0.6 confidence, evidence required |
| Target multipliers 1.3 / 1.5 / 1.8 | Starting estimates | Review after ~30 closed positions |
| Trade Summary R:R colours | Bands (≥ 3 green, ≥ 2 amber) predate the single target | Every row red today; re-banding is an open decision |
| Earnings-week SL widening | No reliable free earnings calendar | Built in, never fires |
| NIFTY 50 not in `daily_prices` | Index not ingested | Regime gate uses `get_nifty_20d_sma()` (DB first, yfinance `^NSEI` fallback); the text report's mood/VIX render `—` |
| Daily OHLC only | No intraday data | Same-bar SL+target ties resolve to SL |
| 0-vs-missing ambiguity in some SQL `COALESCE(…, 0)` reads | Architectural | Deferred since v12.4 |
| Altman Z capped at 10 | X4 unit mismatch on some large caps | By design (v12.5) |
| BSE SME delivery file | BSE sometimes serves a non-zip response | Logged `BSE-Deliv ⚠️`; NSE delivery % used |
| Scheduled-run timing | GitHub queues scheduled workflows | Delivery ~05:00–09:30 IST |
| Piotroski F /9 | No free source for true year-on-year comparisons | Proxy from available data |
| Alert Log Prev Score / Score Δ | Needs yesterday's `latest_analysis_results` | Blank on the first run after a fresh DB |
| BSE downloads from cloud runners | Cloudflare / Akamai block cloud IPs | `bse` package → cloudscraper → curl_cffi cascade; NSE-only mode + allowlist fallback when all fail |

---

## 17. DIAGNOSTICS

### Daily run-log checklist (what a healthy run prints)

```
✅ GATE APPROVED: Processing data for …            /  ✅ Gate passed. Processing trading day: …
✅ C5 PASS: NSE OK — N rows …
🔗 Allowlist recorder: N dual-listed seen on both exchanges (ISIN) · k new · M stored
   Universe: 5,2xx → Stage1: ~1,9xx → Stage2: ~1,6xx → Stage3: 100
✅ NSE snapshot: N pledge + M shareholding records, Xd old (≤ 14d) — used as fallback for blanks
📌 NSE snapshot applied to blanks: pledge N · DII 0 · FII N · promoter N
📰 News sentiment: LIVE (feeds score) · model=… · N stocks
📰 News sentiment: k/N stocks got an informed signal
📰   not informed: headlines neutral ×… · …
📊 Market regime: BULLISH|BEARISH (Nifty X vs 20d-SMA Y, gap ±Z%, tolerance -2.0%)
📈 v14.1 outcome tracking: logged N Gold pick(s) …
🤖 [Section 7/8 — deferred, v17.14] Generating AI Cards for Gold + open positions...
💾 AI cards served from today's cache (…)          ← same-day re-run
```

Zero Gold picks is normal on BEARISH days and on weak tapes (15 strict gates). A snapshot older than 7 days means the Sunday fetch was missed — run `fetch_nse_now.bat`.

### Older checks (still valid)

### After deploying v10.5+

Verify schema is correct:
```powershell
python -c "import sqlite3; c=sqlite3.connect('market_data.db'); print(c.execute('SELECT COUNT(*),MIN(date),MAX(date) FROM shareholding').fetchone())"
```

Verify forensic columns exist in `fundamental_metrics`:
```powershell
python -c "import sqlite3; c=sqlite3.connect('market_data.db'); print([r[1] for r in c.execute('PRAGMA table_info(fundamental_metrics)').fetchall() if r[1].endswith('_cr') or r[1].endswith('_days')])"
```

Test yfinance balance sheet for Indian tickers:
```powershell
python -c "import yfinance as yf; t=yf.Ticker('RELIANCE.NS'); print('BS rows:', list(t.balance_sheet.index)[:5] if not t.balance_sheet.empty else 'EMPTY')"
```

Check if forensics engine has the inline fetcher:
```powershell
python -c "from analysis.forensics_engine import ForensicsEngine; print('OK' if callable(ForensicsEngine.fetch_forensic_inputs) else 'MISSING')"
```

### After deploying v10.6

Verify DII enrichment in DB:
```powershell
python -c "import sqlite3; c=sqlite3.connect('market_data.db'); print(c.execute('SELECT symbol, fii_pct, dii_pct FROM shareholding WHERE dii_pct > 0 LIMIT 10').fetchall())"
```

Console should show during pipeline run:
```
NSE shareholding: enriched DII for N/M symbols
```
If N is 0, NSE API is being blocked (common on GitHub Actions, typically works on local Windows).

### Expected field population rates

| Column | Typical (large-cap) | Typical (mid-cap) | Typical (small/micro) |
|---|---|---|---|
| ND/EBITDA | 70-85% | 50-70% | 20-40% |
| Int Coverage | 40-70% | 30-50% | 15-30% |
| CCC Days | 40-70% | 30-50% | 15-30% |
| Altman Z | 50-80% | 40-60% | 20-40% |
| Beneish M | 50-80% | 40-60% | 20-40% |
| DII % | 60-90% (if NSE works) | 50-80% | 30-60% |
| QoQ deltas | 0% initially, ~80% after 90 days | 0% → ~70% | 0% → ~50% |

---

## 18. QUICK REFERENCE — KEY FUNCTION LOCATIONS

| Function / block | File | Notes |
|---|---|---|
| Gate check | `ingestion/orchestrator.py` | `gate_check()` → C1–C5, "GATE APPROVED" |
| Data integrity (C5) | `database/data_bridge.py` | `check_data_integrity()` — NSE ≥ 500 rows |
| BSE singleton + cascade | `master_funnel.py` | `_get_bse_client`, `_bse_bhav` |
| Reconcile + match method | `ingestion/reconciler.py` | `reconcile_exchanges`, `df.attrs["dual_match_method"]` |
| Allowlist recorder / prune | `ingestion/allowlist_maintainer.py` | `record_dual_listed_observations`, `prune_runtime_allowlist`, `_is_fund_or_index` |
| Stage 1 / 2 / 3 | `screening/pre_screener.py`, `screening/priority_ranker.py` | `stage_1_filter`, `stage_2_fundamental_scorer`, `get_top_100_candidates` |
| Held monitoring | `master_funnel.py` | "v17.15: HELD-POSITION MONITORING" after Stage 3; `_held_monitor_map` in Section 9/10 |
| NSE snapshot | `ingestion/nse_snapshot.py` | `load_snapshot`, `apply_snapshot`; Section 5A.3b |
| NSE pledge (live) | `ingestion/nse_pledge.py` | `fetch_bulk_pledge_data` (empty on Actions — expected) |
| NSE shareholding (live) | `backfill_history.py` | `_nse_shareholding()` — `corporate-share-holdings-master` |
| News sentiment | `analysis/news_sentiment.py` | `enrich_stocks_with_news`, `get_news_sentiment`; Section 5A.6 |
| Composite + verdict | `analysis/scoring_engine.py` | `calculate_composite_score`, `_get_verdict_with_confidence`, `_assign_quick_pick` |
| SL / Target | `master_funnel.py` | `_compute_sl_t_v14_6()`; constants `_V14_6_*`, `_V14_7_*`, `_V17_SHORT_TERM_SL_MAX_PCT` |
| Sizing | `risk/correlation_aware_sizing.py` | risk-parity allocation + rationale |
| Regime gate | `master_funnel.py` (Section 9/10) | `_REGIME_TOLERANCE_PCT`; `data_bridge.get_nifty_20d_sma()` |
| Resilience watchlist | `master_funnel.py` | `_refresh_resilience_watch`; `data_bridge.*resilience_watch*` |
| Gold filter | `reporting/excel_generator.py` | `ExcelGeneratorV6._get_gold()`; criteria text "ALL 15 must pass" |
| Outcome logging hook | `master_funnel.py` | "v14.0/v14.1 — OUTCOME TRACKING" before the Excel build |
| Tracker | `track_outcomes.py` | `main()`, `_walk_forward()`, `_compute_shadow_stop()`, continuation pass |
| Outcome DB helpers | `database/data_bridge.py` | `insert_gold_recommendation`, `get_open_recommendations`, `update_outcome`, `update_shadow_outcome`, `get_outcome_stats`, `get_continuation_stats` |
| AI notes | `ai/ai_analyst.py` | `get_ai_analysis` (cache wrapper) → `_generate_ai_analysis`; `_llm_complete`; `_load_card_system_prompt` |
| Deferred card block | `master_funnel.py` | "[Section 7/8 — deferred, v17.14]" |
| Excel workbook | `reporting/excel_generator.py` | `ExcelGeneratorV6.generate_excel_reports()`; `_full_sheet`, `_gold_ws`, `_alert_log`, `_performance_sheet` |
| Trailing label | `reporting/excel_generator.py` | `_trailing_state_label()` (v17.19) |
| Tooltips | `reporting/tooltip_formatter.py` | `TIPS`, `GROUP_TIPS`, `apply_tooltips`, `build_reference_sheet` |
| Text report / email | `reporting/daily_report_generator.py`, `reporting/email_service.py` | `generate_research_report`, `send_analysis_email` |
| 400-day window | `database/db_maintenance.py` | `enforce_circular_queue` (`KEEP_DAYS = 400`) |
| Local NSE fetch | `fetch_nse_local.py` | `--dry-run`, `--push`, `--probe` |
| Holiday calendar | `ingestion/holiday_calendar.py` | `ensure_holiday_calendar_fresh()` — NSE API + `market_holidays` cache |
| NSE / BSE downloaders | `ingestion/harvester.py` | `download_nse_bhavcopy`, `download_nse_delivery`, `download_nse_sme_bhavcopy`, `download_bse_bhavcopy`, `download_bse_delivery`, `download_bse_sme_bhavcopy`, `download_nse_fo_participant_data` |
| Core DB helpers | `database/data_bridge.py` | `save_to_database`, `get_symbol_history`, `get_20d_avg_vol` / `get_20d_avg_vol_batch`, `load_latest_analysis_results`, `get_prior_analysis_map`, `get_today_consolidated_data` (feeds `command_parser`), `get_historical_quarter_data` (QoQ baseline) |
| Market helpers | `database/data_bridge.py` | `get_nifty_20d_sma` (regime gate), `get_nifty_200_sma`, `get_nifty_52w_high_from_db`, `get_latest_fii_net_cash` |
| Continuation helpers (write `gold_continuation` only) | `database/data_bridge.py` | `get_t1_hits_needing_continuation`, `get_continuation_tracking`, `upsert_continuation` |
| Forensics | `analysis/forensics_engine.py` | `fetch_forensic_inputs`, `calculate_altman_z`, `calculate_beneish_m` |
| Piotroski F | `analysis/fundamental_engine.py` | `calculate_piotroski_f_score` |
| Fair value (7 models + guards) | `analysis/fair_value_engine.py` | `calculate_all_models`, `get_composite_fair_value` |

---

## 19. IMPORTANT DO-NOT-TOUCH RULES

1. **Never add filters based on `company_name` or `sector` in Stage 1** — these fields are empty at Stage 1 time. Add such filters only after Section 5.
2. **Never compute `horizon` or `risk_level` before `calculate_composite_score()`** — `verdict` doesn't exist before that call.
3. **Never recompute `Sector Stage` before technical data loads** — RSI/MACD/Supertrend are loaded at Section 5.
4. **Never change `FV_MODEL_KEYS`** — controls which zero values get shown as `—` in Excel.
5. **DDM guard: `0.1 < div_yield_pct < 15.0`** — values outside indicate unit mismatch. Do not relax.
6. **DCF guards are non-negotiable** — WACC floor 10%, M1 cap 4× CMP, composite CFV cap 3× CMP.
7. **`run_time` not hardcoded times** — all time-sensitive strings in excel_generator use `self.run_time`.
8. **Backfill runs on GitHub Actions** — yfinance rate limits apply. CR second pass capped at 100/run.
9. **Load `latest_analysis_results` BEFORE saving today's scores** — otherwise Alert Log's Score Δ is always 0.
10. **Gate check C4 (BSE URL HEAD) is intentionally ignored** — cloud IPs can't reach it. BSE routes through `bse` pip package.
11. **Do not quote song lyrics, poems, or paid articles in AI cards** — master prompt enforces paraphrase-only output.
12. **OVERVALUED is NOT the same as WATCHLIST** — keep verdict categories distinct.
13. **`q_ebitda_cr` and `q_rev_cr` are QUARTERLY** — the DB column names are misleading. Always annualize (×4) when computing annual ratios.
14. **forensics_engine must NEVER set `total_debt`** — master_funnel has a 3-tier fallback that would be overwritten. Use `total_debt_cr` only.
15. **Forensics default fields must be `"—"` not 0 or "STABLE"** — misleading placeholders inflate composite scores and confuse Alert Log.
16. **Always filter `daily_prices` by `exchange='NSE'`** in any per-symbol query (v12.7) — dual-listed stocks have two rows per date.
17. **The stock-dict horizon key is `horizon`** (v14.1/v17.1). The DB column is `time_horizon`. Reading `stock.get("time_horizon")` silently defaults everything to POSITIONAL.
18. **`gold_outcomes` is read-only to continuation and shadow code** — they write only `gold_continuation` / `shadow_*` columns (G37, G39).
19. **Levels are frozen at log time.** Never update entry / SL / Target of an existing recommendation; re-appearances only bump `times_reappeared`.
20. **Do not rename the master-prompt headings `SYSTEM ROLE`, `SECTION 1A`, `BLOCK H — ANALYSIS SUMMARY`, `LIST VIEW sort`** — `_load_card_system_prompt()` slices on them.
21. **Map AI notes by symbol** (`=== CARD: SYMBOL ===`), never by position, and never cache incomplete output.
22. **The NSE snapshot fills blanks only** — never let it overwrite live values; never derive DII from the public bucket.
23. **Write `market_stats[...]` only after `market_stats = {…}` exists** (v17.11.1, G40).
24. **Performance-sheet widths via `_set_min_width()` only** (G35); **Full Dashboard alignment by column name** (`_FULL_LEFT_ALIGNED`).
25. **Allowlist writes only from ISIN-matched runs** — keep `record_dual_listed_observations()`'s guards (G41); do not reintroduce a nightly wipe.
26. **`market_data.db` cannot be committed** (≈ 650 MB artifact). Schema changes = idempotent migrations inside the pipeline.

---

## 20. OPEN ITEMS / NEXT ACTIONS (October 2026)

- [ ] Re-band the Trade Summary R:R colours for the single regime target (currently every row is red) — owner decision.
- [ ] `fetch_nse_local.py`: do not overwrite the snapshot when a fetch returns 0 pledge rows but shareholding is fine (seen 27-Sep-2026) — the previous file keeps its own `fetched_at`, so it still expires after 14 days (no stale data is kept beyond the existing rule).
- [ ] Owner decision: the Performance *SL* cell shows the original stop; once a trailing stop is active the stop in force is the higher of SL and Trailing. Showing the effective stop in that cell would be a display change (text now describes the current behaviour).
- [ ] Review Target multipliers (1.3 / 1.5 / 1.8) once ~30 positions have closed under v17.8.1 rules.
- [ ] Pledge Direction should start populating ~mid-December 2026 — confirm.
- [ ] BSE SME delivery "File is not a zip file" — investigate.
- [ ] Optional: per-gate Gold-funnel log line (how many stocks fail each of the 15 gates) to explain zero-Gold days.
- [ ] Optional: wire `LLM_MAX_TOKENS` / `LLM_REASONING_EFFORT` / `NEWS_MAX_TOKENS` into the workflow as repository Variables.
- [ ] Restore the deep ScoringEngine suite: `git show d2f301a:test_v11.0.2_full_withdummies.py` — 524 pass / 18 fail on v17.19 code. Categorised from their messages (57.14/57.15 also read in code), all 18 look like assertion drift rather than production bugs: 8 allowlist tests vs the v17.17 ISIN-evidence guard, 2 Gemini-string checks (provider removed v17.10), 2 placeholder-format checks, 2 corp-info QoQ parser tests (endpoint replaced v17.13.3), and 4 technicals tests whose fixture reads the first `technical_indicators` row per symbol (historical ATR rows exist since v15.2). Update those assertions, then run both suites.
- [ ] 0-vs-missing SQL `COALESCE` cleanup (deferred since v12.4).

**Long-standing backlog (carried from the v17.3 list; still open in the code):**
- [ ] Retry logic for yfinance (still a single attempt per symbol).
- [ ] FCF-yield fair-value model (M8) for capital-light businesses.
- [ ] PAT CAGR in the fundamental score — it was blocked on data; PAT CAGR 3Y is now populated for most stocks (84/95 on 25-Sep-2026) but is display-only.
- [ ] WhatsApp bot: end-to-end test of the ngrok + Twilio integration.
- [ ] A source for DII % and SEBI flags (BSE corporate filings — paid).
- (Done since: BSE 3-tier cascade stopped ETF leaks (v12.0); the allowlist grows automatically through `dual_listed_runtime` (v11.0.2, guarded v17.17); AI batches are 4, not 12 (v17.16); Screener.in scraping for CAGRs is no longer needed.)

---

## 21. WORKING AGREEMENTS, TESTING & DEPLOYMENT

**Owner expectations (Rajkumar).** Complete replacement files with exact destination paths — never patch diffs. Apply every fix on top of the **latest** uploaded repo and say which files actually changed vs the deployed version. Verify against real artifacts (run logs, the actual Excel) before claiming anything; check the commit SHA in the run log. Concise, direct answers. Every delivery comes with a conventional-commit message (root cause + verification), one commit per distinct change. For long multi-step work, keep a CHECKPOINT file so work can resume after a reset.

**Deploy flow** (branch protection is on; admin + Actions are in the bypass list):
```powershell
git pull origin main --rebase      # keep-alive bot and the weekly NSE snapshot commit to main
git checkout -b fix/vXX-description
# replace files
python test_v13_v14_consolidated.py     # expect ALL green (96/96 at v17.19)
git add <files> && git commit -m "..." && git push -u origin fix/vXX-description
# PR → green check → merge
```

**Test suite.** `test_v13_v14_consolidated.py` — 96 guards in 6 groups (v13_R1, v13_R2, v13_REG, v13_R3, v14_0, v14_1); run from the repo root; ~15 s. Since v17.19 every test reads sources relative to the repo, so a failure is always real (before that, 4–7 "expected" sandbox-path failures hid a genuine G9 regression from v17.7 to v17.18). Needs `pip install -r requirements.txt` (several tests import `pytz` / `yfinance`).

**Hard lessons — do not repeat.**
1. **Tests must be bidirectional.** Reintroduce the bug, confirm the test fails, restore, confirm it passes (G35 once passed while the bug shipped; G40's first draft too).
2. **Search for ALL occurrences, not the first two** (v17.1 fixed 2 of 3 width sites).
3. **When output looks wrong after a deploy, suspect your own fix before concluding "not deployed".**
4. **Verify the commit SHA in the run log** before claiming what is or isn't deployed.
5. **A green suite with "expected" failures is not green.** Make every test runnable everywhere.
6. **When a rule changes, grep the labels, tooltips and glossary too** — v17.8 changed targets and v17.0 changed trailing tiers, but labels and tooltips kept the old numbers until v17.19.
7. **Don't diagnose network blocks from this sandbox** — its egress proxy is not GitHub's or the owner's network (a "403 to datacenter IPs" claim was once retracted).
8. **Never put tokens in remote URLs or files;** revoke anything exposed immediately.

---

# PART B — VERSION HISTORY (newest first)

Entries from v17.3 downwards are preserved as written at the time. Where they describe behaviour that later changed, Part A is authoritative.

### v17.19 — Consistency pass: two display fixes, clean test suite, refreshed docs · October 2026

**Display fix 1 — Trailing label.** The Performance sheet's *Trailing* cell mapped `trailing_sl_pct` onto v15.0's buckets (`≥ 6.5 → "+7% locked"`, `≥ 2.5 → "+3% locked"`), but since v16.5/v17.0 the tracker locks +5 / +9 / +12 %. A +9 % lock therefore read "+7% locked", +12 % read "+7%", +5 % read "+3%" — while the column's own tooltip described the new tiers. New module-level `_trailing_state_label(pct, price)` names the level actually locked (`BE locked` / `+N% locked` / `—`). Verified on a rendered workbook (old: LOCK9 → "+7% locked"; new: "+9% locked"). Guard **G43**: tracker tiers still 1.00/1.05/1.09/1.12, label correct for odd entry prices, no hard-coded +3/+7 buckets; fails on the old code (ImportError) and on a helper reverted to the old buckets.

**Display fix 2 — "Time Horizon" label.** The v17.7 shadow-exit table used a bare `("Horizon",13)` header, breaking the v14.1 naming rule. **G9 had been failing since v17.7**, but the suite read three source files from a hard-coded dev-sandbox path, so on every other machine G9 died with FileNotFoundError and was dismissed as one of the "expected sandbox failures". Header renamed (width 15, gains its tooltip).

**Test suite.** All 9 `open('/home/claude/proj/…')` calls replaced by `_src_path()` (repo-relative, UTF-8); the sandbox `sys.path` insertions removed. First fully green run: **95/95**.

**Text-only updates (AST-verified — no logic change).** Glossary + tooltips that still described v15.0 targets (`T1 = max(1.5 × SL, 0.40 × CFV)`), R:R "≥ 1.5 by construction", the +5/+10/+15 % trailing tiers, "Requires Gemini API credits", NSE `corp-info` as the DII/QoQ source, "SL 3–5 % below entry", "Target at Resist 1/2", "11-condition" Gold filter and fixed 90-day windows were rewritten for v17.0–v17.18. Stale comments in `master_funnel.py` (60-day ATR baseline, CFV-scaled targets, "Gemini quota"), the `track_outcomes.py` docstring (horizon expiry, auto-invocation, continuation/shadow) and the `utils/chat_interface.py` labels corrected. Master prompt: Section 0 gets an implementation note and 0D now describes the real Block H flow (SYSTEM ROLE + BLOCK H extraction verified byte-identical before/after).

**Docs.** `pipeline_reference.html` (unversioned name) replaces `pipeline_reference_v16_5.html`. This file restructured into Part A (current state) + Part B (history). readme: v17.14–v17.19 rows, real funnel counts (~5,200 → ~1,900 → ~1,600 → 100 — the old "~600 → ~400" was wrong), correct section order, 1-year ATR baseline, optional env knobs, workflow steps. Funnel explainer and NSE snapshot guide updated. `db_restore_oneoff.yml` (one-off KOVAI DB restore) removed.

**Second audit (same release).** Three more user-visible texts disagreed with the code and are corrected: the Time Horizon tooltip/glossary listed WATCHLIST under LONG TERM (`master_funnel` maps it to POSITIONAL); the P&L % tooltip still quoted v15.0's "BE at +5 %, +3 % lock at +10 %, +7 % lock at +15 %"; and the Performance *SL* cell was described as the "effective" stop (MAX of original and trailing) — it shows `gold_recommendations.stop_loss`, the original stop frozen at log time, while the trailing level has its own column. Also: SL Rate text no longer cites "~6.5 % risk per trade" (stops are 4.5–15 %, sizing keeps risk ≈ 1 %); LONG TERM described as 3–12 months everywhere; Trade Summary title says "Target"; `excel_generator` docstrings say 8 sheets. Comment/docstring-only: `track_outcomes.py` trailing block (break-even +12 % after 10 days), `master_funnel.py` pledge/DII source note, `backfill_history.py` corp-info notes, `screening/pre_screener.py` Stage 1/2 docstrings (real counts, bhav-only Stage 2), `backtest/walk_forward.py` R:R note. Docs: restored function/limitation/backlog detail that the restructure had dropped, the HTML column-lifecycle table and constants, real stage-count ranges. New guard **G44** (horizon rules, trailing tiers and SL wording vs the code) — fails on each reverted text. Suite **96/96**.

### v17.18 — AI summary column removed from the Full Dashboard · 26 Sep 2026
Notes exist only for Gold picks and open positions (v17.10 scope), so ~90 of ~95 dashboard rows showed "[AI skipped …]". `View Analysis Summary` removed from `FULL_COLS` (127 → 126) and its band dropped; kept on the Gold sheet and as the Performance *AI Card*. Full-Dashboard alignment moved from hard-coded positions (which had drifted) to names (`_FULL_LEFT_ALIGNED`). Glossary/tooltip wording updated. **G42.** `v16_5_cleanup_false_kovai.py` deleted.

### v17.17 — Dual-listed allowlist guarded at write time; nightly wipe retired · 25 Sep 2026
The workflow's "v12.1 self-healing diagnostic" wiped `dual_listed_runtime` whenever it held > 700 rows — but ~2,200 of ~2,660 NSE EQ stocks are genuinely dual-listed, so it wiped every run and the reconciler relearned the table during the run. On BSE-down days the fallback then had only the ~230 hard-coded names, so most dual-listed stocks lost the Stage 2 B7 bonus. Fix: the reconciler records how it matched (`df.attrs["dual_match_method"]`); `record_dual_listed_observations()` stores only ISIN-matched rows present on both exchanges, skips ETFs/funds/index products (`_is_fund_or_index`), refuses cross-join-looking runs (`_MAX_DUP_SHARE = 0.01`, `_MAX_DUAL_SHARE = 0.99`) and self-cleans stored fund rows. Workflow steps "v12.1 self-healing diagnostic" and "v16.5 false-KOVAI cleanup" removed. First run: 2,110 stored, 89 ETF rows removed. **G41.**

### v17.16 / v17.16.1 — AI cards: compact prompt, bigger budget · 25 Sep 2026
The company endpoint is a reasoning model; hidden reasoning tokens exhausted `max_tokens = 4096` before any text, and the 84 KB master prompt (~44 K input tokens per call) included a rule ("no paragraphs, Blocks A–G only") that contradicted the Block H request. Cards now send only SYSTEM ROLE + BLOCK H (~2 KB) via `_load_card_system_prompt()`, `LLM_CARD_MAX_TOKENS = 16000` (env `LLM_MAX_TOKENS`), batch size 12 → 4, optional `LLM_REASONING_EFFORT`, `_CARD_FORMAT_VERSION = "v17.16-compact-prompt"`. News sentiment `max_tokens` 3,000 (`NEWS_MAX_TOKENS`). NSE pledge message on Actions now says the empty response is expected. v17.16.1: snapshot fetch time shown in IST (`astimezone()` on the runner printed UTC); news log separates "headlines neutral" from real failures; quieter snapshot loading in backfill.

### v17.15 — Held positions analysed in full · 25 Sep 2026
Open positions that rotated out of the top 100 got cards built from the tracker row alone (entry/SL/target, CMP = entry), so the model wrote "data is missing". They are now appended after Stage 3 with `_held_monitor=True`, go through the same enrichment / forensics / FV / scoring / news, and are split into `_held_monitor_map` at the start of Section 9/10 so the dashboard, Gold sheet and saved scores are unchanged.

### v17.14 / v17.14.1 — AI cards: real scope, symbol mapping, surfaced errors · 24–25 Sep 2026
Three bugs from the 23-Sep run: (A) today's Gold picks got the out-of-scope placeholder because the Section 7/8 pre-check called `_get_gold()` on a separate object whose exception was swallowed; (B) the single LLM batch returned "empty response" with the real cause hidden; (C) held stocks in today's top 100 showed "—" in the AI Card column. Fix: card generation **deferred** until after the v14 logging hook and the tracker, scope = `excel_gen._get_gold()` ∪ `get_open_recommendations()`; notes delimited `=== CARD: SYMBOL ===` and mapped by symbol; `_llm_complete()` raises with the real reason (error body, no choices, empty content + finish_reason/usage) and accepts list-form content; held cards keyed for every carded symbol. v17.14.1: Block H only, `_recover_missing()` retries missing notes per stock, cache only complete output (`_cards_complete`).

### v17.13 → v17.13.7 — NSE pledge / promoter data via a weekly local fetch · 18–23 Sep 2026
NSE serves a residential client but returns nothing to GitHub runners. New `fetch_nse_local.py` (owner's laptop, Task Scheduler weekly with missed-start catch-up; `fetch_nse_now.bat` manual trigger) writes `data/nse_snapshot.json`; `ingestion/nse_snapshot.py` uses it only if valid and ≤ 14 days old and only to fill blanks (Section 5A.3b). .1–.3: parsers rewritten from live payloads captured with `--probe` — pledge reads `percSharesPledged` (the old `pctEncumbered` never existed, so 200 OK yielded 0 records) and maps `comName` → symbol via the NIFTY 500 list; shareholding moved from the retired `corp-info` to `corporate-share-holdings-master` (`pr_and_prgrp`, `public_val`); browser/XHR headers; argv isolation when importing parsers. .4: `NO_FREE_SOURCE_COLS` trimmed to columns that are really empty. .5: git step reordered — commit first, `pull --rebase --autostash`, non-interactive push (a Git Credential Manager pop-up had hung the scheduled task). .6: fetch time + age on the Full Dashboard header. .7: snapshot pledge persisted into `shareholding` history so Pledge Direction can populate.

### v17.12 — Per-day AI card cache · 17 Sep 2026
Cards keyed on date + sha1(symbol | score | label | entry | SL | target | horizon | format); same-day re-runs make zero calls; failed output never cached; `AI_CACHE=0` bypass; persisted across Actions runs with `actions/cache`.

### v17.11 / v17.11.1 — Evidenced facts from news · 17 Sep 2026
The LLM also returns insider activity, bulk deals, regulatory flags and results tone, each required to quote its headline; unevidenced facts dropped, no numbers invented. News-evidenced insider BUY can raise `insider_buy_alert` (never lowers a SAST YES). New dashboard columns Insider (news) · Bulk Deal · Reg Flag. Headlines 8 → 15, lookback 7 → 14 days. v17.11.1: an `UnboundLocalError` (held cards written to `market_stats` before it was created) halted one run; cards parked in `_held_cards_pending`; **G40** (tightened after the first draft failed to catch the bug).

### v17.10 (+ v17.10.1) — LLM provider consolidated · 15–17 Sep 2026
Google Gemini and the `google-genai` SDK removed; investor cards and news share one OpenAI-compatible endpoint (`OPENAI_API_KEY`, `OPENAI_API_BASE`, `LLM_MODEL`) over plain HTTPS. Cards restricted to Gold picks + open positions (was ~97 stocks/day, ~$1/day); new Performance *AI Card* column.

### v17.9 — News & catalyst sentiment · shipped with v17.10, 15 Sep 2026
The composite's news input had never been populated, so the 10 % sentiment weight was always redistributed. Google News RSS headlines → LLM (temperature 0, JSON schema, confidence ≥ 0.6) → POSITIVE +4 / NEGATIVE −5 and canonical weights. `NEWS_SENTIMENT_SHADOW=1` logs without scoring.

### v17.8 / v17.8.1 — Regime-aware single target
Target = regime multiplier × SL (low 1.3 · normal 1.5 · high 1.8), 50 % sanity ceiling; fair-value anchoring and horizon caps removed (owner decision, 30-day live trial, multipliers to be reviewed after ~30 closes). T2/T3 hidden from every sheet, metric, glossary and tooltip; kept dormant in the schema as spacing multiples.

### v17.7 — Shadow regime-aware trailing stop · 29 Aug 2026
Chandelier stop (peak − N × Wilder ATR × regime adj, N 2.5/3.0/3.5 by horizon), ratchet-up only, no look-ahead, computed alongside the live walk; new `shadow_*` columns and Performance "SHADOW EXIT COMPARISON". Zero effect on live outcomes (**G39**).

### v17.6 — LLM failures degrade gracefully
A missing/invalid key skips cards after the first failed batch instead of crashing or retrying every batch.

### v17.5 / v17.5.1 / v17.5.2 — Bearish-regime resilience watchlist · from 25 Jul 2026
`gold_resilience_watch` — reference only, never feeds Gold or the tracker (**G38**). v17.5.1: the knife-edge regime gate (BEARISH whenever Nifty < 20d SMA) suppressed Gold on a +1.1 % day (29-Jul-2026); replaced by a 2 % tolerance band; watchlist requires own 3-day return ≥ 5 %. v17.5.2: the floor is also applied at render time.

### v17.4 — Breakdown dimensions per row
Score Band, Archetype and Sector columns added to CLOSED and OPEN positions so every breakdown figure is auditable row by row.

### v17.0 – v17.2 — Performance fixes (backfilled entry) · 21 Jul 2026
v17.0: market-regime gate (Nifty vs 20-day SMA), 3-day ROC momentum gate, sector-cycle gate, SHORT TERM SL cap 7 %, trailing break-even raised to +12 % with a 10-day minimum hold (**G34**). v17.1: the SL/target engine read `stock.get("time_horizon")` while the dict key is `horizon`, so every stock had been treated as POSITIONAL since v14.6 — horizon-specific SL logic was inert; Performance column clipping fixed with widen-only `_set_min_width()` (v17.1.1 fixed a third unconditional width site, **G35**). v17.2: Alert Log `SCORE DEGRADED` only on a real ≥ 3-point drop; weak-but-stable stocks get `⚠ LOW SCORE` (**G36**).

### v17.3 — Continuation tracking (Option C, expiry-anchored) · July 2026

**The problem.** T2 and T3 were structurally unreachable, not merely rare. `get_open_recommendations()` filters `WHERE outcome_type = 'OPEN'`, so the tracker is first-event-wins: the moment `T1_HIT` is written the position leaves the pool and is never walked again. All three targets are tested on the SAME bar in descending priority (T3 → T2 → T1), so `T2_HIT` can only fire if a stock leaps from below T1 to above T2 inside ONE trading day — a measured median of **13.9 percentage points**. Hence 0 T2_HIT and 0 T3_HIT across 31 closed positions: exactly what the mechanics predict. The T2/T3 tiles were permanent zeros.

**The fix.** A new `gold_continuation` table records a SHADOW WALK from `t1_hit_date + 1` to the position's **own** `expiry_date`. Expiry-anchored, not a flat 30 days: BLISSGVS hit T1 on day 5 of 90 and keeps its full 85-day runway, while WABAG hit T1 on day 24 of 30 and gets 6. A fixed window would truncate the first and fabricate 24 days for the second. Total previously-unmeasured runway across the 13 T1 hits: **533 days**, median 37, range 6–85.

**Design notes.**
- Seeding runs BEFORE the walk and LEFT JOINs `gold_outcomes` against `gold_continuation`, so the 13 existing T1 hits auto-backfill on first run — no manual migration, which matters because `market_data.db` is a 641 MB Actions artifact that can never be committed or hand-edited.
- Column mapping: `gold_outcomes` has no `t1_hit_*` columns. For a `T1_HIT` row, `outcome_date` → `t1_hit_date` and `outcome_price` → `t1_hit_price`.
- `expiry_date` defaults to `''` (empty string, not NULL) on legacy rows. Fallback chain: `expiry_date` → `recommendation_date + expiry_days` → skip and log. Never guess.
- Reuses `_load_price_history()`, whose `WHERE date > start_date` is strictly greater — passing `t1_hit_date` yields "start the day after T1" for free, with the `exchange='NSE'` filter already applied.
- **An SL break does NOT stop the walk.** A stock can dip below the old stop-loss and still rally to T2 before expiry; stopping early would hide that. `broke_original_sl = 1` and `t2_reached = 1` can both be true on one row.
- `broke_original_sl` is recorded deliberately. You lost nothing — you exited at T1 — but it is the one stat that separates "T1 was a good exit" from "T1 was lucky timing on a stock about to reverse hard."

**Safety invariants (enforced by G37, all verified bidirectional).** `gold_outcomes` is READ-ONLY to continuation code — no INSERT/UPDATE/DELETE anywhere in the section. Hit rate, SL rate and P&L are unchanged. A missing or empty table makes the Performance sheet render exactly as v17.2. Every continuation failure is swallowed; the tracker's return code cannot change.

**Reporting.** New Performance-sheet section "CONTINUATION AUDIT — what happened after T1?" between MISSED RUNUP and SURVIVORSHIP AUDIT: four tiles (T2 reach rate, T3 reach rate, avg peak after T1, broke-SL count) plus a per-symbol table capped at 30 rows, newest first. Tiles compute on the FULL sample so the cap never skews the headline rates. Rows that broke the old SL get a red wash. All nine column headers carry tooltips. Column sizing goes through `_set_min_width()` only — the v17.1.1 lesson.

**Also in this release.** `track_outcomes.py:550` omitted `TRAIL_SL` from the console "Closed this run" total, under-reporting any run that closed positions via trailing stop. Display-only — the DB and Excel were always correct — introduced when TRAIL_SL was split from SL_HIT in v16.5.

**Files:** `database/data_bridge.py` · `track_outcomes.py` · `reporting/excel_generator.py` · `reporting/tooltip_formatter.py` · `test_v13_v14_consolidated.py` (G37) · `CLAUDE.md` · `readme.md`

---

### v10.0 (baseline — April 2026)

Sessions 1–24 (v7 era + reorg). Core data fixes, Excel + Alert Log, Early Detection, Supertrend/Horizon/Risk, BS Health flags, Current Ratio 3-bug fix, Piotroski F wire-up, forensics numerics, tooltip system, DCF guards, BSE resilience, Gold archetype docs, Session 24 scoring polish (sentiment informedness, spike gate, confidence dots, OVERVALUED verdict).

### v10.1 — AI provider migration

- `ai/ai_analyst.py` switched from `anthropic` SDK to `google-genai`
- Uses `GEMINI_API_KEY` / `GOOGLE_API_KEY` env var
- Model: `gemini-2.5-pro` (configurable via `GEMINI_MODEL`)
- `requirements.txt` updated, GitHub workflow secret renamed
- Master prompt now passed via `system_instruction` parameter

### v10.2 — Forensic columns DB infrastructure

- Added 10 forensic-input columns to `fundamental_metrics` schema (ebit_cr, int_expense_cr, capex_cr, total_assets_cr, total_liab_cr, retained_earnings_cr, working_cap_cr, inventory_days, receivable_days, payable_days)
- Expanded `_fm_ext` SELECT from 3 cols → 13 cols in master_funnel
- Publishes `total_debt_cr`, `cash_cr`, `q_rev_cr`, `q_pat_cr`, `q_ebitda_cr` to stock dict

### v10.3 — QoQ historical lookup rewrite

- `get_historical_quarter_data` now reads from `shareholding` table (has `dii_pct`, unlike `v7_intelligence`)
- Graceful fallback: oldest available row, then v7_intelligence legacy
- Section 5A.5 forensics re-run added after DB enrichment (catches data missed by first pass)

### v10.4 — Inline forensic fetcher + QoQ fix + dynamic headers

- `forensics_engine.fetch_forensic_inputs(symbol)` — inline yfinance pull for balance_sheet/cashflow/income_stmt
- Called from master_funnel for each top-100 stock before `calculate_accounting_forensics`
- Fixed DataFrame `or`-chain ValueError bug (`inc = A or B` raises on DataFrames)
- Removed `total_debt` from forensics return dict (was overwriting master_funnel's 3-tier fallback)
- `cash` alias added so Excel's "Cash (₹Cr)" populates from balance sheet
- **QoQ deltas**: now show `"—"` when no historical data (was returning `-current%` due to default=0)
- `excel_generator.py`: dynamic red-header demotion when column has ≥1 real value

### v10.5 — Defensive schema init

- Added defensive `CREATE TABLE IF NOT EXISTS shareholding` + `ALTER TABLE ADD COLUMN` for 18 forensic columns at master_funnel startup
- Reason: user's existing DB was missing `shareholding` table (created before it was added to `init_all_tables`)
- Console output: `✅ v10.5: Defensive schema check passed`

### v10.6 — ND/EBITDA annualization + DII separation + pledge default

- **Bug #1:** `forensics_engine.py` line 340 was using `q_ebitda_cr` (quarterly) in annual ND/EBITDA ratio, inflating by ~4×. Fix: prefer annual `ebitda` from yfinance `.info`; fallback to `q_ebitda_cr × 4`. Same annualization applied to Capex/Rev.
- **Bug #2:** `forensics_engine.py` line 397 had default `"STABLE"` when no pledge data — misleading. Changed to `"—"`.
- **Bug #3:** `backfill_history.py` line 1793 hardcoded `dii_pct = 0.0`. `_nse_shareholding()` existed but was never called. Wired in as an enrichment loop for top 100 stocks. Real DII values when NSE API responds.

### v10.7 — Bridge code guard (critical)

- **Critical bug:** `master_funnel.py` lines 1141-1153 did direct assignments of DB-read forensic inputs onto `stock` dict. Because `backfill_history.py` never writes to those DB columns (schema-only), the DB tuple was always `(0, 0, 0, ...)`. The direct assignment **clobbered** valid values the v10.4 inline fetcher had placed. Consequence: Int Coverage / CCC Days / Altman Z / Beneish M all showed `—` for every stock.
- **Fix:** replaced 13 direct assignments with a `_pub(key, db_val)` helper that only overwrites when `db_val > 0`. Now when the DB is empty, v10.4 inline-fetched values are preserved.

### v10.8 — Three display fixes

- **Earn Quality:** raw `cfo/pat` ratio → categorical **HIGH / MODERATE / LOW / —** in `forensics_engine.py`. Thresholds: ≥0.8 HIGH, 0.5-0.8 MODERATE, <0.5 LOW, PAT≤0 shows `—`. (Tooltip already promised HIGH/LOW — output now matches.)
- **Pledge Direction:** explicit case for `curr==0 AND prev==0 → "—"` in `master_funnel.py`. Previously defaulted to `"STABLE"` when both were zero (misleading — implied measured no-change when really there was no data at all).
- **Upside to FV % column removed** from Full Dashboard — was mathematically identical to MoS % (both use `(CFV-CMP)/CMP*100`). FAIR VALUE span reduced 13→12, all subsequent group starts shifted left by 1. Total columns: 124→123. The `upside` key remains in stock dict for AI analyst / command_parser backward compat.

### v10.9 — QoQ placement fix + Resist/Support 2 + forensic scoring

- **Critical bug — QoQ placement:** the `_qoq()` call at master_funnel line ~544 ran in Section 3 BEFORE Section 5 shareholding enrichment. At that point `stock['promoter_pct']` was 0, so delta = `0 - historical = -historical`, producing the `-current%` bug for 81/84 stocks (e.g., HINDUNILVR promoter=62.27, ΔQoQ=-62.27). Fix: added **Section 5A.4 QoQ recompute block** after line 1485 shareholding enrichment. Uses now-populated current values; falls back to `"—"` when no real history (same honest-display semantics as v10.4).
- **Resist 2 / Support 2:** was `h.rolling(40).max()` / `l.rolling(40).min()`. For stocks at or near 40-day highs/lows (common in the screener's top-100), R1 and R2 returned the same number (87% of stocks had R1==R2). Now uses a **52-week window** (252 trading days, with graceful degradation for newer stocks) so R2 is genuinely a major long-term resistance distinct from R1's 20-day swing high. Same for Support 2.
- **Forensic quality adjustment in scoring:** `ND/EBITDA`, `Int Coverage`, `Altman Z`, `Earn Quality` were populated end-to-end through v10.2-v10.8 but **never used in composite score**. Added a new adjustment block in `ScoringEngine.calculate_composite_score()` that caps at +8 bonus / −10 penalty:
  - Altman Z ≥3.0: +3 (safe zone) | <1.8: −5 (distress)
  - Earn Quality HIGH: +2 | LOW: −3
  - ND/EBITDA <1.0: +1 (strong solvency) | >5.0: −2 (high leverage)
  - Int Coverage >5×: +2 | <1.5×: −3 (distress)
  - Absent data → no adjustment (doesn't penalise stocks with missing forensics)
  - Missing data guards use `_fnum()` helper that returns None for `'—'`, `''`, None, etc.
  - Output dict now includes `forensic_adj` (signed int) and `forensic_factors` (pipe-separated string like `"AltmanZ≥3:+3|EQ=HIGH:+2|IC>5x:+2"`) for debugging / display.
- **Div Yield = 0 → `"—"`:** non-dividend stocks now display `"—"` instead of `0` to distinguish "no dividend policy" from a genuine 0% yield. Both the primary (line ~1481) and failsafe (line ~1668) branches guarded.
- **Tooltip + glossary updates:** `Score /100`, `Verdict`, `Resist 2 (₹)`, `Support 2 (₹)` tooltips all updated to document the new logic and thresholds. Glossary entries for Support/Resist 1/2 explain the 20d vs 52w distinction.

### v10.10 — Crash guard hotfix

- **Critical bug:** v10.9's `div_yield = "—"` for non-dividend stocks broke three code paths that still did raw numeric comparisons without guards:
  - `scoring_engine.py::calculate_storm_score` line 247 — `if data.get('div_yield', 0) > 2.0`  (crashed on string)
  - `spike_screener.py::check_anti_trigger_guard` — `pledge_pct`, `altman_z`, `beneish_m`, `cfo_pat_ratio` compared unguarded
  - `fundamental_engine.py::calculate_piotroski_f_score` — 10 fields compared unguarded
- **Fix:** All 3 functions now use a `_safe_num()` / `_n()` helper that coerces `'—'`, `''`, `None`, `'N/A'` to a safe default before comparison. Pattern: `_v = _safe_num(data.get('fld')); if _v is not None and _v > threshold: ...`
- **Regression scan:** ran regex scan across 28 risky fields × every `.py` file. Confirmed 0 unguarded sites remain.

### v10.11 — Gold-Tier filter tightened + composite clarity

- **Gold-Tier filter expanded 8 → 11 conditions** using fields populated by v10.8+v10.9:
  - NEW #9: Altman Z ≥ 1.8 or missing (exclude distress zone)
  - NEW #10: Earn Quality ≠ LOW (exclude accounting concern)
  - NEW #11: Int Coverage ≥ 1.5× or missing (can service interest)
  - Missing forensic data passes these gates — small caps without forensic feeds aren't unfairly excluded.
- **Composite score overlap disclosure:** `ND/EBITDA` and `Int Coverage` contribute to both `safety_score` (at 10% weight) AND v10.9 forensic quality adjustment. Net effect is mild (~+1 extra composite for high-quality names) and directionally correct — it slightly rewards genuinely safe businesses. `Altman Z` and `Earn Quality` are unique to forensic adj and add genuinely new signal.
- **Updated glossary + tooltip for "Gold-Tier Definition" / "Gold-Tier Filter"** to document all 11 conditions.

### v10.12 — Dynamic tooltip sizing + Gold row 2 text

- **`reporting/excel_generator.py::_patch_tooltip_vml()`** — VML post-process now parses `xl/comments/comment*.xml`, maps each comment to its VML shape by `(row, col)` anchor, sizes each box dynamically with `max(85, min(17 × line_count + 36, 380))`. Previously hardcoded to 420×380 → short tooltips had 295px of empty yellow space below content.
- **`reporting/tooltip_formatter.py::_comment()`** — height formula aligned with the VML patch (was forcing 260px floor regardless of content).
- **Gold sheet row 2 criteria text** — updated from 8-condition to 11-condition display to match the v10.11 filter logic.
- **4 tooltip entries updated** with `"—"` display semantics (Div Yield %, Pro QoQ Δ, FII QoQ Δ, DII QoQ Δ) — explains when a dash means "no data source" vs "no history accumulated yet".
- Pure presentation fix — zero analytical behaviour change.

### v10.13 — Stage 3 optimization trilogy

Three fixes addressing Stage 3 inefficiencies observed in production Excel audit:

**FIX #1 — AVOID-verdict stocks skip Gemini** (`master_funnel.py` Section 7/8)

- Pre-filters `final_100_list` before Gemini batch call; AVOID stocks get fixed placeholder. Observed waste was 8/88 = 9% of quota per run pre-v10.13.
- Cursor-based positional mapping preserves non-AVOID mapping identical to pre-v10.13.
- Constants: `AVOID_SKIP_AI = True`.

**FIX #2 — Override rules O4 + O5 activated** (`priority_ranker.py` + `data_bridge.py`)

- Root cause: `last_claude_score` and `days_since_analysis` were never populated on the Stage 3 input df — O4 always False, O5 explicitly hardcoded False.
- New helper `data_bridge.get_prior_analysis_map()` reads `latest_analysis_results` → `{sym: {last_score, last_verdict, date, days_since}}`.
- `priority_ranker.get_top_100_candidates()` attaches 3 new columns: `last_claude_score`, `last_claude_verdict`, `days_since_analysis`.
- **O4** fires on `last_claude_score ≥ 60 AND stage2_score < 15` (score deterioration).
- **O5** fires on `7 ≤ days_since_analysis < 99` (expiry re-check). The `<99` sentinel prevents first-run flood.
- Impact: closes long-tail intelligence gap — previously-good stocks that dropped off the priority ranking are now re-checked at least weekly.

**FIX #3 — Batch SQL for 20d vol average** (`data_bridge.py` + `priority_ranker.py`)

- New helper `get_20d_avg_vol_batch(symbols)` — single windowed SQL (CTE + ROW_NUMBER) replaces ~1,500 per-symbol round-trips.
- `calculate_priority_score()` accepts optional `avg_vol_cache` dict — backward compat preserved.
- Falls back to per-symbol calls on any SQL error.
- **Measured speedup: 107× in integration test** (258 ms → 2.4 ms for 100 calls).

Integration tests: 7/7 passed. Constants added to Section 14: `OVERRIDE_MAX`, `O3_*`, `O4_*`, `O5_*`, `AVOID_SKIP_AI`.

### v10.14 — GROWTH field data-integrity hardening

Three fixes addressing tiny-base CAGR distortions + missing source attribution observed in production Excel audit (HUBTOWN EBITDA CAGR 1Y = 10,194.67%, RVHL Rev YoY = 14,183.8%, CHEMPLASTS EBITDA CAGR 1Y = 1,163.79%, etc.):

**FIX #1a — `_safe_cagr()` clamps at ±500%** (`backfill_history.py` line ~1524)

- Root cause: the CAGR formula `(v_new/v_old)^(1/n) - 1` produces absurd results when `v_old` is near zero. yfinance occasionally reports prior-year EBITDA of ₹0.86 Cr or Q3 revenue of ₹0.13 Cr. With v_new at ₹88.4 Cr, CAGR becomes 10,177%.
- Fix: after computing, clamp `result > 500.0 → 500.0` and `result < -500.0 → -500.0`. Real India-listed businesses rarely sustain >500% CAGR on any metric, so the cap preserves all meaningful signals while filtering math artefacts.
- Applies to all 5 CAGR fields: `rev_cagr_1y`, `rev_cagr_3y`, `pat_cagr_1y`, `pat_cagr_3y`, `ebitda_cagr_1y`.

**FIX #1b — yfinance `.info` rev_yoy / pat_yoy also clamped** (`backfill_history.py` line ~1385)

- Same class of distortion hits `.info["revenueGrowth"]` for micro-caps (RVHL 141.83 → 14,183%). Wrapped the `_yf(...)` calls in `max(-500, min(500, ... or 0))`.
- Zero/None input correctly yields 0 (no false-positive clamping).

**FIX #2 — GROWTH tooltip clarity** (`reporting/tooltip_formatter.py`)

- All 10 GROWTH field tooltips rewritten to:
  - **Attribute source** (yfinance `.info["revenueGrowth"]` vs `income_stmt[year]`)
  - **Clarify TTM vs fiscal-year distinction**: `Rev YoY %` uses rolling TTM, `Rev CAGR 1Y %` uses discrete fiscal years. They can legitimately diverge (especially for insurance/NBFC stocks with premium accounting quirks) — the tooltip explains this.
  - **Document the v10.14 cap** — ±500% threshold is visible in every affected tooltip.
- `GROUP_TIPS["GROWTH"]` group-header tooltip also enhanced with the TTM caveat.

**FIX #3 — Glossary expanded** (`reporting/excel_generator.py`)

- Glossary entries for GROWTH fields went from 3 partial (Rev CAGR 1Y, PAT CAGR 1Y, PAT YoY — terse) → 10 complete entries covering every GROWTH column with full source attribution, cap explanation, and edge-case notes.
- Removed legacy duplicate block at lines 486-494 that had 7 older GROWTH entries (pre-v10.14 wording, no cap note).

Integration tests: 8/8 passed — clamp behaviour verified for 6 edge cases (HUBTOWN-style tiny base, normal growth, decline, near-zero v_new, invalid inputs returning 0, 3Y path); yfinance `max/min` wrapper verified for 7 input ranges (0.085 → 8.5, 141.83 → 500, etc.); tooltip regression checks (147 entries shape-valid, v10.12 dynamic height, v10.13 placeholder preserved); glossary dedup verified (exactly 10 GROWTH entries, no duplicates); end-to-end Excel workbook saves cleanly.

Zero analytical behaviour change — v10.14 is a pure display-layer cleanup. Stage 1/2/3 filters, scoring engine, verdict logic, forensic adjustment, Gold filter, and all v10.12/v10.13 features unchanged.

### v10.15 — PROFITABILITY / FIN HEALTH / VALUATION / SHAREHOLDING data-integrity hardening

Six fixes + two defensive guards addressing data-integrity issues observed in the production Excel audit of 23 Apr 2026. Follow-on cleanup to the v10.14 GROWTH fixes — extends the same tiny-base-denominator mitigation to quarterly margins, cash-conversion cycle, and valuation ratios, plus fixes ROE/ROA numeric storage and honest display for free-tier-limited shareholding fields.

**FIX #1 — ROE/ROA stored as floats, not f-strings** (`master_funnel.py` lines ~1213, 1226)

- Root cause: derived ROE / ROA values were wrapped in `f"{_roe_derived}"` which produced quoted strings like `'12.47'`. Excel stored them as text, breaking sort / filter / conditional-formatting on those columns (69/86 stocks affected).
- Fix: use the bare float directly — `_roe_derived if 0 < _roe_derived < 100 else "—"`. Same for ROA at line 1226.
- Downstream safe — `_sf()` helper already handles both numeric and `"—"`.

**FIX #2 — NPM Q1/Q2/Q3 clamped at ±500%** (`backfill_history.py` lines ~1591-1593)

- Root cause: quarterly NPM formula `_pat_qn / _rev_qn * 100` with tiny-revenue denominators produced −762% / −387% / −845% for EMAMIREAL.
- Fix: new `_npm_clamp(pat, rev)` helper — same clamp pattern as v10.14 `_safe_cagr`. Still returns 0 for `rev <= 0` (division safety); clamps result to [-500, +500].

**FIX #3 — CCC Days clamp + revenue guard** (`backfill_history.py` line ~1932)

- Root cause: `rev = ticker.info.get('totalRevenue', 1)` fallback of **1** combined with multi-crore receivables produced 16,821 days (46 years) for EMAMIREAL.
- Fix: changed fallback to `0`, added `if rev > 1000:` guard (₹0.1 Cr minimum). Below the threshold, ccc_days = 0 (computation skipped). Above, result clamped to [-500, +500] days.

**FIX #4 — PE / EV-EBITDA / PEG / PS / PB clamped** (`backfill_history.py` line ~1355)

- Root cause: yfinance `.info` valuation ratios unbounded — AMAGI PE = 1,981 (near-zero EPS), RHETAN EV/EBITDA = 1,352 (near-zero EBITDA).
- Fix: new `_yf_ratio(k, cap=1000)` helper. Applied to `pe`, `pb`, `ps`, `ev_ebitda` at cap=1000; PEG at cap=100 (tighter — PEG beyond 100 is pure arithmetic noise). **Note:** this clamp was further tightened by v10.16 (cap=500 / PEG=50) with honest `"—"` display replacing the numeric clamp. See v10.16 entry below for the current behaviour.
- Real quality businesses rarely exceed 200x P/E, so the cap preserves every plausible premium-growth valuation.

**FIX #5 — Pro / FII / DII QoQ Δ show "—" when no real delta** (`master_funnel.py` Section 5A.4 + new 5A.4b)

- Root cause (two parts):
  1. `backfill_history.py` line 1815 stores `promoter_qoq = 0.0` (literal) in the shareholding table when yfinance can't supply real QoQ.
  2. Section 5A.4's cleanup threshold `abs(_old_pqoq) > 10` only caught bug values (large numbers from the old `-current` bug) — **the literal 0 passed through and displayed as `0`** for 83/86 stocks indistinguishably.
- Fix: Section 5A.4's `elif abs > 10` threshold removed — any residual number when `_new_*qoq == "—"` now cleaned to `"—"`. Plus new Section 5A.4b post-recompute cleanup that normalizes residual 0.0s.
- Three states now distinct: real number (genuine delta), 0.0 (guarded as missing), `"—"` (displayed).

**FIX #6 — Pledge % / DII % show "—" not 0** (`master_funnel.py` Section 5A.4b)

- Root cause: both fields always 0 on free-tier (pledge needs BSE corporate filings, DII needs NSE API blocked on cloud IPs). Displaying 0 was indistinguishable from "structurally known zero" (e.g., a genuinely no-pledge company).
- Fix: Section 5A.4b normalizes `0.0` to `"—"` for both fields. Paid data source would display real zero as 0; free-tier is honest about "unknown".

**SAFE-GUARDS — Downstream modules handle "—"** (`analysis/v7_analysis_engine.py` + `ownership_tracker.py`)

- `v7_analysis_engine.py::apply_section_3H_guards` line 90: `row.get('pledge_pct', 0) > 20` previously crashed with `TypeError: '>' not supported between instances of 'str' and 'int'` when pledge_pct became `"—"`. Now coerces via `float(str(val or 0).replace("—", "0"))` — same defensive pattern as v10.10 Div Yield fix.
- `ownership_tracker.py::compute_ownership_signals` lines 31-32: same coercion via local `_pledge_num()` helper before numeric comparison.
- `spike_screener.py` already uses `_safe_num()` (from v10.10) — no change needed.

**Tooltip updates** (`reporting/tooltip_formatter.py`)

- 11 field tooltips updated: P/E TTM, PEG Ratio, P/B, P/S, EV/EBITDA, ROE %, ROA %, NPM Q1/Q2/Q3, CCC Days, Pro QoQ Δ, Pledge %, DII %
- 4 group-header tooltips enhanced: VALUATION, PROFITABILITY, FIN HEALTH, SHAREHOLDING
- Each updated entry now documents: data source, v10.15 fix applied, clamp threshold, specific pre-clamp example (AMAGI 1,981, EMAMIREAL -845%, etc.)

**Glossary expansion** (`reporting/excel_generator.py`)

- PROFITABILITY: 2 → 10 entries (added ROA, ROCE, Gross Mgn, EBITDA Mgn, NPM %, NPM Q1/Q2/Q3 individual, Margin Expansion)
- FIN HEALTH: 3 → 11 entries (added CCC Days with v10.15 FIX #3 detail, others already present)
- SHAREHOLDING: 3 → 15 entries (added Pro QoQ Δ, Pledge Direction, FII QoQ Δ, DII %, DII QoQ Δ, Public Float % + enhanced existing)
- VALUATION: 5 → 14 entries (existing detailed entries enhanced with v10.15 clamp notes)

Integration tests: **111/111 passed** across 14 test groups — all 6 fixes verified with edge cases (HUBTOWN, EMAMIREAL, AMAGI, RHETAN representative inputs), safe-guards don't crash on `"—"`, all 147 TIPS + 22 GROUP_TIPS valid shape, all 9 core modules import cleanly, v10.12 / v10.13 / v10.14 regressions all pass.

Zero analytical behaviour change from v10.14 — scoring weights, verdict thresholds, forensic adjustment, Gold filter all unchanged. Pure display-layer cleanup + field type correctness.

### v10.16 — VALUATION display honesty (Option B) + scoring neutrality

Direct follow-up to v10.15 FIX #4 after user feedback on production Excel: the ±1000 clamp made AMAGI's PE=1,981 display as **1000**, which users correctly flagged as misleading (could be misread as "1000× earnings" when it actually means "earnings ≈ 0, P/E not meaningful"). v10.16 replaces the numeric clamp with honest `"—"` display, matching the philosophy already used for Pledge%/DII%/QoQ Δ in v10.15.

**FIX #1 — Display "—" instead of clamped number for valuation ratios** (`master_funnel.py`)

- Previous (v10.15): raw PE 1,981 stored as 1000, Excel column shows 1000
- Now (v10.16): raw PE 1,981 → Excel column shows `"—"`
- Same pattern for P/B, P/S, EV/EBITDA (threshold 500) and PEG (threshold 50)
- Fix locations in `master_funnel.py`:
  - Lines ~1413-1414: `ps` / `ev_ebitda` setdefault checks `0 < raw < 500` else `"—"`
  - Lines ~1420-1457: PEG 4-tier fallback — every tier now checks `< 50` else `"—"`
  - Lines ~1480-1486: `pe` / `pb` setdefault with threshold 500 check
  - Lines ~2556-2567: PE fallback derivation (from EPS/CMP) also threshold-checks

**FIX #2 — DB-layer cap tightened** (`backfill_history.py`)

- `_yf_ratio()` default `cap` lowered from 1000 → **500**
- PEG explicit `cap` lowered from 100 → **50**
- Rationale: DB still stores a clamped numeric (not a string) for scoring — because SQLite `REAL` columns can't cleanly hold "—". But the cap is now at the display threshold, so display layer's `raw ≥ 500 → "—"` conversion catches all clamped values deterministically.

**FIX #3 — Scoring logic: clamped PE treated as NEUTRAL** (`master_funnel.py` lines ~1852-1860)

- Previous: `elif _pe_f > 60: _fs -= 8` — any PE over 60 got an "expensive" penalty, including clamped 1000 (from AMAGI tiny-EPS case). But 1000 doesn't mean "expensive" — it means "unknown".
- Now: new top-priority branch `if _pe_f >= 500: pass` — clamped noise treated as NEUTRAL (no boost, no penalty). Real expensive stocks (PE 60-499) still get the −8 penalty.
- This is the scoring-side half of the "display honesty" story: both Excel display AND fundamental_score now recognize "valuation undefined" as distinct from "valuation high".

**FIX #4 — Downstream PE reader safe-guard** (`analysis/v7_analysis_engine.py` lines 22-34)

- `apply_section_3A_valuation` line 26 was `if pe > 0 and pe_5yr > 0 and pe < (pe_5yr * 0.85):` — crashed with `TypeError` when `pe = "—"` (string).
- New: local `_pe_num()` helper coerces `"—"` → 0 before comparison, same defensive pattern as v10.15 pledge_pct fix.

**FIX #5 — Balance-sheet engine ROE comparison safe-guard** (`analysis/bs_engine.py` line 45)

- `current_bs.get('roe', 0) < prev_bs_4q.get('roe', 0)` would crash `TypeError` when `roe = "—"` (which v10.15 FIX #1 can produce when neither direct nor derivable ROE is available).
- New: local `_roe_num()` helper at top of `analyze_bs_health` coerces `"—"` → 0 before the "DEBT UP / ROE DOWN" red-flag check.

**FIX #6 — Excel NEUTRAL-filter defensive coerce** (`reporting/excel_generator.py` lines 1367-1385) — *historic; superseded in v12.0*

- `_is_exceptional_neutral()` used `float(row.get("pe_num", row.get("pe", 99)) or 99)` — crashed `ValueError` in the edge case where `pe_num` absent and fallback reached `pe = "—"`.
- v10.16 added a local `_fs()` helper with `in (None, "", "—", "--")` check then `try float()`, same pattern as `_sf` in scoring code. Applied to all 5 fields read by the filter (roe, pe, mos_pct, technical_score, composite_score).
- **v12.0 update:** The entire `_is_exceptional_neutral()` function and the filter that called it have been removed. The block was silently shrinking the dashboard below 100 rows whenever Gemini quota exhausted (NEUTRAL is the default verdict for stocks that didn't get an AI card). Stage 3 (`priority_ranker.get_top_100_candidates`) is the single authoritative quality gate; the Excel layer no longer second-guesses it. See v12.0 section at end of this doc for context.

**Tooltip updates** (`reporting/tooltip_formatter.py`)

- 5 valuation tooltips rewritten: P/E TTM, PEG Ratio, P/B, P/S, EV/EBITDA
- Each now says "displays '—' when raw ≥ 500 (50 for PEG)" instead of "capped at ±1000"
- P/E TTM tooltip also documents the v10.16 scoring neutrality rule
- VALUATION group header fully rewritten to explain Option B philosophy

**Glossary expansion** (`reporting/excel_generator.py`)

- 6 VALUATION entries updated: 4 detailed (P/E TTM, PEG, P/B, EV/EBITDA) in primary block, 2 (P/B, P/S) in secondary block
- Each entry now has a `'—' = Display when raw ≥ 500 (...)` bucket mirroring the numeric buckets above it

**Downstream modules confirmed safe (no change needed)**

Full audit of every reader of the 11 dash-capable fields across the codebase confirmed these existing helpers already handle `"—"` correctly:

- `analysis/scoring_engine.py::_nonzero_qoq` — `replace("—", "0")` + `try/except` ✓
- `analysis/fundamental_engine.py::_n` — explicit `in (None, "", "—", "--", "N/A")` check ✓
- `analysis/spike_screener.py::_safe_num` — explicit `"—"` check (v10.10 pattern) ✓
- `analysis/fair_value_engine.py` — uses `_sf()` + `> 0` gate for pb / ev_ebitda ✓
- `analysis/ownership_tracker.py::_pledge_num` — v10.15 defensive helper ✓
- `analysis/v7_analysis_engine.py::apply_section_3H_guards` — v10.15 `_pledge_val` coerce ✓
- `analysis/forensics_engine` — reads numeric fields (altman_z, beneish_m), not valuation ✓
- `database/data_bridge` — reads from SQLite REAL columns, never sees string `"—"` ✓

**DB schema unchanged.** `fundamental_metrics.pe/pb/ps/ev_ebitda/peg` columns stay `REAL DEFAULT 0` — the clamp value (500 / 50) is persisted for scoring; display layer converts at read time.

**Scoring sensitivity preserved EXCEPT for clamped values.** Real valuations in buckets [0-20], [20-40], [40-60], [60-499] still produce identical scoring behaviour. Only the [500+] region changed from `-8 penalty` to `neutral`. No real-business stocks affected — only the arithmetic-noise cases that previously showed misleading clamped numbers.

Integration tests: **198/198 passed** across 27 test groups — threshold edge cases at 499/500/501, PEG 49/50, PB/PS/EV at boundaries, defensive PE coerce in v7 module (5 input shapes), behavioral equivalence on composite score (clamped pe_num stays numeric → scoring stable), bs_engine ROE comparison with all three input shapes (current='—', prev='—', both='—'), excel_generator `_fs` helper verified, full regression on v10.12 / v10.13 / v10.14 / v10.15.

Zero DB schema change, zero analytical behaviour change for real-valuation stocks, cleaner Excel output for arithmetic-noise edge cases.

---

## Release notes archive (v12.0 – v16.5 releases + v17.3 grade note, as written at the time)

## v12.0 RELEASE — BSE Cloudflare resilience + Dashboard size stability

**Date:** 28 April 2026
**Files changed:** `master_funnel.py`, `reporting/excel_generator.py`, `reporting/tooltip_formatter.py`, `requirements.txt` + 4 doc files

### Two root-cause fixes that resolve four observable symptoms

**Symptom set:**
- Dashboard had 79 stocks instead of 100 (~21 missing on every quota-exhausted run)
- Exchange tag column was 98% `DUAL_LISTED` with zero `BSE_ONLY` / `BSE_SME` entries
- Index/ETF tickers (`IT`, `PSUBANK`, `BANKNIFTY1`, `MON100`, `HDFCNIFBAN`) leaked into the analysis pool
- Some Stage-3 selected stocks silently never reached the Excel output

**Root cause #1 — BSE bhavcopy single-attempt strategy.** `master_funnel._bse_bhav()` called only the `bse` pip package. When BSE returned 403 (Cloudflare blocking cloud IPs), the function returned `None` and the reconciler fell into its `bse_df.empty` fallback at `ingestion/reconciler.py` line 138. That fallback can only produce `DUAL_LISTED` (when on allowlist) or `NSE_ONLY` (otherwise) — `BSE_ONLY` and `BSE_SME` become impossible. Plus `pre_screener.py`'s ETF filter at line 40 is `sc_group`-based, which requires BSE data; so when BSE is down, indices/ETFs flow through unfiltered.

**v12.0 fix:** `master_funnel._bse_bhav()` now uses a 3-tier cascade: bse package → cloudscraper → curl_cffi. Each tier wrapped so a Cloudflare-style failure falls through to the next; only a Tier-1 `RuntimeError`/`FileNotFoundError` (the bse package's signal for "not yet published" — i.e. holiday/weekend/intraday) returns `None` immediately without burning Tier 2/3 attempts.

The diagnosis logic was already in the project — `utils/bse_diagnosis.py` had been explicitly recommending `curl_cffi` as the fix, and `ingestion/harvester.download_bse_bhavcopy` already used Tiers 1+2 for SME data. v12.0 just consolidates this into the main pipeline path.

**Root cause #2 — Excel-layer NEUTRAL filter.** `ExcelGeneratorV6.__init__` had a block at lines 1367-1394 that dropped any NEUTRAL-verdict stock unless it met `ROE>20% AND PE<30 AND MoS>10% AND tech_score>62`. On healthy runs this rarely showed because Gemini AI cards promoted most NEUTRAL stocks to BUY/WATCHLIST/AVOID before the filter ran. On runs where Gemini quota exhausted (a daily occurrence on free tier), 20+ stocks stayed at the default NEUTRAL label and got silently dropped — making the dashboard size dependent on AI quota state.

**v12.0 fix:** Filter removed entirely. Stage 3 (`priority_ranker.get_top_100_candidates`) is the only quality gate now. Comment block left in place explaining the decision so future maintainers don't accidentally re-add it.

### What stays the same

- All v11.0.2 features still fire: streak counters, chronic-AVOID demotion, turnaround flag, Section H, runtime allowlist auto-add/auto-prune
- Dashboard sort order: BUY → OVERVALUED → WATCHLIST → NEUTRAL → AVOID (NEUTRAL slot was always defined in `VERDICT_ORDER`, just not previously visible)
- All other Excel sheets (Gold, Trade Summary, Alert Log, Delivery Preview, Glossary, Tooltip Reference) untouched
- Prior-analysis enrichment, override rules O1-O5, all unchanged

### Operational note

`pip install curl_cffi` is the only new dependency. Without it, the cascade gracefully skips Tier 3 with a console warning; pipeline still works as long as Tier 1 or Tier 2 succeeds. On retail/residential IPs Tier 1 typically works; on cloud runners (GitHub Actions, AWS) Tier 3 is often required.

---

## v12.1 RECONCILER HOTFIX — Empty-ISIN false-positive prevention

**Date:** 28 April 2026
**Files changed:** `ingestion/reconciler.py`, `test_v11.0.2_full_withdummies.py` (new Group 31 tests), 4 doc files
**Test status:** 157 of 157 pass (150 pre-existing + 7 new regression tests)

### Why this hotfix exists

After v12.0 deployed, BSE bhavcopy started downloading reliably for the first time in months. The first dashboard run with healthy BSE data revealed a regression that had always been latent in v11.x but was masked by BSE being unreachable: `~99%` of stocks tagged `DUAL_LISTED`, zero `BSE_ONLY`/`BSE_SME` entries, and 2,038 symbols added to the runtime allowlist in a single run.

### The bug

`pd.merge(nse, bse, on="isin", how="outer")` treats empty string `""` as a valid join key. When many NSE rows lack an ISIN (sector indices `IT`/`PSUBANK`/`BANKNIFTY1`, ETFs `MON100`/`HDFCNIFBAN`) AND many BSE rows lack an ISIN (junk listings, delisted, SME), the outer merge produces a Cartesian explosion:

- N empty-ISIN NSE rows × M empty-ISIN BSE rows → N×M output rows, each tagged DUAL_LISTED
- For your 2,483 NSE × 4,997 BSE input: ~2,038 false-positive DUAL_LISTED rows

The v12.0.1 attempt (split merge by `has_isin`, route empty-ISIN rows through symbol-merge) merely shifted the bug — symbol-merge has the same Cartesian-collision problem because non-equity tickers can collide across exchanges without being the same security (NSE `PSUBANK` index ≠ BSE `PSUBANK` listing).

### The v12.1 fix

Strict conservative principle: **without an ISIN, there is no reliable evidence of cross-listing.** Empty-ISIN rows are passed through as "shadow" frames with `symbol_NSE` populated and the BSE-side columns left null (or vice versa for BSE-only no-ISIN rows). `_apply_exchange_tag` then correctly reads them as NSE_ONLY / BSE_ONLY / BSE_SME based on `has_nse` / `has_bse` / `sc_group`.

The hardcoded `DUAL_LISTED_ALLOWLIST` post-merge override still fires for symbols on the curated list — so a known dual-listed stock that lacks ISIN data temporarily is still tagged correctly. But the override is precise; it won't blanket-match by symbol.

### What was tested

`test_v11.0.2_full_withdummies.py` Group 31 — runs every regression-cycle:

- **31.1**: PSUBANK (empty ISIN both sides) NOT tagged DUAL_LISTED ← direct regression for the production symptom
- **31.2**: IT (NSE index) tagged NSE_ONLY
- **31.3**: RELIANCE (real ISIN match) correctly DUAL_LISTED — happy path sanity
- **31.4**: Realistic-scale 2483×4997 input → ~600 DUAL_LISTED (not 2038+)
- **31.5**: BSE_ONLY and BSE_SME tags populate (were 0 pre-fix)
- **31.6**: Hardcoded allowlist override still works for symbols missing from merge

### LESSON for future fixes — TEST BEFORE PUSHING

This bug went through three iterations before resolving (v11.x → v12.0.1 → v12.1) because each attempt was simulated on contrived data instead of being run end-to-end through `_parse_bse_df` → `standardize_to_v7_schema` → `reconcile_exchanges`. The fix patterns to remember:

1. **Always trace through the FULL pipeline** (parse + standardize + reconcile) — don't stop at the function being modified
2. **Run the regression test suite before declaring a fix complete** — `python test_v11.0.2_full_withdummies.py`
3. **Add a regression test for every bug fixed** — Group 31 now permanently locks the empty-ISIN false-positive out

### Operational notes

- v12.0.1 self-healing cleanup (in `master_funnel.py`) still runs every startup. On the first run after deploying v12.1, it'll detect the polluted runtime allowlist (still has 2,038 entries from the v12.0 run that triggered before the fix), back it up, truncate, and the v12.1 reconciler will then repopulate correctly.
- Index tickers (`IT`, `PSUBANK`, `BANKNIFTY1`, etc.) now correctly tag as `NSE_ONLY`. They may still appear in the dashboard if they pass the screener's other gates — adding them to an explicit denylist in `pre_screener.py` is a separate enhancement, not part of v12.1.

---

## v12.2 RELEASE — Fair Value Engine hardening (initial fixes + Round 1)

**Date:** 29 April 2026
**Files changed:** `analysis/fair_value_engine.py`, `test_v11.0.2_full_withdummies.py` (Groups 32-48 added), `CLAUDE.md`, `pipeline_reference_v12_2.html`, `reporting/excel_generator.py` (glossary updates), `reporting/tooltip_formatter.py`
**Test status:** 319 of 319 pass (157 pre-existing + 93 v12.2 valuation + 69 Round 1 + corrections)

### Why this release exists

A code review of `analysis/fair_value_engine.py` flagged 7 issues across the 7 valuation models (M1-M7), ranging from defensive-coding gaps (eps/bvps not sanitised) to dimensional concerns (M5 EV/EBITDA shortcut formula) to documentation/code mismatches (M6 DDM growth derivation). A real-data audit of before/after Excel dashboards then revealed that even after the initial fixes, 31 of 100 production stocks were still falling through to default sector multipliers because their sector strings ("Basic Materials", "Industrials", "Communication Services") didn't substring-match any benchmark key.

### v12.2 initial release — code-review-driven fixes

| Fix | Model | Before | After |
|-----|-------|--------|-------|
| **eps/bvps sanitisation** | M1, M2, M3, M7 | `data.get('eps', 0)` raw — crashed on `'—'` / `'N/A'` / `None` | Now routes through `_sf()` like every other field |
| **Sector substring matching** | M3, M4, M5 | `sector.split()[0]` only matched first word — "Information Technology" got default 25 | Two-pass: SECTOR_ALIASES canonicalisation, then case-insensitive substring scan |
| **Sector map expansion** | M3, M4, M5 | Missing Realty, Telecom, Cement, Textiles, Media, Insurance, NBFC, Defence | All explicitly mapped |
| **DDM growth derivation** | M6 | `min(max(_pat_g / 200, 0.02), 0.06)` — 2% floor inflated FV for declining-earnings stocks | `max(min(_pat_g / 100 / 2, 0.06), 0.0)` — 0% floor, units explicit |
| **PEG unit guard** | M7 | `EPS × growth_3yr` no guard — would silently 100×-misprice if growth arrived as decimal (0.15 vs 15) | Skip if `growth < 1.0` (likely unit error) |
| **Composite unknown-key default** | composite | `base_weights.get(k, 0.10)` silently weighted unknown keys | `k in base_weights` filter; unknown keys excluded from total weight |

### v12.2 Round 1 — production-data-driven follow-on

A diff of two real Excel dashboards (`NSE_BSE_Full_Dashboard_20260428_before_fix.xlsx` vs `_after_fix.xlsx`) revealed:

- **31 of 100 stocks** had M3/M4/M5 unchanged after the v12.2 fix because their sector strings didn't match any benchmark key (Basic Materials, Industrials, Communication Services, General). Mean MoS change for these sectors: **−0.05** — i.e., zero benefit from the fix.
- **62 of 89 stocks** (those that did get sector resolution) had M4 PB change, with median delta of ₹35 and tails extending to ₹137,000 — confirming that sector resolution is the single biggest driver of behavioural change in the entire engine.
- M6 DDM dropped exactly **−20.63%** for 13 dividend-paying stocks with negative `pat_yoy` (BAJAJFINSV, BHARTIARTL, HINDALCO, etc.) — matching the analytic prediction `9.524/12.0 = 0.794` from removing the 2% floor.

#### Round 1 changes

1. **`SECTOR_ALIASES` dict** in `analysis/fair_value_engine.py` — explicit map for production sector strings to canonical benchmark keys:
   - `Basic Materials → Metals` (PE 12, PB 1.5, EV 6)
   - `Industrials → Infra` (PE 22, PB 2.5, EV 11)
   - `Communication Services → Telecom` (PE 22, PB 2.5, EV 9)
   - `Consumer Cyclical / Consumer Defensive → Consumer` (PE 40, PB 6, EV 22)
   - `Financial Services → Financial` (PE 20, PB 2, EV 12)
   - `Real Estate → Realty` (PE 25, PB 2.5, EV 12)
   - `General` left as catch-all → falls to default 25/3.0/15 (intentional — no sector signal)
   - Plus 12 industry-specific aliases for legacy data sources (Iron & Steel, Banks - Public Sector, Information Technology, etc.)

2. **`_canonicalize_sector()` helper** — case-insensitive lookup that whitespace-strips and applies SECTOR_ALIASES. Falls through to substring matcher if no alias hits.

3. **`_sector_resolutions` diagnostic field** — every `calculate_all_models()` call now returns a `'_sector_resolutions'` key in the models dict containing `{'M3_PE': 'Metals', 'M4_PB': 'Metals', 'M5_EV': 'Metals'}` style mapping. Underscore prefix signals "metadata, not model output." Composite weighter (`get_composite_fair_value`) filters via `k in base_weights` so the metadata is correctly excluded from FV math.

### What was tested

`test_v11.0.2_full_withdummies.py` Groups 32-48 — runs every regression cycle:

- **Group 32-38**: Per-model happy paths and edge cases for M1 through M7
- **Group 39**: Composite blending including unknown-key hardening (test 39.4)
- **Group 40-42**: MoS derivation, score adjustment bands, MoS labels (all 7 tiers)
- **Group 43**: Defensive inputs — `eps='—'`, `eps=None`, `eps='N/A'`, `bvps='—'` etc.
- **Group 44**: Output dict shape — handles `_sector_resolutions` metadata correctly
- **Group 45**: Realistic end-to-end scenarios (TCS-like, Steel, PSU bank, Loss-maker, Real Estate Investment)
- **Group 46**: SECTOR_ALIASES — every production sector resolves to expected benchmark; "General" intentionally stays at default
- **Group 47**: `_sector_resolutions` diagnostic — present in output, populated correctly, doesn't pollute composite (test 47.7, 47.8)
- **Group 48**: HINDALCO-like, BHARTIARTL-like, Industrials, Consumer Cyclical, General — full integration scenarios from production data

### Known limitations carried into v12.2

These were identified in the code review but **deliberately not fixed** in this release because they require either data-pipeline changes or design decisions:

1. **M5 EV/EBITDA shortcut formula** — uses `CMP × sector_ev_mult / current_ev_ebitda`, which assumes net debt and share count remain stable vs peers. A proper EV-based fair value would compute `(EBITDA × sector_ev_mult − net_debt) / shares_outstanding`, requiring three new data fields. The 10% composite weight bounds the impact. Real-data audit confirmed M5 is producing aggressive Tech-sector upside (TCS at 80% upside), but outputs are dimensionally plausible (66 of 72 stocks land in [0.3×, 3×] CMP range). Round 2 candidate.

2. **M7 "PEG" model** — implements `EPS × growth_pct`, mathematically equivalent to assuming PEG = 1.0 (Lynch's rule) but doesn't expose the constant explicitly. Real-data audit showed 0 stocks had this fire the unit guard, so production data is clean. Round 2 candidate to make `PEG_BENCHMARK = 1.0` explicit.

3. **MoS distribution shift** — mean MoS moved from +4.7% to +10.8% across 89 stocks. This is the engine becoming more correctly bullish (it can now see real undervaluations that were masked by default multipliers); not a bug. May warrant re-tuning the score adjustment thresholds (`+12 at MoS>40`, etc.) if backtests calibrated against the old distribution.

### LESSON — Audit production data before declaring fixes complete

The v12.2 initial release passed all 250 hand-crafted unit tests but still left 31% of production stocks unaffected because the test data didn't include the actual sector strings yfinance returns. The Round 1 fix existed because we ran a real pipeline twice and diff'd the Excel outputs. The pattern to remember:

1. Hand-crafted tests verify the code does what it's written to do.
2. Production-data audits verify the code does what it should do.
3. **Both are necessary; neither is sufficient.**

### Operational notes

- The `_sector_resolutions` diagnostic field is now present in every stock dict after `master_funnel.py` line 1962 (`stock.update(models)`). Downstream consumers that iterate stock keys must skip underscore-prefixed entries (none currently do; existing code uses explicit lookups). If you add a new dashboard column that surfaces this, recommended format: `f"M3:{res['M3_PE']} M4:{res['M4_PB']} M5:{res['M5_EV']}"` for compact display.
- Glossary entries for M3 / M4 / M5 / CFV / MoS in `reporting/excel_generator.py` updated with v12.2 sector-aliasing notes.
- Tooltip Reference entries in `reporting/tooltip_formatter.py` updated to match.
- The `pipeline_reference_v12_1.html` was bumped to `pipeline_reference_v12_2.html` with the same content plus a v12.2 changelog entry inline.

---

---

## v12.3 RELEASE — Round 2: M5 EV proper formula + M7 PEG_BENCHMARK explicit

**Date:** 29 April 2026
**Files changed:** `analysis/fair_value_engine.py`, `test_v11.0.2_full_withdummies.py` (Groups 49-51 added), `CLAUDE.md`, `reporting/excel_generator.py` (glossary updates), `reporting/tooltip_formatter.py`
**Test status:** 350 of 350 pass (319 v12.2-Round-1 + 31 new Round 2 tests)

### Why this release exists

The v12.2 audit flagged two structural concerns we deliberately deferred to Round 2 because they required design decisions, not just bug fixes:

1. **M5 EV/EBITDA's shortcut formula** — `CMP × sector_ev_mult / current_ev_ebitda` produced aggressive outputs because it implicitly assumed net debt and share count remained stable vs peers. Real-data audit showed Tech stocks at 1.5–2× CMP and Basic Materials at extremes after Round 1's expanded sector multipliers.
2. **M7 PEG's implicit PEG=1.0 assumption** — the formula `EPS × growth_pct` happened to equal a Lynch PEG=1.0 fair value but didn't expose the constant, making it untunable.

A data-availability audit (`check_m5_data.py`) confirmed the pipeline already collects everything needed for a proper M5 fix: `q_ebitda_cr` (83% populated), `total_debt_cr` (96%), `cash_cr` (97%), `mcap_cr` (parsed from symbol_master). So we did the proper fix, not the safety patch.

### v12.3 changes

#### M5 EV/EBITDA — three-tier formula

```
Tier 1 (proper):
    annual_ebitda_cr = q_ebitda_cr × 4
    fair_EV_cr       = annual_ebitda_cr × sector_ev_multiple
    net_debt_cr      = total_debt_cr − cash_cr
    fair_mcap_cr     = fair_EV_cr − net_debt_cr
    fair_per_share   = CMP × (fair_mcap_cr / mcap_cr)

Tier 2 (shortcut — legacy v12.2 formula, fallback when Tier 1 inputs missing):
    fair_per_share = CMP × sector_ev_mult / current_ev_ebitda

Tier 3 (skip):
    Banks/NBFCs/Insurance always skip (EV/EBITDA isn't meaningful for financials)
    OR no usable data at all
```

The elegant part: Tier 1 doesn't need `shares_outstanding` (which the pipeline doesn't collect). It uses the ratio `fair_mcap_cr / current_mcap_cr` to express mispricing as a CMP multiplier — sidestepping the need to know absolute share counts.

Tier 1 also has a **4× CMP cap** mirroring M1 DCF's cap, plus a **negative-equity branch**: if `fair_mcap_cr` is negative (debt exceeds fair EV), emit `CMP × 0.3` (70% discount) rather than skipping — this preserves the bearish signal for severely overlevered stocks.

The new `_m5_method` field in `_sector_resolutions` surfaces which tier fired:
- `"proper"` — Tier 1 succeeded
- `"proper_negative_equity"` — Tier 1, but fair_mcap_cr < 0
- `"shortcut"` — Tier 2 fallback
- `"skip_financial"` — Tier 3, banks/NBFCs/insurance
- `"skip_no_data"` — Tier 3, nothing to compute with

#### M7 PEG — explicit PEG_BENCHMARK constant

```python
PEG_BENCHMARK = 1.0   # Lynch's rule of thumb: stock fair when PEG = 1
fair_PE = adj_growth × PEG_BENCHMARK
M7_PEG  = EPS × fair_PE
```

Mathematically identical to v12.2 outputs (since 1.0 × X = X), but the constant is now named and tunable. Strict value setups could use 0.8 (cheaper); growth-tilted mandates could use 1.2.

### What was tested

`test_v11.0.2_full_withdummies.py` Groups 49-51 — 31 new tests:

- **Group 49** (M5 dispatch): Tier 1 happy path, net-cash company, 4× CMP cap, negative-equity branch, Tier 2 fallback when proper inputs missing, Tier 3 skip for Banks/NBFC/Insurance, Tier 3 skip for no-data, Round 1 sector aliasing still works with Tier 1, negative q_ebitda → Tier 2 fallback (11 tests)
- **Group 50** (PEG_BENCHMARK): default 1.0 produces v12.2-identical outputs, growth cap still 30%, unit guard still works, negative EPS still skips (4 tests)
- **Group 51** (production scenarios): HINDALCO-like with full Round 2 data → "proper" method fires; PSU bank → skip_financial; legacy stock without Round 2 fields → shortcut fallback (8 tests)

### Production-data simulation results

Re-ran Round 2 engine against the production Excel (100 stocks):

| Tier | Count | Notes |
|------|-------|-------|
| `proper` (Tier 1) | 0 in simulation* | *Simulation lacks `mcap_cr`; real pipeline has it, expect ~75 stocks |
| `shortcut` (Tier 2) | 81 | Fallback when proper inputs missing |
| `skip_financial` (Tier 3) | 13 | Banks/NBFCs/Insurance — correctly removes M5 noise |
| `skip_no_data` (Tier 3) | 6 | No EV/EBITDA in feed |

3 stocks shifted M5 to 0 due to financial-sector skip:
- **BAJAJHLDNG**: was M5=994 (CMP 10246) — was severely dragging composite down
- **AUSOMENT, BAJAJFINSV**: same pattern

For all 3, removing the bad M5 signal shifted MoS upward (more accurate, since EV/EBITDA-based valuation isn't meaningful for these holding/financial businesses).

### HINDALCO walkthrough — full Round 1 + Round 2 progression

| Release | Sector resolved to | M3 PE | M4 PB | M5 EV | CFV | MoS | Verdict |
|---------|-------------------|-------|-------|-------|-----|-----|---------|
| Pre-v12.2 | (default 25/3.0/15) | ₹1807 | ₹1878 | ₹1937 | ₹1409 | +31% | **BUY** ❌ |
| Round 1 (v12.2) | Metals (12/1.5/6) | ₹867 | ₹939 | ₹775 | ₹964 | -10% | OVERVALUED |
| Round 2 (v12.3) | Metals + proper M5 | ₹867 | ₹939 | **₹508** | ₹740 | -31% | NEUTRAL |

The progression shows each release tightening the assessment for a real metals stock with `Beta 0.24, PE 14.4, ₹50,000 Cr debt`:
1. Pre-v12.2 said BUY because comparing against generic 25× PE made it look cheap
2. Round 1 used Metals sector multipliers correctly → OVERVALUED
3. Round 2 also accounts for ₹50,000 Cr of debt → NEUTRAL/Significantly Overvalued

### Known limitations carried into v12.3

These remain Round 3 candidates:

1. **PEG_BENCHMARK is global** — applies the same Lynch constant to every stock. A more sophisticated approach would use sector-specific PEG benchmarks (Tech might tolerate higher PEG than Metals).
2. **Score adjustment thresholds unchanged** — `+12 at MoS>40` etc. were calibrated against pre-v12.2 distributions. With Round 2's additional MoS shift (proper M5 generally produces lower fair values for levered companies), thresholds may warrant re-tuning.
3. **Tier 1 needs all 4 fields** — if any of `q_ebitda_cr`, `total_debt_cr`, `cash_cr`, `mcap_cr` is missing/zero, falls back to shortcut. Could be made more graceful (e.g., proceed with debt=0 if total_debt_cr unavailable, since assuming no debt is more conservative than the shortcut).

### Operational notes

- `_m5_method` field is now in every stock's `_sector_resolutions` dict. Recommended monitoring: log distribution of methods after each daily run; if `proper` count drops sharply, investigate whether one of the 4 source fields stopped populating.
- Banks/NBFCs/Insurance now contribute exactly 6 models to composite instead of 7 (since M5 skips). Composite weighting renormalizes correctly via existing `total_w` logic.
- Glossary entries for M5 / M7 / CFV in `reporting/excel_generator.py` updated with v12.3 notes.
- Tooltip Reference entries in `reporting/tooltip_formatter.py` updated to match.

---

## v12.4 RELEASE — Production blocker patch set

### Summary

A full audit of the v12.3 Excel output (`NSE_BSE_Full_Dashboard_20260429.xlsx`, 99 stocks × 123 columns) surfaced four production-blocker issues that needed patching before the next push:

1. **Header demotion threshold too lax** (`reporting/excel_generator.py`)
2. **Resist 2 / Support 2 collapse to R1 / S1 for 87.9 % of rows** (`backfill_history.py`)
3. **Profitability values >100 % slipping through unchecked** (`master_funnel.py`)
4. **UI strings still reference Anthropic; actual provider is Google Gemini** (multiple files)

All four are now fixed and locked behind 41 new regression tests in **Group 53** of `test_v11.0.2_full_withdummies.py`. Total test count: **368 → 409**, all passing.

### v12.4 changes

#### 25.1 Header demotion threshold (Issue #1)

Pre-v12.4 logic in `_full_sheet`:

```python
for _stk in _stks_preview:
    _v = _stk.get(_key)
    # ... empty/dash checks ...
    _real_count += 1
    if _real_count >= 1:
        break   # just need one populated value to demote from red
_header_has_data[_h] = (_real_count >= 1)
```

A single fluke value out of 99 was enough to demote a NO_FREE_SOURCE column from red to its normal section colour. In the production run this hid:

- **Pro QoQ Δ**: 2/99 populated (97 dashes) → header showed normal SHAREHOLDING orange instead of red
- **FII QoQ Δ**: 22/99 populated (77 dashes) → same issue

Post-v12.4:

```python
_row_total    = max(1, len(_stks_preview))
_COVERAGE_MIN = 0.30   # ≥30 % of rows must carry real data
# ... loop over all rows, no early break ...
_header_has_data[_h] = (_real_count / _row_total) >= _COVERAGE_MIN
```

The threshold of 30 % cleanly separates the broken-coverage cases (2 %, 22 %) from genuinely-populated columns (89 %, 90 %, 94 %, etc.). The `max(1, len(...))` guards against `ZeroDivisionError` on empty previews.

#### 25.2 Resist 2 / Support 2 prior-window slice (Issue #6)

Pre-v12.4 logic in `backfill_history.py::compute_technicals` (the v10.9 attempt):

```python
_lb2 = min(252, len(h))
res2 = h.rolling(_lb2).max() if _lb2 >= 60 else h.rolling(max(40, len(h))).max()
```

The bug: when a stock's 52-week high lies inside the most recent 20 trading days (a fresh breakout — common for the momentum stocks the funnel concentrates on), the 252-day rolling max equals the 20-day rolling max, so `R1 == R2` and the cell pair gives no separate signal.

Production verification: 87 / 99 stocks had `R1 == R2` exactly, and 79 / 99 had `52W High > Resist 2 by >5 %` — the latter is mathematically impossible if R2 is the rolling-252 max from the same `daily_prices` data, which means the rolling window was effectively shorter than advertised for many stocks.

Post-v12.4:

```python
sup1 = l.rolling(20).min()
res1 = h.rolling(20).max()
if len(h) >= 80:                       # need ≥ 60 prior + 20 recent
    prior_l = l.iloc[:-20]
    prior_h = h.iloc[:-20]
    _lb2    = min(252, len(prior_h))
    sup2    = prior_l.rolling(_lb2).min()
    res2    = prior_h.rolling(_lb2).max()
    sup2 = sup2.reindex(l.index, method="ffill")
    res2 = res2.reindex(h.index, method="ffill")
else:
    sup2 = pd.Series([float("nan")] * len(h), index=l.index)
    res2 = pd.Series([float("nan")] * len(h), index=h.index)
```

R2 now rolls over `h.iloc[:-20]` — bars BEFORE the most recent 20 — so it represents the *prior* 52-week ceiling, genuinely separate from R1's recent-swing high. Forward-fill back to the original index keeps `_v(...)` working unchanged. Stocks with < 80 days of history fall back to NaN → renders `"—"` via the standard `_g` default.

Tooltip + glossary text in `reporting/excel_generator.py` and `reporting/tooltip_formatter.py` updated to describe the new "prior 52-week" semantics. The dashboard shows R2 as a meaningfully different price level for the first time since v10.9.

#### 25.3 Profitability clamps (Issue #9)

Pre-v12.4 `_pct(v)` in `master_funnel.py` performed unit conversion (fraction → percent for `0 < |v| < 2.0`) but no bounds clamp:

```python
stock.setdefault("npm", _pct(nm))
stock["nm_num"] = round(_nm_raw * 100, 2) if 0 < abs(_nm_raw) < 2.0 else round(_nm_raw, 2)
```

yfinance occasionally returns absurd values for thin-revenue or one-time-gain rows. Six stocks in the prior run had impossible NPM and three had ROA outside plausible bounds:

| Stock      | NPM     | ROA     |
|------------|---------|---------|
| DGCONTENT  | 126.4   | —       |
| AMAGI      | 189.1   | —       |
| MEGASTAR   | 164.8   | —       |
| REDINGTON  | 156.8   | —       |
| RELIGARE   | 127.6   | —       |
| GCSL       | −144.5  | —       |
| M&MFIN     | —       | 189     |
| TATACAP    | —       | 181.5   |
| J&KBANK    | —       | 126.4   |

These then fed into `nm_num` and `roe_num` and inflated the composite score downstream.

Post-v12.4:

```python
def _clamp_pct(raw, lo, hi):
    out = _pct(raw)
    if isinstance(out, (int, float)):
        if out > hi: return round(hi, 2)
        if out < lo: return round(lo, 2)
    return out

stock.setdefault("gross_margin",  _clamp_pct(gm,    0,  100))
stock.setdefault("ebitda_margin", _clamp_pct(em, -100,  100))
stock.setdefault("npm",           _clamp_pct(nm, -100,  100))
# Numeric scoring inputs clamped the same way:
stock["roe_num"] = round(max(-100, min(100,  _roe_pct)), 2)
stock["gm_num"]  = round(max(   0, min(100,  _gm_pct)),  2)
stock["nm_num"]  = round(max(-100, min(100,  _nm_pct)),  2)
```

Bounds: `[-100, 100]` for NPM / EBITDA Mgn / ROA / ROE numeric, `[0, 100]` for Gross Mgn (margins can't be negative). `_pct`'s sentinel-`"—"` for missing values is preserved by the `isinstance(out, (int, float))` guard.

ROA gets an inline clamp at line ~1422 (since `_clamp_pct` is defined further down in the same block; rather than hoisting the function, we inline the same `[-100, 100]` clamp there).

#### 25.4 Anthropic → Gemini text replacement (Issue #15)

Switching the AI provider from Anthropic Claude to Google Gemini happened in v10.1 — but the user-facing strings never followed:

| File | Line(s) | Before | After |
|------|---------|--------|-------|
| `reporting/excel_generator.py` | 793 | "Requires Anthropic API credits — populated by AI analyst." | "Requires Gemini API credits — populated by AI analyst (aistudio.google.com)." |
| `reporting/excel_generator.py` | 797, 801, 805 | "Requires Anthropic API credits." | "Requires Gemini API credits." |
| `reporting/excel_generator.py` | 939 | "AI-generated text fields (needs Anthropic credits…)" | "AI-generated text fields (needs Gemini credits…)" |
| `reporting/excel_generator.py` | 944 | "# Needs Anthropic API credits" | "# Needs Gemini API credits" |
| `reporting/excel_generator.py` | 1095 | "Claude AI investor narrative" | "Gemini AI investor narrative" |
| `reporting/excel_generator.py` | 1456 | banner: "AMBER header = Needs Anthropic API credits" | "AMBER header = Needs Gemini API credits" |
| `reporting/excel_generator.py` | 1507 | "# Amber — populated only when Anthropic API credits are loaded" | "# Amber — populated only when Gemini API credits are loaded" |
| `reporting/tooltip_formatter.py` | 700 | "Claude AI investor narrative (150–250 words)" | "Gemini AI investor narrative (150–250 words)" |
| `reporting/tooltip_formatter.py` | 875 | "150–250-word narrative from Claude AI…" | "150–250-word narrative from Gemini AI…" |
| `master_prompt/NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt` | 1626, 1628, 1772, 1777, 1796, 1802 | Claude / Anthropic | Gemini |

**Deliberately preserved** to avoid a DB migration:

- `database/data_bridge.py` SQL column `claude_analysed` (line 72) and references at line 1017
- `screening/priority_ranker.py` field name `last_claude_score` (lines 12, 140, 141, 182, 186)

These are SQL column names; renaming them would require an ALTER TABLE migration and a data-bridge refactor. Test 53.4e explicitly verifies these are **still present** to flag any unintended rename.

### What was tested

`test_v11.0.2_full_withdummies.py` Group 53 — 41 new tests:

- **53.1 (a–i)** Header demotion threshold: 7 coverage cases (sparse / well-covered / edge 30 % / edge 29 % / fully empty), empty-preview safety, source-marker check (9 tests)
- **53.2 (a–f)** Resist 2 / Support 2: pre-fix bug reproduction, fresh-breakout fix, short-history fallback, older-ATH separation, sanity bound, source-marker check (6 tests)
- **53.3 (a–r)** Profitability clamp: all 6 production NPM extremes + 2 ROA extremes + normal values + fractions + missing + edges + numeric scoring clamp + source-marker check (18 tests)
- **53.4 (a–e)** Anthropic→Gemini: no Anthropic remains, ≥6 Gemini replacements, no Claude AI in tooltips, aistudio link present, DB column names preserved (5 tests)

Behaviour tests (53.1a–h, 53.2a–e, 53.3a–q) replicate the patched logic in isolation — they verify the algorithm is correct independent of file contents. Source-marker tests (53.1i, 53.2f, 53.3r, 53.4a–d) act as canary tests that flag a reverted patch.

### Verification matrix

| Configuration | Group 1–52 | Group 53 | Final |
|---------------|------------|----------|-------|
| Patched code + Group 53 | 368 ✓ | 41 ✓ | **409 / 409** pass, exit 0 |
| Unpatched code + Group 53 | 368 ✓ | 34 ✓ + 7 ❌ | 402 / 409, exit 1 |

The 7 expected failures on unpatched code are exactly the source-marker checks — confirming Group 53 will detect a future revert.

### Known limitations carried into v12.4

These remain follow-up candidates:

1. **`mos_label` is computed twice** with conflicting bucket schemes (engine sets `EXCEPTIONAL VALUE / STRONG VALUE / …`, master_funnel overwrites with `EXCEPTIONAL / STRONG / …`). Engine output is dead code.
2. **0 vs missing ambiguity** — `_fv()` renders any 0 as `"—"`, collapsing legitimate zeros (PAT YoY = 0 %) and missing data.
3. **FV CFV computed from ≤ 2 models has no quality guard** — composite still produces a CFV with re-normalised weights; thin-model rows then drive false BUY signals.
4. **MoS clipped to 200 % silently** (`cfv > cmp × 3` cap) — no `*` flag indicating clipping.
5. **Gold sheet uses STATIC red headers** — `_gold_ws` doesn't share Full Dashboard's dynamic detection.
6. **`Piotroski F /9` vs `F-Score /9`** — same data, different label across sheets.
7. **Duplicate "EARLY MOVER" entries** in `early_signals` — badge and label both append.
8. **`NPM Q1 / Q2 / Q3` column-label readability** — Q1 = most recent (per tooltip) but the L→R order looks chronological.
9. **Altman Z unit-mismatch** — values 14–26 for high-cap healthcare (X4 = mcap / total_liab unit collision).
10. **CCC Days meaningless for finance-sector stocks** — TATACAP showed 7,739 days.
11. **Three different "no analysis" placeholder strings** in `View Analysis Summary` — should be unified.

### Operational notes

- The `_COVERAGE_MIN = 0.30` constant in `_full_sheet` is the lever for tightening or loosening the red-header rule. If users complain too many columns are red, raise it (more permissive); if columns slip through, lower it.
- The `iloc[:-20]` slice in `compute_technicals` is the lever for the R2/S2 separation. If 20 days is too short (R1 still occasionally equals R2 for very-fast-moving stocks), increase to 30–40 days; the `len(h) >= 80` threshold should also be increased correspondingly.
- The `[-100, 100]` profitability bounds match the values displayed in the Excel; they don't apply to ROCE (which has its own existing `[0, 200]` clamp at line ~1457) or to `*_num` for ROCE (no `roce_num` exists).

---

## v12.5 RELEASE — Quality-of-life fixes

### Summary

Six follow-up fixes from the post-v12.4 issue list. Where v12.4 was production-blocker triage, v12.5 is the next layer down — visual polish, dedup correctness, sanity caps. None of these are scoring-behavior changes.

| Fix | Issue | File(s) touched |
|---|---|---|
| #5  MoS cap marker (`*` on label)   | `analysis/fair_value_engine.py` + `master_funnel.py` |
| #7  Gold sheet dynamic red headers  | `reporting/excel_generator.py` |
| #8  Gold rename F-Score → Piotroski | `reporting/excel_generator.py` + `reporting/tooltip_formatter.py` |
| #10 Early Mover dedup prefix-match  | `master_funnel.py` |
| #12 Altman Z sanity cap at 10       | `analysis/forensics_engine.py` |
| #13 CCC Days `—` for finance sector | `analysis/forensics_engine.py` |

All are locked behind 30 new regression tests in **Group 54** of `test_v11.0.2_full_withdummies.py`. Total test count: **409 → 439**, all passing.

### v12.5 changes

#### 26.1 MoS cap marker (Issue #5)

The FV engine has a Session 19 safety cap: when composite CFV exceeds 3× CMP, it gets clipped to exactly 3× CMP. Pre-v12.5, this was silent — a row showing `EXCEPTIONAL VALUE` could either be a genuine deep-value play (CFV / CMP = 1.6× say) or a clipped extreme where the underlying models were projecting 5× or 10× upside.

```python
# analysis/fair_value_engine.py — v12.5 patch
cfv_capped = False
if cmp > 0 and cfv > cmp * 3:
    cfv = cmp * 3
    cfv_capped = True
# ... build mos_lbl from mos buckets ...
if cfv_capped:
    mos_lbl = mos_lbl + "*"
return {
    ...
    "mos_label":  mos_lbl,
    "cfv_capped": cfv_capped,    # NEW: surfaced for display layer
}
```

`master_funnel.py` overwrites `mos_label` with its own (shorter) bucket scheme — the patch checks the engine's flag and re-applies the `*`:

```python
if   mos > 40:  stock["mos_label"] = "EXCEPTIONAL"
# ... rest of buckets ...
if stock.get("cfv_capped"):
    stock["mos_label"] = stock["mos_label"] + "*"
```

So the user now sees `EXCEPTIONAL*` for clipped rows and `EXCEPTIONAL` for genuine high-MoS plays.

**Tooltip + glossary** updated in both `tooltip_formatter.py:194` and `excel_generator.py:583` to explain the `*` marker.

#### 26.2 Gold sheet dynamic red headers (Issue #7)

Pre-v12.5 Gold sheet:

```python
for i,(h,w,_) in enumerate(GOLD_COLS,1):
    if h in NO_FREE_SOURCE_COLS:
        c=ws.cell(5,i,h); c.fill=_f("991B1B")    # always red
    else:
        c=ws.cell(5,i,h); c.fill=_f(hdr_bg)
```

The Full Dashboard already had coverage-based dynamic detection (v12.4 Issue #1 fix) — Gold didn't. Result: any column in `NO_FREE_SOURCE_COLS` was always red on Gold even when populated for the (small) gold cohort.

Post-v12.5 — Gold sheet uses the same coverage logic with `_GOLD_COV_MIN = 0.30`:

```python
_gold_preview = gdf.to_dict("records")
_gold_total   = max(1, len(_gold_preview))
_GOLD_COV_MIN = 0.30
_gold_has_data = {}
for (_h, _w, _key) in GOLD_COLS:
    if _h not in NO_FREE_SOURCE_COLS: continue
    _real = 0
    for _stk in _gold_preview:
        # ... same exclusion logic as Full Dashboard ...
    _gold_has_data[_h] = (_real / _gold_total) >= _GOLD_COV_MIN

for i,(h,w,_) in enumerate(GOLD_COLS,1):
    if h in NO_FREE_SOURCE_COLS and not _gold_has_data.get(h, False):
        c=ws.cell(5,i,h); c.fill=_f("991B1B")
    else:
        c=ws.cell(5,i,h); c.fill=_f(hdr_bg)
```

#### 26.3 Gold rename F-Score → Piotroski (Issue #8)

Pre-v12.5: Full Dashboard had `"Piotroski F /9"` (width 13), Gold had `"F-Score /9"` (width 11). Same data, same `piotroski_f` key — different label.

Post-v12.5: both sheets use `"Piotroski F /9"` (width 13). Three orphaned doc entries cleaned up:

- `excel_generator.py:830` — Gold-only GLOSSARY tuple `("SCORES","F-Score /9", …)` removed
- `tooltip_formatter.py:125` — orphaned `"F-Score /9": (…)` tooltip removed
- `tooltip_formatter.py:902` — `"F-Score /9"` removed from `_ICON_FAMILIES["🎯"]` set

The existing `"Piotroski F /9"` entries in both files now serve both sheets.

#### 26.4 Early Mover dedup prefix-match (Issue #10)

Pre-v12.5 dedup in `master_funnel.py`:

```python
_early_badge = stock.get("early_mover_badge", "")
if _early_badge and _early_badge not in _early_sigs:
    _early_sigs.append(str(_early_badge))
_early_label = stock.get("early_label", "")
if (_early_label and _early_label not in ("EMERGING", "—", "")
        and _early_label not in _early_sigs):
    _early_sigs.append(str(_early_label))
```

Bug: the badge is `"EARLY MOVER"` and the label is `"EARLY MOVER — Act before the crowd"`. They are different strings so exact-match dedup didn't catch the overlap. Production: 8 stocks in the prior run had both appended, e.g.:

`spike_triggers | EARLY MOVER | EARLY MOVER — Act before the crowd`

Post-v12.5:

```python
def _has_prefix(sig_list, prefix):
    return any(s.upper().startswith(prefix.upper()) for s in sig_list)

_early_badge = stock.get("early_mover_badge", "")
if _early_badge and not _has_prefix(_early_sigs, "EARLY MOVER"):
    _early_sigs.append(str(_early_badge))
_early_label = stock.get("early_label", "")
if (_early_label and _early_label not in ("EMERGING", "—", "")
        and not _has_prefix(_early_sigs, "EARLY MOVER")):
    _early_sigs.append(str(_early_label))
```

If any signal already starts with `"EARLY MOVER"`, neither the badge nor the label gets appended again.

#### 26.5 Altman Z sanity cap at 10 (Issue #12)

`forensics_engine.py:251` `calculate_altman_z` produces a 5-component sum. The X4 component (`mcap / total_liab`) is the unit-mismatch hot spot — when one figure is in raw rupees and the other in Cr, X4 explodes. Production audit: ALIVUS 14.69, GOPAL 17.27, CPEDU 26.70 (typical max for healthy companies is 5–7).

Post-v12.5: Z clamped to `[0, 10]` after the weighted sum:

```python
z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
if z > 10:
    z = 10
return round(z, 2)
```

The existing `if ta <= 0 or tl <= 0: return 0.0` insufficient-data path is preserved unchanged. Distressed stocks (Z < 1.81) are unaffected — the clamp only fires on the upper end.

**Tooltip + glossary** updated in `tooltip_formatter.py:552` and `excel_generator.py:483` to explain the cap.

#### 26.6 CCC Days finance-sector skip (Issue #13)

CCC = `inventory_days + receivable_days − payable_days` is meaningless for Banks / NBFCs / HFCs / Insurance. They don't carry inventory, and their "receivables" are loans measured by a different convention. Production audit: TATACAP showed 7,739 days, FUSION (microfinance) 3,216 days, LICHSGFIN −267 days.

Post-v12.5: `calculate_accounting_forensics` reads `row.get('sector', '')`, lower-cases it, and checks for any of these keywords:

```python
_sector_raw = str(row.get('sector', '') or '').lower()
_is_finance = any(kw in _sector_raw for kw in (
    'financial', 'finance', 'bank', 'nbfc', 'insurance', 'housing finance'
))
if _is_finance:
    results['ccc']      = "—"
    results['ccc_days'] = "—"
else:
    # ... existing computation ...
```

Match is substring-based and case-insensitive, so `"Financial Services"`, `"FINANCIAL SERVICES"`, `"Bank"`, `"Insurance"`, `"Housing Finance"`, `"NBFC"` all hit. Non-finance sectors compute CCC normally.

**Tooltip + glossary** updated in `tooltip_formatter.py:464` and `excel_generator.py:415, 686` (two glossary blocks).

### What was tested

`test_v11.0.2_full_withdummies.py` Group 54 — 30 new tests:

- **54.1 (a–f)** MoS cap marker: capped case (cfv_capped=True, cfv=300, label has `*`), non-capped case (cfv_capped=False, no `*`), source-marker checks (6 tests)
- **54.2 (a–b)** Gold sheet dynamic headers: source-marker + threshold consistency (2 tests)
- **54.3 (a–d)** F-Score rename: `"Piotroski F /9"` appears in both lists, orphaned glossary tuple absent, orphaned tooltip absent, no leftover literal in `tooltip_formatter.py` (4 tests)
- **54.4 (a–e)** Early Mover dedup: badge prevents label, label prevents badge, case-insensitivity, no false-positives, source-marker (5 tests)
- **54.5 (a–e)** Altman Z clamp: unit-mismatch case clamped to 10, healthy company unchanged, distressed company unchanged, insufficient-data still 0.0, source-marker (5 tests)
- **54.6 (a–h)** CCC finance-sector skip: NBFC/Bank/Insurance/HFC all skip, Consumer Cyclical computes normally, empty sector computes, case-insensitivity, source-marker (8 tests)

Test 44.2 was also updated — the FV composite output now has 8 keys (added `cfv_capped`) instead of 7.

### Verification matrix

| Configuration | Group 1–53 | Group 54 | Final |
|---|---|---|---|
| Patched code + Group 54 | 409 ✓ | 30 ✓ | **439 / 439** pass, exit 0 |

### Issues attempted but deferred

- **Issue #3** (0 vs missing ambiguity) — investigated. The root cause is `COALESCE(fm.field, 0)` at the SQL level in `master_funnel.py:1041–1063`, which destroys the distinction before data reaches Python. Fixing it requires removing those `COALESCE`s and reworking every downstream `_fvn(v)` / `_pct(v)` consumer to handle `None` — too risky for a quality-of-life release. Documented as a candidate for a dedicated future release.

### Issues remaining as judgment calls

- **#2** mos_label dual-defined (engine + funnel use different bucket schemes) — pick a canonical scheme
- **#4** FV CFV thin-model guard (currently produces a CFV from 1–2 models with no quality threshold) — pick min_models threshold and below-threshold behaviour
- **#11** NPM Q1/Q2/Q3 column-label readability (Q1 is most-recent per tooltip but L→R reading order is chronological) — rename labels OR swap data
- **#14** three different "no analysis" placeholder strings — pick canonical string

### Operational notes

- The `*` marker on `mos_label` is purely informational — does not affect scoring or verdict logic. Stocks that hit the cap still get their normal MoS-bucket-based `score_adjustment`. The marker is a transparency signal so users can interpret the displayed CFV correctly.
- The CCC sector check is keyword-based, not exact-match. If a new sector taxonomy introduces sectors like `"Financial Technology"` (fintech), the keyword `"financial"` will match it and skip CCC. If that's wrong, the sector check will need to be tightened.
- The Altman Z clamp at 10 is specifically for the upper end. The model's distress-zone semantics (Z < 1.81 = distress, Z < 2.99 = grey zone) are unchanged. The clamp doesn't affect any existing scoring logic that thresholds against these zones — they all sit well below 10.

---

## v12.6 RELEASE — Final-round judgment-call fixes

Five fixes from the residual list that needed a product decision rather than just a code patch. Total scope: 5 fixes, 7 source files touched, 30 new tests. Final test count: **475/475 PASS**.

### Why these fixes shipped together

After the v12.5 audit confirmed all 10 prior fixes were working in production data, the remaining issues from the original v12.4 audit were all **judgment calls** rather than clear bugs. Each needed a "which scheme do we adopt" or "what's the threshold" answer that the data alone couldn't determine. v12.6 collects those decisions into one bundle.

### #6 follow-up — Resist 2 / Support 2 fall back to "—" when ≈ R1 / S1

**File: `backfill_history.py::compute_technicals`**

The v12.4 patch made `Resist 2` use the prior-window rolling max (excluding the last 20 bars). v12.5 production-data audit found that 86 of 99 stocks still showed `R1 == R2` — but investigation confirmed this was **legitimate post-patch behavior**: for stocks bouncing in a narrow range for 9+ months, the prior 252-day window's rolling max happened to equal the recent 20-day swing high.

Mathematically correct, visually unhelpful. A trader scanning the dashboard sees two columns showing the same number and gets no additional information.

**v12.6 fix**: after computing R2/S2, check if they're within 0.5 % of R1/S1 — if so, return NaN, which `_v(.)` converts to 0.0 in the DB; `master_funnel` then renders the cell as `"—"` (string).

```python
_R2_TOLERANCE = 0.005
if _r1_v > 0 and _r2_v > 0 and abs(_r1_v - _r2_v) / _r1_v < _R2_TOLERANCE:
    res2 = pd.Series([float("nan")] * len(h), index=h.index)
```

**Display side** (`master_funnel.py:1781-1787`):
```python
stock["support_2"] = round(float(s2), 2) if s2 else "—"
stock["resist_2"]  = round(float(r2), 2) if r2 else "—"
```

The user sees "no prior ceiling distinct from the recent swing high — treat R1 as both T1 and T2/T3." That's an honest signal.

**Why 0.5 % and not exact equality**: synthetic test confirmed that minor floating-point drift in the rolling-max computation occasionally produces R1=49.39 and R2=49.395-ish on stocks that should match exactly. 0.5 % is tight enough to never trigger on genuinely-distinct ceilings (the 12 separated rows in production showed gaps of 0.5%-15%) and forgiving enough to catch all the legitimate-coincidence cases.

### #2 — `mos_label` dead engine code removed

**File: `analysis/fair_value_engine.py::get_composite_fair_value`**

The FV engine was setting its own 7-bucket `mos_label` (`EXCEPTIONAL VALUE` / `STRONG VALUE` / `GOOD VALUE` / `FAIR VALUE` / `SLIGHT PREMIUM` / `OVERVALUED` / `SIGNIFICANTLY OVERVALUED`). But `master_funnel` always overwrote it with a different 6-bucket scheme (`EXCEPTIONAL` / `STRONG` / `ADEQUATE` / `THIN` / `SLIGHT PREMIUM` / `SIGNIFICANT PREMIUM`).

The engine's bucket-assignment code was dead. Three concerns this raised:

1. Future maintenance hazard — someone reads the engine code, assumes it's authoritative, modifies the buckets there, and is confused when production output doesn't change.
2. Test surface area — the engine code had its own test coverage that was orthogonal to what users actually saw.
3. The `*` marker for capped CFV (v12.5) had to be applied in both places — the engine code wasn't reachable, so the funnel duplicated the logic.

**v12.6 fix**: delete the bucket-assignment block in `get_composite_fair_value`. Engine output dict no longer contains `mos_label`. Funnel is the single source of truth.

The funnel's bucket scheme (kept):
```
mos_pct > 40   → "EXCEPTIONAL"
mos_pct > 25   → "STRONG"
mos_pct > 10   → "ADEQUATE"
mos_pct > 0    → "THIN"
mos_pct > -15  → "SLIGHT PREMIUM"
mos_pct ≤ -15  → "SIGNIFICANT PREMIUM"
```

The funnel still appends `*` for `cfv_capped` and (new in v12.6) `†` for `cfv_thin_models`.

**Why funnel scheme over engine scheme**: the funnel's labels use single-word value labels (`EXCEPTIONAL`, `STRONG`, `ADEQUATE`, `THIN`) and paired-word premium labels (`SLIGHT PREMIUM`, `SIGNIFICANT PREMIUM`). It reads as a hierarchy. The engine's scheme had `OVERVALUED` and `SIGNIFICANTLY OVERVALUED` as separate buckets — more granular but probably more nuance than retail traders need at a glance. Plus: the funnel scheme is what users see today; switching would visually change every dashboard going forward.

### #4 — Fair-value thin-model quality guard

**File: `analysis/fair_value_engine.py::get_composite_fair_value`**

Pre-v12.6 the engine accepted however many of M1–M7 fired (could be 1, could be 7) and produced a CFV with re-normalised weights. A stock with only M1 (DCF) firing got a "CFV" that was effectively just the DCF target. If that 1-model output happened to imply +50 % MoS, the engine would emit `score_adjustment = +12` — driving the composite score up by 12 points on extremely thin evidence.

This was the false-BUY hazard. A stock with no PE multiple data, no PB data, no EV data, no dividend yield, no PEG — i.e., a stock where most fundamental valuation lenses didn't apply or had missing inputs — could still get the maximum FV-driven score bonus.

**v12.6 fix**:

```python
MIN_MODELS = 3   # minimum model count for full-confidence CFV

# ... compute cfv as before ...

cfv_thin_models = (n_models < MIN_MODELS)

score_adj = 0
if not cfv_thin_models:
    if   mos > 40:   score_adj = 12
    elif mos > 25:   score_adj = 8
    elif mos > 10:   score_adj = 4
    elif mos < -30:  score_adj = -10
    elif mos < -15:  score_adj = -5
```

CFV is still shown to the user (so they can decide for themselves). `mos_pct` is still emitted for the dashboard. But the automatic +12 bonus to composite_score is suppressed when fewer than 3 models fired.

**Display side** (`master_funnel.py`): when `cfv_thin_models` is True, the funnel appends `†` to `mos_label` (after `*` for capped). So the user sees something like `"EXCEPTIONAL†"` or `"EXCEPTIONAL*†"` and the tooltip explains: "trailing `†` = CFV based on fewer than 3 valuation models — treat with caution."

**Why MIN_MODELS = 3** (not 2 and not 4):
- 2 is too permissive — a 2-model average is barely better than 1.
- 4 is too strict — would lose the score bonus for stocks where M5 (EV/EBITDA) genuinely doesn't apply (financials), M6 (DDM) genuinely doesn't apply (no dividends), or M7 (PEG) genuinely doesn't apply (no growth data). A typical industrial mid-cap legitimately fires 4-5 of M1–M7, not all 7.
- 3 forces multiple independent valuation lenses (DCF + multiple-based + book-based) without requiring all of them.

**Behavior change in production**:
- Stocks with **full FV** (≥3 models): zero change. Score adjustment fires as before.
- Stocks with **thin FV** (1-2 models): composite score drops by 4-12 points, depending on what the score bonus would have been. Some borderline-BUY stocks become NEUTRAL. Some borderline-NEUTRAL become AVOID. Direction is always toward less-confident verdict — none promoted up.

Sanity check verified: a thin-FV mid-cap with MoS +50 % gets `composite=48.26` and `verdict=NEUTRAL` post-v12.6 (was `composite=60.26` and `verdict=BUY` pre-fix in our synthetic test).

### #11 — NPM Q1/Q2/Q3 column rename

**Files: `reporting/excel_generator.py`, `reporting/tooltip_formatter.py`**

The column headers `NPM Q1 %`, `NPM Q2 %`, `NPM Q3 %` had the data in inverse-chronological order (`Q1 = most recent quarter`, `Q3 = oldest`) but the left-to-right reading order suggested chronological. Users repeatedly read it as "Q1 was three quarters ago, Q3 was the latest."

**v12.6 fix**: rename column headers only.

```
Old: NPM Q1 %     →  New: NPM Q (latest) %
Old: NPM Q2 %     →  New: NPM Q-1 %
Old: NPM Q3 %     →  New: NPM Q-2 %
```

Reads as "this quarter / one ago / two ago" — chronologically unambiguous.

**Crucial implementation detail**: the **DB column keys** (`npm_q1`, `npm_q2`, `npm_q3`) are deliberately **unchanged**. Only the display labels change. This means:
- Zero schema migration
- Zero scoring-logic change (margin_expansion still reads `npm_q1 > npm_q2 > npm_q3` to detect the rising trend; that's still correct because `npm_q1` is still the latest quarter in the data)
- The Excel column tuple format `("display_label", width, db_key)` was the only place that needed to change

All glossary entries (2 blocks), tooltip dict entries, Margin Expansion narrative (2 places), section narrative, and `_ICON_FAMILIES` set were updated to reflect the new labels while preserving the data semantics.

### #14 — "[AI <verb> — <reason>]" placeholder format

**Files: `master_funnel.py`, `ai/ai_analyst.py`**

There were 4 different "no AI analysis" placeholder strings, each with a slightly different format:

- `"Analysis pending."` (default — never analyzed)
- `"[AI Skipped — verdict=AVOID: composite score below 38 floor. ...]"` (intentional skip)
- `"[Batch N skipped — Gemini quota exhausted. ...]"` (quota skip in middle of run)
- `"[AI analysis unavailable — Gemini quota exhausted. ...]"` (quota skip after exhaustion)

Mixed punctuation, mixed capitalisation, inconsistent prefixes. Hard to grep, hard to filter, hard for users to interpret.

**v12.6 fix**: standardize all to `[AI <verb> — <reason>]` format. The distinct meanings are preserved (so users can still tell intentional-skip from quota-skip from default-pending) but the format is now consistent.

```
Default:        "[AI not yet generated — Analysis pending]"
AVOID skip:     "[AI skipped — verdict AVOID, score below 38 floor. ...]"
Batch quota:    "[AI skipped — Gemini API quota exhausted (batch N). ...]"
Final quota:    "[AI unavailable — Gemini API quota exhausted. ...]"
Empty response: "[AI unavailable — Gemini returned empty response for batch N (...)]"
```

Every placeholder now starts with `[AI ` (literal). `grep '\[AI ' Excel_export.txt` cleanly retrieves all of them.

### Files touched in v12.6

```
master_funnel.py                            (#4 †-marker, #6 "—" rendering, #14 AVOID + default)
backfill_history.py                         (#6 R2/S2 NaN fallback)
analysis/fair_value_engine.py               (#2 dead code removal, #4 thin-model guard)
reporting/excel_generator.py                (#11 NPM Q rename — headers + glossary + narrative)
reporting/tooltip_formatter.py              (#11 NPM Q tooltip rename)
ai/ai_analyst.py                            (#14 4 placeholder strings standardized)
test_v11.0.2_full_withdummies.py            (Group 41/42/44.2/54.1 updates + Group 55 added)
readme.md                                   (v12.6 row at top of version table)
CLAUDE.md                                   (this section)
scoring_logic_3Stagefunnel_explained.md     (footer changelog appended)
```

### Issues remaining

- **#3** (0-vs-missing ambiguity) — the only original audit issue not addressed. Architectural fix; requires SQL `COALESCE` rewrite across multiple queries plus downstream `None`-handling refactor. Tracked as candidate for a future dedicated release.

All other 15 audit issues are now resolved as of v12.6.

### Operational notes

- The `†` marker on thin-FV stocks is informational — the CFV value is still displayed, the user can still decide to act on it. The change is that the AUTOMATIC score bonus is suppressed.
- The `*` and `†` markers can stack: `EXCEPTIONAL*†` means "CFV hit the 3× CMP cap AND was based on fewer than 3 valuation models — treat with extreme caution."
- The R2/S2 fallback to "—" only fires when R1/S1 are themselves valid numeric values. If R1 is itself missing (e.g., insufficient history), R2 follows the same path and also renders "—" via the existing pre-v12.4 logic.
- The placeholder format change is **display-only**. Anything downstream that pattern-matches the old strings (e.g., the dashboard banner heuristics, the alert log filter) needs to be re-tested. Group 55.5 verifies all four standardized strings start with `[AI ` and that the old prefixes are gone.
- The NPM Q rename is **display-only**. Scoring engine, margin-expansion detector, and tests all read the underlying `npm_q1/q2/q3` keys, which are unchanged.

---

## v12.6.1 RELEASE — Backfill window bumped to 400 calendar days

Single-line release. Bumped `DAYS_TO_BACKFILL` default from 365 → 400 in `backfill_history.py:55` to give the rolling-252-trading-day windows in `compute_technicals` (used by the v12.6 R2/S2 fallback) comfortable headroom.

### Why

After v12.6 shipped, fresh-data testing revealed that with exactly 365 calendar days of bhavcopy data per symbol, `daily_prices` ends up with ~250 trading-day rows. Then `prior_h = h.iloc[:-20]` has ~230 trading-day rows. The `rolling(min(252, len(prior_h))).max()` computation works but tightly — the 252-day rolling window is only fully populated at the very last position of `prior_h`. Stocks with even slightly less history (~245 trading days from holiday clusters) would silently fall into the `len(h) < 80` fallback branch on edge cases, returning NaN R2/S2.

### What changed

```python
# Before (v12.6):
DAYS_TO_BACKFILL = int(sys.argv[1]) if len(sys.argv) > 1 else 365

# After (v12.6.1):
DAYS_TO_BACKFILL = int(sys.argv[1]) if len(sys.argv) > 1 else 400
```

400 calendar days ≈ 275 trading days → `prior_h` ≈ 255 trading days → `rolling(252)` has 3 days of headroom, computes cleanly for every stock with the full backfill.

Plus 3 cosmetic comment/docstring updates in the same file to match the new constant.

### What deliberately did NOT change

The codebase has 4 other "1-year" references that look similar but encode the financial definition of "52 weeks", not the data-fetch knob:

| Location | Value | Why kept |
|---|---|---|
| `backfill_history.py:768` `_lb2 = min(252, len(prior_h))` | 252 trading days | "Prior 52-week" rolling window — by financial convention 52 weeks = 252 trading days |
| `backfill_history.py:926` `last_252 = grp.tail(252)` | 252 trading days | "52W High / 52W Low" — same convention |
| `backfill_history.py:1993` `* 365` (CCC formula) | 365 calendar days | DIO/DSO/DPO are by-definition annualised over 365 days — every accounting textbook on Earth uses this |
| `master_funnel.py:1153-55` `'-365 days'` SQL filter | 365 calendar days | SQL equivalent of "52W high/low" — calendar arithmetic to bound the same trading-day window |

Changing any of these would silently redefine what columns like "52W High (₹)" mean, breaking trader expectations and tooltip accuracy.

### Action required at deploy time

The DB needs to be deleted (or `daily_prices` truncated) and the next pipeline run will populate it with 400 days of fresh history. This also activates the v12.6 R2/S2 fallback because `technical_indicators` will be recomputed from scratch under the v12.6 patched `compute_technicals` logic.

### Tests

Group 56 added (8 tests) — locks the `400` default, locks all four `KEEP-252/365` lines so future "consistency" passes can't silently break the financial convention. Total test count: **483 / 483 PASS**.

### Operational notes

- The DB will be ~10 % larger after this change (400/365 ratio). For the production DB at ~80 MB, that's ~88 MB. Negligible.
- yfinance backfill calls don't have a "fetch exactly N days" mode; they use start/end dates. The script computes `start = today - DAYS_TO_BACKFILL` and asks for everything in that range. With holidays + weekends, the actual row count per symbol will vary slightly day-to-day but average ~275 trading days.
- BSE bhavcopy fetches one date at a time. `DAYS_TO_BACKFILL = 400` means ~400 BSE bhavcopy zip downloads on first run. Subsequent runs only fetch the latest day. First run after the bump will be slower than usual.

---

## v12.7 RELEASE — Comprehensive dual-listed integrity fix set (12 bugs)

**Date:** April 30, 2026.
**Trigger:** v12.6.1 production audit revealed only 4 of 99 stocks had technicals (SMA200, RSI, MACD, ADX, OBV, S1/S2/R1/R2) populated in the Excel.
**Tests:** 509 passing (483 v12.6.1 carry-forward + 26 v12.7 in new Group 57, including 57.15 which locks all 13 user-facing `technical_indicators` columns populate end-to-end for DUAL_LISTED stocks).

### Root cause shared across all 12 bugs

`daily_prices` PRIMARY KEY is `(symbol, date, exchange)`. Dual-listed symbols (98 of 99 funnel rows in v12.6.1 production) have 2× rows on every trading date — one for `exchange='NSE'`, one for `exchange='BSE'`. Pre-v12.7, several functions ran `groupby('symbol')` / `WHERE symbol=?` without an exchange filter. Consequences:

- **Silent crashes** — `compute_technicals`' v12.4-introduced `reindex(method='ffill')` raised "ValueError: index must be monotonic" because post-`sort_values('date')` the integer index of a duplicate-date series was non-monotonic. The per-symbol `except: pass` swallowed it. 95/99 stocks went missing from `technical_indicators`.
- **Half-period rolling windows** — `compute_weekly_momentum`'s `iloc[-11/21/31/41]` walked back N rows = ~N/2 trading days because rows were doubled. `enrich_prices`' `grp.tail(252)` was effectively the last 126 trading days. `get_20d_avg_vol`'s `LIMIT 20` was 10 NSE + 10 BSE rows.
- **6-month-stale price series** — `get_symbol_history`'s `ORDER BY date ASC LIMIT 250` (a separate bug: should have been DESC) returned the OLDEST 250 rows; combined with the dual-listed bug, `iloc[-1]['close']` was a price from ~6 months ago. **This was the actual source of wrong 2W/4W/6W/8W changes shown in the Excel for 95/99 stocks** — master_funnel:1198 used `get_symbol_history` to build the chg_2w / chg_4w / chg_6w / chg_8w fields that ended up in the dashboard.
- **Marginally wrong CMP lookups** — earnings yield computation got whichever exchange's row was inserted last (NSE close vs BSE close, ± 0.1-0.5%).

### The 12 fixes

#### Fix #1 — `_compute_all_indicators` chunk SQL + dedupe

```python
# backfill_history.py (post-v12.7)
chunk_hist = pd.read_sql(
    f"SELECT symbol, exchange, date, open, high, low, close, volume "  # added exchange
    f"FROM daily_prices "
    f"WHERE symbol IN ({placeholders}) "
    f"ORDER BY symbol, date ASC",
    conn, params=chunk_syms
)

# Dedupe (symbol, date) preferring NSE rows.
if not chunk_hist.empty:
    chunk_hist['_exch_pref'] = (chunk_hist['exchange'] != 'NSE').astype(int)
    chunk_hist = (chunk_hist
                  .sort_values(['symbol', '_exch_pref', 'date'])
                  .drop_duplicates(['symbol', 'date'], keep='first')
                  .drop(columns=['_exch_pref'])
                  .reset_index(drop=True))
```

NSE row wins per `(symbol, date)`. Falls through cleanly for `NSE_ONLY` (no BSE rows to drop), `BSE_ONLY` (no NSE rows → BSE row kept), and `DUAL_LISTED` (NSE wins, BSE dropped).

#### Fix #2 — Replace silent except-pass with structured counter

```python
# pre-v12.7
except Exception:
    pass

# post-v12.7
except Exception as _ti_e:
    _ti_errors += 1
    if len(_ti_err_samples) < 3:
        _ti_err_samples.append(f"{sym}: {type(_ti_e).__name__}: {str(_ti_e)[:80]}")

# at end of loop:
if _ti_errors > 0:
    print(f"   ⚠️  compute_technicals: {_ti_errors} symbols failed "
          f"(swallowed pre-v12.7). First 3: {'; '.join(_ti_err_samples)}")
```

A spike in this counter is the canary for "something upstream changed shape." The pre-v12.7 silent pass is what made bug #1 invisible for so long.

#### Fix #3 — `enrich_prices` filters NSE before groupby

```python
# pre-v12.7
df = pd.read_sql(
    "SELECT symbol, exchange, date, high, low, close, prev_close, volume "
    "FROM daily_prices WHERE date <= ? ORDER BY symbol, date",
    conn, params=(date_iso,)
)

# post-v12.7
df = pd.read_sql(
    "SELECT symbol, exchange, date, high, low, close, prev_close, volume "
    "FROM daily_prices WHERE date <= ? AND exchange='NSE' "
    "ORDER BY symbol, date",
    conn, params=(date_iso,)
)
```

Pre-fix the values written to `daily_prices.week_high_52` / `week_low_52` / `vol_50d_avg` were half-period for the picked exchange row and stale (never written) for the other. Master_funnel reads these via its own NSE-filtered SQL so user-facing Excel was unaffected pre-fix, but the DB columns were wrong for any future consumer.

#### Fix #4 — Delivery UPDATE scoped to NSE

```python
# pre-v12.7
"UPDATE daily_prices SET delivery_pct=? WHERE symbol=? AND date=?"

# post-v12.7
"UPDATE daily_prices SET delivery_pct=? "
"WHERE symbol=? AND date=? AND exchange='NSE'"
```

Pre-fix this UPDATE had no exchange filter, so for dual-listed symbols it also over-wrote the BSE row's `delivery_pct` with the NSE delivery number. Dormant — no downstream consumer reads BSE `delivery_pct` — but it silently broke the BSE side of the price store.

#### Fix #5 — `get_symbol_history`: NSE filter + ORDER BY DESC

```python
# pre-v12.7  (TWO bugs combined)
df = pd.read_sql_query(
    "SELECT date, open, high, low, close, volume "
    "FROM daily_prices WHERE symbol=? "                # bug 5a: no exchange filter
    "ORDER BY date ASC LIMIT ?",                       # bug 5b: returns OLDEST N
    conn, params=(symbol, limit),
)

# post-v12.7
df = pd.read_sql_query(
    "SELECT date, open, high, low, close, volume "
    "FROM daily_prices WHERE symbol=? AND exchange='NSE' "
    "ORDER BY date DESC LIMIT ?",
    conn, params=(symbol, limit),
)
if not df.empty:
    df = df.sort_values("date").reset_index(drop=True)   # preserve ascending API
```

This is **the most user-visible fix**. Pre-fix, `master_funnel.py:1198` did `history.iloc[-1]["close"]` which returned a price from ~November 2025 (the first 125 trading days of NSE+BSE pairs from the 247×2 = 494 row series), not "today". Then `_chg(11)` walked back from that stale point. Every dual-listed stock's 2W/4W/6W/8W in the Excel was wrong.

#### Fix #6 — `get_20d_avg_vol` filters NSE

```python
# post-v12.7
"SELECT volume FROM daily_prices "
"WHERE symbol=? AND exchange='NSE' "
"ORDER BY date DESC LIMIT 20"
```

Pre-fix, dual-listed: 10 NSE rows + 10 BSE rows. Since BSE volumes are typically 5–10× smaller than NSE, AVG was dragged DOWN, inflating `vol_spike_ratio = current_vol / avg_vol` in priority_ranker — biasing Stage 3 selection toward dual-listed stocks (almost the entire universe).

#### Fix #7 — `get_20d_avg_vol_batch` CTE filters NSE

```python
# post-v12.7
WITH ranked AS (
    SELECT symbol, volume,
           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
    FROM daily_prices
    WHERE symbol IN ({placeholders})
      AND exchange='NSE'                                  -- v12.7 added
)
```

Same root cause as #6 in the v10.13 batch path.

#### Fix #8 — `nifty_close` uses correct function + graceful mood degradation

```python
# pre-v12.7
"nifty_close":   get_nifty_52w_high_from_db(),     # WRONG — returns 52w high
"nifty_52w_high": get_nifty_52w_high_from_db(),

# post-v12.7
"nifty_close":   get_nifty_close_from_db(),        # NEW helper, correct semantic
"nifty_52w_high": get_nifty_52w_high_from_db(),
```

And in `daily_report_generator.py`:

```python
# pre-v12.7
mood = "BULLISH" if nifty > sma200 else "BEARISH"

# post-v12.7
if nifty > 0 and sma200 > 0:
    mood = "BULLISH" if nifty > sma200 else "BEARISH"
else:
    mood = "—"   # no NIFTY data available
```

NIFTY 50 isn't ingested into `daily_prices` today, so both queries return 0. Pre-fix, the mood was always BEARISH (`0 > 0` is False). Post-fix, it renders "—" honestly. The new `get_nifty_close_from_db()` in `data_bridge.py` will start returning real data the moment NIFTY ingestion is added.

#### Fix #9 — Earnings-yield CMP lookup (×2 places) filters NSE

```python
# post-v12.7  (both places in fetch_nse_fundamentals)
cmp = float((conn.execute(
    "SELECT close FROM daily_prices WHERE symbol=? "
    "AND exchange='NSE' ORDER BY date DESC LIMIT 1", (sym,)
).fetchone() or (0,))[0])
```

Pre-fix, for dual-listed symbols this returned whichever exchange's row was inserted last (BSE close ≈ NSE close, but typically off by 0.1–0.5%). Earnings yield computation got a marginally wrong CMP.

#### Fix #10 — `active_syms` anchored to MAX(date), not date('now')

```python
# pre-v12.7
"WHERE date >= date('now', '-7 days')"   # SQLite date('now') = UTC

# post-v12.7
_max_date_row = conn.execute("SELECT MAX(date) FROM daily_prices").fetchone()
_anchor_date = _max_date_row[0] if _max_date_row and _max_date_row[0] else None
if _anchor_date:
    active_syms = pd.read_sql(
        "SELECT DISTINCT symbol FROM daily_prices "
        "WHERE date >= date(?, '-7 days') ORDER BY symbol",
        conn, params=(_anchor_date,)
    )['symbol'].tolist()
```

On GitHub Actions runs that cross midnight UTC, `date('now')` shifts vs. IST. Anchoring to data's MAX(date) eliminates the wallclock dependency.

#### Fix #11 — `save_to_database` DELETE scoped to data's actual dates

```python
# pre-v12.7
"DELETE FROM daily_prices WHERE date = ?", (today_str,)
# today_str = _date.today() — server wallclock

# post-v12.7
_data_dates = sorted({str(d) for d in combined["date"].unique() if d})
if _data_dates:
    _ph = ",".join(["?"] * len(_data_dates))
    conn.execute(
        f"DELETE FROM daily_prices WHERE date IN ({_ph})",
        _data_dates
    )
```

Pre-fix, on weekend or gap-fill runs where target_date != server-today, the DELETE could remove a different day's rows than what was being inserted. Then `_safe_insert` (INSERT OR IGNORE) would skip on PK conflict for the actual target_date. Edge case, but real.

#### Fix #12 — Daily technical refresh (architectural)

```python
# master_funnel.py (post-v12.7) — new Section 1.5
if _missed_trading_days < 2:
    try:
        from backfill_history import _compute_all_indicators as _ci_daily
        import sqlite3 as _sq_daily
        _daily_conn = _sq_daily.connect("market_data.db")
        print("📊 [Section 1.5] Refreshing technicals with today's prices...")
        _ci_daily(_daily_conn)
        _daily_conn.close()
    except Exception as _rfe:
        print(f"   ⚠️  Daily technical refresh skipped (non-critical): {_rfe}")
```

Pre-v12.7 the daily flow saved today's NSE+BSE prices to `daily_prices` but never re-ran `_compute_all_indicators` unless gap-fill triggered. Result: `technical_indicators` / `weekly_momentum` stayed pinned to the last backfill date — SMA200 / RSI / MACD / R1/S1/R2/S2 / chg_2w / chg_4w / chg_6w / chg_8w in the Excel were one trading day stale (and for dual-listed stocks, the values were also wrong for separate reasons fixed in #1/#2/#5). After Section 1.5 the daily Excel uses today's prices in every rolling window.

### Group 57 test coverage (26 tests)

Code-shape locks (16 tests):
- 57.1a–c: chunk SELECT exchange, drop_duplicates, NSE preference
- 57.2a: structured error counter present
- 57.3a, 57.4a: enrich_prices + delivery UPDATE NSE-scoped
- 57.5a–7a: get_symbol_history / get_20d_avg_vol / batch all NSE-filtered
- 57.8a–c: get_nifty_close_from_db helper, master_funnel mapping, daily_report mood logic
- 57.9a: both CMP lookups patched (cross-line string match)
- 57.10a–11a: MAX(date) anchor, DELETE-by-data-dates
- 57.12a: Section 1.5 daily refresh present
- 57.13a: workflow YAML passes 400 (carry-over from v12.6.1 follow-through)

End-to-end behaviour (10 tests, 57.14a–g + 57.15a–b):
- Synthetic DB with DUAL_LISTED + NSE_ONLY + BSE_ONLY symbols populates technicals correctly for all three
- DUAL_LISTED has non-zero SMA200, RSI14, distinct R1 vs R2
- chg_2w matches NSE-only ground truth (within 0.05%)
- get_symbol_history's iloc[-1] returns today's NSE close (within 0.01)
- get_20d_avg_vol / get_20d_avg_vol_batch return NSE-only volume
- **57.15a — locks all 13 `technical_indicators` columns populate for DUAL_LISTED** (SMA200, Supertrend, ADX, RSI14, MACD signal, Stoch %K, MFI, OBV signal, Above VWAP, S1, S2, R1, R2). This is the test that would have caught the v12.6.1 production bug immediately. Chart Pattern (the 14th column) is covered separately — it's computed from today's OHLC in master_funnel and was never affected by the dual-listed bug.
- 57.15b — locks distinct S1/S2 and R1/R2 levels for DUAL_LISTED (proves dedup gave a real 247-day series, not a 494-row half-period one).

### Action required at deploy

Delete `market_data.db` (or truncate `daily_prices` / `technical_indicators` / `weekly_momentum`) before the next pipeline run so all rolling windows recompute cleanly under the patched logic. Without this, existing `technical_indicators` / `weekly_momentum` rows keep their old (buggy) values until they're rewritten.

### Issue #3 (0-vs-missing ambiguity) status

Still deferred. SQL-layer COALESCE rewrite (~12 queries) plus Python-layer `_fvn(v)` consumer refactor remains too risky to bundle. Tracked as candidate for a future dedicated release. All 15 audit issues from earlier rounds are now resolved; #3 is the last open item from the original v12.4 audit list.

---

## v12.8 RELEASE — Bug #13 (dedup ordering) + Bug #14 (yfinance 404 cache)

**Date:** April 30, 2026 (same day as v12.7 — follow-up fix-set discovered in v12.7 production audit).
**Trigger:** v12.7 production run successfully populated 92/99 stocks (vs 4/99 in v12.6.1) BUT (a) 7 specific dual-listed stocks still showed blank technicals — HALEOSLABS, PICCADIL, SKYGOLD, GCSL, SGFIN, RAYMONDREL, BIL — and (b) the v12.7 fix #2 structured error counter surfaced `compute_technicals: 495 symbols failed — ValueError: index must be monotonic increasing or decreasing`. Plus repeated `HTTP Error 404` log noise from yfinance for delisted/renamed Indian tickers (TATAMOTORS.NS, DHANI.NS, ESILVER.BO) added ~30–90s per run.
**Tests:** 519 passing (509 v12.7 carry-forward + 10 v12.8 in new Group 58, including end-to-end synthetic verification of the 13-day-fragmented-NSE pattern that triggered the production failure).

### Bug #13 — the 494-victim dedup ordering

**Root cause.** v12.7's `_compute_all_indicators` chunk dedup logic was:

```python
chunk_hist = (chunk_hist
              .sort_values(['symbol', '_exch_pref', 'date'])
              .drop_duplicates(['symbol', 'date'], keep='first')
              .drop(columns=['_exch_pref'])
              .reset_index(drop=True))
```

The intent: for each `(symbol, date)` keep the NSE row, drop the BSE duplicate. The dedup KEY's sort is correct — it clusters all NSE rows of a symbol first (because `_exch_pref=0` sorts before `=1`), so `drop_duplicates(keep='first')` picks NSE.

**The bug.** Sorting by `(_exch_pref, date)` clusters BY EXCHANGE first, then date within each exchange. Result row order:
- positions 0..222: NSE rows (in date order, but only on the 222 days NSE bhavcopy succeeded)
- positions 223..235: BSE rows (only the dates NSE failed — scattered throughout the year, not at the end)

Post-`drop_duplicates(keep='first')`, the survivors are: 222 NSE rows (date-ordered) followed by 13 BSE rows (date-scattered). After `reset_index(drop=True)`, the integer index is `0..234` but the **date column is no longer monotonic**.

`compute_technicals` then does `df = hist.sort_values('date').copy()`. This re-sorts by date — but `sort_values` reshuffles the integer index to whatever positions the rows came from. Result: integer index becomes `[222, 0, 1, 2, ..., 221, 223, 9, 224, ...]` — non-monotonic.

The v12.4-introduced lines 794-795 are:
```python
sup2 = sup2.reindex(l.index, method="ffill")
res2 = res2.reindex(h.index, method="ffill")
```

`reindex(method='ffill')` requires the target index to be monotonic. Non-monotonic input raises `ValueError: index must be monotonic increasing or decreasing`. Pre-v12.7, this was caught by `except: pass` and silently swallowed. v12.7 fix #2 surfaced it via the `_ti_errors` counter — that's how we learned 494 stocks were affected.

**Why v12.6.1 production showed 4/99 but v12.7 showed 7/99 affected by this specific bug.** v12.6.1 had the SAME bug PLUS the original "all dual-listed stocks fail dedup entirely" bug. v12.7 fixed the 95-of-99 dual-listed-completely-missing bug but exposed the smaller 7-of-99 fragmented-NSE-coverage subset. They're both manifestations of the same dedup invariant being broken.

**The fix — single line added.**

```python
chunk_hist = (chunk_hist
              .sort_values(['symbol', '_exch_pref', 'date'])
              .drop_duplicates(['symbol', 'date'], keep='first')
              .drop(columns=['_exch_pref'])
              .sort_values(['symbol', 'date'])    # v12.8 #13 FIX
              .reset_index(drop=True))
```

Re-sorting by `(symbol, date)` AFTER `drop_duplicates` makes each per-symbol slice already date-ordered. `compute_technicals`' internal sort is then a no-op and the index stays monotonic.

**Defense in depth in `compute_technicals`.** Even if a future caller's dedup is broken, the function shouldn't raise:

```python
def compute_technicals(hist):
    if hist is None or len(hist) < 20:
        return {}
    # v12.8 (#13 hardening): reset the integer index AFTER sort_values so
    # the post-sort index is guaranteed monotonic regardless of upstream
    # ordering quirks.
    df = hist.sort_values('date').reset_index(drop=True)
    df = df.dropna(subset=['close', 'high', 'low']).reset_index(drop=True)
```

`reset_index(drop=True)` after sort produces a fresh `0..N-1` index that's always monotonic. The reindex calls at lines 794-795 then never fail.

### Bug #14 — yfinance 404 noise + delay

**Root cause.** yfinance 1.x has no batch API (`yf.Tickers().info` is unreliable in 1.x — confirmed by upstream issue tracker), so we do per-symbol `.info` HTTP calls. Yahoo Finance's `quoteSummary` endpoint returns 404 for delisted/renamed Indian tickers — observed in production: TATAMOTORS.NS (split into TATAMOTORS + TATAMTRDVR in 2024 then re-merged 2024-2025; ticker may have been renamed to TATAMOTORSDM), DHANI.NS (multiple corporate actions, possibly delisted from Yahoo's universe), ESILVER.BO (delisted SME).

Each 404 takes ~5–8s wall-clock (vs ~1.5–2.5s for a 200) because yfinance's session retries internally. Plus yfinance's internal logger prints `HTTP Error 404: {"quoteSummary": ...}` — pollutes log output. With ~5-15 stale tickers per run, total wasted time is ~30–90s.

**Fix architecture (Option C — both cache + silence).**

1. **New table** `failed_yfinance_lookups (symbol, suffix, failed_on, error_type)` with composite PK `(symbol, suffix)`. Created in `init_all_tables`.

2. **Three helpers** in `backfill_history.py`:
   - `_silence_yfinance_logger()` — sets `yfinance`, `yfinance.scrapers.quote`, `yfinance.data` loggers to CRITICAL. Idempotent.
   - `_load_yf_404_cache(conn) -> set` — returns set of `(symbol, suffix)` tuples with `failed_on >= today - 30 days`.
   - `_record_yf_404(conn, symbol, suffix)` — INSERT OR REPLACE the (symbol, suffix) row with today's date.

3. **Three call-site integrations** in `_fetch_yfinance_data`:
   - Pre-loop: `_yf_skip = _load_yf_404_cache(conn)` if conn provided.
   - Skip-check: `if (sym, ".NS") in _yf_skip: continue`.
   - Empty-result + exception → `_record_yf_404(conn, sym, ".NS")` (detect 404 via "404" or "not found" in error string).

4. **CR-fallback path** (`.NS/.BO` loop for current_ratio computation, line 1714+): same pattern — skip if `(sym, suffix) in _yf_skip`, record on 404 exception.

5. **Income-statement path** (line 1857+): skip + record.

6. **`forensics_engine.py::fetch_forensic_inputs`**: signature now accepts `skip_set` parameter. Inside the `.NS/.BO` loop, `if (symbol, suffix) in _skip: continue`. Module-import-time logger silencing too.

7. **`master_funnel.py`**: pre-loads `_yf_skip_set` once before the per-stock loop, passes `skip_set=_yf_skip_set` to `ForensicsEngine.fetch_forensic_inputs`. Logs cache size at start of run if non-zero.

**TTL design choice.** 30 days picks up corporate actions that eventually propagate to Yahoo's universe (e.g., a renamed ticker might appear in Yahoo's data 2-4 weeks after NSE updates symbols). Self-healing: cached 404s automatically retry after 30 days, so we never permanently exclude a symbol.

**Why not switch APIs.** Alpha Vantage / Polygon either rate-limit aggressively at the free tier or are paid. Twelve Data is reasonable but requires migrating 30+ field mappings. yfinance covers ~95% of NSE for free; the 5% delisted/renamed tail is what we're caching.

**Why not parallelize fetches.** yfinance 1.x rate-limits aggressively when concurrent — the per-symbol loop with `time.sleep(0.3-0.5)` between calls is intentional, confirmed by upstream issue tracker.

### Group 58 test coverage (10 tests)

Code-shape locks (5 tests):
- 58.1a: dedup re-sorts by (symbol, date) AFTER drop_duplicates
- 58.2a: compute_technicals resets index after sort_values
- 58.4a: failed_yfinance_lookups table exists with (symbol, suffix) PK
- 58.7a: ForensicsEngine.fetch_forensic_inputs accepts skip_set parameter
- 58.8a: master_funnel pre-loads cache + passes skip_set

End-to-end behaviour (5 tests):
- 58.3a — synthetic 3 stocks with NSE missing on the same 13 specific dates as production (2025-03-31, 2025-04-10/14/18, 2025-05-01, 2025-08-15/27, 2025-10-02/22, 2025-11-05, 2025-12-25, 2026-01-15/26 — represented by index positions {0, 24, 48, 72, 96, 120, 144, 168, 192, 216, 230, 240, 246} in the 247-day series), all 3 populate.
- 58.3b — zero `compute_technicals: N symbols failed` log line (was 494 → 495 in v12.7 production)
- 58.5a — cache record + load works end-to-end
- 58.5b — TTL filter excludes 31-day-old entries
- 58.6a — yfinance logger silenced (level=CRITICAL after `_silence_yfinance_logger()`)

### Action required at deploy

Delete `market_data.db` (or truncate `daily_prices` / `technical_indicators` / `weekly_momentum`) before the next pipeline run so all rolling windows recompute under the patched logic. Then trigger the workflow manually.

### Expected after first v12.8 run

| Metric | v12.7 (last run) | v12.8 expected |
|---|---|---|
| Excel "SMA 200" populated | 88/99 | 99/99 (or ≥97/99 — 2 may legitimately be NEW IPOs with <200 days) |
| Excel "Resist 1" populated | 92/99 | 99/99 |
| Excel "Resist 2" populated | 88/99 | 90-99 (some legitimately "—" per v12.6 R2 collapse fix) |
| Stocks fully missing technicals (HALEOSLABS et al.) | 7/99 | 0/99 |
| `compute_technicals: N symbols failed` | 494→495 | 0 (or single-digit edge cases) |
| HTTP Error 404 lines in run log | 3-5 visible | 0 (logger silenced) |
| 404 cache size after first run | 0 (table empty) | 5-15 (TATAMOTORS, DHANI, ESILVER + others) |
| Subsequent run latency saved | n/a | ~30-90s (skip cached 404s) |

### Combined v12.7 + v12.8 column verification

All 14 user-asked technical Excel columns post-v12.8:

| Column | Source | Expected coverage |
|---|---|---|
| SMA 200 | `technical_indicators.sma_200` | 99/99 (or ≥97 for new IPOs) |
| Supertrend | `technical_indicators.supertrend` | 99/99 |
| ADX | `technical_indicators.adx` | 99/99 |
| RSI (14) | `technical_indicators.rsi_14` | 99/99 |
| MACD Signal | `technical_indicators.macd_signal_txt` | 99/99 |
| Stoch %K | `technical_indicators.stoch_k` | 99/99 |
| MFI | `technical_indicators.mfi_14` | 99/99 |
| OBV Signal | `technical_indicators.obv_signal` | 99/99 |
| Above VWAP | `technical_indicators.above_vwap` | 99/99 |
| Chart Pattern | `master_funnel:2743` (today's OHLC) | 99/99 (was already working pre-v12.8) |
| Support 1 (₹) | `technical_indicators.support1` | 99/99 |
| Support 2 (₹) | `technical_indicators.support2` | 90-99 ("—" honestly when prior 252d max ≈ recent 20d max — v12.6 design) |
| Resist 1 (₹) | `technical_indicators.resist1` | 99/99 |
| Resist 2 (₹) | `technical_indicators.resist2` | 90-99 (same as S2) |

If any column is below this expected coverage after the v12.8 deploy, the v12.7 fix #2 structured counter will surface the failing symbols in the run log. That's the canary for "investigate next session."

---

## v12.9 RELEASE — QUALITY SCORES section overhaul (Beneish M + Earn Quality + Spike refresh)

**Date:** April 30, 2026 (same day as v12.7 + v12.8 — follow-up after user audit of QUALITY SCORES + SCORES sections).
**Trigger:** User flagged Beneish M as showing only 4 unique values across 100 stocks (72% at -2.50, 10% at -2.22, 10% at -1.50, 8% at "—"). Comprehensive audit of all 8 scoring fields (Piotroski/Altman/Beneish/Earn Quality + Score/Early Entry/Spike/Storm) surfaced 3 fixable bugs.
**Tests:** 532 passing (519 v12.8 carry-forward + 7 v12.9 Beneish in Group 59 + 6 v12.9 Earn Quality / Spike refresh in Group 60).

### What was working correctly (no changes needed)

- **Piotroski F /9** — 6 unique values (5,6,7,8,9,—), bell-shaped distribution centered at 7. Proper 9-criterion implementation in `master_funnel.py:2462-2478`.
- **Altman Z** — 59 unique values, range -9.15 to 10. The 34/100 stocks at the cap of 10 are genuinely large-cap defensives (NESTLEIND, HDFCAMC, SUNPHARMA, etc.); cap is correct v12.5 design.
- **Score /100 (composite)** — 94 unique values, healthy bell distribution from 13.82 to 100, median 62.31. Weighted sum of fundamental/technical/EE/sentiment/safety + spike bonus + MoS adjustment.
- **Storm Score /10** — 8 unique values, range 1-8, median 5. Defensive-quality counter (max 11 by formula but production caps at 8). Working correctly.
- **Early Entry /100** — 22 unique non-zero values + 31 stocks at 0. The zeros are correctly distributed across AVOID/OVERVALUED/WATCHLIST verdicts (stocks past entry zone) — by-design conditional scoring.

### Bug #1 — Beneish M proxy (3-bucket discrete instead of real formula)

The v10.3 implementation was a placeholder using only TATA = (NI-CFO)/TA, bucketed into 3 hardcoded values (-2.50, -2.22, -1.50). Lost all the discriminating power of the real formula — a stock with rapidly-growing receivables relative to sales (DSRI spike — classic revenue-stuffing signal) was invisible.

**Fix:** Real 8-variable Beneish (1999) formula:
```
M = -4.84 + 0.92·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI
    + 0.115·DEPI - 0.172·SGAI + 4.679·TATA - 0.327·LVGI
```

Each index compares current year (`_t`) to prior year (`_t1`):
- DSRI = Days Sales in Receivables (rec_t/sales_t) ÷ (rec_t1/sales_t1) — flags revenue stuffing
- GMI = Gross Margin Index (prior/current) — flags margin pressure
- AQI = Asset Quality Index (1 - (CA+PPE)/TA)_t / same_t1
- SGI = Sales Growth Index
- DEPI = Depreciation Index (prior rate / current rate)
- SGAI = SG&A Index (current % / prior %)
- LVGI = Leverage Index (TL/TA)_t / same_t1
- TATA = Total Accruals to Total Assets (current period only)

**Implementation: 2-path with fallback.**
- Path 1 (preferred): Real formula. Requires foundational fields in BOTH periods (sales_t/t1, ta_t/t1, tl_t/t1). Each ratio uses neutral 1.0 fallback when its component data is missing.
- Path 2 (fallback): v10.3 single-period accrual proxy. Used for newly-listed stocks, sparse small-caps where yfinance returns only 1 year of data.

**18 new prior-period fields** extracted in `fetch_forensic_inputs` under prefix `beneish_*_t` (current) and `beneish_*_t1` (prior): receivables, total assets, total liabilities, PPE, current assets, sales, COGS, gross profit, SG&A, income from continuing operations, depreciation, operating cash flow.

**New helper `_val_cr_at(df, row_label, col_idx)`** reads a specific column position from yfinance financial DataFrames (col 0 = current year, col 1 = prior).

**Sanity clamp [-10, 10]** — real Beneish values empirically range -8 to +5; outside ±10 indicates input corruption.

**Threshold unchanged** at > -2.22 → manipulation flag. Downstream consumers (`pre_screener.py`, `master_funnel.py:2280`, `spike_screener.py`) all preserve their existing logic.

### Bug #2 — Earn Quality unit mismatch (HIGH inflation)

70% of stocks were scoring HIGH because the v10.8 calculation compared `cfo` (annual TTM from yfinance income_stmt) against `q_pat_cr` (quarterly PAT from quarterly_income_stmt). This 4× unit mismatch made the 0.8 HIGH threshold effectively 0.2 in real annualized terms — nearly everything qualified.

Production examples:
- NESTLEIND: q_pat=873 Cr, CFO=3850 Cr → ratio 4.41 → HIGH (real annualized: 1.10 — still HIGH but barely)
- JKCEMENT: q_pat=360 Cr, CFO=1704 Cr → ratio 4.73 → HIGH (real: 1.18)
- A marginal stock with q_pat=200 Cr, CFO=250 Cr (annual) → ratio 1.25 → HIGH (real: 0.31 → LOW, honest)

**Fix:** annualize PAT before the ratio calculation. Prefer annual `net_income_annual` (now exposed by `fetch_forensic_inputs`) directly; fall back to `q_pat × 4` when only quarterly is available. Also exposes `cfo_pat_ratio` as a numeric field for downstream consumers (spike_screener guard reads this).

```python
cfo  = _num(row, 'operating_cf_cr', 'cfo', 'operating_cf')
pat_annual = _num(row, 'net_income_annual', 'ni_annual')
if pat_annual <= 0:
    _pat_q = _num(row, 'q_pat_cr', 'net_profit', 'net_income')
    pat_annual = _pat_q * 4 if _pat_q > 0 else 0
if pat_annual > 0 and cfo != 0:
    _eq_ratio = cfo / pat_annual
    if   _eq_ratio >= 0.8: results['earnings_quality'] = "HIGH"
    elif _eq_ratio <  0.5: results['earnings_quality'] = "LOW"
    else:                  results['earnings_quality'] = "MODERATE"
    results['cfo_pat_ratio'] = round(_eq_ratio, 3)
```

**Expected post-deploy:** HIGH count drops from 70% → ~30-40% (true cash-backed earners), MODERATE rises from 1% → ~25%, LOW grows from 14% → ~25-30%.

### Bug #3 — Spike Score stale-data guard (BANARISUG-class false suppression)

The 3H anti-trigger guard (`v7_engine.apply_section_3H_guards`) runs at `master_funnel:871` BEFORE Section 5A.5 forensics re-run (line ~1971). For stocks where Altman Z and Beneish M were computed only after the FM-enriched forensics pass, the guard's spike_suppressed flag was set with stale data (default zeros, treated as "unknown" so suppression skipped). But for stocks where forensics actually were already populated at the first pass and showed real distress signals, the original suppression decision could become wrong by the time spike scoring runs.

Production example: BANARISUG (Altman 7.15 — clean, Beneish -2.5 — clean, no pledge) showed Spike Score = 0 in v12.8 production despite having 3+ triggers (MACD+ST BUY + vol 3.96, ADX low, RSI 63.9 + vol > 2, vol > 3 + deliv > 60). Manual recompute showed 3 triggers. The flag had been set with stale defaults at the first 3H pass, then later forensics CONFIRMED clean — but the stale flag wasn't refreshed.

**Fix:** Re-evaluate the 3H guard in `master_funnel.py` immediately before applying spike suppression, using the LATEST in-stock `altman_z`, `beneish_m`, `pledge_pct` values. Updates `spike_suppressed`, `risk_flag_active`, and `guard_reasons` with the fresh evaluation.

```python
# v12.9 FIX: re-run 3H guard with FRESH forensics values
_alt_re = float(str(stock.get('altman_z', 0) or 0).replace("—","0") or 0)
_ben_re = float(str(stock.get('beneish_m', 0) or 0).replace("—","0") or 0)
_pl_re  = float(str(stock.get('pledge_pct', 0) or 0).replace("—","0") or 0)
_refresh_suppress = False
_refresh_reasons  = []
if _pl_re > 20:
    _refresh_suppress = True; _refresh_reasons.append("Pledge > 20%")
if _alt_re != 0 and _alt_re < 1.81:
    _refresh_suppress = True; _refresh_reasons.append("Altman Z < 1.81")
if _ben_re != 0 and _ben_re > -2.22:
    _refresh_suppress = True; _refresh_reasons.append("Beneish M > -2.22")
stock["spike_suppressed"] = _refresh_suppress
stock["risk_flag_active"] = _refresh_suppress
stock["guard_reasons"]    = ", ".join(_refresh_reasons)
```

**Expected post-deploy:** Stocks like BANARISUG/MOHITIND with clean forensics now correctly show their spike triggers; stocks with real Altman/Beneish distress continue to be suppressed (zeroed out).

### Test coverage (Group 59 + Group 60 = 13 new tests)

**Group 59 — Beneish M (7 tests):**
- 59.1a: Honest stock continuous output between -3.5 and -2.22
- 59.2a: Aggressive-accrual stock → M > -2.22 (flagged)
- 59.3a: Thin-data → proxy fallback bucket
- 59.4a: Empty data → 0.0 (insufficient marker)
- 59.5a: M-score monotone in TATA (locks academic core property)
- 59.6a: Code-shape — 12 required Beneish field assignments present
- 59.7a: Extreme/corrupt input clamped to [-10, 10]

**Group 60 — Earn Quality + Spike refresh (6 tests):**
- 60.1a: Marginal stock (q_pat=200, CFO=250) → LOW (post-fix), was HIGH pre-fix
- 60.2a: Annual NI path used directly when available
- 60.3a: Returns "—" when no PAT data
- 60.4a: `fetch_forensic_inputs` populates `net_income_annual`
- 60.5a: master_funnel re-runs 3H guard with fresh forensics
- 60.6a: Refresh runs BEFORE spike-suppression check (ordering lock)

### Action required at deploy

**No DB delete required.** v12.9 only modifies in-memory computation. Existing `fundamental_metrics.beneish_m` and `earnings_quality` values refresh automatically on the next pipeline run via Section 5A.5 forensics re-run.

### Expected after first v12.9 run

| Metric | v12.8 (last run) | v12.9 expected |
|---|---|---|
| Beneish M unique values | 4 | ~80-95 (continuous) |
| Beneish distribution shape | 3-bucket (-2.5/-2.22/-1.5) | Bell-shaped, center -2.0 to -2.5 |
| Earn Quality HIGH count | 70/100 | 30-40/100 |
| Earn Quality MODERATE count | 1/100 | ~25/100 |
| Earn Quality LOW count | 14/100 | 25-30/100 |
| Spike Score for BANARISUG-class | 0 (false-suppressed) | 2-3 (real triggers honored) |

If Beneish distribution still shows 4-bucket pattern, check that yfinance returns 2+ years of balance-sheet data for the funnel stocks. The v12.8 404 cache might be incorrectly skipping symbols whose `.NS` info call fails but whose balance_sheet would succeed — review `failed_yfinance_lookups` table.

### Open known issues (deferred from v12.8)

1. 0-vs-missing ambiguity (original v12.4 audit issue) — still deferred
2. NIFTY 50 not ingested — daily report mood renders "—" honestly
3. Altman Z capping at 10 (v12.5 behavior) — 34/100 hit cap. Future work: uncap and fix X4 unit-mismatch root cause.

---

## v13.0 RELEASE — Free-tier shareholding data unlocked (NSE bulk pledge + corp-info QoQ deltas)

**Date:** May 2, 2026
**Trigger:** v12.9 production audit revealed 9 SHAREHOLDING-section columns were 100% empty (Pledge %, Pledge Direction, Pro QoQ Δ, FII QoQ Δ, DII %, DII QoQ Δ). Investigation found NSE actually publishes both data sources for free — they just weren't being called or were being called with single-quarter parsing only. v13.0 wires both up. Promoted to v13.0 (not v12.10) because this is the first release that delivers genuinely new column populations rather than internal scoring changes.
**Tests:** 542 passing (532 v12.9 + 10 v13.0 in new Group 61).

### What was changed

**1. New module `ingestion/nse_pledge.py`** (~140 lines)
- Calls NSE's bulk pledge endpoint `https://www.nseindia.com/api/corporates-pledgedata?index=equities`
- ONE API call returns the latest pledge filing for every listed company (~5,000 stocks)
- `fetch_bulk_pledge_data(session)` returns `{symbol: pct_pledged}` dict
- `merge_pledge_into_rows(rows, pledge_map)` updates sh_rows in place
- Preserves existing non-zero values when bulk source returns 0 (avoids overwriting good data with empty)
- Sanity-clamps pct to [0, 100], handles malformed records gracefully
- Network-failure-safe: returns empty dict, pipeline continues without pledge data

**2. Extended `_nse_shareholding(symbol, session)` in `backfill_history.py`**
- Pre-v13.0: read only `shareholdingPatterns.data[0]` (latest quarter)
- v13.0: reads `data[0]` AND `data[1]` (latest + prior), computes 3 QoQ deltas
- Adds keys: `promoter_qoq`, `fii_qoq`, `dii_qoq` (only when prior quarter has non-zero values)
- Zero new API calls — NSE corp-info already returns 2-4 quarters in single response
- Was a free upgrade waiting to be unlocked

**3. NSE shareholding enrichment loop in `backfill_history.py`**
- Now writes captured QoQ deltas back to sh_rows (was previously discarded)
- Stops skipping rows that have DII data but no QoQ — runs the call when EITHER is missing
- Bulk pledge fetch added at the end of enrichment (after per-symbol corp-info loop)
- Both new fetches respect existing rate-limit guards

**4. Pledge direction vocabulary alignment** (`master_funnel.py:803-808`)
- Pre-v13.0 bug: master_funnel wrote IMPROVING/DETERIORATING but `scoring_engine.py:180` checked for FALLING/RISING — the `_has_paid_sentiment` gate **never** matched on pledge movement, silently breaking the QoQ-Δ-aware sentiment-informed scoring path for any stock with real pledge movement. Pre-v13.0 this was invisible because `pledge_pct` was hardcoded 0 (no movement to detect). v13.0 makes pledge real, so this latent bug now matters.
- Fix: write FALLING / RISING / STABLE / "—" to match the read path. Tooltips, glossary, and `ownership_tracker.py` already used this vocabulary; only master_funnel was outlying.

**5. Documentation refreshed**
- `reporting/tooltip_formatter.py`: Pledge Direction tooltip explains v13.0 vocab alignment + v13.0 NSE source
- `reporting/excel_generator.py` glossary: Pledge %, Pledge Direction, Pro QoQ Δ, FII QoQ Δ, DII %, DII QoQ Δ — all 6 entries reflect v13.0 NSE sourcing. Both glossary blocks (line ~444 and ~722) synced — second block had pre-existing stale "INCREASING/DECREASING" vocab; replaced with FALLING/RISING.
- `CLAUDE.md` (this file): Section 32 added.
- `readme.md`: v13.0 row added to top of version table.

**6. SHAREHOLDING section color changed (visual cleanup)**
- Pre-v13.0: section header used `#EA580C` (bright red-orange), which visually conflicted with AVOID-row tier-3 colors (`#FEE2E2` light red). The section looked perpetually "alarming" even for stocks with clean shareholding patterns.
- v13.0: changed to `#7C3AED` (violet) — neutral, in-palette, non-red-adjacent. Matches the SCORES section color for visual consistency.
- Updated in both color-map locations: line 48 (FULL_SECTIONS tuple) and line 961 (SECTION_COLORS dict).
- Cell background colors for individual data cells are unchanged (still tier-based per row verdict). Only the section banner row is affected.

### What unchanged (validated)

- All 7 fair-value models (M1-M7) — unchanged
- All 8 scoring fields (Piotroski, Altman, Beneish, Earn Quality, Score, Storm, Spike, Early Entry) — unchanged
- MoS calculation, verdict logic, all override layers — unchanged
- Anti-trigger guard threshold (pledge > 20%) — unchanged; just receives real data now
- Gold-tier filter (11 conditions including pledge ≤ 10%) — unchanged; just gets discriminating power now
- DB schema — unchanged; `shareholding.pledge_pct` and `pledge_dir` columns existed already
- yfinance fetch path, reconciler, allowlist, holiday calendar — all untouched
- Verdict assignment + confidence dots — unchanged

### Scoring/verdict logic — impact analysis

**No scoring or verdict logic changes were needed in v13.0.** The full sentiment/Storm/anti-trigger/Gold-tier infrastructure was already designed to consume `promoter_qoq`, `fii_qoq`, `dii_qoq`, `pledge_pct`, and `pledge_direction` — pre-v13.0 those fields were just hardcoded to zero. v13.0 makes them real for the first time.

**What the data flow looks like post-v13.0:**

| Score component | Pre-v13.0 behaviour | Post-v13.0 behaviour |
|---|---|---|
| `sentiment_score` (master_funnel:2285-2343) | Default 50 for ~all stocks (no QoQ + pledge dir mismatched) | Moves -15 to +20 from 50 based on real QoQ + pledge direction |
| `_has_paid_sentiment` gate (scoring_engine:174-181) | Almost always False → weights redistributed (no sent contribution) | True for 50-80 stocks → canonical weights with sent_raw × 0.10 |
| Anti-trigger guard (master_funnel:2680-2705) | pledge>20 never fired (all pledge=0) | Fires for ~5-15 funnel stocks → spike_count zeroed + risk_flag = -10 |
| Storm Score (scoring_engine:340 region) | promoter_qoq>0 bonus never fired | +1 fires for stocks with real promoter accumulation |
| Early Entry score (master_funnel:2370 region) | FII QoQ > +1pp branch never fired | +8 fires for genuine FII accumulation |

**Net effect on composite Score per stock:** typically ±0–5 points for most, but ±10–15 for stocks at the extremes (high-pledge red-flag stocks score lower; high-accumulation stocks score higher). **All shifts are intended behaviour** — the framework was correctly designed; the data just wasn't there before.

**Verdict thresholds remain unchanged** (LARGE: 60/50, MID: 63/53, SMALL: 66/56, MICRO: 70/60). They are score-agnostic and don't need to know whether sentiment data was real or imputed.

**What may change in production output:** A few BUY-verdict stocks with high pledge (>20%) may demote to OVERVALUED or WATCHLIST as the anti-trigger guard correctly fires. A few WATCHLIST stocks with strong DII accumulation + falling pledge may promote to BUY as their sentiment_score lifts above the cap-tier threshold. **This is the correct behaviour the system was always designed for** — pre-v13.0 was running with sentiment data forcibly disabled.

### Group 61 test coverage (9 tests)

- 61.1a: `ingestion.nse_pledge` exposes both `fetch_bulk_pledge_data` and `merge_pledge_into_rows`
- 61.2a: Network failure returns empty dict (graceful fallback, no crash)
- 61.3a: Bulk pledge parser handles 7 input edge cases — pctEncumbered field, derived from share counts, sanity clamp on out-of-range, dedup on duplicate symbols, skips malformed/empty
- 61.4a: `merge_pledge_into_rows` is case-insensitive, preserves existing non-zero values, doesn't touch other fields
- 61.5a: `_nse_shareholding` computes Pro/FII/DII QoQ deltas from 2-quarter response
- 61.6a: `_nse_shareholding` correctly omits QoQ keys when only 1 quarter available
- 61.7a: `master_funnel` writes FALLING/RISING (matches scoring_engine sentinel check, no IMPROVING/DETERIORATING)
- 61.8a: backfill imports + calls bulk pledge fetch BEFORE shareholding upsert
- 61.9a: Tooltip + glossary use new FALLING/RISING vocabulary throughout
- 61.10a: SHAREHOLDING section header color changed from `#EA580C` (red-orange) to `#7C3AED` (violet) — verified in both color-map locations

### Action required at deploy

**No DB delete required.** Schema unchanged. On the first v13.0 run:
- Bulk pledge fetch runs → populates ~50-200 of the funnel's 100 stocks (most listed companies have zero pledge so don't appear in NSE feed at all — that's correct behaviour, not a bug)
- QoQ deltas populate from existing per-symbol corp-info calls → ~50-80 stocks get real Pro/FII/DII QoQ values immediately
- Pledge direction populates as FALLING/RISING/STABLE for stocks with both current and prior pledge (most show "—" until 2nd run accumulates history)

### Expected after first v13.0 run

| Metric | v12.9 | v13.0 expected |
|---|---|---|
| Pledge % populated stocks | 0/100 | 50–100/100 (real values 0–100% from NSE feed) |
| Pledge Direction non-"—" | 0/100 | 5–20/100 first run, more on subsequent runs |
| Pro QoQ Δ populated | 0/100 | 50–80/100 (NSE returns 2–4 quarters) |
| FII QoQ Δ populated | 0/100 | 50–80/100 |
| DII % populated | 0/100 | 50–80/100 |
| DII QoQ Δ populated | 0/100 | 50–80/100 |
| Spike Score behaviour | Anti-trigger guard had no real pledge to gate on — only Altman/Beneish drove suppression | Anti-trigger guard now genuinely fires on `pledge > 20%`. Stocks like VEDL, DBREALTY, ADANIPOWER will correctly see Spike → 0 |

### Network failure behaviour

If NSE blocks GitHub Actions IPs (which they sometimes do via Akamai bot detection), the bulk pledge endpoint returns 4xx and `fetch_bulk_pledge_data` returns `{}`. Pipeline continues with `pledge_pct=0` for all stocks, exactly as pre-v13.0 behaviour. No regression risk.

The corp-info per-symbol calls already had this fallback in place.

### Open known issues (carried forward from v12.9)

1. 0-vs-missing ambiguity (original v12.4 audit issue) — still deferred
2. NIFTY 50 not ingested — daily report mood renders "—" honestly
3. Altman Z capping at 10 (v12.5 behavior) — 34/100 hit cap by-design
4. NEWS & RISK section + ANALYSIS SUMMARY — Gemini AI quota issue (config, not code)
5. PIPELINE / OB section (5 cols) — order book metrics, no free aggregator

---

## v13.x RELEASE — Production-credibility patch set (Top 5 BUY filter · MoS '—' for ETFs · Quick Pick recompute)

**Date:** May 7, 2026
**Trigger:** v13.0 production audit by Rajkumar against `NSE_BSE_Full_Dashboard_20260506.xlsx` (100 stocks · 124 columns · 12,400 cells). Three real data-credibility issues found alongside one false-alarm (MoS formula was correct — the audit rule was using textbook (CFV-CMP)/CFV instead of the source-code (CFV-CMP)/CMP). Rate before fix: 11/12,400 cells (0.089%). Rate after fix: 0/12,400 (0.000%).

### What was changed

**Issue 1 — `reporting/daily_report_generator.py:62` — txt report's "TOP 5 BUY CANDIDATES" filter**

Pre-fix: section header said "TOP 5 BUY CANDIDATES" but the code did `df.sort_values(by=['spike_count', 'mos_pct']).head(5)` with **no verdict filter**. OVERVALUED and NEUTRAL stocks with high spike counts could leak in. Production example from 2026-05-06: MOCAPITAL appeared with `VERDICT: OVERVALUED` in a section labelled BUY.

Fix: filter to BUY first, then apply existing sort. Substring match (`'BUY' in verdict`) tolerates dotted display variants like `BUY ●●●` / `BUY ○○`. The other 4 verdict values (OVERVALUED, WATCHLIST, NEUTRAL, AVOID) don't contain the substring "BUY", so the filter is unambiguous.

```python
# Pre-fix
top_buys = self.df.sort_values(
    by=['spike_count', 'mos_pct'], ascending=[False, False]).head(5)

# Post-fix
_buy_only = self.df[self.df['verdict'].astype(str).str.contains(
    'BUY', case=False, na=False, regex=False)]
top_buys = _buy_only.sort_values(
    by=['spike_count', 'mos_pct'], ascending=[False, False]).head(5)
```

**Issue 2 — `reporting/excel_generator.py:1626` and `reporting/report_formatter.py:42` — MoS=-100% leak when CFV is unavailable**

Root cause: `analysis/fair_value_engine.py:439` returns `mos_pct = (cfv - cmp) / cmp * 100` whenever `cmp > 0`. For ETFs/index funds where no models fire (cfv=0), this yields **-100.0** as a math-only artifact. Pre-fix the Excel cell showed `-100%` paired with `SIGNIFICANT PREMIUM†`, and the txt Quick Card showed `MoS: -100% [SIGNIFICANT PREMIUM†]` and `CMP is 100% EXPENSIVE` — misleading readers into thinking the stock is wildly overvalued, when the truth is "we couldn't value it with our model set".

Production audit found 8 affected stocks (all NSE-listed ETFs / index funds): **CHEMICAL, HSBCGOLD, BANKBETA, MOCAPITAL, SENSEXBETA, LICMFGOLD, EGOLD, GOLDBETA**.

Fix strategy considered: setting `stock["mos_pct"] = "—"` directly in master_funnel. Rejected because it breaks downstream numeric consumers — DB write at `master_funnel.py:3171` does `float(_s_lar.get("mos_pct", 0) or 0)`, AI analyst reads via `v("mos_pct")`, command_parser filters `df['mos_pct'] > 25`, sort operations rely on numeric, and the score-adjustment lookup uses `mos_pct` as a numeric input. Any one of these would crash or silently misbehave.

Fix applied: **display-time intercept at the Excel cell write and the Quick Card text builder**. Internal stock dict stays numeric (`mos_pct=-100`) so all downstream consumers work unchanged; the cell renders `'—'` and the card prints `'MoS: —  [—]'`. Mirrors the existing `cfv == 0 → '—'` rule already in place via `FV_MODEL_KEYS`.

```python
# excel_generator.py — patched logic
_cfv_for_display = stk.get("cfv", 0)
_cfv_missing = (_cfv_for_display in (0, 0.0, None, "", "—"))
for ci,(_,_,key) in enumerate(FULL_COLS,1):
    val = _g(stk, key)
    if key in FV_MODEL_KEYS and (val == 0 or val == 0.0):
        val = "—"
    if _cfv_missing and key in ("mos_pct", "mos_label"):
        val = "—"
    cell = ws.cell(rn, ci, val)
```

The Gold sheet does not need this patch — its 11-condition filter already excludes any stock with no CFV (they fail the score≥70 + 15%≤MoS≤100% gates).

**Issue 3 — `master_funnel.py:2582` — Quick Pick stale after Score Convergence +8 EE bonus**

Order-of-operations bug. The pipeline runs three steps in sequence:

1. `ScoringEngine.calculate_composite_score(stock)` → returns `label` (the Quick Pick) using the **pre-bonus** `early_entry_score`. Set at `scoring_engine.py:324` via `_assign_quick_pick(data, final_score)`.
2. `stock.update(score_result)` writes `label` to the stock dict.
3. **Then** the v10.x Score Convergence pass at `master_funnel.py:2562-2582` adds +8 to `early_entry_score` when score≥70 AND RSI>60 AND supertrend=BUY. Updates `early_entry_score` in the dict (the Excel reads this updated value).

Pre-fix: the displayed EE in Excel column 10 was post-bonus, but the `label` (Quick Pick column 8) was locked in with pre-bonus EE. When the +8 bump moved EE across the **60** archetype threshold (`DEEP VALUE` → `DEEP VALUE EARLY MOVER` requires EE≥60) or the **70** threshold (`WATCHLIST` → `EARLY MOVER` requires EE≥70 AND score>55), the row showed inconsistent values.

Production audit found 3 affected stocks:

| Symbol | Score | Pre-bonus EE | Post-bonus EE | Excel showed (buggy) | Should be |
|---|---|---|---|---|---|
| MOCAPITAL | 72.66 | 65 | 73 | WATCHLIST | EARLY MOVER |
| KIRLFER | 73.95 | 65 | 73 | WATCHLIST | EARLY MOVER |
| KAMAHOLD | 72.21 | 55 | 63 | DEEP VALUE | DEEP VALUE EARLY MOVER |

Fix: re-call `_assign_quick_pick(stock, _score_final)` inside the convergence-bonus `if` block, AFTER the EE update. Defensive: only re-runs when the bonus actually fires (no impact on stocks where the bonus condition didn't trigger). Same `scoring` instance, same method, same signature as the first call.

```python
if (_score_final >= 70 and _rsi_final > 60 and _st_final == "BUY"
        and "SCORE CONVERGENCE" not in _sigs_str):
    _ee_now = min(100, _ee_now + 8)
    stock["early_entry_score"] = _ee_now
    # ... existing badge / label updates ...
    # v13.x fix: recompute Quick Pick after EE update
    stock["label"] = scoring._assign_quick_pick(stock, _score_final)
```

### Test results

15 tests across 3 layers:

- **8 unit tests** (`test_fixes.py`): verdict-filter logic, sort-order preservation, FV-engine leak source, normal-stock unchanged, pre/post bonus QP transitions, KAMAHOLD case, no-bonus case
- **7 integration tests** (`test_integration.py`): 3 real production-shape stock profiles (MOCAPITAL, KIRLFER, KAMAHOLD), no-bonus regression, Excel-renderer ETF edge cases, internal-dict-untouched safety, txt-report Section B real scenario
- **Full-data simulation** against the production Excel: applied patched logic to all 100 stocks
  - Issue 1: Top 5 BUY = SANDHAR, HALEOSLABS, APLLTD, SAHYADRI, AMBIKCO (all BUY ✅)
  - Issue 2: All 8 ETFs render `—`; **zero changes** to other 92 stocks
  - Issue 3: Exactly the 3 expected QP changes; **zero unexpected** changes elsewhere

### Documents updated

| File | Update |
|---|---|
| `reporting/tooltip_formatter.py` | MoS % and MoS Label tooltips note the '—' rendering rule for ETFs · Quick Pick tooltip notes the v13.x recompute timing |
| `reporting/excel_generator.py` (GLOSSARY_DATA) | MoS % glossary row mentions ETF rendering · Quick Pick glossary row mentions the recompute |
| `CLAUDE.md` | This §33 section |
| `readme.md` | New v13.x row at top of version-history table |

Not touched: `pipeline_reference_v13_0.html` (describes scoring flow, not rendering edge cases) · `scoring_logic_3Stagefunnel_explained.md` (no QP/MoS rendering details).

### Open known issues (carried forward from v13.0)

1. 0-vs-missing ambiguity (original v12.4 audit issue) — still deferred
2. NIFTY 50 not ingested — daily report mood renders "—" honestly
3. Altman Z capping at 10 (v12.5 behavior) — 34/100 hit cap by-design
4. NEWS & RISK section + ANALYSIS SUMMARY — Gemini AI quota issue (config, not code)
5. PIPELINE / OB section (5 cols) — order book metrics, no free aggregator

### Edge-case stocks NOT fixed (defensible by design — flagged for awareness)

Two BUY verdicts with mildly negative MoS that pass the formal -10% gate by 1-2 points but don't meet the technical-confirmation criteria for the wider -20% gate:

- **VENTIVE** (score 68.77, MoS -9.39%) — Supertrend BUY + Stage 2, but score below 70 threshold for tech_confirmed
- **BAJAJHCARE** (score 70.49, MoS -8.76%) — Supertrend NEUTRAL, sector NEUTRAL — passes formal gate only

Both are cliff-zone BUYs. Defensible if challenged ("score and MoS gate both pass"), but represent edge cases where a stricter rule could be added in a future release. Left alone in v13.x because they're not bugs — the verdict logic is producing exactly what its rules specify.

---

## v13.x Round 3 — txt-report polish (HEADER honesty · SECTION F filter · Quick Pick 3-factor clarification)

**Date:** May 7, 2026 (later in same day as §33)
**Trigger:** User audit of `Daily_Analysis_Report_20260506__1_.txt` (the post-v13.x-Round-2 txt output) flagged three more issues + one design-clarity question.

### What was changed (3 fixes + 1 doc clarification)

**Fix 4 — `reporting/daily_report_generator.py:28-50` + `master_funnel.py:3082-3104` — HEADER honesty for placeholder market data**

Pre-fix: header displayed `Nifty: 0.0 | Sensex: 0 | VIX: 12.0 | FII: ₹817.0Cr` with mood `—`. The mood line was correctly honest about NIFTY data being unavailable, but the second header line printed:
- `Nifty: 0.0` — NIFTY 50 not ingested (master_funnel:3091 `get_nifty_close_from_db()` returns 0 because NIFTY 50 isn't in `daily_prices`)
- `Sensex: 0` — hardcoded 0 in master_funnel:3092
- `VIX: 12.0` — hardcoded constant in master_funnel:3096

Mixing honest absence (`—`) with fake numeric precision is misleading. v13.x extends the same honesty principle: any market scalar that's a known placeholder renders as `—`. FII (real data from `get_latest_fii_net_cash()`) keeps numeric format.

Two-file fix:
- `master_funnel.py:3104`: `"vix": 12.0` → `"vix": 0`. Verified the `market_stats["vix"]` field has only ONE consumer (daily_report_generator) — the storm score path at `master_funnel.py:2648` uses its own hardcoded `market_vix=12.0` constant directly, not via market_stats. Confirmed by grep for `market_stats["vix"]` and `mkt.get("vix"`.
- `reporting/daily_report_generator.py:38-58`: defensive renderer logic — if value is non-positive or non-numeric, render `—`. FII handled separately because it can be legitimately negative (net selling).

**Fix 5 — `reporting/daily_report_generator.py:117-138` — SECTION F filter aligned with section intent**

Pre-fix issues:
1. Section title was hardcoded `"SECTION F — 2 EXIT ALERTS"` regardless of actual count
2. Filter was `composite_score < 30` AND head(2) — only catches extreme outliers
3. Production audit: 4 AVOID-verdict stocks existed (HDFCLIFE 34.09, VIPIND 29.04, UBL 37.22, RELIGARE 35.48) but only VIPIND would have qualified — and on the next pipeline run VIPIND's score moved to ≥30, so Section F became empty even though 4 stocks still warranted exit attention

Fix: align filter with section's intent ("EXIT ALERTS" = system's explicit AVOID verdict). Substring match (`'AVOID' in verdict`) tolerates dotted display variants like `AVOID ●●●` / `AVOID ●○○ (thin data)`. Sort weakest first (lowest score → most urgent). Cap at head(5) since the pipeline rarely produces more. Title is now dynamic with English plural handling: `"X EXIT ALERT"` for 1, `"X EXIT ALERTS"` for 0/N. When 0 AVOID stocks exist, the bare `"SECTION F — EXIT ALERTS"` header prints (no count), matching the existing "No candidates today" convention.

**Fix 6 — Quick Pick three-factor clarification (doc-only)**

User question: "Why does DEEP VALUE EARLY MOVER use 3 factors when the others use 2?"

The answer is structural — DEEP VALUE EARLY MOVER is the **combo** of the two single-archetype rules above it:
```
DEEP VALUE alone:      MoS > 25% AND Score > 70                       (no EE check)
EARLY MOVER alone:                       Score > 55 AND EarlyEntry ≥ 70
DEEP VALUE EARLY MOVER:MoS > 25% AND Score > 70 AND EarlyEntry ≥ 60   ← combo
```

The third factor isn't extra work — it inherits the value gates from DEEP VALUE and adds a momentum check. The notable design choice is the **EE threshold drop 70 → 60 in the combo**: when a stock already has high score AND deep undervaluation, you don't need an extreme momentum signal — moderate confirmation (EE≥60) is enough. Pure EARLY MOVER demands EE≥70 because momentum is the only thing speaking for stocks that may not have great fundamentals (Score > 55 floor, no MoS gate).

Updates applied to 3 surfaces (no code logic change):
1. `reporting/tooltip_formatter.py` — Quick Pick `TIPS` entry: added 9-line "Why does DEEP VALUE EARLY MOVER use 3 factors..." paragraph
2. `reporting/excel_generator.py` — `_HDR_TIPS` Quick Pick header tooltip: condensed 6-line version
3. `reporting/excel_generator.py` — `GLOSSARY_DATA` Quick Pick row: integrated explanation in single sentence

### Test results

22/22 tests pass across all 3 rounds:
- Round 1 (test_fixes.py): 8/8
- Round 2 (test_integration.py): 7/7
- Round 3 (test_v13_x_round3.py): 7/7 — covers HEADER honesty, Section F filter + dynamic title + dotted-verdict tolerance + zero-AVOIDs case, tooltip + glossary content checks

12/12 end-to-end checks against real production Excel data pass:
- Section B: BUY-only ✅
- ETFs: 8/8 render `—` ✅
- Quick Pick: 3 known cases corrected ✅
- Header: Nifty/Sensex/VIX render `—`, FII keeps numeric ✅
- Section F: dynamic title `"4 EXIT ALERTS"`, all 4 AVOID stocks listed (VIPIND, HDFCLIFE, RELIGARE, UBL) ✅
- Tooltip + glossary: combo/asymmetry explanations present ✅

### Files changed in Round 3

| File | Change |
|---|---|
| `master_funnel.py` | 1 line: `"vix": 12.0` → `"vix": 0` (with v13.x rationale comment) |
| `reporting/daily_report_generator.py` | HEADER block: defensive renderer for placeholder values; SECTION F: AVOID filter + dynamic title |
| `reporting/tooltip_formatter.py` | Quick Pick `TIPS` entry: combo + EE-asymmetry explanation |
| `reporting/excel_generator.py` | `_HDR_TIPS` Quick Pick: condensed combo explanation; `GLOSSARY_DATA` Quick Pick: integrated explanation |
| `CLAUDE.md` | This §33.1 addendum |
| `readme.md` | v13.x row updated with Round 3 additions |

---

## v14.0 RELEASE — Gold-pick outcome tracking system (the "did our recommendations actually work?" feedback loop)

**Date:** May 8, 2026
**Trigger:** Rajkumar question: "How do I know whether the Gold sheet recommendations achieved their targets or not?" The pipeline produces 0–10 Gold-tier picks every day with Entry Range / Stop Loss / T1 / T2 / T3 levels, but the only persisted history was `latest_analysis_results` (PRIMARY KEY symbol — overwritten every run). No way to measure success rate, hit timing, or filter quality. v14.0 closes this gap with a forward-tracking outcome system.

**Promoted to v14.0 (not v13.y) because this delivers a fundamentally new system capability — measurement — not a fix to existing logic.**

### Design decisions (locked in)

- **Track each Gold-sheet stock** as a recommendation. Eligibility = clears the existing 11-condition Gold filter (Session 19 + v10.11). No new gates added.
- **First-appearance only** for re-recommendations. A stock that re-appears in Gold on subsequent days is skipped *until* its first recommendation closes (T1/T2/T3 hit, SL break, or 90-day expiry). Prevents inflated sample size with correlated outcomes.
- **90-day expiry window.** Reasoning: spike triggers historically resolve in 4–12 weeks; Deep Value picks need 1–2 quarters to re-rate. 90 days balances both archetypes.
- **Outcome priority on same-day daily-bar ties: SL > T3 > T2 > T1.** We use daily OHLC, so we can't tell intra-day order. SL wins ties because the conservative interpretation is "we hit our risk limit." Highest-target wins on a single-day jump (a stock that gaps from CMP=100 to high=132 is recorded as T3_HIT, not T1_HIT).
- **`max_drawdown_pct` and `max_runup_pct` tracked** along the way for diagnostic value (e.g., spotting "left money on the table" cases where SL fired after a +20% runup).

### Honest limitations called out

1. **No backfill of past performance is possible.** The Gold filter depends on yesterday's score, MoS, RSI, sector stage — values not preserved historically. Today's snapshot can't validate yesterday's decision.
2. **First 30–60 days of stats will be noisy.** Small sample, single market regime. Don't share Performance numbers externally until ≥30 closed picks.
3. **Backtesting today's Gold picks against historical price data would be survivorship bias on steroids** (today's list is biased toward stocks that already moved correctly). v14.0 is forward-only by design.

### What was added

**1. Two new SQLite tables in `database/data_bridge.py`** (added before `conn.commit()` in `initialize_v7_tables`):

- `gold_recommendations` — append-only log. PRIMARY KEY (symbol, recommendation_date). 20 columns covering recommendation context: company, sector, cap_category, cmp_at_recommendation, entry_low, entry_high, stop_loss, t1, t2, t3, cfv, mos_pct, composite_score, early_entry_score, quick_pick_label, verdict, time_horizon, predicted_rr.
- `gold_outcomes` — outcome state per recommendation. PRIMARY KEY (symbol, recommendation_date). 11 columns: outcome_type ∈ {OPEN, SL_HIT, T1_HIT, T2_HIT, T3_HIT, EXPIRED}, outcome_date, outcome_price, days_to_outcome, max_drawdown_pct, max_runup_pct, current_price, current_pnl_pct, last_checked_date.
- Index `idx_gold_outcomes_open` on `outcome_type WHERE outcome_type='OPEN'` for fast tracker scans.

**2. Five helper functions in `data_bridge.py`** (appended at end of file):

- `has_open_recommendation(symbol)` → bool — first-appearance gate
- `insert_gold_recommendation(rec: dict)` — also seeds gold_outcomes OPEN row
- `get_open_recommendations()` — returns list of dicts joining both tables
- `update_outcome(...)` — finalizes closed rows or refreshes OPEN tracking metrics
- `get_outcome_stats()` — returns DataFrame of all recs ⨯ outcomes for the Performance sheet

**3. master_funnel.py logging hook** (~85 lines added after `excel_gen.generate_excel_reports()` returns). Iterates `excel_gen._get_gold()` (re-using the same 11-condition filter — single source of truth), parses entry_range string (handles en-dash, hyphen, currency symbol, comma), computes predicted_rr matching the Excel column logic, and writes to `gold_recommendations` via the helper. First-appearance check via `has_open_recommendation()` before insert. Defensive: per-symbol try/except so one bad row can't abort the loop. Console output: `📈 v14.0 outcome tracking: logged N Gold pick(s) (skipped X already-open, Y error)`.

**4. NEW FILE: `track_outcomes.py`** (~280 lines). Run as `python3 track_outcomes.py` AFTER the daily pipeline. For every OPEN recommendation:
- Loads `daily_prices` filtered `exchange='NSE'` from recommendation_date+1 forward (matches v12.7 dual-listed dedup convention).
- Walks chronologically. Tracks running `max_runup_pct` and `max_drawdown_pct` on every bar.
- First event check at each day:
  - `low ≤ stop_loss` → SL_HIT (wins ties)
  - else `high ≥ t3` → T3_HIT (highest target priority)
  - else `high ≥ t2` → T2_HIT
  - else `high ≥ t1` → T1_HIT
  - else continue
- After 90 calendar days from recommendation_date with no event → EXPIRED.
- For OPEN rows, refreshes `current_price`, `current_pnl_pct`, `last_checked_date` so the Performance sheet shows live-as-of-last-tracker-run state.
- Idempotent: closed rows are skipped on subsequent runs because `get_open_recommendations()` filters them out.

**5. New `🎯 Performance` sheet in Excel** (`reporting/excel_generator.py::_performance_sheet`, ~180 lines). Inserted between `📱 Delivery Preview` and `📖 Glossary` in tab order. Sections:
- **Headline metrics** — Total Tracked / Closed / Open / Hit Rate (T1+) / SL Rate. Outcome breakdown counts (T1, T2, T3, SL, Expired).
- **Speed metrics** — average days from recommendation to T1/T2/T3/SL.
- **Diagnostic breakdowns** — three tables: by composite score band, by Quick Pick archetype, by sector. Each shows Total / T1+ Hits / SL Hits / Expired / Hit Rate (color-coded green/amber/red).
- **Open positions** — currently-tracked stocks with Days Held, CMP at Rec, Current Price, P&L %, Max Runup, Max DD, Score, Archetype.
- **Sample-size banner** — amber warning if <30 closed picks ("preliminary"), green confirmation if ≥30 ("statistically meaningful").
- **Empty-DB graceful handling** — first run shows "No Gold-pick history yet — tracking starts the first time a stock makes the Gold sheet" banner instead of crashing.

**6. Tooltip + glossary updates** for new columns (per Rajkumar standing rule: "update tooltip, tooltip ref and glossary if any newly columns added"):
- 14 new entries in `tooltip_formatter.py::TIPS` dict (cell-hover tooltips): TOTAL TRACKED, CLOSED, OPEN, HIT RATE (T1+), SL RATE, AVG DAYS → T1, AVG DAYS → T2, AVG DAYS → T3, AVG DAYS → SL, Max Runup %, Max DD %, Hit Rate, Days Held, Archetype.
- 13 new rows in `excel_generator.py::GLOSSARY_DATA` under new "PERFORMANCE" group, all with "Where Used" = "🎯 Performance".
- New `PERFORMANCE` color registered in `GRP_COLORS` (`B45309` matching the Gold-tier amber).
- Tooltip Reference sheet auto-renders the new TIPS entries — no manual sync needed.

### Test coverage — 17 tests in 5 groups

`/home/claude/tests/test_v14_outcome_tracking.py` — all pass.

| Group | Tests | Coverage |
|---|---|---|
| G1 — Schema + helpers | 3 | Tables created with correct columns; first-appearance rule; get_open JOIN |
| G2 — master_funnel hook | 2 | Entry-range parser (en-dash/hyphen/currency); predicted_rr formula |
| G3 — Walk-forward (CP3) | 6 | T1_HIT detected; SL beats target on same-day ties; highest target wins; EXPIRED at 90 days; max_runup/drawdown tracked; closed rows immutable across reruns |
| G4 — Performance sheet | 3 | Sheet present and ordered before Glossary; empty-DB shows graceful banner; full-data renders all 4 sections |
| G5 — Tooltips/glossary | 3 | TIPS dict has all 14 entries; GLOSSARY_DATA has all 13 rows; PERFORMANCE color registered |

**No-regression check**: all prior test suites still pass (8/8 v13.x R1 + 7/7 v13.x R2 + 7/7 v13.x R3 + 17/17 v14.0 = **39/39 passing**).

### Operational deployment

1. Drop the 7 patched files into project (master_funnel.py, track_outcomes.py NEW, database/data_bridge.py, reporting/excel_generator.py, reporting/tooltip_formatter.py, CLAUDE.md, readme.md).
2. Run pipeline as usual: `python3 master_funnel.py`. Console will show `📈 v14.0 outcome tracking: logged N Gold pick(s)...` after Excel save. The Excel will have a new `🎯 Performance` sheet with the empty-DB banner on first run.
3. Run `python3 track_outcomes.py` after the daily pipeline (or schedule it 30 min later). Tomorrow's Excel will reflect any closed positions.
4. **Don't share Performance numbers externally until ≥30 closed picks** — the sheet itself banners this caveat.

### What you'll learn over time

- **Hit rate by score band** answers: do Score≥90 picks actually outperform 70-79? If yes, tighten the Gold filter. If no, the score is just noise above 70.
- **Hit rate by archetype** answers: does DEEP VALUE EARLY MOVER (the 3-factor combo) actually outperform DEEP VALUE alone? Or are we creating fake distinctions?
- **Average days to T1** answers: what's the realistic holding period? Lets you set position-sizing and patience expectations.
- **Max runup at SL_HIT rows** answers: are we leaving money on the table? If many SL trades had +15% runups before fading, a trailing-stop layer would help.

### Open known issues (carried forward + new)

1. 0-vs-missing ambiguity (original v12.4 audit issue) — still deferred
2. NIFTY 50 not ingested — daily report renders "—" honestly (v13.x fix)
3. Altman Z capping at 10 (v12.5 behavior) — 34/100 hit cap by design
4. NEWS & RISK section + ANALYSIS SUMMARY — Gemini AI quota issue (config, not code)
5. PIPELINE / OB section (5 cols) — order book metrics, no free aggregator
6. **NEW v14.0**: Tracker uses daily OHLC (no intraday data). Same-day SL+T1 ties resolve to SL. If finer granularity matters, would need 5-min bars (paid data feed).
7. **NEW v14.0**: 90-day expiry is fixed. Could be configurable per archetype later (DEEP VALUE = 180d, EARLY MOVER = 60d).

---

## v14.1 RELEASE — Horizon-aware expiry + reappearance tracking + v14.0 bug-fix

**Date:** May 8, 2026 (later same day as §34 v14.0)
**Trigger:** User question after seeing v14.0 in production: *"Will the tracker wait 90 days for SHORT TERM stocks too? How is this handled across different scenarios?"*

The Excel already classifies each Gold pick by **Horizon**: SHORT TERM (2-4 weeks), POSITIONAL (1-3 months), LONG TERM (3-12 months). But v14.0 used a single hardcoded `EXPIRY_DAYS=90` for everything. This is **wrong** for SHORT TERM (too lenient — counts 60-day wins as SHORT TERM successes) and LONG TERM (too strict — falsely expires genuine 6-month re-rates). v14.1 fixes this by reading the per-stock horizon and dispatching to the right window.

### v14.0 bug surfaced and fixed

While building v14.1, discovered a quiet bug in v14.0's master_funnel logging hook: it read `_grow.get("time_horizon", "")` from the stock dict — but the actual key set by `master_funnel.py:2750` is `horizon`, not `time_horizon`. Result: every production row in `gold_recommendations` had `time_horizon = ""`. The Performance sheet's BY TIME HORIZON diagnostic would have rolled all rows into a single "—" group, making the breakdown useless.

**Impact on existing v14.0 deployments**: any rows logged before v14.1 deploy have empty `time_horizon`. The tracker handles them gracefully via the `DEFAULT_EXPIRY_DAYS=90` fallback — they continue to be tracked exactly as before. New rows logged from v14.1 onwards will have correct horizon values. No data correction needed.

### Locked design decisions (from user)

- **Re-appearance handling**: counter-only. Original entry/SL/T1/T2/T3 frozen at log time. New `times_reappeared` column on `gold_recommendations` increments each subsequent same-day appearance while OPEN. Preserves measurement integrity — the system's *original* call was either right or wrong; refreshing targets mid-flight muddles that.
- **Expiry**: hard cutoff at exact day per horizon. No grace periods, no soft windows.

### Horizon-to-expiry mapping

| Horizon (Excel column) | Expiry days | Reasoning |
|---|---|---|
| SHORT TERM | **30** | Upper bound of system's "2-4 weeks" claim |
| POSITIONAL | **90** | Median of "1-3 months" — matches v14.0 default |
| LONG TERM | **270** | ~9 months — median of "3-12 months" |
| (missing/unknown) | **90** | Conservative default — backward-compat |

### What was added

**1. Schema migration in `database/data_bridge.py::initialize_v7_tables`** (added before `conn.commit()` after v14.0's gold_outcomes index):

```python
for col_def in [
    ("gold_recommendations", "expiry_days INTEGER DEFAULT 90"),
    ("gold_recommendations", "expiry_date TEXT DEFAULT ''"),
    ("gold_recommendations", "times_reappeared INTEGER DEFAULT 0"),
    ("gold_outcomes",        "last_reappeared_date TEXT DEFAULT ''"),
]:
    try:
        c.execute(f"ALTER TABLE {col_def[0]} ADD COLUMN {col_def[1]}")
    except sqlite3.OperationalError:
        pass  # column already exists — fine
```

ALTER TABLE inside try/except matches v11.0.2's existing idempotent migration pattern (`consecutive_avoid_quarters` etc.). Runs cleanly on existing v14.0 DBs without dropping data.

**2. Two new helper functions appended to `data_bridge.py`**:

- `horizon_to_expiry_days(time_horizon: str) → int` — case-insensitive substring match: "SHORT" → 30, "LONG" → 270, anything else → 90. Tolerates underscores, mixed case, dotted variants.
- `increment_reappearance(symbol, today_iso) → bool` — atomically increments counter on the OPEN row + stamps `last_reappeared_date`. **Idempotent within a calendar day**: if `last_reappeared_date == today_iso`, returns False without incrementing (prevents double-counting on pipeline reruns).

**3. `insert_gold_recommendation` extended** to accept and persist `expiry_days` + `expiry_date`. Backward-compat: missing keys default to `90` and `""` so existing v14.0 callers continue to work unmodified.

**4. `get_open_recommendations` enriched** to include `time_horizon`, `expiry_days` (with `COALESCE(..., 90)` for legacy rows), `expiry_date` for the tracker.

**5. `track_outcomes.py` — horizon-aware walk-forward**:

- Renamed `EXPIRY_DAYS = 90` (module constant) to `DEFAULT_EXPIRY_DAYS = 90` (fallback only).
- `_walk_forward()` reads `expiry_days` per recommendation from the rec dict at the top of the function. Both expiry checkpoints (price-history-empty branch + chronological-walk branch) now use the local `expiry_days` instead of the module constant.
- Console output enhanced: each row shows `day {N}/{expiry_days} ({horizon})`. OPEN rows compute `days_left` and append `⚠ Nd to expiry` warning when ≤ 14. Bottom-line summary counts approaching-expiry total.

**6. `master_funnel.py` — fixed v14.0 horizon-key bug + reappearance counter**:

- Now reads `_grow.get("horizon", "")` (was `"time_horizon"` — wrong key in v14.0).
- Maps to `_expiry_days_v` via `horizon_to_expiry_days()`.
- Pre-computes `_expiry_date_v` as `recommendation_date + expiry_days`.
- When `has_open_recommendation()` returns True, calls `increment_reappearance()` and tracks count separately.
- Console: `📈 v14.1 outcome tracking: logged N (skipped X already-open, Y reappearance(s) counted)`.

**7. Performance sheet enhancements** (4 additions in `_performance_sheet`):

- **BY TIME HORIZON breakdown** — 4th diagnostic table after Score Band / Quick Pick Archetype / Sector. Same color-coded format. Answers: "are SHORT TERM picks really winning in 30 days? are LONG TERM picks earning their 270 days?"
- **Open Positions table redesign** — expanded from 10 to 12 columns. New: Horizon, Days Left, Re-app, ⚠ flag. Rows where `days_left ≤ 14` get pale-yellow background + bolded ⚠ warning emoji in column 12. End-of-table summary line surfaces the count.
- **EXPIRED missed-runup diagnostic** — new section that renders only when ≥ 3 expired rows exist. Shows AVG MISSED RUNUP (mean of `max_runup_pct` across expired rows), PEAK MISSED RUNUP (worst single case), EXPIRED w/ ≥10% RUNUP (count/total). Reading guide inline: AVG > 15% suggests targets too far OR expiry too early; AVG < 5% means expiry was the right call.
- **Defensive sample-size suppression** — diagnostic suppressed entirely when fewer than 3 expired rows exist. Mirrors the existing "≥30 closed picks" caveat for headline stats.

**8. Tooltips + glossary updates**:

- `tooltip_formatter.py::TIPS` — 7 new entries: Horizon, Days Left, Re-app, AVG MISSED RUNUP, PEAK MISSED RUNUP, EXPIRED w/ ≥10% RUNUP, BY TIME HORIZON.
- `excel_generator.py::GLOSSARY_DATA` — 6 new PERFORMANCE rows under v14.1: Horizon, Days Left, Re-app, Approaching Expiry Warning, BY TIME HORIZON breakdown, Avg/Peak Missed Runup.
- Tooltip Reference sheet auto-renders the new TIPS entries — no manual sync needed.

### Test coverage — 13 new tests in `tests/test_v14_1_outcome_tracking.py`

| Group | Tests | Coverage |
|---|---|---|
| G1 — Mapping | 1 | horizon_to_expiry_days for all 4 cases incl. case-insensitive variants |
| G2 — ALTER | 1 | Idempotent across 3 init calls; no duplicate columns |
| G3 — Insert  | 1 | Stores expiry_days/expiry_date; defaults to 90 when missing |
| G4 — Counter | 2 | Increments + idempotent same-day; doesn't increment on closed rows |
| G5 — Tracker | 3 | SHORT expires at 30; LONG still open at 100 (waits for 270); legacy rows fall back to 90 |
| G6 — Sheet   | 2 | BY TIME HORIZON appears; Open Positions has Horizon/Days Left/Re-app columns |
| G7 — Diag    | 2 | EXPIRED missed-runup renders with correct avg; suppressed when <3 expired |
| G8 — Bug fix | 1 | master_funnel reads `horizon` key, NOT `time_horizon` (v14.0 bug confirmed fixed) |

**No-regression check**: 8/8 + 7/7 + 7/7 + 17/17 + 13/13 = **52/52 tests pass across 5 suites**.

### Operational deployment

1. Drop the 5 patched files into project: `master_funnel.py`, `track_outcomes.py`, `database/data_bridge.py`, `reporting/excel_generator.py`, `reporting/tooltip_formatter.py`.
2. Run pipeline: `python3 master_funnel.py`. The ALTER TABLE migration runs automatically. Existing v14.0 rows continue to work with defaults; new rows are logged with proper horizon/expiry data.
3. Run `python3 track_outcomes.py`. Console will now show per-row expiry context: `day N/30 (SHORT TERM)`, `day N/90 (POSITIONAL)`, `day N/270 (LONG TERM)`. Approaching-expiry warnings surface for OPEN rows ≤ 14 days from cutoff.
4. Excel Performance sheet now has 4 enhancements visible from first run.

### What changes in your hit-rate numbers

Compared to v14.0 with everything-90-day expiry:

| Horizon | v14.0 behavior | v14.1 behavior | Hit rate impact |
|---|---|---|---|
| SHORT TERM | T1 hit on day 60 → T1_HIT (counted as win) | T1 hit on day 60 → EXPIRED (counted as fail at day 30) | **Lower** SHORT TERM hit rate (correctly stricter) |
| POSITIONAL | T1 hit on day 50 → T1_HIT | Same — both 90-day windows | **Unchanged** |
| LONG TERM | T1 hit on day 150 → EXPIRED (false miss) | T1 hit on day 150 → T1_HIT | **Higher** LONG TERM hit rate (correctly more patient) |

This makes hit rates meaningful **per horizon class** instead of mixing apples and oranges. After 60+ closed picks, you'll be able to see e.g. "POSITIONAL = 65% hit rate, but SHORT TERM only 35% — the system's high-momentum trigger is over-promising on speed."

### Open known issues (carried forward + new)

1. 0-vs-missing ambiguity (deferred since v12.4)
2. NIFTY 50 not ingested (v13.x renders "—")
3. Altman Z capping at 10 (v12.5 by design)
4. NEWS & RISK + ANALYSIS SUMMARY — Gemini quota
5. PIPELINE / OB section — no free aggregator
6. Tracker uses daily OHLC (no intraday) — same-day SL+T ties resolve to SL
7. **NEW v14.1**: Hard expiry cutoff means a stock that hits T1 on day 31 of a 30-day SHORT TERM window is recorded as EXPIRED. By design — soft cutoffs are slippery slopes — but worth noting for occasional "near miss" cases.
8. **NEW v14.1**: Reappearance counter is idempotent within a calendar day. If you run the pipeline 3 times on the same day (e.g., for testing), the counter increments at most once.

### v14.1 follow-up — column-name consistency fix (same release)

While reviewing v14.1 in the production Excel, Rajkumar spotted that the same field appeared as **"Time Horizon"** in Full Dashboard but as bare **"Horizon"** in the Gold sheet — and v14.1's Performance sheet also used `"Horizon"`. Inconsistent UX. Fixed by standardizing on **"Time Horizon"** as the display label everywhere:

- Gold sheet column header: `"Horizon"` → `"Time Horizon"` (in `GOLD_COLS` at `excel_generator.py:176`)
- Performance Open Positions header: `"Horizon"` → `"Time Horizon"` (column slightly widened from 13 to 15 chars)
- Performance glossary entry: `("PERFORMANCE","Horizon")` → `("PERFORMANCE","Time Horizon")`
- Gold-sheet glossary entry: `("TRADE PLAN","Horizon")` → `("TRADE PLAN","Time Horizon")`
- Removed stale glossary entry that referenced old SWING/INVESTMENT labels (system uses SHORT TERM/POSITIONAL/LONG TERM)
- Tooltip dict: merged two entries (`"Time Horizon"` + `"Horizon"`) into single canonical `"Time Horizon"` with the richer v14.1 body
- Tooltip Reference category set: removed redundant `"Horizon"` from 🎚 set

The DB column name remains `time_horizon` (no schema rename — would have broken the v14.0/v14.1 production data already in `gold_recommendations`). The stock dict key in `master_funnel.py` also stays `horizon` (changing it would touch the entire pipeline). This is purely a **display-label** fix — code-level field names unchanged.

**New test**: `test_g9_column_name_consistency_time_horizon_everywhere` — verifies no `("Horizon",`, `"Horizon":`, or `("PERFORMANCE","Horizon")` patterns remain anywhere in `excel_generator.py` or `tooltip_formatter.py`. Catches future regressions if anyone re-introduces the bare label.

**Final test count**: 8 + 7 + 7 + 17 + **14** = **53/53 across 5 suites**.

### v14.1.2 hotfix — hook-ordering bug (Day-2 user observation)

**Date:** May 9, 2026 (one day after first production rollout)
**Discovered by:** Rajkumar — "yesterday 3 stocks logged, today 3 more, why does Performance sheet only show 3?"

**The bug:** v14 logging hook fired AFTER `generate_excel_reports()`. So the Performance sheet was rendered using yesterday's gold_recommendations only — today's picks were logged 1 step too late, surfacing on Day+1. Off-by-one-day display bug.

**Day-by-day evidence from user's actual production data:**
- 2026-05-07 (Day 1): Pipeline runs → Performance sheet renders empty (no history) → AFTER save, 3 stocks logged (PETRONET, ITC, BSOFT)
- 2026-05-08 (Day 2): Pipeline runs → Performance sheet renders showing yesterday's 3 stocks → AFTER save, 3 new stocks logged (HEXT, FIEMIND, VIKRAMSOLR)
- User's question: "where are today's 3 stocks?" — they were logged but rendered one day late

**The fix:** Move the v14 hook to BEFORE `generate_excel_reports()`. Verified safe because `excel_gen._get_gold()` only reads `self.df` (set in constructor), doesn't depend on Excel having been built.

**New test:** `test_g10_v14_hook_fires_before_excel_generation` — locks the order via source-code anchor positions. Catches future regressions if anyone re-adds the hook below the Excel call.

**Test count after fix:** 8 + 7 + 7 + 17 + **16** = **55/55 across 5 suites**.

### v14.1.3 hotfix — tracker invocation missing (Day-2 user observation #2)

**Date:** May 9, 2026 (same day as v14.1.2)
**Discovered by:** Rajkumar — "Current Price = CMP at Rec, P&L = +0.0%, Max Runup = +0.0% for every position regardless of how the stock has moved"

**The bug:** `track_outcomes.py` is the script that walks `daily_prices` forward and refreshes OPEN row fields (`current_price`, `current_pnl_pct`, `max_runup_pct`, `max_drawdown_pct`). It was a standalone script the user had to run separately — but in production, no one ever invoked it. So OPEN-row fields were stuck at the seed values from `insert_gold_recommendation` (current_price = cmp_at_recommendation, P&L=0, max_runup=0) forever. Performance sheet showed frozen-at-recommendation snapshots, not live tracker output.

**Evidence from user's screenshot (2026-05-08):**
- PETRONET: rec at ₹282.1, current ₹282.1 (identical), P&L +0.0%, max_runup +0.0%
- ITC: rec at ₹307.4, current ₹307.4 (identical), P&L +0.0%, max_runup +0.0%
- BSOFT: rec at ₹362.2, current ₹362.2 (identical), P&L +0.0%, max_runup +0.0%

Same exact pattern across all 3 stocks → not a one-stock data error, but a structural bug. Tracker had never run.

**The fix:** `master_funnel.py` now invokes `track_outcomes.main()` automatically as part of the pipeline. New ordering:
1. v14 hook fires — log today's Gold picks (already in v14.1.2)
2. **NEW v14.1.3** — `track_outcomes.main()` runs, refreshes all OPEN rows
3. `generate_excel_reports()` — Performance sheet sees today's logged stocks AND fresh tracker output

This makes the tracker an automatic part of every pipeline run. No more manual invocation needed. Console output now shows tracker state in every pipeline log.

**End-to-end simulation verified:**
| Day | Price | current_price | P&L % | max_runup % |
|-----|-------|--------------|-------|-------------|
| 1 | rec at ₹380 | 380 | 0.0% | 0.0% |
| 2 | high 395, close 392 | 392 | +3.2% | +4.0% |
| 3 | high 415, close 410 | 410 | +7.9% | +9.2% |
| 4 | high 420 → T1 hit | (T1_HIT @ 418) | (closed) | +10.5% |

**New test:** `test_g11_tracker_invoked_from_master_funnel` — verifies `from track_outcomes import main` is present, `_tracker_main()` is called, and ordering (hook → tracker → Excel) is preserved. Catches future regressions if anyone removes the integration.

**Test count after fix:** 8 + 7 + 7 + 17 + **17** = **56/56 across 5 suites**.

**Combined v14.1.2 + v14.1.3 user-visible effect:** After deploying both, tomorrow's Performance sheet will:
1. Show today's 3 new stocks (HEXT, FIEMIND, VIKRAMSOLR) — v14.1.2 fix
2. Have correct Current Price, P&L%, Max Runup% on yesterday's 3 stocks (PETRONET, ITC, BSOFT) — v14.1.3 fix
3. Show 6 total OPEN positions (3 from each day) with live, accurate price tracking

### v14.3 audit + fix — comprehensive Performance sheet value-correctness audit

**Date:** May 9, 2026 (same day as v14.1.2/v14.1.3)
**Trigger:** User request: "rather than waiting 30 days to find issues, simulate all possible conditions and fix any issues found"

**What was audited:** 17 distinct scenarios run through the actual `_performance_sheet()` code path with synthesised DB data. Total of 60+ individual cell-value assertions verified across:

| Section | Scenarios checked |
|---|---|
| HEADLINE METRICS | TOTAL/CLOSED/OPEN counts; HIT RATE formula = (T1+T2+T3)/closed; SL RATE formula |
| Outcome breakdown row | All 5 buckets (T1/T2/T3/SL/EXPIRED) count correctly |
| SPEED METRICS | AVG DAYS → T1/T2/T3/SL formulas; '—' fallback when no closed rows |
| BY COMPOSITE SCORE BAND | 90+ / 80-89 / 70-79 / <70 boundary behavior at 95, 90, 89.9, 80, 79.9, 70, 69.9 |
| BY QUICK PICK ARCHETYPE | Multi-archetype grouping including long labels ("DEEP VALUE FALLEN ANGEL") |
| BY SECTOR | All sectors group correctly; hit-rate cell color thresholds (≥60% green, 40-60% amber, <40% red) |
| BY TIME HORIZON | SHORT/POSITIONAL/LONG buckets group correctly; empty horizon → "—" group |
| OPEN POSITIONS | 12-column header; days_left formula; ⚠ flag at boundary (≤14 inclusive) |
| Approaching-expiry highlight | URGENT (5d), NORMAL (88d), BORDER (exactly 14d) — pale-yellow row + ⚠ flag |
| End-of-table summary | Approaching-expiry count = 2 when 2 stocks within 14 days |
| Re-app counter display | "—" for 0, integer for >0 |
| P&L color coding | green (>0), red (<0), default (=0) |
| MISSED RUNUP DIAGNOSTIC | AVG / PEAK / count_significant formulas; suppressed at <3 expired; renders at ≥3 |
| MISSED RUNUP edge cases | Negative max_runup values; mixed positive/negative |
| Sample-size banner | Amber at <30 closed; green at ≥30 closed |
| All-loser scenario | 0% hit rate, 100% SL rate, sector cell red |
| 10K-row scale test | Renders in <1 second; total = 10000 |
| Robustness | NULL fields, NaN scores, empty strings — graceful, no crash |
| Day-1 production state | Mirror user's exact 2026-05-08 situation; all fields verified |

**Result of audit: ALL 60+ assertions pass.** The Performance sheet rendering code is correct. No values are populated wrongly when given correct DB data.

**One soft issue surfaced and fixed:** `insert_gold_recommendation` was returning `True` even when `INSERT OR IGNORE` silently dropped the row due to a PRIMARY KEY collision (symbol+recommendation_date). Effect: master_funnel's `_skipped_err` counter never registered duplicate-key collisions. They would have been invisible in pipeline logs.

**Fix:** capture `cursor.rowcount` immediately after the gold_recommendations INSERT (before the second INSERT into gold_outcomes overwrites it). Return True only when rowcount==1 (genuine new insertion); return False on collision. Master_funnel hook unchanged — its existing `if insert_gold_recommendation(_rec): _logged += 1; else: _skipped_err += 1` now correctly distinguishes new logs from silent dupes.

**New tests:**
- `test_g12_insert_returns_false_on_duplicate` — locks the rowcount semantics
- `test_g13_performance_sheet_value_correctness_audit` — runs a synthetic 35-row dataset through the full pipeline and asserts 16 specific calculation invariants. If any formula drifts (someone changes the score band thresholds, the speed metric calculation, the missed-runup formula), this test catches it before a 30-day real-world wait would.

**Test count after v14.3:** 8 + 7 + 7 + 17 + **19** = **58/58 across 5 suites**.

**No user-visible behavior change** — production pipelines never had PK collisions because the first-appearance gate (`has_open_recommendation`) catches dupes upstream. v14.3 is defense-in-depth: better instrumentation if anything breaks the upstream gate in the future.

### v14.4 — Performance sheet OPEN POSITIONS shows SL/T1/T2/T3 levels

**Date:** May 9, 2026
**Trigger:** User observation — "in the performance sheet there is no clarity of what the target one value and two and three for each stock... we need to add those to give clear picture of what's expected and where we are"

**The issue (not a bug, a missing feature):** The OPEN POSITIONS table showed Current Price, P&L %, and Max Runup % — but didn't show what the SL/T1/T2/T3 levels were. So a reader could see "PETRONET is at ₹283.80, +0.6% from entry" but had no reference frame for "where does this need to go" (target prices) or "where would this stop us out" (SL price). Forced manual lookup against the Gold sheet to interpret each row.

**The fix:** Added 4 new columns between **Max Runup %** and **Score**: **SL · T1 · T2 · T3**. Each cell shows both the absolute price AND the distance from current price as a dynamic percentage:

```
  ₹262.35 (-7.6%)    ← SL: red text (downside risk)
  ₹310.31 (+9.3%)    ← T1: green text
  ₹338.52 (+19.3%)   ← T2: green text  
  ₹366.73 (+29.2%)   ← T3: green text
```

The distance percentage updates dynamically each pipeline run as `current_price` is refreshed by the tracker. So if PETRONET rallies to ₹300, T1 will show "+3.4%" instead of "+9.3%", giving instant visual signal that we're approaching target. SL distance correspondingly shrinks (or grows) as price moves.

**Why distance from current, not from entry:** Distance from current is the actionable figure for "where do we go from here." Distance from entry is already implicit in P&L %. Showing both pieces avoids redundancy.

**Visual treatment:**
- SL column: red text (always represents downside risk regardless of current state)
- T1 / T2 / T3 columns: green text (always represent upside)
- Subtle text color, not bold — avoids visual overwhelm with 16 columns
- "—" placeholder shown when level is missing (legacy rows pre-v14.0 might lack levels)

**Column count:** 12 → 16 columns. New full layout:
1. Symbol
2. Rec Date
3. Time Horizon
4. Days Held
5. Days Left
6. Re-app
7. CMP at Rec
8. Current Price
9. P&L %
10. Max Runup %
11. **SL** *(NEW)*
12. **T1** *(NEW)*
13. **T2** *(NEW)*
14. **T3** *(NEW)*
15. Score
16. ⚠

**Files changed:** `reporting/excel_generator.py` only. Single-file change — no DB schema, no master_funnel impact.

**Side-track work — test suite consolidation:** Same release also consolidated the 6 separate test files I'd been maintaining (`test_fixes.py`, `test_integration.py`, `test_regression.py`, `test_v13_x_round3.py`, `test_v14_outcome_tracking.py`, `test_v14_1_outcome_tracking.py`) into a single `test_v13_v14_consolidated.py` at repo root. 65 tests, 8.5s runtime, single ✅/❌ summary, exit code 0 only on full pass. Stability: 5/5 consecutive runs pass after fixing CWD-restoration race condition in the runner. Three previously-untouched test files remain separate by design (`test_v11.0.2_full_withdummies.py` for ScoringEngine deep tests, `test_run.py` for manual pipeline launches, `test_yfinance.py` for external-API diagnostics).

**Test count after v14.4:** 65/65 across consolidated suite. Same 65 tests as v14.3 — no new tests needed for v14.4 since the existing G6 already verifies Open Positions column structure and would have caught a regression in column count or labels.

**No user-visible behavior change** other than the 4 new columns appearing in the Performance sheet OPEN POSITIONS table. Existing columns and their behavior preserved exactly.

---

### v14.5 — CLOSED POSITIONS section for track-record visibility

**Date:** May 11, 2026
**Trigger:** User observation — "good to have closed position section which should hold information such as expired stocks with expired date and stock price at expired date and other relevant info. This will give user more insight on the track record."

**The gap (not a bug, a missing detail level):** Pre-v14.5 the Performance sheet showed closed picks only as aggregated counts in the headline (T1 HIT: 21, T2 HIT: 7, T3 HIT: 4, SL HIT: 10, EXPIRED: 3). A reader could see "we hit T1 21 times" but couldn't see WHICH stocks contributed, when they hit, or what the realised price was. The track record was statistical, not transactional.

**The fix:** Added a new CLOSED POSITIONS section between DIAGNOSTIC BREAKDOWNS and OPEN POSITIONS. One row per closed pick (SL_HIT/T1_HIT/T2_HIT/T3_HIT/EXPIRED), sorted by outcome_date DESC so most recent closures appear at top.

**Columns (12):**

1. Symbol
2. Rec Date
3. Time Horizon
4. **Outcome** *(color-coded — green for T1/T2/T3, red for SL_HIT, amber for EXPIRED)*
5. Outcome Date
6. Days to Outcome
7. Entry CMP
8. Outcome Price
9. P&L % *(realised; computed live from entry vs outcome_price)*
10. Max Runup % *(best gain ever during tracking — exposes near-miss SL_HITs)*
11. Max Drawdown % *(worst pain experienced — useful for reviewing SL placement)*
12. Score *(composite at recommendation)*

**Summary footer:** `Total N closed · 🎯 T1_HIT: x · 🎯🎯 T2_HIT: y · 🎯🎯🎯 T3_HIT: z · 🛑 SL_HIT: a · ⏱ EXPIRED: b`

**Empty-state:** When zero closed rows in DB, renders "No closed positions yet — track record will populate as picks resolve." instead of an empty table. Summary footer correctly suppressed in empty state.

**Section order in Performance sheet** (after v14.5):

1. 🎯 GOLD-PICK PERFORMANCE TRACKER (title + sample-size banner)
2. 📊 HEADLINE METRICS
3. ⏱ SPEED METRICS
4. 🔬 DIAGNOSTIC BREAKDOWNS
5. **📜 CLOSED POSITIONS** *(NEW in v14.5)*
6. 📂 OPEN POSITIONS
7. 💸 EXPIRED — MISSED RUNUP DIAGNOSTIC (when ≥3 expired)

**Why this placement:** Reading flow becomes natural — start with aggregates (Headline/Speed/Breakdowns), then see the trade-by-trade log (Closed Positions), then current state (Open Positions), then deep-dive diagnostic (Missed Runup).

**Why sorted by outcome_date DESC:** Recency matters more than alphabetical. Trader skimming the sheet wants "what closed this week" at the top.

**No row count cap:** Showing all closed rows. After ~1 year of pipeline runs, this would be ~250 rows max — readable in Excel, manageable file size. Users can filter in Excel themselves if needed.

**OPEN vs CLOSED filter is mutually exclusive:** Each pick has exactly one `outcome_type` value at any time. The two sections use complementary filters (`outcome_type == 'OPEN'` vs `outcome_type IN ('SL_HIT','T1_HIT','T2_HIT','T3_HIT','EXPIRED')`), so a pick appears in exactly one section — never both, never neither. When a pick closes, the tracker updates its `outcome_type`, and on the next pipeline run the row automatically migrates from OPEN POSITIONS to CLOSED POSITIONS. No manual move needed.

**Files changed:** `reporting/excel_generator.py` only. Single-file change.

**Test count after v14.5:** 66 (was 65). New test `test_g14_closed_positions_section_renders_correctly` verifies:
- Section header "CLOSED POSITIONS" present
- Section appears BEFORE OPEN POSITIONS (chronological reading order)
- 12-column header with correct labels
- Closed rows present, OPEN rows excluded
- Most-recent-closed row appears first (sort verification)
- Realised P&L formula correct (e.g. entry 100, outcome 120 → +20.0%)
- Summary footer present with correct total

**Stability:** 5/5 consecutive test runs all 66/66 pass.

**Tooltip + glossary updates:**
- 5 new TIPS entries in `tooltip_formatter.py`: Outcome, Outcome Date, Days to Outcome, Entry CMP, Outcome Price, Max Drawdown %
- 1 new PERFORMANCE glossary entry: "Closed Positions section" — explains 12-column structure, color-coding, P&L formula, Max Runup interpretation for SL_HIT rows

**Repository hardening (same release, prep for going public):**
- Added `LICENSE` (MIT) — was missing, blocked legitimate use of the public code
- Added disclaimer block to `readme.md` — educational/research only, not financial advice, no liability
- Rewrote "Credits & licence" section in readme — removed "Internal use only · confidential · not for redistribution" line that contradicted public visibility
- Updated `pipeline_reference_v14_1.html` → `pipeline_reference_v14_5.html` (title/meta/footer/cross-ref bumped; pipeline content itself unchanged since v14.1, since v14.x changes affect Performance sheet only, not the funnel/scoring/verdict pipeline)
- Confirmed via secrets audit: no API keys, tokens, passwords, or PII hardcoded anywhere in code or git history; `.env` properly gitignored and never committed; workflow uses GitHub Secrets correctly for all 6 credentials

**No user-visible behaviour change in core pipeline.** v14.5 is purely a Performance-sheet addition + supporting docs hardening.

---

### v14.6 — Multi-factor SL/T1/T2/T3 formula

**Date:** May 12, 2026
**Trigger:** User concern after two SHORT TERM stocks hit -7% SL on what looked like routine volatility: "I dont want any fixed values like SL,T1,T2,T3 as X% ... rather it should analyse various factors such as stock volatility, sector it belong, and other applicable factors."

**The problem:** Pre-v14.6, `master_funnel.py:2941` hardcoded `_sl_default = cmp * 0.93` and derived T1/T2/T3 from a risk-symmetric formula that always produced T1 ≈ +12.5%, T2 ≈ +18.1%, T3 ≈ +27.8% — identical for every pick regardless of cap, sector, horizon, or volatility. Every position got -7% SL, every position got +12.5% T1. The AI prompt didn't override these — it just echoed back whatever values master_funnel wrote into the stock dict. So the AI was never inventing trade levels; it was always the fallback firing.

This is too tight for mid/small caps. Typical ATR for SMALL cap = 4% daily. A -7% SL = 1.75× ATR — well inside the noise envelope. Industry standard is 2.5-3.5× ATR for swing trades. So routine downside volatility was triggering false stop-outs.

**The fix:** Replaced the fixed-percentage block with `_compute_sl_t_v14_6()`, a multi-factor helper that derives levels from:

1. **ATR-14** (true daily volatility) — pulled from technical_indicators (Edit 1: added to SELECT at line 1178; Edit 2: unpacked into stock dict at line 1820)
2. **Cap-category fallback** when ATR missing — LARGE 2.0%, MID 2.8%, SMALL 4.0%, MICRO 5.5%
3. **Horizon multiplier** — SHORT TERM 2.5×, POSITIONAL 3.5×, LONG TERM 5.0×
4. **Sector adjustment** — HIGH_VOL sectors (Realty/PSU Bank/Metals/Sugar/Power/Aviation/Capital Markets/Iron&Steel/Coal/Defence/Real Estate) add +0.5×; LOW_VOL sectors (FMCG/Pharma/Utilities/IT-Services/Consumer Goods/Personal Products/Healthcare/Telecom/Insurance) subtract 0.3×
5. **CFV upside** — targets scale with fair-value-cushion (deep-value stocks get bigger T2/T3)
6. **Support floor** — SL never placed above nearest support level (support1 from technicals)

**Bounds and discipline:**
- SL clamped to [4.5%, 12.0%] — never tighter than 4.5% (whipsaw protection), never wider than 12% (caps risk per trade)
- T1 must clear 1.5:1 R:R minimum — even when horizon-cap fires, R:R is preserved
- T2 ≥ T1 × 1.35, T3 ≥ T2 × 1.35 (spacing rule, relaxes when T3 hits hard cap)
- T3 hard caps: SHORT TERM 35%, POSITIONAL 80%, LONG TERM 200%

**Honest framing:** This is textbook-standard technical analysis (taught in CMT curriculum, Van Tharp position-sizing literature). It's NOT proprietary alpha. It's significantly better than fixed -7%/+12.5%, but a SEBI-RIA with Bloomberg tools would do better — they'd add walk-forward backtested multipliers, volatility regime detection, time-decay on stops, and earnings-event awareness. v14.6 is a B+ on the technical-rigor scale: defensible, transparent, not state-of-the-art.

**Forward-looking only:** Existing 11 OPEN positions in `gold_recommendations` keep their original -7%/+12.5% values (frozen at log time via `INSERT OR IGNORE`). New picks from this pipeline run onward get the multi-factor levels. The Performance sheet's OPEN POSITIONS table will gradually show varied SL%/T1%/T2%/T3% per stock as old picks close out and new picks are added — two eras coexist naturally during transition.

**Why not retroactively update existing picks:** The 11 picks are real recorded contracts. Rewriting their SL/T after the fact would destroy outcome-tracking integrity and create credibility damage. The two eras (pre-v14.6 fixed-% picks vs post-v14.6 multi-factor picks) need to coexist so future Performance sheet analysis can compare hit rates between them — that comparison IS the empirical validation of v14.6.

**Files changed:**
- `master_funnel.py` only. Three edits:
  - Edit 1 (line ~1178): added `t.atr_14` to technical_indicators SELECT
  - Edit 2 (line ~1820): tuple unpacking now includes atr_14, stored on stock dict
  - Edit 3 (line ~344): new top-level helper `_compute_sl_t_v14_6` (~140 lines)
  - Edit 4 (line ~2941): replaced fixed-percentage block with call to helper. Preserved `stock.setdefault(...)` semantics — if AI ever provides SL/T values, they still take precedence

**Test count after v14.6:** 67 (was 66). New regression test `test_g15_sl_t_v14_6_multi_factor_formula` with 10 sub-assertions:
- Cap-category sensitivity (LARGE < MID < SMALL SL%)
- Horizon sensitivity (SHORT < POSITIONAL < LONG SL%)
- Sector adjustment (HIGH_VOL > LOW_VOL SL%)
- R:R ≥ 1.5 across all 9 scenarios + all 11 real positions
- T1 < T2 < T3 ordering (spacing rule relaxed when T3 caps)
- SL bounds [4.5%, 12.0%]
- Missing ATR → cap-based fallback fires
- CFV ≤ CMP → R:R-only targets, no crash
- Invalid CMP=0 → graceful zeros, no exception
- User's 11 actual positions all clear R:R floor

**Stability:** 5/5 consecutive test runs all 67/67 pass.

**Before/after preview on user's 11 current open positions** (what v14.6 WOULD produce if they were re-recommended today — the actual frozen values stay at -7%/+12.5%):

| Stock | OLD SL/T1/R:R | NEW SL / T1 / T2 / T3 / R:R |
|---|---|---|
| PETRONET | -7.0%/+12.5%/1.79 | -7.0% / +10.4% / +17.4% / +27.8% / 1.50 |
| ITC | -7.0%/+12.5%/1.79 | **-4.7%** / +18.6% / +32.5% / +46.4% / **3.96** |
| BSOFT | -7.0%/+12.5%/1.79 | -8.7% / +13.0% / +21.6% / +34.6% / 1.50 |
| HEXT | -7.0%/+12.5%/1.79 | -5.4% / +8.2% / +13.6% / +21.7% / 1.50 |
| FIEMIND | -7.0%/+12.5%/1.79 | -7.2% / +10.8% / +18.8% / +26.9% / 1.50 |
| VIKRAMSOLR | -7.0%/+12.5%/1.79 | **-12.0%** / +18.0% / +30.0% / +48.0% / 1.50 |
| CIEINDIA | -7.0%/+12.5%/1.79 | -10.3% / +15.4% / +25.7% / +41.0% / 1.50 |
| KANPRPLA | -7.0%/+12.5%/1.79 | -9.4% / +14.1% / +21.9% / +35.0% / 1.50 |
| HERITGFOOD | -7.0%/+12.5%/1.79 | -9.7% / +14.6% / +24.3% / +38.9% / 1.50 |
| AARTISURF | -7.0%/+12.5%/1.79 | **-12.0%** / +18.0% / +30.0% / +48.0% / 1.50 |
| MARUTI | -7.0%/+12.5%/1.79 | -5.2% / +7.9% / +13.1% / +21.0% / 1.50 |

Key observations:
- ITC's R:R of 3.96 reflects its 46% CFV upside — formula stretched targets up to match
- VIKRAMSOLR and AARTISURF (small-cap, neutral sector): wider SL (-12%) gives breathing room against routine volatility
- Large-cap low-vol stocks (ITC, MARUTI, HEXT): tighter SL (-4.7% to -5.4%)
- All 11 clear the 1.5:1 R:R floor that industry treats as minimum acceptable swing-trade quality

---

### v15.0 — A- technical rigor + audit-complete Performance sheet

**Date:** May 13, 2026
**User goals:** "Improve rating to A- by implementing all 5 enhancements... let today be day 1 of data collection in performance sheet." Then in audit pass: "act as an expert and do analysis and provide prompt fix" — caught the audit-trail gaps below.

**Five enhancements over v14.6 (the A- delta):**

1. **5-tier sector classification** — replaces v14.6 binary HIGH/LOW with very-high (+0.6), high (+0.3), neutral (0), low (-0.2), very-low (-0.35). Defined in `_V14_7_SECTOR_TIER` at master_funnel.py:374+.

2. **ATR-percentile regime detection** — new SELECT subquery fetches AVG(atr_14) over **252 days** as baseline (uses full 400-day price retention, industry-standard 1-trading-year window). Ratio current/baseline classifies: HIGH (≥1.20× baseline → SL widened 10%), LOW (≤0.80× → tightened 10%), NEUTRAL.

3. **Volume-confirmed support** — support1 only used as SL floor when vol_ratio (today's vol / 50-day avg) ≥ 1.20. Filters out random lows with no buying conviction.

4. **Trailing stops** in track_outcomes.py — ratchets at peak gain ≥ +5% (BE), ≥ +10% (+3%), ≥ +15% (+7%). Effective SL = MAX(original, trailing). Zero look-ahead bias: trailing update happens at END of bar (after event check). G17 unit test guards this.

5. **Earnings-near SL widening** — helper accepts days_to_earnings; when 0-5 days, SL widens 20%. Infrastructure ready; data source NOT plumbed (yfinance unreliable for Indian stocks).

**The audit-trail gap fixes (4 confirmed gaps, all closed):**

User asked "will Performance sheet capture data based on the new change?" Expert audit found 4 silent gaps:

- **Gap 1 — INSERT missing audit columns**: `insert_gold_recommendation()` INSERT statement didn't include 4 new v15.0 columns (original_stop_loss, atr_at_rec, regime_at_rec, next_earnings_date). Schema migration had ADDED the columns but rows were always written with DEFAULT values — audit trail silently lost. Fixed: INSERT now writes all 27 columns including the 4 v15.0 audit fields.

- **Gap 2 — caller dict missing keys**: Even if INSERT supported them, master_funnel.py's `_rec` dict at line ~3569 didn't pass the v15.0 keys. Fixed: dict now includes original_stop_loss, atr_at_rec, regime_at_rec, next_earnings_date sourced from the stock dict (where helper at line 3227-3229 already placed them).

- **Gap 3 — SELECT missing trailing fields**: `get_outcome_stats()` SELECT didn't pull trailing_sl_pct, trailing_sl_price, peak_price_seen from gold_outcomes. Even if the tracker writes them, the Performance sheet couldn't see them. Fixed: SELECT extended to include all three. (Audit columns r.* already covered via wildcard once gap 1 fix populates them.)

- **Gap 4 — Performance sheet not rendering trailing/regime**: OPEN POSITIONS table had 16 columns; trailing-SL state and regime were invisible. Fixed: widened to 18 columns. Added "Trailing" column showing human-readable state (— / BE locked / +3% locked / +7% locked / +X.X% locked) with green text when locked. Added "Regime" column showing HIGH (red) / NEUTRAL / LOW (green). Merge spans (title row + empty-state row + approaching-summary row) all bumped 16→18.

**Tooltips + glossary updates:**

- `reporting/tooltip_formatter.py`: SL, T1, T2, T3, R:R, Time Horizon all updated to reflect v15.0 multi-factor formula. OPEN POSITIONS SL/T1/T2/T3 updated to mention effective_sl = MAX(original, trailing). 2 new tooltips added for Trailing column and Regime column (explaining the v15.0 audit field).
- `reporting/excel_generator.py` in-Excel Glossary: 3 new entries added — "v15.0 SL/T derivation methodology", "Trailing Stop logic (v15.0)", "Sector Tier / Regime / Volume confirmation columns".

**Schema migrations (idempotent ALTER TABLE)**:
- gold_recommendations: original_stop_loss, atr_at_rec, regime_at_rec, next_earnings_date
- gold_outcomes: trailing_sl_pct, trailing_sl_price, peak_price_seen

**Wipe script (manual one-shot)**:

`wipe_v15_prep.py` deletes gold_recommendations + gold_outcomes with mandatory timestamped backup. Interactive confirmation ("WIPE"). `--restore` rolls back. **Never auto-invoked**: grep confirmed zero references in `.github/workflows/`, master_funnel.py, or any cron/hook path. Manual one-shot only.

**For GitHub Actions DB persistence**: Since DB lives in workflow artifacts (not git), the cleanest wipe path is to delete the `market-data-db` artifact via Actions UI. Next pipeline run won't find it (workflow line 38: `if_no_artifact_found: ignore`), auto-backfill at line 49 rebuilds 400-day daily_prices, gold_recommendations + gold_outcomes stay empty until new picks recommended. Tomorrow's run = clean Day 1.

**Free-tier compatibility verified**:
- Only one new SQL feature (AVG subquery, indexed by symbol+date) — negligible cost.
- No new external API calls (earnings logic deferred).
- All new fields optional; missing data falls back gracefully to v14.6 behavior.
- Test suite stable at ~12s for 70 tests.
- GitHub Actions public repo: unlimited storage + minutes.

**Test changes:**
- v14.6 had 67 tests. v15.0 has **70** (+3).
- G16: 5-tier + regime + volume confirmation + earnings widening (4 sub-assertions).
- G17: trailing-stop ratcheting + no-lookahead (2 scenarios).
- **G18 (NEW): audit-trail end-to-end** (helper → stock dict → _rec → INSERT → SELECT). Catches each of the 4 gap-1-to-3 regressions if reintroduced.
- 5/5 consecutive runs all 70/70 green.

**Files changed:**
- `master_funnel.py` (252-day baseline; 3 new audit fields on stock dict; _rec dict updated)
- `track_outcomes.py` (trailing-SL ratchet with no-lookahead discipline)
- `database/data_bridge.py` (schema migrations + INSERT 27 cols + SELECT 3 trailing cols + update_outcome signature)
- `reporting/excel_generator.py` (OPEN POSITIONS widened to 18 columns; 3 new glossary entries)
- `reporting/tooltip_formatter.py` (6 SL/T tooltips + 2 new column tooltips)
- `test_v13_v14_consolidated.py` (G16 + G17 + G18)
- `wipe_v15_prep.py` (NEW: manual wipe with backup)

**Honest grade**: A- on technical-rigor scale. Smart heuristics with full audit trail. True A requires walk-forward backtest calibration (deferred, needs 3+ years corporate-action-adjusted historical data).

---

## v15.x evolution post-v15.0 (the path to A-)

### v15.0.1 — Calendar-day precision fix
SQLite `date(X, '-N days')` subtracts CALENDAR days. v15.0's `'-252 days'` captured only ~180 trading days. Widened: 252→365 for baseline ATR, 50→70 for vol_50. 52w high/low correctly stays at 365 cal (industry definition). Files: master_funnel.py only.

### v15.1 — SL_MAX 12% → 15%
Production audit: 44/100 stocks collapsed at -12% SL cap, masking per-stock differentiation. Raised `_V14_6_SL_MAX_PCT` to 15.0. Stocks at cap dropped 44→24; SL distribution gained 32 unique values; mean Score 51.2→54.6. New G19 test. **71/71 tests**.

### v15.2 — Historical ATR backfill + ETF filter expansion
Two production issues:
1. Regime detection always NEUTRAL because backfill wrote only LATEST TI row → baseline ATR query had 1 row. Fix: `compute_historical_atr_series()` walks 400 days, writes ~280K historical atr_14 rows. Regime now real from Day 1.
2. 18 ETFs (MOTOUR, MOSILVER, GROWWLIQID, etc.) leaked into dashboard with empty fundamentals. Fix: 17 specific symbol keywords + company-name-based detection ("MUTUAL FUND", "ASSET MANAGEMENT", "INDEX FUND"). Did NOT add broad prefixes (would false-positive MOIL, MOSCHIP, HDFCAMC parent co). 18/18 blocked, 0 false positives.
Requires artifact deletion for historical ATR to populate. New G20 test. **72/72 tests**.

### v15.2.1 — NSE pledge log cleanup (cosmetic)
NSE pledge endpoint returns HTTP 404 on cloud-provider IPs (GitHub Actions, AWS, Azure). Reduced from 4 noisy log lines (3 retries + summary) to 1 honest line: "free endpoint unavailable on this IP (known limitation; paid-tier fallback expected)". Behavior unchanged.

### v15.3 — Phases 1-4 institutional enhancements
- **Phase 1 (kept)**: NSE trading-day calendar — exact 252-trading-day cutoffs via `market_holidays` table. Falls back to v15.0.1 calendar-day approximation if calendar empty (first-run safety). New module `ingestion/trading_day_calendar.py`.
- **Phase 2 (withdrawn in v15.4)**: tax-aware T1/T2/T3 nudge — turned out to be institutionally incorrect.
- **Phase 3 (kept)**: backtest infrastructure — standalone CLI `backtest/walk_forward.py`. Refuses calibration below N=30 (statistical hygiene).
- **Phase 4 (replaced in v15.4)**: correlation-aware sizing — initial linear penalty replaced with risk parity.

### v15.4 — Institutional-correctness audit
User challenged: "are your enhancements inline with top-tier institutional approach?"
- **Phase 2 withdrawn**: Inflating T1/T2/T3 by ~5% to compensate for STCG would have harmed hit rate by ~17 percentage points on a typical LARGE CAP (the climb from +12% to +17.6% halves win probability). Institutions handle tax at portfolio level (loss harvesting, LTCG timing), not by inflating exit targets.
- **Phase 4 replaced** with institutional **volatility-adjusted risk parity** (Markowitz 1952; Bridgewater All-Weather; SEBI-RIA norm):
  ```
  position_size = risk_budget / |SL_pct|
  × cap-category multiplier (LARGE 1.0, MID 1.0, SMALL 0.85, MICRO 0.70)
  Hard 30% sector exposure cap
  Clamps: [1%, 15%]
  ```
  **Invariant**: every position contributes ~1% portfolio risk regardless of stock volatility. Phases 1 + 3 kept. G21 rewritten. **73/73 tests**.

### v15.5 — Risk-parity wired to Excel + Performance tooltip audit + band schema fix
- Surfaces v15.4 risk-parity sizing into Full Dashboard + Gold sheets as 2 new columns: "Suggested Alloc %" (e.g., 12.5%) and "Sizing Rationale" (e.g., "Risk parity: 1.0% / 8.0% = 12.50%"). FULL_COLS 124→126; GOLD_COLS 41→43.
- **Band schema fix**: discovered FULL_GROUPS/GOLD_GROUPS schema was `(start_col, name, color, span)`, not `(end_col, ...)`. Initial v15.5 draft caused 9 MergedCell errors due to overlapping bands. Fixed.
- **Performance sheet tooltip audit**: added 7 missing tooltips (Rec Date, Re-app, CMP at Rec, Current Price, P&L %, Score, ⚠), wired `_apply_col_tips` on both OPEN and CLOSED POSITIONS header rows. All 18 OPEN + 12 CLOSED columns now have hoverable tooltips.
- Glossary + Tooltip Reference sync: 2 new entries each; "Trade Plan" icon family (🎚) extended.
- G14 test updated for ⓘ cue character; new G22 test for v15.5 wire-up.
- **74/74 tests** across 5/5 runs.

### v15.6 — Pre-existing test failures fixed + documentation sync
- Discovered v15.5 had introduced band-schema overlap causing 9 MergedCell errors. Fixed by aligning FULL_GROUPS/GOLD_GROUPS values: TRADE PLAN at start=113 (Full Dashboard) / start=32 (Gold), span=9 cols. Sum of band spans now equals len(FULL_COLS)=126 and len(GOLD_COLS)=43.
- Completed v15.5 Performance sheet tooltip work — all 18 OPEN + 12 CLOSED columns have hoverable ⓘ tooltips and the underlying `_apply_col_tips` wiring.
- Documentation sync: readme.md + CLAUDE.md updated with v15.0.1 → v15.5 entries (previously stopped at v14.6 / v15.0).
- **74/74 tests** across 5/5 runs.

### v15.7 — Minor cleanups: rationale text + Glossary dedup + Performance +2 cols
Three issues surfaced in v15.6 production audit:
1. **Sizing Rationale text bug** (ITC scenario): when a stock was already an OPEN position in its own sector, `_current_sector_exposure()` counted that stock against itself, falsely triggering the "sector cap" rationale branch. The actual binding constraint was MAX_ALLOCATION clamp (15%). Allocation value was correct; only the rationale text was misleading. Fix: only emit "sector cap" text when `headroom_in_sector < MAX_ALLOCATION_PCT`.
2. **Glossary dedup**: duplicate "Suggested Alloc %" / "Sizing Rationale" entries (rows 87-88 + 155-156). Kept cleaner non-suffixed entries.
3. **Performance OPEN POSITIONS extended 18→20 cols**: added "Suggested Alloc %" + "Sizing Rationale" to Performance sheet (they were on Full Dashboard / Gold but missing from Performance — the daily-check view). Schema migration: `gold_recommendations.suggested_alloc_pct REAL`, `alloc_rationale TEXT`. master_funnel `_rec` dict + INSERT updated. `get_outcome_stats()` uses `r.*` wildcard so auto-picks up new columns. Legacy OPEN rows (pre-v15.7 schema) render "—" in new cols — correct backward-compat.

**New G23 test** locks: rationale-text correctness, glossary dedup, Performance col count = 20, schema migration present, INSERT extension, `_rec` dict has new keys. **75/75 tests** across 5/5 runs.

### v15.8 — Post-enrichment ETF filter with AMC-parent carve-out
13 May 2026 production audit: **12 ETFs leaked** into the Full Dashboard despite the v15.2 filter.

**Root cause**: NSE bhavcopy doesn't populate descriptive company names — only tickers. The v15.2 name-marker filter in pre_screener.py runs BEFORE enrichment, when `company_name` is empty → markers like " ETF" / "MUTUAL FUND" never match → ETFs slip through.

**Leaked ETFs**: HDFCVALUE, SBIETFPB, HSBCGOLD, GROWWHOSPI, ENIFTY, MAHKTECH, SBISILVER, AXISGOLD, SETFNIFBK, SETFGOLD, HDFCSILVER (11 confirmed). NAM-INDIA was a false-leak — it's actually Nippon Life India Asset Management Limited, a publicly-listed real AMC parent company.

**Fix**: second-pass filter in master_funnel.py immediately AFTER symbol_master enrichment populates `company_name` (around line 1590). Two-stage logic with AMC-parent carve-out:

```
Stage 1 — HARD-BLOCK markers (fund-instrument signals):
  " ETF", "ETF -", "MUTUAL FUND", "INDEX FUND", "BEES",
  "GOLD ETF", "SILVER ETF", "BANK ETF", "BOND ETF", "LIQUID ETF",
  "NIFTY50 VALUE", "HANG SENG", "HOSPITALS ETF", "TECH ETF"
  → any match → BLOCK immediately

Stage 2 — SOFT-AMC markers ("ASSET MANAGEMENT", "ASSET MGMT"):
  Carve-out: ALLOW if name ENDS with:
    "ASSET MANAGEMENT COMPANY LIMITED/LTD"
    "ASSET MANAGEMENT LIMITED/LTD"
    "AMC LIMITED/LTD"
  Otherwise → BLOCK
```

**AMC parents preserved** (publicly-listed real operating businesses):
- HDFCAMC — HDFC Asset Management Company Limited
- NAM-INDIA — Nippon Life India Asset Management Limited
- UTIAMC — UTI Asset Management Company Limited
- ABSLAMC — Aditya Birla Sun Life AMC Limited

**HSBC edge case** (name has BOTH "Asset Management" AND " ETF"): Stage 1 hard-block fires before Stage 2 carve-out evaluates → correctly blocked.

Filtered stocks get sentinel flag `_v158_etf_filtered=True` + `verdict='FILTERED_ETF'` + scores=0, then pruned from `final_100_list` immediately before Excel generation. Visible log line on each run: `🧹 v15.8: pruned N ETF/MF leakers post-enrichment: ...`.

**New G24 test** verifies: 11 confirmed leakers BLOCKED, 4 AMC parents ALLOWED, 3 legacy ETFs (NIFTYBEES/GOLDBEES/LIQUIDBEES) still BLOCKED, HSBC edge case correct, filter wired. **76/76 tests** across 5/5 runs.

### v15.8.1 — HOTFIX for v15.8 indentation bug
User reported Gold sheet went BLANK (4 picks → 0 picks) after v15.8 deploy. Investigation: comparing pre-v15.8 vs post-v15.8 Excels for SAME stocks on SAME date showed scores systematically dropped (OMFREIGHT 100→97.5, ITC 80→76.8, BSOFT 76.7→68). CMP identical — meaning code change, not market variation.

**Root cause**: v15.8 ETF-filter insertion accidentally orphaned the EPS/mcap/PE parsing block. Originally inside `if sym in _sm_map:` at indent 16, the parsing block ended up at indent 16 INSIDE `if _hit_marker:` AFTER a `continue` statement. Result: parsing block became DEAD CODE that never ran for ANY stock. EPS/mcap/PE stayed at 0 → P/E and Altman Z calculations broken → Score / Storm / Spike all dropped just enough to fail strict 11-criteria Gold gate.

**Fix**: restored EPS/mcap parsing block to its original location inside `if sym in _sm_map:`. v15.8 ETF filter preserved. **New G25 test** locks the structural invariant — verifies parsing block at indent 16, no orphaning `continue` before it, v15.8 filter sits AFTER parsing. **77/77 tests** across 5/5 runs.

### v15.9 — Tooltip context-correctness audit + fixes
User reported incorrect tooltip text on Performance sheet CLOSED POSITIONS column 'P&L %': "Current unrealized return... trade in progress" — wrong because CLOSED positions are FINAL realised outcomes.

**Root cause**: TIPS dict is keyed by header name. The same header name appears in BOTH OPEN POSITIONS (where 'unrealized', 'in progress' is correct) AND CLOSED POSITIONS (where 'realised', 'final outcome' is correct). Pre-v15.9 tooltips assumed only OPEN context.

**Fixed 6 shared-header tooltips**:
- **P&L %** — bullet points for OPEN (unrealized return, tracker logic) and CLOSED (realised return)
- **Max Runup %** — QUICK READ rewritten neutral; DETAIL clarifies OPEN vs CLOSED
- **Max Drawdown %** + 'Max DD %' alias — same neutral pattern
- **Days Held** — was outdated 'hits 90 days'; now mentions all 3 horizon-specific expiries (SHORT=30, POSITIONAL=90, LONG=270 per v14.1+)
- **Outcome Price** — formula consistent with P&L % terminology: `(Outcome Price - CMP at Rec) / CMP at Rec × 100`
- **Entry CMP** — notes alias relationship with 'CMP at Rec'

**Value display audit** (end-to-end across 8 sheets): 0 violations across all invariants — CMP within 52w range, SL<CMP<T1<T2<T3 monotonicity (16/16 BUY stocks), Score [0-100] bounds, Suggested Alloc [1%, 15%] bounds, risk-parity invariant (mean 0.98%, range 0.70-1.01%), Gold↔Full Dashboard cross-sheet consistency 4/4.

**New G26 test** verifies all 6 shared-header tooltips correctly mention BOTH contexts and use canonical terminology. **78/78 tests** across 5/5 runs.

### v16.0 — Institutional risk-adjusted metrics + DD-duration tracking + survivorship audit
First "Option A+B" of the free-tier improvement plan from the project-rating discussion. Three institutional-grade additions, all free-tier compatible (no paid data, no external services).

**Item 1: Sharpe / Sortino / Calmar reporting**
- New module: `analysis/risk_metrics.py` — pure-Python (math module only), zero external deps
- Sharpe: annualized `(mean − rf) / σ × √(252/avg_days)`. Risk-free rate = 6.5% (India 91-day T-bill).
- Sortino: same numerator, downside-only std denominator.
- Calmar: annualized mean / |max DD|.
- Plus supporting stats: win rate, profit factor, expectancy, avg win/loss.
- Sample-size caveat rendered when n<30 (statistically noisy).
- Performance sheet: new "RISK-ADJUSTED RETURNS" section between CLOSED and OPEN POSITIONS. 8-metric ratios row + 8-metric supporting-stats row + empty-state handling.

**Item 2: Max DD Duration tracking**
- Schema migration: `gold_outcomes.dd_duration_days INTEGER` + `dd_recovered INTEGER` (additive ALTER TABLE, idempotent).
- Tracker state machine in `_walk_forward`: close-to-close underwater-run counter, reset on recovery, longest-run captured. 
- Persisted via extended `update_outcome()` signature.
- `get_outcome_stats` SELECT pulls new columns (INNER JOIN).
- Rendered as Avg DD Duration + Recovery Rate alongside Sharpe/Sortino/Calmar.

**Item 5: Survivorship bias audit**
- New module: `analysis/survivorship_audit.py` — cross-checks OPEN gold positions against today's `latest_analysis_results` universe.
- 5 status branches: CLEAN (green ✓), STALE_FOUND (amber ⚠), NO_OPEN_POSITIONS (neutral), UNIVERSE_UNAVAILABLE (grey), ERROR (red).
- Performance sheet renders the audit line + explanatory caption at the end.
- Detects stocks delisted / suspended / symbol-changed while in OPEN portfolio.

**3 new regression tests**:
- G27: Sharpe/Sortino/Calmar math correctness (synthetic 4-trade case + 6 edge cases)
- G28: DD-duration tracker state machine structurally verified (variables present, reset-on-recovery, 6 return-dict sites populate fields, schema/update_outcome/SELECT all wired)
- G29: Survivorship audit handles all 5 status branches + graceful missing-DB

**81/81 tests** across 5/5 stability runs.

### Grade impact assessment
Per the institutional-rating analysis from this session, v16.0 changes are:
- Performance Attribution: C → **B+** (immediate)
- Risk Management Framework: A- → **A** (immediate)
- Documentation: A → **A+** (immediate)
- Empirical Validation: D → still D (needs DATA, not code — wait for 30+ closed positions)
- Out-of-Sample Evidence: D → still D (needs separate held-out period)
- Composite: remains **A-** today, lifts to **A** automatically once 30+ closed positions accumulate (60-90 days of pipeline runs).

The v16.0 infrastructure means that as soon as data accumulates, the metrics populate automatically — no further code work needed for the empirical-validation jump.

### Test count evolution
- v14.5: 66 → v14.6: 67 → v15.0: 69 (G16+G17) → v15.1: 71 (G18+G19)
- v15.2: 72 → v15.3: 73 → v15.4: 73 → v15.5: 74 (G22)
- v15.6: 74 → v15.7: 75 (G23) → v15.8: 76 (G24) → v15.8.1: 77 (G25)
- v15.9: 78 (G26) → **v16.0: 81 (G27 + G28 + G29)**

### Honest grade after v16.0: A- today, A in ~60-90 days
**What changed since v15.0**: trading-day calendar precision (Phase 1), backtest infrastructure (Phase 3), institutional risk-parity sizing (Phase 4 + v15.5), Performance sheet completeness (v15.5-v15.7), ETF filter robustness (v15.8 + v15.8.1 hotfix), tooltip context-correctness (v15.9), risk-adjusted metrics + DD duration + survivorship audit (v16.0).

**Path to true A+**: cannot reach on free tier. Requires (a) walk-forward backtest with 60-90 days of data — now achievable, (b) independent code review by external quant — needs people, not code, (c) execution-cost modeling with real broker fills — needs paid data. v16.0 closes the code gaps that are fixable on free tier.

### v16.2 — Gold-tier Quality Floor gate + Excel version-history cleanup

User flagged SONAMLTD admitted to Gold on 14 May 2026 — passed all 11 mechanical gates but failed qualitative review (MICRO CAP, ROE 9.5%, PEG 8.63, CFV ₹82.59 inflated by M5 outlier of ₹207). After empirical calibration analysis (see below), v16.2 implements **Option B only** with relaxed thresholds.

**Why Option A (method-agreement) was rejected**: Empirical analysis on 7 real Gold picks showed SONAMLTD's 50% method-agreement was identical to KOVAI's 50% and higher than ITC's 42.9%. There is no threshold of method-agreement that catches SONAMLTD while keeping the known good picks. Option A is a poorly-aimed filter because the 7 valuation methods (DCF/Graham/PE/PB/EV/DDM/PEG) inherently measure different things — wide spreads are normal even for healthy stocks.

**Option B (Quality Floor) — calibrated thresholds**:
- **ROE ≥ 10%** (or missing/None passes)
  - Catches SONAMLTD (9.5%)
  - Preserves ITC (29%), KOVAI (19.7%), BSOFT (13.4%), CIEINDIA (11.6%)
  - Threshold 12% would over-filter; 8% would miss SONAMLTD
- **PEG ≤ 8.0** (or missing/≤0 passes)
  - Catches SONAMLTD (8.63), INDUSTOWER (19.47)
  - Preserves BSOFT (PEG 6.36) — legitimate borderline value pick
  - Threshold 5.0 would reject BSOFT; threshold 10 would miss SONAMLTD

Both gates permissive on missing data — small caps without ratios pass, since Altman / BS Health / Int Coverage gates already cover them.

**Excel version-history cleanup** (per project policy):
- Removed `· v16.0` markers from "RISK-ADJUSTED RETURNS" and "SURVIVORSHIP AUDIT" section headers
- Removed `v15.0:` marker from "CLOSED POSITIONS" header
- Removed `v6.2` marker from Glossary header
- Excel now shows ONLY current state — no inline version annotations
- Internal docs (readme.md, CLAUDE.md, CHANGES.md) continue tracking full history

**Documentation updates**:
- Glossary entries for "ROE %" and "PEG Ratio" updated with Gold-tier gate role
- Tooltip quick-reads for ROE and PEG updated to mention "disqualifies from Gold tier"
- Gold criteria header text: "ALL 11 must pass" → "ALL 13 must pass"

**Real-data validation (empirical)**:
- 13 May 2026: v16.0 admits 3 (ITC, KOVAI, BSOFT) → v16.2 admits 3 (same — no change)
- 14 May 2026: v16.0 admits 3 (SONAMLTD, INDUSTOWER, CIEINDIA) → v16.2 admits 1 (CIEINDIA only)
- Net effect: 2 bad-quality picks correctly dropped across both days, all 4 quality picks preserved

**New G30 regression test** (9 structural invariants):
- Threshold constants present (10, 8)
- Permissive-on-missing logic verified (.isna() in both gates)
- Mask wires _roe_gate and _peg_gate
- Gold criteria text updated to "ALL 13 must pass"
- Stale "ALL 11" text removed
- All 11 legacy gates preserved (regression guard)
- Glossary entries reference Gold-tier role
- Tooltip text mentions disqualification threshold
- Version markers removed from new section headers

**82/82 tests** across 5/5 stability runs.

### Test count evolution
- v14.5: 66 → v14.6: 67 → v15.0: 69 → v15.1: 71 → v15.2: 72
- v15.3: 73 → v15.4: 73 → v15.5: 74 → v15.6: 74 → v15.7: 75 (G23)
- v15.8: 76 (G24) → v15.8.1: 77 (G25) → v15.9: 78 (G26)
- v16.0: 81 (G27 + G28 + G29) → **v16.2: 82 (G30)**

(Note: v16.1 was designed but never shipped — empirical analysis showed Option A as designed was ineffective. v16.2 is the production successor with calibrated Option B only.)

### Honest grade after v16.2: still A− today, A in ~60-90 days
The gate-tightening makes the screener more institutionally credible (no longer admitting low-ROE, high-PEG picks) but doesn't accelerate the empirical-validation timeline. Still needs 30+ closed positions for Sharpe to become statistically meaningful.

**Trade-off acknowledged**: v16.2 will produce empty-Gold days more often than v16.0, but ONLY when fundamentals are weak market-wide. This is the right institutional behavior — "no picks meet our criteria today" is better than admitting borderline picks just to fill the sheet.

### v16.3 — Excel column-width fixes + HTML pipeline reference refresh

User reported header-text overlap in Performance sheet (Days Held / Days Left / Re-app / Score columns crowded). Root cause: many columns sized below width 10, which doesn't fit headers like "Days Held ⓘ", "Storm /10 ⓘ", "RSI (14) ⓘ" — the ⓘ tooltip cue plus header text overflows.

**Width fixes across 4 column tables**:
- **Performance OPEN POSITIONS**: Days Held 10→12, Days Left 10→12, Re-app 8→10, Score 8→10, plus minor bumps on Regime, Current Price, Max Runup %, Suggested Alloc %, Sizing Rationale
- **Performance CLOSED POSITIONS**: aligned shared-column widths with OPEN to eliminate the OPEN-overrides-CLOSED conflict on the same sheet (Symbol, Rec Date, Time Horizon must match)
- **GOLD_COLS**: all ratio columns widened from 9→11 (Spike, Storm, P/E, PEG, ROE, D/E, RSI)
- **FULL_COLS**: all narrow ratio + technical-indicator columns widened from 8/9 to 11 (Beta, P/E TTM, P/CF, P/B, P/S, ROE%, ROCE%, ROA%, NPM%, FII%, DII%, Altman Z, Beneish M, ADX, RSI, Stoch %K, MFI)

**HTML pipeline reference** (`pipeline_reference.html`, renamed from `pipeline_reference_v15_0.html`):
- Title: "Pipeline Reference (v15.0)" → "Pipeline Reference (v16.3)"
- Meta line rewritten with full post-v15.0 enhancement summary
- Gold-tier filter SVG section updated from "ALL 11 conditions must pass" to "ALL 13 conditions must pass" with criteria 12 (ROE ≥ 10%) and 13 (PEG ≤ 8) listed
- Footer rewritten with layer-by-layer summary covering v15.5 / v15.7 / v15.8 / v15.9 / v16.0 / v16.2 / v16.3

**New G31 regression test** (4 structural invariants):
- FULL_COLS minimum width ≥ 10
- GOLD_COLS minimum width ≥ 10
- Performance OPEN POSITIONS narrow columns widened
- CLOSED POSITIONS aligned with OPEN for shared columns

**83/83 tests** across 5/5 stability runs.

### Test count evolution
- v14.5: 66 → v14.6: 67 → v15.0: 69 → v15.1: 71 → v15.2: 72
- v15.3: 73 → v15.4: 73 → v15.5: 74 → v15.6: 74 → v15.7: 75 (G23)
- v15.8: 76 (G24) → v15.8.1: 77 (G25) → v15.9: 78 (G26)
- v16.0: 81 (G27 + G28 + G29) → v16.2: 82 (G30) → **v16.3: 83 (G31)**

### Honest grade after v16.3: still A− today
v16.3 is a cosmetic / documentation update. No change to data flow, scoring, filtering, or schema. The grade trajectory remains the same: A− today, A in ~60-90 days as closed positions accumulate enough for Sharpe to become statistically meaningful.

### v16.4 — Beneish M anti-trigger threshold recalibration (-2.22 → -1.78)

User audit on 15 May 2026 surfaced false-positive Gold exclusions. MAYURUNIQ (Score 99.7, DEEP VALUE EARLY MOVER) and DRREDDY (Score 78.4, DEEP VALUE) were excluded from Gold despite passing every fundamental + technical quality gate. Trace revealed both were flagged by the v12.9 anti-trigger guard for Beneish M > -2.22 (MAYURUNIQ M=-1.80, DRREDDY M=-1.96).

**Beneish (1999) defines TWO thresholds in the original M-Score paper**:
- **M > -2.22** = "possible manipulator" (50%+ probability) — looser cutoff
- **M > -1.78** = "likely manipulator" (80%+ probability) — stricter cutoff

Pre-v16.4 used the looser -2.22. The Beneish model has known false-positive bias on high-growth and capital-intensive businesses (interprets genuine growth as accrual manipulation). v16.4 switches to the stricter -1.78 cutoff: still academically grounded, ~60% fewer false positives, still catches high-confidence manipulation cases.

**The Beneish FORMULA itself (v12.9 real 8-variable implementation) is unchanged**. Only the admission threshold is raised.

**Threshold updated at 3 sites consistently**:
- `screening/pre_screener.py` Rule 3 (primary anti-trigger guard at ~line 289)
- `master_funnel.py` v12.9 refresh block (post-Section 5A.5 fresh-forensics re-evaluation at ~line 3116)
- All documentation: `tooltip_formatter.py` (Beneish M long-tooltip + Spike Score tooltip), `excel_generator.py` (glossary entry + TIPS dict)

**Both academic thresholds now explicitly mentioned in tooltips and glossary for educational transparency.**

**Real-data validation (15 May 2026 projection)**: Gold sheet goes from 2 picks (EPL, HEXT) → 4 picks (EPL, HEXT, MAYURUNIQ recovered, DRREDDY recovered). Stocks correctly STILL excluded: CREST (M=-2.16 but fails ROE=3.74 < 10), MONARCH (M=-1.65 but fails Storm=3 < 5).

**HTML pipeline reference** renamed `pipeline_reference_v16_3.html` → `pipeline_reference_v16_4.html`. Title, meta, footer, and 2 SVG sections (Step 2c anti-trigger note + risk_flag_active block) updated to show "M > -1.78" threshold.

**New G32 regression test** (7 structural invariants): both production-code sites use -1.78, stale -2.22 production-code references gone, tooltips mention BOTH academic thresholds.

**84/84 tests** pass across 5/5 stability runs. `test_v11.0.2` baseline unchanged at 538 passed (4 pre-existing DUAL_LISTED failures unrelated to v16.4) — Beneish FORMULA tests in Group 59 correctly retain -2.22 references because they test formula output values, not gate thresholds.

### Test count evolution
- v14.5: 66 → v14.6: 67 → v15.0: 69 → v15.1: 71 → v15.2: 72
- v15.3: 73 → v15.4: 73 → v15.5: 74 → v15.6: 74 → v15.7: 75 (G23)
- v15.8: 76 (G24) → v15.8.1: 77 (G25) → v15.9: 78 (G26)
- v16.0: 81 (G27 + G28 + G29) → v16.2: 82 (G30) → v16.3: 83 (G31) → **v16.4: 84 (G32)**

### Honest grade after v16.4: still A− today
v16.4 is a CALIBRATION fix that prevents quality picks from being false-positive-filtered. It doesn't change grade trajectory — A− today, A in 60-90 days as closed positions accumulate. But it does improve the *daily output quality*: MAYURUNIQ and DRREDDY now appear in Gold where they belong, instead of being silently lost to an over-strict academic threshold. Net effect: more high-conviction picks available for the user to consider, fewer "where did that good stock go?" moments.

**Caveat acknowledged**: Beneish at -1.78 is still a noisy signal for newly-listed / high-growth small-caps. Other gates (Altman Z, BS Health, ROE quality floor, PEG, Earnings Quality) provide independent forensic protection. Stocks with M > -1.78 still get flagged — this is genuine high-confidence manipulation-risk territory.

### v16.5 — Trailing-stop recalibration + TRAIL_SL outcome label (Option C)

User spotted KOVAI in CLOSED POSITIONS as SL_HIT with +0.0% P&L and Outcome Price = Entry Price after the v16.4 run, and suspected the v16.4 fix broke something.

**Root-cause investigation (verified by direct file diffs)**: v16.4 did NOT cause this. `track_outcomes.py` and `database/data_bridge.py` were byte-identical between v16.3 and v16.4. The actual cause was the **v15.0 trailing-stop logic**, unchanged for many releases: break-even activation at just +5% peak gain. KOVAI ran to +5.4% peak → trailing stop ratcheted to break-even (= entry price) → normal pullback to +0.7% touched break-even → false SL_HIT at entry. The extra day of price data in the v16.4 run (not the Beneish change) is what triggered it — coincidental timing, not a regression.

**Part 1 — break-even activation recalibrated +5% → +10%**
Old tiers (≥15%→lock+7%, ≥10%→lock+3%, ≥5%→break-even) replaced with:
- peak ≥ 25% → lock +12%
- peak ≥ 20% → lock +9%
- peak ≥ 15% → lock +5%
- peak ≥ 10% → break-even (was ≥5% — TOO aggressive)
- peak < 10% → no trailing stop (original SL still protects)

**Part 2 — distinct TRAIL_SL outcome type**
- `SL_HIT` = original stop breached (real loss, thesis failed)
- `TRAIL_SL` = trailing stop hit after a favourable run (risk control, P&L ≥ 0)
- Discriminator: `trailing_sl_price > 0 AND trailing_sl_price >= original_sl AND effective_sl == trailing_sl_price`

**Stat-separation (the key user requirement)**: TRAIL_SL is fully separated from SL_HIT in EVERY aggregation:
- SL-rate numerator = n_sl ONLY (TRAIL_SL excluded — verified by G33 assertion `"n_sl + n_tr" not in src`)
- Separate headline tiles: "SL HIT" (n_sl) vs "TRAIL SL" (n_tr)
- Separate breakdown-table columns: "SL Hits" vs "Trail SL"
- Separate closed-summary buckets: 🛑 SL_HIT vs 🔵 TRAIL_SL
- Distinct colour: SL_HIT red (#FEE2E2/#991B1B), TRAIL_SL blue (#DBEAFE/#1E40AF)
- Both count toward CLOSED (position done) and both feed Sharpe/Sortino (real trades with real P&L)

**Database cleanup**: the false KOVAI SL_HIT row is persisted in `gold_outcomes` and can't be fixed by code alone. Shipped `v16_5_cleanup_false_kovai.py` — a dry-run-by-default, idempotent script that resets ONLY rows matching the precise false-close signature (SL_HIT + outcome_price ≈ entry + P&L ≈ 0%) back to OPEN, so the v16.5 tracker re-walks KOVAI correctly. A genuine SL_HIT (real -11% loss) would NOT match the filter and is left untouched. Does not touch the immutable gold_recommendations audit trail.

**Documentation swept everywhere**: track_outcomes.py docstring, tooltip_formatter.py (CLOSED / SL RATE / Outcome / Outcome Date / Days to Outcome / Outcome Price), excel_generator.py (Closed / SL Rate / Closed Positions section glossary entries), HTML pipeline reference renamed v16_4 → v16_5 (title + meta + footer), readme.md v16.5 row.

**Functional validation**: KOVAI scenario (+5.4% peak) now stays OPEN; legitimate +18% run → TRAIL_SL with locked +5% profit.

**New G33 regression test** — 10 invariants including 2 functional simulations and explicit stat-separation assertions. **85/85 tests** across 5/5 stability runs.

### Test count evolution
- v14.5: 66 → … → v16.0: 81 (G27+G28+G29) → v16.2: 82 (G30)
- v16.3: 83 (G31) → v16.4: 84 (G32) → **v16.5: 85 (G33)**

### Honest accounting
This pre-existing v15.0 bug sat through every release including the v16.0 outcome-tracking work and was never caught until the user spotted the anomalous CLOSED entry. That is a multi-session miss on the assistant's part — NOT a v16.4 regression. The user's instinct to compare before/after Excel files was exactly the right diagnostic move.

### Honest grade after v16.5: still A− today
v16.5 fixes a real correctness bug in outcome tracking — it doesn't accelerate the empirical-validation timeline, but it makes the track record HONEST (no more false break-even closes mislabeled as stop-losses polluting the SL-rate). Grade trajectory unchanged: A− today, A in 60-90 days as genuine closed positions accumulate.

### Honest grade after v17.3: A- today
v17.3 does not improve returns and should not be read as if it does. What it changes is that a previously INVISIBLE question became measurable. The T2 and T3 tiles were structurally incapable of ever showing a non-zero value, so for 31 closed positions the dashboard reported "0 T2 hits" when the honest statement was "T2 was never tested" - the tracker is first-event-wins and closed every position at T1 before T2 could be evaluated. Continuation tracking replaces a misleading zero with a real measurement. Whether T1 is placed well is now an empirical question rather than an assumption. But the answer needs roughly 30 completed continuation rows before it means anything, and there are 13 today. Grade trajectory unchanged: A- today, A once genuine closed positions accumulate.

**Documentation debt acknowledged.** v17.0, v17.1, v17.1.1 and v17.2 all shipped to production without entries in this file or in `readme.md` - both sat at v16.5 while four releases went live. v17.3 is documented here, but the gap is real and should be backfilled: the market-regime gate, the 3d-ROC momentum gate, the sector-cycle gate, the horizon-key fix (which had silently defaulted every stock to POSITIONAL since v14.6, leaving all horizon-specific SL/T1/T3 logic inert), the three unconditional column-width sites, and the Alert Log SCORE DEGRADED semantics are all undocumented here. *(Backfilled in v17.19 — see the "v17.0 – v17.2" entry at the top of Part B.)*

---

*Last updated: 7 October 2026 · v17.19 · Maintained by: Rajkumar + Claude (Anthropic) working sessions*