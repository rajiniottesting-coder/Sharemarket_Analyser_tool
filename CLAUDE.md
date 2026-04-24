# CLAUDE.md — NSE/BSE Stock Analyser Tool
## AI Context File · Version 10.16 · April 2026

This file gives Claude (or any AI assistant) complete project context to understand, debug, or extend this codebase without needing additional explanation. **Read it first** before making any change.

---

## 1. PROJECT PURPOSE

A fully automated, cloud-hosted daily pipeline that:

1. Downloads NSE + BSE market data every trading morning
2. Screens 5,000+ stocks through a 3-stage funnel → 100 candidates
3. Runs deep fundamental + technical + forensic + AI analysis on those 100
4. Delivers a colour-coded 7-sheet Excel research dashboard by **06:00–06:30 AM IST**
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
│   ├── orchestrator.py           Gate check (6 conditions) + NSE holiday calendar 2026
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
| `market_holidays` | NSE holiday calendar 2026 | Static |

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

### Verdict thresholds — `scoring_engine.py::CAP_THRESHOLDS`

```python
CAP_THRESHOLDS = {
    "LARGE": (60, 50),   # (BUY_min, WATCHLIST_min)
    "MID":   (63, 53),
    "SMALL": (66, 56),
    "MICRO": (70, 60),
}
AVOID_BELOW = 38   # Universal floor
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

### Resistance / Support formula (v10.9 corrected)

```python
_lb2  = min(252, len(h))               # 52 weeks, with graceful degradation
sup1  = l.rolling(20).min()            # short-term swing low
sup2  = l.rolling(_lb2).min()          # 52-week low (major floor)
res1  = h.rolling(20).max()            # short-term swing high
res2  = h.rolling(_lb2).max()          # 52-week high (major ceiling)
```

Pre-v10.9 had `sup2 = rolling(40).min()` / `res2 = rolling(40).max()`. Because the screener targets momentum stocks near highs, 87% of stocks had `res1 == res2` — the 20-day high WAS the 40-day high. Now R2 is a genuinely different long-term reference level.

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
- **C2** Not an NSE holiday (static `HOLIDAYS_2026`)
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

- Cron: `0 0 * * 2-6` = 00:00 UTC = **05:30 IST**, Tue–Sat
- Expected delivery: 06:00–06:30 IST
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
- Fix: new `_yf_ratio(k, cap=1000)` helper. Applied to `pe`, `pb`, `ps`, `ev_ebitda` at cap=1000; PEG at cap=100 (tighter — PEG beyond 100 is pure arithmetic noise).
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

**Tooltip updates** (`reporting/tooltip_formatter.py`)

- 5 valuation tooltips rewritten: P/E TTM, PEG Ratio, P/B, P/S, EV/EBITDA
- Each now says "displays '—' when raw ≥ 500 (50 for PEG)" instead of "capped at ±1000"
- P/E TTM tooltip also documents the v10.16 scoring neutrality rule
- VALUATION group header fully rewritten to explain Option B philosophy

**Glossary expansion** (`reporting/excel_generator.py`)

- 6 VALUATION entries updated: 4 detailed (P/E TTM, PEG, P/B, EV/EBITDA) in primary block, 2 (P/B, P/S) in secondary block
- Each entry now has a `'—' = Display when raw ≥ 500 (...)` bucket mirroring the numeric buckets above it

**DB schema unchanged.** `fundamental_metrics.pe/pb/ps/ev_ebitda/peg` columns stay `REAL DEFAULT 0` — the clamp value (500 / 50) is persisted for scoring; display layer converts at read time.

**Scoring sensitivity preserved EXCEPT for clamped values.** Real valuations in buckets [0-20], [20-40], [40-60], [60-499] still produce identical scoring behaviour. Only the [500+] region changed from `-8 penalty` to `neutral`. No real-business stocks affected — only the arithmetic-noise cases that previously showed misleading clamped numbers.

Integration tests: **65/65 passed** across 9 test groups — threshold edge cases at 499/500/501, PEG 49/50, PB/PS/EV at boundaries, defensive PE coerce in v7 module (5 input shapes), behavioral equivalence on composite score (clamped pe_num stays numeric → scoring stable), full regression on v10.12/v10.13/v10.14/v10.15.

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
| NSE holiday calendar | `ingestion/orchestrator.py` | `HOLIDAYS_2026` dict |
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
- [ ] Verify ETFs = 0 in output after pipeline run
- [ ] Expand DUAL_LISTED_ALLOWLIST as new IPOs confirm dual-listing
- [ ] Investigate BSE corporate filings API for Pledge % and separate DII (paid)

---

*Last updated: April 2026 · v10.11 · Maintained by: Rajkumar + Claude (Anthropic) working sessions*