# CLAUDE.md — NSE/BSE Stock Analyser Tool
## AI Context File · Version 10.0 · April 2026

This file gives Claude (or any AI assistant) complete project context to understand, debug, or extend this codebase without needing additional explanation. Read it first before making any change.

---

## 1. PROJECT PURPOSE

A fully automated, cloud-hosted daily pipeline that:

1. Downloads NSE + BSE market data every trading morning
2. Screens 5,000+ stocks through a 3-stage funnel → 100 candidates
3. Runs deep fundamental + technical + forensic + AI analysis on those 100
4. Delivers a colour-coded 7-sheet Excel research dashboard by **06:00–06:30 AM IST**
5. Sends an optional WhatsApp summary of top picks via Twilio
6. Maintains its own SQLite history with a 90-day circular queue

**Single-user tool.** Pre-market preparation. Zero manual intervention on trading days.

---

## 2. FOLDER STRUCTURE (v10 — proper packages)

The codebase was reorganised in v8 from a flat file layout into proper packages. All cross-module imports now use fully-qualified names (e.g. `from analysis.scoring_engine import ScoringEngine`).

```
Sharemarket_Analyser_tool/
├── master_funnel.py              ~2,460 lines — Pipeline orchestrator (Sections 0–13)
├── backfill_history.py           ~1,850 lines — 365-day historical builder
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
│   ├── forensics_engine.py       Altman Z + Beneish M (numeric)
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
│   ├── data_bridge.py            ~800 lines — DB consolidation + helpers
│   ├── database_manager.py       Connection + schema management
│   └── db_maintenance.py         90-day rolling circular queue
├── ai/
│   └── ai_analyst.py             Anthropic Claude batch analysis
├── reporting/
│   ├── excel_generator.py        ~1,530 lines — 7-sheet ExcelGeneratorV6
│   ├── tooltip_formatter.py      ~980 lines — cell/group/reference tooltips
│   ├── daily_report_generator.py Plain-text research report
│   ├── report_formatter.py       Investor-card formatter
│   ├── email_service.py          Gmail SMTP delivery
│   ├── whatsapp_gateway.py       Twilio Flask webhook
│   └── command_parser.py         `why RELIANCE`, `early movers today`, etc.
├── master_prompt/
│   └── NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt   System prompt for Claude
└── utils/
    ├── bse_diagnosis.py          BSE connectivity debug helper
    └── chat_interface.py         Local REPL for command_parser
```

---

## 3. DATABASE SCHEMA (SQLite — `market_data.db`, ~400 MB)

Tables are created by two files working together:
- `backfill_history.py::init_all_tables()` — creates the full 15-table set (called once on cold start)
- `database/data_bridge.py::initialize_v7_tables()` — ensures pipeline-critical tables exist and runs `ALTER TABLE IF NOT EXISTS` migrations for additive schema changes

| Table | Contents | Size |
|---|---|---|
| `daily_prices` | OHLCV + delivery % + 52w hi/lo + day chg % | 365d × 5,000 syms |
| `symbol_master` | Company name, sector, cap category, ISIN, BSE code | ~5,000 symbols |
| `fundamental_metrics` | PE, PB, EPS, Div Yield, Beta, ROE, D/E, Margins, CAGR | ~5,000 symbols |
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
               3B/3D/3G  Forensics (Beneish, Altman, CFO/PAT)
               3E  Capital allocation (ROCE)
               3F  Ownership trends (Promoter/FII QoQ)
               3G  Growth quality (CAGR tiers)
               3H  Anti-trigger guard (pledge/Beneish/Altman/CFO)
               3I  Early entry score — DEFERRED to Section 6 (needs real technicals)
               3J/3K  Bulk deal sentiment + insider buying
               3L  Sector rotation stage — PLACEHOLDER (recomputed after tech loads)
Section 4    Balance Sheet Health — FIRST PASS (pre-FM, mostly placeholder)
Section 4B   NSE fundamentals refresh via yfinance (top-100 only)
Section 5    DB enrichment: technicals + fundamentals + weekly momentum
             → After technical data loads: Sector Stage RECOMPUTED HERE
             → Ghost-key derivation: fcf_positive_4q, promoter_q_increase,
               fii_buy_3q, rev_growth_yoy, fii_3q_trend, promoter_buying_30d
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
Section 7/8  AI investor cards (ai.ai_analyst — Anthropic Claude, batches of 10–15)
Section 9/10 7-sheet Excel dashboard (reporting.excel_generator.ExcelGeneratorV6)
             + text research report (reporting.daily_report_generator)
Section 12   Email delivery (reporting.email_service)
Section 13   DB maintenance — 90-day circular queue (database.db_maintenance)
```

**CRITICAL ORDER RULES:**

- Technical data (RSI/MACD/Supertrend) loads at Section 5. Any code using these must run AFTER that point.
- `composite_score` and `verdict` are set by `ScoringEngine.calculate_composite_score()`. `horizon` and `risk_level` must run AFTER this call.
- BS Health runs twice: first pass at Section 4 (pre-enrichment, mostly HEALTHY), second pass after Section 5 (real data).
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
```

**Session 24 refinements** (all in `scoring_engine.py`):

1. **Sentiment informedness check** — if none of the paid/AI sentiment signals fired (FII/Promoter/DII QoQ, insider buy, news sentiment, pledge direction), the 10% sentiment weight **redistributes** proportionally to Fundamental/Technical/Early/Safety. No "free 5 points" for missing data.
2. **Fundamental-gated spike bonus** — full +10 only when `fundamental_score ≥ 55`. Otherwise capped at +3 so momentum can't rescue genuinely weak stocks.
3. **Confidence dots** — HIGH ●●● (≥ 5 points clear of threshold), MEDIUM ●●○ (2–5), LOW ●○○ (< 2; cliff zone).
4. **OVERVALUED verdict** — new distinct verdict for stocks that clear the BUY score threshold but fail the MoS gate. Reads as "great business, currently expensive". Styled in soft orange (`FED7AA` / `7C2D12`) — not green, not red.
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

**MoS gate for BUY** — normally MoS ≤ −10% blocks BUY (becomes OVERVALUED). Relaxed to MoS ≤ −20% if **technically confirmed**: `score ≥ 70 AND Supertrend=BUY AND Sector Stage 2`. See `_get_verdict_with_confidence()`.

### Score inputs by category

- **Fundamental:** PE, ROE, D/E, Current Ratio, Gross Margin, Net Margin, Earnings Yield, Promoter %, **PAT YoY** (+8/+4/+2/−7), **Rev YoY** (+5/+3/+1/−4), **FCF Yield** (+6/+3/−5)
- **Technical:** RSI, ADX, MACD, Supertrend, VWAP, OBV, **Stochastic K** (oversold zone 20–40 = +5), **MFI** (>60 = +4, <30 = −3)
- **Safety:** Pledge %, Beta, D/E, **FCF** (negative = −8, positive = +3), **BS Health** (ALERT = −15, WATCH = −5, HEALTHY+FCF = +3)
- **Sentiment:** `fii_3q_trend` (derived from `fii_qoq`: >1% = UP, <−1% = DOWN), `smart_money_sentiment`, `insider_buy_alert`, `news_sentiment`, `pledge_direction`

### Ghost keys (derived before storm/sentiment scoring)

Populated in `master_funnel.py` just before the composite-score call:

- `fcf_positive_4q` ← `fcf > 0`
- `promoter_q_increase` ← `promoter_qoq > 0.3`
- `fii_buy_3q` ← `fii_qoq > 0.3`
- `rev_growth_yoy` ← `rev_yoy`
- `fii_3q_trend` ← derived from `fii_qoq`
- `promoter_buying_30d` ← `promoter_qoq > 0.5`

### Quick-pick labels — `_assign_quick_pick`

Used on the Gold sheet and for badges:

- `DEEP VALUE EARLY MOVER` — MoS > 25 AND score > 70 AND early ≥ 60
- `DEEP VALUE` — MoS > 25 AND score > 70
- `EARLY MOVER` — early ≥ 70 AND score > 55
- `AVOID / EXIT` — score < 38 OR (score < 45 AND MoS < −30)

---

## 7. FAIR VALUE ENGINE (`analysis/fair_value_engine.py`) — Session 19 guards

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
**Score adjustment** on `mos_pct`: > 40 = +12, > 25 = +8, > 10 = +4, < −30 = −10, < −15 = −5

### Session 19 DCF guards (critical — prevents absurd valuations)

Real bug observed: SBIN (beta 0.2) came out at ₹12,126 vs CMP ₹1,108 (10.9×) before guards. Fix applied in three layers:

- **WACC floor at 10%** — `wacc = max(gsec + beta × 5.5, 0.10)`. With gsec=6.8% + beta=0.2, raw WACC is 7.9%, giving `wacc − gt = 3.4%` and producing nonsense. Floored WACC avoids this. Indian equity discount rates below 10% are unrealistic.
- **M1 cap at 4× CMP** — even with WACC floored, aggressive growth assumptions can still blow up for outliers. Hard cap.
- **Composite CFV cap at 3× CMP** — belt-and-suspenders for when other models (Graham, EV/EBITDA) occasionally spike. 3× CMP = 200% MoS, already the extreme edge of plausible.

### DDM guard — strict

`0.1 < div_yield_pct < 15.0` only. Values outside this range indicate unit mismatch or no dividend. Do NOT relax.

---

## 8. EXCEL DASHBOARD (`reporting/excel_generator.py`) — 7 sheets

**Class:** `ExcelGeneratorV6(data, date_str, run_time=None, prev_scores=None, gap_days=None)`

### Sheets

1. **📊 Full Dashboard** — 100 stocks × ~120 columns
2. **⭐ Gold – Early Movers** — MOMENTUM (EE ≥ 70) OR VALUE (MoS ≥ 25% AND Score ≥ 70)
3. **📊 Trade Summary** — Entry / SL / T1 / T2 / T3 / R:R for Gold stocks
4. **🔔 Alert Log** — daily score changes, 8-way Action Required logic
5. **📱 Delivery Preview** — WhatsApp + Email text preview
6. **📖 Glossary** — 80+ column definitions
7. **💡 Tooltip Reference** — Session 16 cell/group tooltips (`reporting/tooltip_formatter.py`)

### Key design constants

- `NAVY = "1E293B"`, `WHITE = "FFFFFF"`, `LG = "F8FAFC"`
- `VERDICT_STYLES` — includes new `OVERVALUED` (bg `FED7AA` / text `7C2D12`)
- `FV_MODEL_KEYS` — M1–M7 + cfv/cfv_low/cfv_high → 0 values shown as `—`
- `NO_FREE_SOURCE_COLS` — red headers (Piotroski F /9, Altman Z, Rev CAGR etc.)
- `REQUIRED_COLS` — default values for all expected keys
- `GOLD_COLS` — 41-column definition for Gold sheet
- `GOLD_GROUPS` — section headers for Gold sheet

### `self.run_time`

Actual IST pipeline time (passed from master_funnel). Used in ALL time-sensitive headers. No hardcoded "20:30 IST" anywhere.

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

## 9. BACKFILL (`backfill_history.py`)

Auto-runs when `daily_prices` has fewer than 50,000 rows (fresh DB).

**Tables populated:**

- `daily_prices` — 365 days of OHLCV per symbol
- `symbol_master` — company names, sectors, cap categories
- `technical_indicators` — all indicators per symbol (latest date)
- `weekly_momentum` — 2w/4w/6w/8w changes + beta_90d
- `delivery_stats` — daily delivery %
- `fundamental_metrics` — PE, PB, ROE, EPS, etc. via yfinance
- `shareholding` — Promoter/FII/DII/Pledge via yfinance

### Supertrend formula (corrected — was inverted)

```python
sma20_st   = c.rolling(20).mean()
_buy_mask  = c > (sma20_st + 0.5 * atr14)   # BUY
_sell_mask = c < (sma20_st - 0.5 * atr14)   # SELL
# else NEUTRAL
```

Old formula had `c > st_up = BUY` which was inverted → always NEUTRAL. Fixed.

### Current Ratio / Quick Ratio — 6 bug fixes

- `_get_bs_row()` helper — keyword search without requiring "Total" prefix
- Excludes "non current", "noncurrent", "other" from CA/CL row matching
- Tries both `.NS` and `.BO` suffixes
- Tries quarterly `balance_sheet` as fallback
- Cap raised from 30 → **100 stocks per run**
- Quick Ratio now `(CA − Inventory) / CL` (was `CR × 0.75`)

---

## 10. INGESTION LAYER (Session 22 BSE resilience)

### Gate check — `ingestion/orchestrator.py::gate_check`

Six conditions, all must pass:

- **C1** Weekday (Mon–Fri)
- **C2** Not an NSE holiday (static `HOLIDAYS_2026` dict in the file)
- **C3** NSE bhav copy URL available (HEAD request to nsearchives CDN)
- **C4** BSE URL check — **IGNORED by master_funnel** (cloud/GitHub IPs can't reach the BSE HEAD endpoint; the `bse` pip package handles Akamai auth internally)
- **C5** Data integrity — run in `master_funnel` AFTER download (`check_data_integrity`)
- **C6** Minimum DB rows

### BSE downloads — `bse` pip package (singleton)

`master_funnel` opens one `BSE()` client at pipeline start, reuses it for bhav + delivery + SME, closes it in the `finally` block. `_parse_bse_df` standardises column names to match the NSE schema.

### Reconciler — `ingestion/reconciler.py::reconcile_exchanges` (Session 22)

Merges NSE + BSE bhav on `isin`. `final_symbol` prefers NSE ticker, `final_close` prefers NSE close.

**`DUAL_LISTED_ALLOWLIST`** — curated frozenset of 206 Nifty-100 + widely-traded mid-cap NSE tickers confirmed to trade on both exchanges. Used as a fallback: when BSE download fails (Cloudflare blocks, pip package missing, cloud IP issues), the reconciler would otherwise tag every stock as `NSE_ONLY`, including SBIN, TITAN, M&M. Instead it tags names from the allowlist as `DUAL_LISTED`. Non-listed stocks still default to `NSE_ONLY` — correct for ~95% of small/mid caps.

Maintenance: rarely changes. New IPOs usually list on both. Removals happen only on delisting.

---

## 11. AI LAYER (`ai/ai_analyst.py`)

- Uses `anthropic` SDK (migrated from deprecated `google-generativeai` in v7).
- `ANTHROPIC_API_KEY` required — raises `ValueError` at import time if missing.
- Master prompt loaded from `master_prompt/NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt` and passed as the `system` parameter. Batch stock data goes into the `user` message.
- `AI_BATCH_SIZE = 12` (10–15 per Section 0D).
- `FundamentalEngine` pre-computes Graham number, PEG, and CFV so the model uses our values instead of estimating.

---

## 12. INFRASTRUCTURE

### GitHub Actions (`.github/workflows/market_run.yml`)

- Cron: `0 0 * * 2-6` = 00:00 UTC = **05:30 IST**, Tue–Sat
- Expected delivery: 06:00–06:30 IST (30–60 min GitHub queue delay)
- Runner: `ubuntu-latest`, Python 3.11
- DB persistence: SQLite artifact `market-data-db`, 7-day retention, `overwrite: true`
- Auto-backfill: if `daily_prices < 50,000` rows → run `backfill_history.py 365`

### Required GitHub Secrets

```
ANTHROPIC_API_KEY        — Claude API for AI investor cards
SENDER_EMAIL             — Gmail sender address
SENDER_APP_PASSWORD      — Gmail 16-char App Password
USER_EMAIL_ID            — Recipient email
TWILIO_ACCOUNT_SID       — WhatsApp delivery (optional)
TWILIO_AUTH_TOKEN        — WhatsApp delivery (optional)
```

---

## 13. KEY CONSTANTS & THRESHOLDS

```python
# Screening
STAGE1_MIN_DELIVERY      = 40      # %
STAGE1_MIN_PRICE         = 10      # ₹
STAGE1_CIRCUIT_THRESHOLD = 19.9    # %
STAGE3_MAX_OVERRIDES     = 20
STAGE3_MIN_LARGE         = 20      # guaranteed large caps
STAGE3_MIN_MID           = 15      # guaranteed mid caps
STAGE3_MAX_SMALL_MICRO   = 65      # cap on small+micro

# Scoring
AVOID_BELOW              = 38
BUY_MIN                  = {LARGE:60, MID:63, SMALL:66, MICRO:70}
WATCH_MIN                = {LARGE:50, MID:53, SMALL:56, MICRO:60}
MOS_GATE_FOR_BUY         = -10     # normal
MOS_GATE_TECH_CONFIRMED  = -20     # if score≥70 + Supertrend=BUY + Stage 2
MAX_SPIKE_BONUS_STRONG   = 10      # fundamental ≥ 55
MAX_SPIKE_BONUS_WEAK     = 3       # fundamental < 55 (Session 24 cap)
EARLY_MOVER_BONUS_FLOOR  = 50      # early_entry_score ≥ 50 → +5
ANTI_TRIGGER_PENALTY     = -10

# Fair Value (Session 19 guards)
DCF_WACC_FLOOR           = 0.10    # 10% minimum WACC
DCF_M1_CAP_MULTIPLE      = 4       # cap M1 at 4× CMP
CFV_CAP_MULTIPLE         = 3       # cap composite CFV at 3× CMP
DDM_DIV_YIELD_MIN        = 0.1     # % (below = no dividend)
DDM_DIV_YIELD_MAX        = 15.0    # % (above = unit mismatch)
DCF_GROWTH_CAP           = 30      # % max growth assumption
DDM_DIV_GROWTH_CAP       = 6       # % max dividend growth
GSEC_RATE                = 6.0     # % 10Y benchmark
EQUITY_PREMIUM           = 5.5     # % added to Gsec for req. return

# Backfill
CR_SECOND_PASS_CAP       = 100     # stocks per run for balance_sheet fetch
SUPERTREND_ATR_MULT      = 0.5     # SMA20 ± 0.5×ATR14
BACKFILL_DAYS            = 365

# Priority ranker
VOL_SPIKE_CAP            = 5       # ×average (prevents ETF arb domination)
PRIORITY_W_VOL           = 25
PRIORITY_W_QUALITY       = 30
PRIORITY_W_DELIVERY      = 20
PRIORITY_W_CAP           = 15
PRIORITY_W_TURNOVER      = 10

# Div yield normalisation (master_funnel)
DIV_YIELD_BAD_THRESHOLD  = 12      # >12% = unit mismatch (store as pct/100)

# AI batching
AI_BATCH_SIZE            = 12      # stocks per Claude API call
```

---

## 14. SESSION HISTORY — fixes applied

### Sessions 1–8 (v7 era, retained)

Core data fixes, Excel + Alert Log, Early Detection fixes, Supertrend/Horizon/Risk, BS Health detailed flags, Current Ratio 3-bug fix, Cron + Glossary, ETF filter + Scoring improvements + Ghost keys. See earlier revisions of this file for details.

### Session 14 — Piotroski F wire-up

- `FundamentalEngine.calculate_piotroski_f_score()` — canonical 9 criteria (Profitability 4 + Leverage/Liquidity 3 + Efficiency 2)
- Exported and called during Section 6 scoring loop
- Realistic distribution on free data: 4–8 range (full 9 needs YoY comparisons)

### Session 15 — Forensics numerics + anti-trigger guard

- `forensics_engine.calculate_altman_z()` — numeric float (was missing-data crash)
- `forensics_engine.calculate_beneish_m()` — returns numeric M-score (was string "MANIPULATION_RISK"/"CLEAN")
- Section 3H anti-trigger enforcement when displaying verdicts

### Session 16 — Tooltip system

- `reporting/tooltip_formatter.py` with `TIPS`, `apply_tooltips`, `apply_group_tooltips`, `build_reference_sheet`
- Cell-level ⓘ indicator + hover definition for every metric
- New Tooltip Reference sheet (7th Excel sheet)

### Session 19 — DCF guards

- WACC floor at 10% (was producing ₹12k fair values on low-beta stocks)
- M1 DCF capped at 4× CMP
- Composite CFV capped at 3× CMP (200% MoS ceiling)

### Session 22 — BSE resilience

- Migrated to `bse` pip package (singleton client, one-open-many-uses)
- Direct-URL BSE harvester calls removed
- Gate check C4 ignored (cloud IPs can't HEAD the BSE URL)
- `DUAL_LISTED_ALLOWLIST` of 206 Nifty-100 / mid-cap names — tagged as `DUAL_LISTED` when BSE bhav is empty

### Session 23 — Gold archetype documentation

- Gold sheet admits two archetypes: MOMENTUM (high Early Entry) and VALUE (low EE but high Score + MoS + clean safety)
- Tooltip copy updated to explain low-EE-on-Gold is correct, not a bug

### Session 24 — Scoring polish (current)

- **Sentiment informedness check** — redistribute 10% weight when no paid/AI signals fired
- **Spike bonus gated** on fundamental quality — full +10 only if fund ≥ 55
- **Confidence dots** — HIGH ●●● / MEDIUM ●●○ / LOW ●○○
- **OVERVALUED verdict** — distinct from WATCHLIST, soft orange styling
- Stage-2 inflation fix in master_funnel (upstream of scoring)

---

## 15. KNOWN ISSUES & LIMITATIONS

| Column | Limitation | Status |
|---|---|---|
| Current Ratio / Quick Ratio | yfinance missing for ~25–40% of Indian stocks | Fixed 2nd-pass balance_sheet; ~60–75% coverage |
| Piotroski F-Score | No free source for true 9-point YoY comparisons | Proxy from available data (Session 14 wire-up) |
| PAT CAGR 3Y / Rev CAGR 3Y | Not in yfinance for Indian stocks | Red headers (no free source) |
| Alert Log Prev Score | Blank on first run | Populates from run 2 onwards |
| Smart Money FII/Promoter | QoQ depends on shareholding backfill | Improves after 2–3 runs |
| Altman Z / Beneish M | Require paid balance-sheet feed | Display `—` when inputs missing |
| BSE SME delivery % | Not available from BSE API | NSE delivery used as primary |
| BSE downloads from cloud | Akamai blocks cloud IPs | Handled via `bse` pip pkg + allowlist fallback |

---

## 16. PENDING / NEXT ACTIONS

- [ ] Add retry logic for yfinance (currently single attempt per symbol)
- [ ] Add Screener.in scraping for PAT CAGR / Rev CAGR data
- [ ] WhatsApp bot: end-to-end test of ngrok + Twilio integration
- [ ] Reduce AI batch size 12 → 8 if response truncation observed
- [ ] FCF-yield based FV model (M8) for capital-light businesses
- [ ] PAT CAGR in fundamental score (needs data source)
- [ ] Verify ETFs = 0 in output after pipeline run
- [ ] Expand DUAL_LISTED_ALLOWLIST as new IPOs confirm dual-listing

---

## 17. QUICK REFERENCE — KEY FUNCTION LOCATIONS

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
| Technical score | `master_funnel.py` | Section 6 scoring loop |
| Fundamental score | `master_funnel.py` | Section 6 scoring loop |
| Safety score | `master_funnel.py` | Section 6 scoring loop |
| Sentiment score | `master_funnel.py` | Section 6 scoring loop |
| Ghost-key derivation | `master_funnel.py` | Before storm-score call |
| Sector Stage (2nd pass) | `master_funnel.py` | After Section 5 tech enrichment |
| BS Health (2nd pass) | `master_funnel.py` | Before composite score |
| Composite score + verdict + confidence | `analysis/scoring_engine.py` | `calculate_composite_score` + `_get_verdict_with_confidence` |
| Storm score | `analysis/scoring_engine.py` | `calculate_storm_score` |
| Fair Value engine | `analysis/fair_value_engine.py` | `calculate_all_models` + `get_composite_fair_value` |
| DCF guards (WACC/M1/CFV caps) | `analysis/fair_value_engine.py` | Session 19 |
| Piotroski F-Score | `analysis/fundamental_engine.py` | `calculate_piotroski_f_score` |
| Altman Z / Beneish M | `analysis/forensics_engine.py` | `calculate_altman_z` / `calculate_beneish_m` |
| Horizon + Risk Level | `master_funnel.py` | AFTER storm score |
| Blank name/sector filter | `master_funnel.py` | Before Excel generation |
| Excel generator (7 sheets) | `reporting/excel_generator.py` | `class ExcelGeneratorV6` |
| Tooltip system | `reporting/tooltip_formatter.py` | `TIPS`, `apply_tooltips`, `build_reference_sheet` |
| Alert Log | `reporting/excel_generator.py` | `_alert_log()` |
| AI investor cards | `ai/ai_analyst.py` | `get_ai_analysis` (Anthropic) |
| Email delivery | `reporting/email_service.py` | `send_analysis_email` |
| Supertrend formula | `backfill_history.py` | SMA+ATR approach |
| CR/QR 2nd pass | `backfill_history.py` | `_get_bs_row` + 2nd-pass block |
| 90-day DB queue | `database/db_maintenance.py` | `enforce_circular_queue` |

---

## 18. IMPORTANT DO-NOT-TOUCH RULES

1. **Never add filters based on `company_name` or `sector` in Stage 1** — these fields are empty at Stage 1 time (FM enrichment hasn't run yet). Add such filters only after Section 5.

2. **Never compute `horizon` or `risk_level` before `calculate_composite_score()`** — `verdict` doesn't exist before that call.

3. **Never recompute `Sector Stage` before technical data loads** — RSI/MACD/Supertrend are loaded at Section 5. Earlier computation uses 0/NEUTRAL defaults.

4. **Never change `FV_MODEL_KEYS`** — controls which zero values get shown as `—` in Excel.

5. **DDM guard: `0.1 < div_yield_pct < 15.0`** — values outside this range indicate unit mismatch or no dividend. Do not relax.

6. **DCF guards are non-negotiable** — WACC floor 10%, M1 cap 4× CMP, composite CFV cap 3× CMP. These prevent the SBIN-style ₹12k fair-value bug.

7. **`run_time` not hardcoded times** — all time-sensitive strings in excel_generator use `self.run_time`.

8. **Backfill runs on GitHub Actions** — yfinance rate limits apply. CR second pass capped at 100/run for safety. Do not raise significantly.

9. **Load `latest_analysis_results` BEFORE saving today's scores** — otherwise Alert Log's Score Δ is always 0. Two-step pattern enforced in master_funnel Section 10.

10. **Gate check C4 (BSE URL HEAD) is intentionally ignored by master_funnel** — cloud IPs can't reach it. BSE always routes through the `bse` pip package. Don't re-enable the C4 halt.

11. **Do not quote song lyrics, poems, or paid articles in AI cards** — the master prompt enforces paraphrase-only output.

12. **OVERVALUED is NOT the same as WATCHLIST** — keep verdict categories distinct in both scoring and Excel styling.

---

*Last updated: April 2026 · v10.0 · Maintained by: Rajkumar + Claude (Anthropic) working sessions*
