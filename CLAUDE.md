# CLAUDE.md — NSE/BSE Stock Analyser Tool
## AI Context File · Version 12.3 · April 2026

This file gives Claude (or any AI assistant) complete project context to understand, debug, or extend this codebase without needing additional explanation. **Read it first** before making any change.

---

## 1. PROJECT PURPOSE

A fully automated, cloud-hosted daily pipeline that:

1. Downloads NSE + BSE market data every trading morning
2. Screens 5,000+ stocks through a 3-stage funnel → 100 candidates
3. Runs deep fundamental + technical + forensic + AI analysis on those 100
4. Delivers a colour-coded 7-sheet Excel research dashboard by **05:00–05:30 AM IST**
5. Sends an optional WhatsApp summary of top picks via Twilio
6. Maintains its own SQLite history with a 400-day rolling circular queue

**Single-user tool.** Pre-market preparation. Zero manual intervention on trading days.

---

## 2. FOLDER STRUCTURE (v10 — proper packages)

The codebase was reorganised in v8 from a flat file layout into proper packages. All cross-module imports use fully-qualified names (e.g. `from analysis.scoring_engine import ScoringEngine`).

```
Sharemarket_Analyser_tool/
├── master_funnel.py              ~2,670 lines — Pipeline orchestrator (Sections 0–13)
├── backfill_history.py           ~1,900 lines — 365-day historical builder
├── requirements.txt
├── ingestion/
│   ├── orchestrator.py           Gate check (6 conditions) — uses holiday_calendar.py
│   ├── holiday_calendar.py       NSE holiday-master API fetcher + DB cache
│   ├── harvester.py              NSE bhav/delivery/SME/F&O downloaders
│   └── reconciler.py             NSE+BSE merge + DUAL_LISTED_ALLOWLIST fallback
├── screening/
│   ├── pre_screener.py           Stage 1 ETF filter + Stage 2 quality score
│   └── priority_ranker.py        Stage 3 ranker + cap diversification + tech bonus
├── analysis/
│   ├── fair_value_engine.py      7 FV models + composite FV + MoS
│   ├── scoring_engine.py         Composite + verdict + confidence + storm
│   ├── forensics_engine.py       Altman Z + Beneish M + ND/EBITDA + CCC + inline yfinance fetcher
│   ├── fundamental_engine.py     Graham, PEG, 9-point Piotroski F
│   ├── technical_engine.py       RSI/MACD/Supertrend/ADX/MFI/Stoch
│   ├── ownership_tracker.py      Promoter/FII/DII QoQ trends
│   ├── spike_screener.py         6-trigger spike score
│   ├── early_detection_engine.py 12-signal early-entry score
│   ├── bs_engine.py              Balance sheet health audit
│   ├── rotation_engine.py        4-stage sector rotation
│   ├── smart_money.py            Bulk-deal + SAST insider scrapers
│   ├── intel_fetcher.py          Market intelligence
│   ├── market_context.py         Regime detection
│   └── v7_analysis_engine.py     Sections 3A–3H analytical overlays
├── database/
│   ├── data_bridge.py            ~920 lines — DB consolidation + helpers
│   ├── database_manager.py       Connection + schema management
│   └── db_maintenance.py         400-day rolling circular queue
├── ai/
│   └── ai_analyst.py             Google Gemini batch analysis (migrated from Anthropic in v10.1)
├── reporting/
│   ├── excel_generator.py        ~1,610 lines — 7-sheet ExcelGeneratorV6
│   ├── tooltip_formatter.py      ~980 lines — cell/group/reference tooltips
│   ├── daily_report_generator.py Plain-text research report
│   ├── report_formatter.py       Investor-card formatter
│   ├── email_service.py          Gmail SMTP delivery
│   ├── whatsapp_gateway.py       Twilio Flask webhook
│   └── command_parser.py         `why RELIANCE`, `early movers today`, etc.
├── master_prompt/
│   └── NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt   System prompt for Gemini
└── utils/
    ├── bse_diagnosis.py          BSE connectivity debug helper
    └── chat_interface.py         Local REPL for command_parser
```

---

## 3. DATABASE SCHEMA (SQLite — `market_data.db`, ~400 MB)

Tables are created by two files working together:
- `backfill_history.py::init_all_tables()` — creates the full 15-table set (called once on cold start)
- `database/data_bridge.py::initialize_v7_tables()` — ensures pipeline-critical tables exist and runs `ALTER TABLE IF NOT EXISTS` migrations for additive schema changes
- **`master_funnel.py` startup (v10.5)** — defensive `CREATE TABLE IF NOT EXISTS shareholding` + `ALTER TABLE ADD COLUMN` for 18 forensic-input columns, protecting against older DBs created before those columns existed

| Table | Contents | Size |
|---|---|---|
| `daily_prices` | OHLCV + delivery % + 52w hi/lo + day chg % | 365d × 5,000 syms |
| `symbol_master` | Company name, sector, cap category, ISIN, BSE code | ~5,000 symbols |
| `fundamental_metrics` | PE, PB, EPS, Div Yield, Beta, ROE, D/E, Margins, CAGR + 18 forensic columns (v10.2) | ~5,000 symbols |
| `shareholding` | Promoter %, FII %, DII %, Pledge % + QoQ changes | ~3,000 symbols |
| `technical_indicators` | RSI14, MACD, Supertrend, ADX, Stoch K, MFI, OBV, VWAP | ~5,000 symbols |
| `weekly_momentum` | 2w / 4w / 6w / 8w price change %, beta_90d | ~5,000 symbols |
| `delivery_stats` | Daily delivery % per symbol | 365 days |
| `fo_participant_data` | FII + DII + Prop net buy/sell ₹ | Latest 5 rows |
| `bulk_deals` | Institutional block trades | Rolling window |
| `insider_trades` | SEBI SAST disclosures | Rolling window |
| `latest_analysis_results` | Composite score + verdict + AI card per symbol | ~100 symbols |
| `v7_intelligence` | News + market intel per symbol | ~100 symbols |
| `run_stats` | One row per pipeline run (gate result, counts, timings) | Append |
| `watchlist` | Personal watchlist overrides | User-defined |
| `market_holidays` | NSE holiday calendar (auto-fetched per year, cached) | API + cache |

**Key DB functions in `database/data_bridge.py`:**

- `save_to_database(df, table, conn)` — upsert with conflict resolution
- `get_symbol_history(symbol, days)` — returns OHLCV DataFrame
- `get_20d_avg_vol(symbol)` — 20-day average volume
- `load_latest_analysis_results()` — for Alert Log prev scores
- `initialize_v7_tables(conn)` — schema creation + migration
- `check_data_integrity(raw_nse, raw_bse)` — C5 gate condition
- `get_today_consolidated_data()` — feeds `command_parser`
- `get_historical_quarter_data(symbols)` — QoQ baseline lookup (v10.3: reads from `shareholding`)
- `get_nifty_52w_high_from_db()`, `get_latest_fii_net_cash()`, `get_nifty_200_sma()`

---

## 4. PIPELINE EXECUTION ORDER (`master_funnel.py::run_master_pipeline`)

```
Section 12B  Gate check (ingestion.orchestrator.gate_check) — 6 conditions must pass
Section 1    Harvest: NSE Bhav + Delivery + BSE Bhav (pip pkg) + F&O + Bulk Deals + Insider
Section 1.2  Gap detection — detect missed trading days
Section 1.3  DB sync — consolidate 5,150 records → daily_prices
Section 1.4  Gap-fill — backfill missing trading days on the fly
Section 0    Pre-screening funnel:
               Stage 1 (pre_screener.stage_1_filter)              : 5,150 → ~600
               Stage 2 (pre_screener.stage_2_fundamental_scorer)  : ~600  → ~400
               Stage 3 (priority_ranker.get_top_100_candidates)   : ~400  → 100
Section 3    For each of 100 stocks:
               3A  Valuation ratios (EY, PE tag, EV/EBITDA)
               3B  Inline forensic-input fetch (v10.4) — pulls ticker.balance_sheet,
                   .cashflow, .income_stmt for this symbol
               3B/3D/3G  Forensics (Beneish, Altman, ND/EBITDA, CCC, CFO/PAT)
               3E  Capital allocation (ROCE)
               3F  Ownership trends (Promoter/FII/DII QoQ) — shows "—" until history
               3G  Growth quality (CAGR tiers)
               3H  Anti-trigger guard (pledge/Beneish/Altman/CFO)
               3I  Early entry score — DEFERRED to Section 6 (needs real technicals)
               3J/3K  Bulk deal sentiment + insider buying
               3L  Sector rotation stage — PLACEHOLDER (recomputed after tech loads)
Section 4    Balance Sheet Health — FIRST PASS (pre-FM, mostly placeholder)
Section 4B   NSE fundamentals refresh via yfinance (top-100 only)
             + NSE shareholding enrichment for DII (v10.6) — top 100
Section 5    DB enrichment: technicals + fundamentals + weekly momentum
             → After technical data loads: Sector Stage RECOMPUTED HERE
             → Ghost-key derivation: fcf_positive_4q, promoter_q_increase,
               fii_buy_3q, rev_growth_yoy, fii_3q_trend, promoter_buying_30d
Section 5A.5 Forensics re-run after DB enrichment (v10.3)
Section 5B   Fair Value engine — 7 models per stock (analysis.fair_value_engine)
Section 6    SCORING LOOP for each stock:
               → Technical score (RSI/MACD/ST/ADX/MFI/Stoch)
               → Fundamental score (PE/ROE/DE/CR/GM/NM/EY/Promoter/PAT_YoY/Rev_YoY/FCF_Yield)
               → Safety score (Pledge/Beta/DE/FCF/BS_Health)
               → Sentiment score (FII trend / Smart Money / Insider / News)
               → Ghost-key injection before storm score
               → Composite score (analysis.scoring_engine)  ← returns verdict + confidence
               → Storm score
               → Horizon + Risk Level  ← computed AFTER verdict
               → Sector Stage (second pass using real RSI/MACD/ST)
               → BS Health re-evaluation  ← SECOND PASS with real FM data
               → Spike Score (analysis.spike_screener)
               → Smart Money signals
               → Early Entry Score (Section 3I — runs here with real technicals)
               → F-Score proxy (9-point from available data)
               → Price targets (T1/T2/T3, entry range, stop loss)
               → Blank name+sector filter (removes ETFs that slipped through)
Section 7/8  AI investor cards (ai.ai_analyst — Google Gemini, batches of 10–15)
Section 9/10 7-sheet Excel dashboard (reporting.excel_generator.ExcelGeneratorV6)
             + text research report (reporting.daily_report_generator)
             + dynamic red-header demotion (v10.4): columns with ≥1 real value
               get their normal section colour instead of red
Section 12   Email delivery (reporting.email_service)
Section 13   DB maintenance — 400-day rolling window (database.db_maintenance)
```

**CRITICAL ORDER RULES:**

- Technical data (RSI/MACD/Supertrend) loads at Section 5. Any code using these must run AFTER that point.
- `composite_score` and `verdict` are set by `ScoringEngine.calculate_composite_score()`. `horizon` and `risk_level` must run AFTER this call.
- BS Health runs twice: first pass at Section 4 (pre-enrichment, mostly HEALTHY), second pass after Section 5 (real data).
- Forensics runs twice (v10.3): first pass in the top-100 loop (Section 3B) with inline yfinance fetch, second pass (Section 5A.5) after DB enrichment catches anything the inline fetch missed.
- `company_name` and `sector` are only available after Section 4B/5 FM enrichment — never at Stage 1.
- Alert Log requires `latest_analysis_results` to be **loaded before** today's scores are saved — otherwise Score Δ is always 0.

---

## 5. SCREENING FUNNEL

### Stage 1 — `screening/pre_screener.py::stage_1_filter` (Section 0A)

Filters applied in order:

1. `sc_group` exclusion: EF, MF, IF, IR, BE → dropped
2. ETF keyword filter (~67 patterns): GOLD1, SILVERAG, QNIFTY, MSCIINDIA, MASPTOP50, BANKBEES, ITBEES, NIFTYBEES, GOLDBEES, PSUBNKBEES, ends-ETF, ends-BEES, ends-INDEX, etc.
3. Volume must be > 0
4. Circuit breaker: abs price change ≥ 19.9% → dropped
5. Penny stock: close < ₹10 → dropped
6. Suspended: status=SUSPENDED → dropped
7. Delivery: delivery_pct < 40% → dropped (unless `watchlist_override`)
8. BSE SME: turnover < ₹5L → dropped

**NOTE:** `company_name` and `sector` are NOT available at Stage 1. Blank-name+sector filter runs in `master_funnel` AFTER FM enrichment (right before Excel generation).

### Stage 2 — `screening/pre_screener.py::stage_2_fundamental_scorer` (Section 0B)

Quality score 0–35: delivery % + turnover + vol spike + exchange listing + price zone.

### Stage 3 — `screening/priority_ranker.py::get_top_100_candidates` (Section 0C)

```
Priority Score = (vol_spike/5 × 25) + (stage2/35 × 30) + (delivery/100 × 20)
              + (cap_bonus × 15) + (turnover_bonus × 10)
```

Cap diversification: LARGE ≥ 20, MID ≥ 15, SMALL+MICRO ≤ 65.

Technical alignment bonus (applied in master_funnel after tech loads):
- Supertrend=BUY + MACD=BUY → +8
- One BUY → +3
- Both SELL → −5

`VOL_SPIKE_CAP = 5×` prevents ETF arbitrage from dominating the ranker.

---

## 6. SCORING SYSTEM (Session 24 refinements)

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
- **Sentiment:** `fii_3q_trend`, `smart_money_sentiment`, `insider_buy_alert`, `news_sentiment`, `pledge_direction`

### Ghost keys (derived before storm/sentiment scoring)

Populated in `master_funnel.py` just before the composite-score call:
- `fcf_positive_4q` ← `fcf > 0`
- `promoter_q_increase` ← `promoter_qoq > 0.3`
- `fii_buy_3q` ← `fii_qoq > 0.3`
- `rev_growth_yoy` ← `rev_yoy`
- `fii_3q_trend` ← derived from `fii_qoq`
- `promoter_buying_30d` ← `promoter_qoq > 0.5`

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
| `earnings_quality` | Earn Quality | cfo / pat |
| `altman_z` | Altman Z | 1.2·x1 + 1.4·x2 + 3.3·x3 + 0.6·x4 + 1.0·x5 |
| `beneish_m` | Beneish M | Accrual-quality proxy (TATA-based tiers) |
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

## 9. EXCEL DASHBOARD (`reporting/excel_generator.py`) — 7 sheets

**Class:** `ExcelGeneratorV6(data, date_str, run_time=None, prev_scores=None, gap_days=None)`

### Sheets

1. **📊 Full Dashboard** — 100 stocks × ~120 columns
2. **⭐ Gold – Early Movers** — MOMENTUM (EE ≥ 70) OR VALUE (MoS ≥ 25% AND Score ≥ 70)
3. **📊 Trade Summary** — Entry / SL / T1 / T2 / T3 / R:R for Gold stocks
4. **🔔 Alert Log** — daily score changes, 8-way Action Required logic
5. **📱 Delivery Preview** — WhatsApp + Email text preview
6. **📖 Glossary** — 80+ column definitions
7. **💡 Tooltip Reference** — Polished hover + ⓘ cue (Session 16)

### Dynamic red-header demotion (v10.4)

Before rendering row-4 headers, walks the top-100 stocks and counts non-`"—"`, non-zero values per column. Columns in `NO_FREE_SOURCE_COLS` with ≥1 real value get their **normal section colour** instead of red. Columns that are genuinely empty for all 100 stocks keep the red `991B1B` header.

### Alert Log 8-way Action Required logic

- Score < 30 → `REVIEW FOR EXIT`
- BUY + MoS > 10% + Score ≥ 65 → `CONSIDER ENTRY`
- BUY + MoS ≤ 0 → `BUY BUT OVERVALUED — WAIT`
- BUY (other) → `MONITOR FOR ENTRY`
- Vol spike ≥ 3× → `VOLUME ALERT — INVESTIGATE`
- Early Entry ≥ 70 → `EARLY MOVER — ACCUMULATE`
- Score Δ ≥ +3 → `SCORE IMPROVING — WATCH`
- Score Δ ≤ −3 → `SCORE DECLINING — CAUTION`
- Default → `MONITOR CLOSELY`

### Tooltip system (Session 16)

`reporting/tooltip_formatter.py` (~980 lines). Three public helpers used by `excel_generator.py`:
- `apply_tooltips(ws, row, col_map)` — per-cell hover + ⓘ indicator
- `apply_group_tooltips(ws, row, group_cols)` — group-header tooltips
- `build_reference_sheet(wb)` — populates the Tooltip Reference sheet

---

## 10. BACKFILL (`backfill_history.py`)

Auto-runs when `daily_prices` has fewer than 50,000 rows (fresh DB).

**Tables populated:**
- `daily_prices` — 365 days of OHLCV per symbol
- `symbol_master` — company names, sectors, cap categories
- `technical_indicators` — all indicators per symbol (latest date)
- `weekly_momentum` — 2w/4w/6w/8w changes + beta_90d
- `delivery_stats` — daily delivery %
- `fundamental_metrics` — PE, PB, ROE, EPS, + 18 forensic input columns (v10.2)
- `shareholding` — Promoter/FII/DII/Pledge via yfinance + NSE corp-info API (v10.6)

### NSE shareholding enrichment (v10.6)

yfinance only provides `heldPercentInstitutions` (FII+DII combined), which is why `dii_pct` was hardcoded to 0.0 in line 1793. v10.6 adds an enrichment loop after the yfinance pass:

```
for each sh_row in sh_rows[:100] where dii_pct == 0:
    _nse_sh = _nse_shareholding(symbol, session)     # returns diisTotal separately
    if _nse_sh.get('dii_pct', 0) > 0:
        update row with real DII, recompute public_float
        time.sleep(0.3)   # NSE rate-limit guard
```

Console output: `NSE shareholding: enriched DII for N/M symbols`. On GitHub Actions runners, NSE API is often blocked by Akamai; on local Windows it usually works.

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

## 11. INGESTION LAYER

### Gate check — `ingestion/orchestrator.py::gate_check`

Six conditions:
- **C1** Weekday (Mon–Fri)
- **C2** Not an NSE holiday (auto-fetched from NSE API + cached in `market_holidays`; fail-closed if calendar unknown — see `holiday_calendar.py`)
- **C3** NSE bhav copy URL available (HEAD request)
- **C4** BSE URL check — **IGNORED by master_funnel** (cloud IPs can't reach it; `bse` pip pkg handles internally)
- **C5** Data integrity — run in `master_funnel` AFTER download
- **C6** Minimum DB rows

### BSE downloads — `bse` pip package (singleton)

`master_funnel` opens one `BSE()` client at pipeline start, reuses it for bhav + delivery + SME, closes it in the `finally` block. `_parse_bse_df` standardises column names.

### Reconciler — Session 22

Merges NSE + BSE bhav on `isin`. `final_symbol` prefers NSE ticker, `final_close` prefers NSE close.

**`DUAL_LISTED_ALLOWLIST`** — 206 Nifty-100/mid-cap NSE tickers. Fallback used when BSE download fails. Maintenance: rarely changes.

---

## 12. AI LAYER (`ai/ai_analyst.py`) — Gemini (migrated v10.1)

- Uses `google-genai` SDK (migrated from `anthropic` in v10.1).
- `GEMINI_API_KEY` or `GOOGLE_API_KEY` required — raises `ValueError` at import time if missing.
- Model: `gemini-2.5-pro` (configurable via `GEMINI_MODEL` env var).
- Master prompt loaded from `master_prompt/NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt`, passed as `system_instruction`. Batch stock data goes into the `contents` parameter.
- `AI_BATCH_SIZE = 12` (10–15 per Section 0D).
- `FundamentalEngine` pre-computes Graham number, PEG, and CFV so the model uses our values instead of estimating.

---

## 13. INFRASTRUCTURE

### GitHub Actions (`.github/workflows/market_run.yml`)

- Cron: `0 23 * * 1-5` = 23:00 UTC Mon–Fri = **04:30 IST Tue–Sat**
- Expected delivery: 05:00–05:30 IST
- Runner: `ubuntu-latest`, Python 3.11
- DB persistence: SQLite artifact `market-data-db`, 7-day retention, `overwrite: true`
- Auto-backfill: if `daily_prices < 50,000` rows → run `backfill_history.py 365`

### Required GitHub Secrets

```
GEMINI_API_KEY           — Google Gemini API for AI investor cards (or GOOGLE_API_KEY)
SENDER_EMAIL             — Gmail sender address
SENDER_APP_PASSWORD      — Gmail 16-char App Password
USER_EMAIL_ID            — Recipient email
TWILIO_ACCOUNT_SID       — WhatsApp delivery (optional)
TWILIO_AUTH_TOKEN        — WhatsApp delivery (optional)
```

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
BACKFILL_DAYS            = 365
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

# AI batching
AI_BATCH_SIZE            = 12
AVOID_SKIP_AI            = True      # v10.13 FIX #1 — AVOID verdict → placeholder, not Gemini call
```

---

## 15. VERSION HISTORY

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

## 16. KNOWN LIMITATIONS

| Column | Limitation | Status |
|---|---|---|
| Current Ratio / Quick Ratio | yfinance missing for ~25–40% of Indian stocks | 2nd-pass balance_sheet; ~60–75% coverage |
| Piotroski F-Score | No free source for true 9-point YoY comparisons | Proxy from available data |
| PAT CAGR 3Y / Rev CAGR 3Y | Not in yfinance for Indian stocks | Red headers (no free source) |
| Alert Log Prev Score | Blank on first run | Populates from run 2 onwards |
| Pledge % | Only in BSE corporate filings | Always 0 until paid source added |
| DII % | NSE API may be blocked on cloud IPs | Real values when API responds, 0 otherwise (v10.6) |
| QoQ deltas (Pro/FII/DII) | Need ≥ 90 days of `shareholding` history | Show `"—"` until accumulation (~3 months) |
| BSE SME delivery % | Not available from BSE API | NSE delivery used as primary |
| BSE downloads from cloud | Akamai blocks cloud IPs | Handled via `bse` pip pkg + allowlist fallback |

---

## 17. DIAGNOSTICS

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

| Function / Block | File | Notes |
|---|---|---|
| Gate check (6 conditions) | `ingestion/orchestrator.py` | `gate_check()` |
| NSE holiday calendar | `ingestion/holiday_calendar.py` | `ensure_holiday_calendar_fresh()` — API + DB cache |
| NSE downloaders | `ingestion/harvester.py` | `download_nse_bhavcopy` etc. |
| BSE singleton client | `master_funnel.py` | `_get_bse_client`, `_close_bse_client` |
| Reconciler + dual-listed fallback | `ingestion/reconciler.py` | `DUAL_LISTED_ALLOWLIST` |
| Stage 1 filter | `screening/pre_screener.py` | `stage_1_filter` |
| Stage 2 quality | `screening/pre_screener.py` | `stage_2_fundamental_scorer` |
| Stage 3 ranker | `screening/priority_ranker.py` | `get_top_100_candidates` |
| Defensive schema init (v10.5) | `master_funnel.py` | startup block ~line 251 |
| Inline forensic fetch (v10.4) | `master_funnel.py` | in top-100 loop ~line 600 |
| QoQ `"—"` fix (v10.4) | `master_funnel.py` | `_qoq()` helper ~line 530 |
| Forensic re-run (v10.3) | `master_funnel.py` | Section 5A.5 ~line 1450 |
| Forensic inline fetcher | `analysis/forensics_engine.py` | `fetch_forensic_inputs(symbol)` |
| ND/EBITDA annualization (v10.6) | `analysis/forensics_engine.py` | ~line 340 |
| Pledge default `"—"` (v10.6) | `analysis/forensics_engine.py` | ~line 397 |
| NSE DII enrichment (v10.6) | `backfill_history.py` | after yfinance pass ~line 1800 |
| NSE corp-info API | `backfill_history.py` | `_nse_shareholding()` |
| Composite score + verdict | `analysis/scoring_engine.py` | `calculate_composite_score` |
| DCF guards | `analysis/fair_value_engine.py` | Session 19 |
| Piotroski F-Score | `analysis/fundamental_engine.py` | `calculate_piotroski_f_score` |
| Altman Z / Beneish M | `analysis/forensics_engine.py` | `calculate_altman_z` / `calculate_beneish_m` |
| Excel generator (7 sheets) | `reporting/excel_generator.py` | `class ExcelGeneratorV6` |
| Dynamic red-header (v10.4) | `reporting/excel_generator.py` | ~line 1183 |
| Alert Log | `reporting/excel_generator.py` | `_alert_log()` |
| AI investor cards | `ai/ai_analyst.py` | `get_ai_analysis` (Gemini) |
| Email delivery | `reporting/email_service.py` | `send_analysis_email` |
| Historical QoQ lookup (v10.3) | `database/data_bridge.py` | `get_historical_quarter_data` |
| 400-day rolling window | `database/db_maintenance.py` | `enforce_circular_queue` (KEEP_DAYS=400) |

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

---

## 20. PENDING / NEXT ACTIONS

- [ ] Add retry logic for yfinance (currently single attempt per symbol)
- [ ] Add Screener.in scraping for PAT CAGR / Rev CAGR data
- [ ] WhatsApp bot: end-to-end test of ngrok + Twilio integration
- [ ] Reduce AI batch size 12 → 8 if response truncation observed
- [ ] FCF-yield based FV model (M8) for capital-light businesses
- [ ] PAT CAGR in fundamental score (needs data source)
- [x] Verify ETFs = 0 in output after pipeline run *(v12.0: BSE 3-tier cascade restores sc_group filter so ETFs stop leaking)*
- [ ] Expand DUAL_LISTED_ALLOWLIST as new IPOs confirm dual-listing *(v11.0.2 added runtime auto-add via `dual_listed_runtime` table)*
- [ ] Investigate BSE corporate filings API for Pledge % and separate DII (paid)

---

## 21. v12.0 RELEASE — BSE Cloudflare resilience + Dashboard size stability

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

## 22. v12.1 RECONCILER HOTFIX — Empty-ISIN false-positive prevention

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

## 23. v12.2 RELEASE — Fair Value Engine hardening (initial fixes + Round 1)

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

## 24. v12.3 RELEASE — Round 2: M5 EV proper formula + M7 PEG_BENCHMARK explicit

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

## 25. v12.4 RELEASE — Production blocker patch set

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

## 26. v12.5 RELEASE — Quality-of-life fixes

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

## 27. v12.6 RELEASE — Final-round judgment-call fixes

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

## 28. v12.6.1 RELEASE — Backfill window bumped to 400 calendar days

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

## 29. v12.7 RELEASE — Comprehensive dual-listed integrity fix set (12 bugs)

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

## 30. v12.8 RELEASE — Bug #13 (dedup ordering) + Bug #14 (yfinance 404 cache)

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

## 31. v12.9 RELEASE — QUALITY SCORES section overhaul (Beneish M + Earn Quality + Spike refresh)

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

## 32. v13.0 RELEASE — Free-tier shareholding data unlocked (NSE bulk pledge + corp-info QoQ deltas)

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

## 33. v13.x RELEASE — Production-credibility patch set (Top 5 BUY filter · MoS '—' for ETFs · Quick Pick recompute)

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

*Last updated: May 7, 2026 · v13.x · Maintained by: Rajkumar + Claude (Anthropic) working sessions*