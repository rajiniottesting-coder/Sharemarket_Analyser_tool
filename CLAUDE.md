# CLAUDE.md — NSE/BSE Stock Analyser Tool
## AI Context File · Version 7.0 · April 2026

This file provides complete project context for Claude (or any AI assistant) to understand, analyse, and fix this codebase without needing additional explanation.

---

## 1. PROJECT PURPOSE

A fully automated, cloud-hosted daily pipeline that:
1. Downloads NSE + BSE market data every trading morning
2. Screens 5,000+ stocks through a 3-stage funnel → 100 candidates
3. Runs deep fundamental + technical + AI analysis on those 100
4. Delivers a colour-coded Excel research dashboard by **6:00–6:30 AM IST**
5. Sends a WhatsApp summary of top picks via Twilio

**Single user tool.** Pre-market preparation. Zero manual intervention on trading days.

---

## 2. FILE REGISTRY (current state)

| File | Lines | Hash (MD5 first 10) | Role |
|------|-------|---------------------|------|
| `master_funnel.py` | 1902 | `ef3e953f0e` | Pipeline orchestrator — runs all sections 0–13 |
| `backfill_history.py` | 1574 | `ed346854cb` | 365-day historical data builder |
| `excel_generator.py` | 1158 | `183c79e7f0` | Excel dashboard generator (6 sheets) |
| `fair_value_engine.py` | 178 | `139429b185` | 7 Fair Value models (DCF, Graham, PE, PB, EV, DDM, PEG) |
| `scoring_engine.py` | 145 | `3b033358e7` | Composite scoring + verdict logic |
| `priority_ranker.py` | 272 | `26bd6c41ba` | Stage 3 ranker + cap diversification |
| `pre_screener.py` | 271 | `a1975023a9` | Stage 1 ETF filter + Stage 2 quality score |
| `orchestrator.py` | ~200 | — | Gate check (6 pre-conditions) |
| `harvester.py` | ~300 | — | NSE + BSE bhav copy downloader |
| `data_bridge.py` | ~800 | — | DB consolidation + all DB functions |
| `v7_analysis_engine.py` | ~250 | — | Sections 3A–3H analytical overlays |
| `spike_screener.py` | 51 | `0f0443f851` | 6-trigger spike score |
| `early_detection_engine.py` | 36 | `7e30a2d6ea` | Early entry signals |
| `rotation_engine.py` | 28 | `3bee829791` | Sector rotation stage (4 stages) |
| `bs_engine.py` | 71 | — | Balance sheet health audit |
| `technical_engine.py` | 54 | — | Technical indicator computation |
| `fundamental_engine.py` | ~150 | — | Fundamental ratio derivations |
| `ai_analyst.py` | ~200 | — | Claude API batch analysis |
| `email_service.py` | ~100 | — | Gmail SMTP delivery |
| `whatsapp_gateway.py` | ~80 | — | Twilio Flask webhook |
| `ownership_tracker.py` | ~100 | — | Promoter/FII ownership trends |
| `forensics_engine.py` | ~100 | — | Beneish M + Altman Z scores |
| `intel_fetcher.py` | ~100 | — | News + market intelligence |
| `market_context.py` | ~80 | — | Market-wide context |
| `db_maintenance.py` | ~60 | — | 90-day rolling queue |
| `.github/workflows/market_run.yml` | — | — | Cron: `0 0 * * 2-6` (00:00 UTC = 05:30 IST) |

---

## 3. DATABASE SCHEMA (SQLite — market_data.db, ~400 MB)

| Table | Contents | Size |
|-------|----------|------|
| `daily_prices` | OHLCV + delivery % + 52w high/low + day change % | 365d × 5,000 syms |
| `symbol_master` | Company name, sector, cap category, ISIN, BSE code | ~5,000 symbols |
| `fundamental_metrics` | PE, PB, EPS, Div Yield, Beta, ROE, D/E, Margins, CAGR | ~5,000 symbols |
| `shareholding` | Promoter %, FII %, DII %, Pledge % + QoQ changes | ~3,000 symbols |
| `technical_indicators` | RSI14, MACD, Supertrend, ADX, Stoch K, MFI, OBV, VWAP | ~5,000 symbols |
| `weekly_momentum` | 2w / 4w / 6w / 8w price change % | ~5,000 symbols |
| `delivery_stats` | Daily delivery % per symbol | 365 days |
| `fo_participant_data` | FII + DII + Prop net buy/sell ₹ | Latest 5 rows |
| `bulk_deals` | Institutional block trades | Rolling window |
| `insider_trades` | SEBI insider disclosures | Rolling window |
| `latest_analysis_results` | Composite score + verdict + AI card per symbol | ~100 symbols |
| `alert_history` | Score changes + alerts triggered | Rolling 90 days |
| `watchlist` | Personal watchlist overrides | User-defined |
| `market_holidays` | NSE holiday calendar 2026 | Static |

**Key DB functions in `data_bridge.py`:**
- `save_to_database(df, table, conn)` — upsert with conflict resolution
- `get_symbol_history(symbol, days)` — returns OHLCV DataFrame
- `get_20d_avg_vol(symbol)` — 20-day average volume
- `load_latest_analysis_results()` — for Alert Log prev scores
- `initialize_v7_tables(conn)` — schema creation + migration

---

## 4. PIPELINE EXECUTION ORDER (master_funnel.py)

```
Section 12B  Gate check (orchestrator.py) — 6 conditions must pass
Section 1    Harvest: NSE Bhav + Delivery + BSE Bhav + F&O + Bulk Deals + Insider
Section 1.2  DB sync — consolidate 5,150 records → daily_prices
Section 0    Pre-screening funnel:
               Stage 1 (pre_screener.py)  : 5,150 → ~600
               Stage 2 (pre_screener.py)  : ~600 → ~400
               Stage 3 (priority_ranker.py): ~400 → 100
Section 3    For each of 100 stocks:
               3A: Valuation ratios (EY, PE tag, EV/EBITDA)
               3B/3D: Forensics (Beneish, Altman, CFO/PAT)
               3E: Capital allocation (ROCE)
               3F: Ownership trends (Promoter/FII QoQ)
               3G: Growth quality (CAGR tiers)
               3H: Anti-trigger guard (pledge/Beneish/Altman/CFO)
               3I: Early entry score — DEFERRED to Section 6
               3J/3K: Bulk deal sentiment + insider buying
               3L: Sector rotation stage — PLACEHOLDER (recomputed after tech loads)
Section 4    Balance Sheet Health — FIRST PASS (pre-FM data, mostly placeholder)
Section 4B   NSE fundamentals refresh via yfinance
Section 5    DB enrichment: technicals + fundamentals + weekly momentum
             → After technical data loads: Sector Stage RECOMPUTED HERE
             → Ghost key derivation: fcf_positive_4q, promoter_q_increase,
               fii_buy_3q, rev_growth_yoy, fii_3q_trend, promoter_buying_30d
Section 5B   Fair Value Engine — 7 models per stock
Section 6    SCORING LOOP for each stock:
               → Technical score (RSI/MACD/ST/ADX/MFI/Stoch)
               → Fundamental score (PE/ROE/DE/CR/GM/NM/EY/Promoter/PAT_YoY/Rev_YoY/FCF_Yield)
               → Safety score (Pledge/Beta/DE/FCF/BS_Health)
               → Sentiment score (FII trend/Smart Money/Insider)
               → Ghost key injection before storm score
               → Composite score (scoring_engine.py)
               → Storm score
               → Horizon + Risk Level  ← computed AFTER verdict
               → Sector Stage (second pass using real RSI/MACD/ST)
               → BS Health re-evaluation  ← SECOND PASS with real FM data
               → Spike Score (spike_screener.py)
               → Smart Money signals
               → Early Entry Score (Section 3I — runs here with real technicals)
               → F-Score proxy (9-point from available data)
               → Price targets (T1/T2/T3, entry range, stop loss)
               → Blank name+sector filter (removes ETFs that slipped through)
Section 7/8  AI investor cards (ai_analyst.py — Claude API, batches of 12)
Section 9/10 Excel dashboard (excel_generator.py)
Section 12   Email delivery (email_service.py)
Section 13   DB maintenance (db_maintenance.py)
```

**CRITICAL ORDER RULES:**
- Technical data (RSI/MACD/Supertrend) loads at Section 5 (L~1100). Any code using these must run AFTER that line.
- `composite_score` and `verdict` set by `calculate_composite_score()`. `horizon` and `risk_level` must run AFTER this call.
- BS Health runs twice: first pass at Section 4 (pre-enrichment, mostly HEALTHY), second pass after Section 5 (real data).
- `company_name` and `sector` are only available after Section 4B/5 FM enrichment — never available at Stage 1.

---

## 5. SCREENING FUNNEL DETAILS

### Stage 1 — `pre_screener.py` (Section 0A)
Filters applied in order:
1. **sc_group exclusion**: EF, MF, IF, IR, BE → dropped
2. **ETF keyword filter** (~67 patterns): GOLD1, SILVERAG, QNIFTY, MSCIINDIA, MASPTOP50, BANKBEES, ITBEES, NIFTYBEES, GOLDBEES, PSUBNKBEES, ends-ETF, ends-BEES, ends-INDEX, etc.
3. **Volume**: must be > 0
4. **Circuit breaker**: abs price change ≥ 19.9% → dropped
5. **Penny stock**: close < ₹10 → dropped
6. **Suspended**: status=SUSPENDED → dropped
7. **Delivery**: delivery_pct < 40% → dropped (unless watchlist_override)
8. **BSE SME**: turnover < ₹5L → dropped

**NOTE:** `company_name` and `sector` are NOT available at Stage 1. Blank name+sector filter runs in master_funnel AFTER FM enrichment (before Excel generation).

### Stage 2 — `pre_screener.py` (Section 0B)
Quality score 0–35: delivery % + turnover + vol spike + exchange listing + price zone

### Stage 3 — `priority_ranker.py` (Section 0C)
```
Priority Score = (vol_spike/5 × 25) + (stage2/35 × 30) + (delivery/100 × 20)
              + (cap_bonus × 15) + (turnover_bonus × 10)
```
Cap diversification: LARGE ≥ 20, MID ≥ 15, SMALL+MICRO ≤ 65
Technical alignment bonus (in master_funnel after tech loads):
- ST=BUY + MACD=BUY → +8
- One BUY → +3
- Both SELL → -5

---

## 6. SCORING SYSTEM

### Composite Score (0–100)
```
= Fundamental×0.35 + Technical×0.30 + EarlyEntry×0.15 + Sentiment×0.10 + Safety×0.10
+ MoS adjustment (-10 to +12)
+ Spike bonus (+2 per trigger, max +10)
+ Early Mover bonus (+5 if early_entry_score ≥ 70)
- Anti-trigger penalty (-10 if risk_flag_active)
```

### Verdict Thresholds (scoring_engine.py — CAP_THRESHOLDS)
```python
CAP_THRESHOLDS = {
    "LARGE": (60, 50),   # (BUY_min, WATCHLIST_min)
    "MID":   (63, 53),
    "SMALL": (66, 56),
    "MICRO": (70, 60),
}
AVOID_BELOW = 38   # Universal floor
```
MoS gate: if MoS < -10% (CMP more than 10% above fair value) → capped at WATCHLIST even if score qualifies for BUY.

### Fundamental Score inputs
PE, ROE, D/E, Current Ratio, Gross Margin, Net Margin, Earnings Yield, Promoter %,
**PAT YoY** (+8/+4/+2/-7), **Rev YoY** (+5/+3/+1/-4), **FCF Yield** (+6/+3/-5)

### Technical Score inputs
RSI, ADX, MACD, Supertrend, VWAP, OBV,
**Stochastic K** (oversold zone 20–40 = +5), **MFI** (>60 = +4, <30 = -3)

### Safety Score inputs
Pledge %, Beta, D/E,
**FCF** (negative = -8, positive = +3),
**BS Health status** (ALERT = -15, WATCH = -5, HEALTHY+FCF = +3)

### Sentiment Score inputs
fii_3q_trend (derived from fii_qoq: >1% = UP, <-1% = DOWN),
smart_money_sentiment, insider_buy_alert

### Ghost Keys (derived before storm/sentiment scoring)
These were previously never populated — now derived in master_funnel before storm score:
- `fcf_positive_4q` ← `fcf > 0`
- `promoter_q_increase` ← `promoter_qoq > 0.3`
- `fii_buy_3q` ← `fii_qoq > 0.3`
- `rev_growth_yoy` ← `rev_yoy`
- `fii_3q_trend` ← derived from `fii_qoq`
- `promoter_buying_30d` ← `promoter_qoq > 0.5`

---

## 7. FAIR VALUE ENGINE (fair_value_engine.py)

7 models, weighted and normalised to active (non-zero) models only:

| Model | Weight | Formula | Condition |
|-------|--------|---------|-----------|
| M1 DCF | 30% | EPS × (1+g)^n / r | Positive EPS |
| M2 Graham | 15% | √(22.5 × EPS × BVPS) | EPS > 0, BVPS > 0 |
| M3 PE | 20% | EPS × sector_median_PE | Sector PE map |
| M4 PB | 15% | BVPS × sector_median_PB | PB available |
| M5 EV/EBITDA | 10% | CMP × (sector_EV_mult / EV_EBITDA) | EV/EBITDA available |
| M6 DDM | 5% | D1 / (r−g), growth capped 6% | Div yield 0.1%–15% ONLY |
| M7 PEG | 5% | EPS × min(growth, 30%) | Growth available |

MoS = (CFV − CMP) / CMP × 100
MoS score adjustment: >40%=+12, >25%=+8, >10%=+4, <-30%=-10, <-15%=-5

---

## 8. EXCEL DASHBOARD (excel_generator.py)

**Class:** `ExcelGeneratorV6(data, date_str, run_time=None, prev_scores=None)`

**6 sheets:**
1. `📊 Full Dashboard` — 99 stocks × 119 columns (all analysis)
2. `⭐ Gold – Early Movers` — Early Entry ≥70 OR (MoS ≥25% AND Score ≥70)
3. `📊 Trade Summary` — Entry/SL/T1/T2/T3/R:R for Gold stocks
4. `🔔 Alert Log` — Daily score changes, 8-way Action Required logic
5. `📱 Delivery Preview` — WhatsApp + Email text preview
6. `📖 Glossary` — 77 column definitions

**Key design constants:**
- `NAVY = "1E293B"`, `WHITE = "FFFFFF"`, `LG = "F8FAFC"`
- `FV_MODEL_KEYS`: M1–M7 + cfv/cfv_low/cfv_high → 0 values shown as "—"
- `NO_FREE_SOURCE_COLS`: columns shown with red headers (Piotroski F /9, Altman Z, Rev CAGR etc.)
- `REQUIRED_COLS`: default values for all expected keys
- `GOLD_COLS`: 41-column definition for Gold sheet
- `GOLD_GROUPS`: section headers for Gold sheet

**`self.run_time`**: actual IST pipeline time (passed from master_funnel). Used in ALL time-sensitive headers. No hardcoded "20:30 IST" anywhere.

**Alert Log 8-way action logic:**
- Score < 30 → `REVIEW FOR EXIT`
- BUY + MoS > 10% + Score ≥ 65 → `CONSIDER ENTRY`
- BUY + MoS ≤ 0 → `BUY BUT OVERVALUED — WAIT`
- BUY (other) → `MONITOR FOR ENTRY`
- Vol spike ≥ 3× → `VOLUME ALERT — INVESTIGATE`
- Early Entry ≥ 70 → `EARLY MOVER — ACCUMULATE`
- Score Δ ≥ +3 → `SCORE IMPROVING — WATCH`
- Score Δ ≤ -3 → `SCORE DECLINING — CAUTION`
- Default → `MONITOR CLOSELY`

---

## 9. BACKFILL (backfill_history.py)

Runs automatically when `daily_prices` has fewer than 50,000 rows (fresh DB).

**Tables populated:**
- `daily_prices` — 365 days of OHLCV per symbol
- `symbol_master` — company names, sectors, cap categories
- `technical_indicators` — all indicators per symbol (latest date)
- `weekly_momentum` — 2w/4w/6w/8w changes
- `delivery_stats` — daily delivery %
- `fundamental_metrics` — PE, PB, ROE, EPS, etc. via yfinance
- `shareholding` — Promoter/FII/DII/Pledge via yfinance

**Supertrend formula (CORRECT — fixed):**
```python
sma20_st   = c.rolling(20).mean()
_buy_mask  = c > (sma20_st + 0.5 * atr14)   # BUY
_sell_mask = c < (sma20_st - 0.5 * atr14)   # SELL
# else NEUTRAL
```
Old formula was INVERTED (c > st_up = BUY) → always NEUTRAL. Fixed.

**Current Ratio fix (3 bugs fixed):**
- `_get_bs_row()` helper: keyword search without requiring "Total" prefix
- Excludes "non current", "noncurrent", "other" from CA/CL row matching
- Tries both `.NS` and `.BO` suffixes
- Tries quarterly balance_sheet as fallback
- Cap: 100 stocks per run (was 30)
- Quick Ratio: `(CA - Inventory) / CL` (was `CR × 0.75`)

---

## 10. INFRASTRUCTURE

**GitHub Actions (`market_run.yml`):**
- Cron: `0 0 * * 2-6` = 00:00 UTC = 05:30 IST
- Expected delivery: 06:00–06:30 IST (30–60 min GitHub queue delay)
- Runner: ubuntu-latest, Python 3.11
- DB persistence: SQLite artifact 'market-data-db' (7-day retention, overwrite=true)
- Auto-backfill: if daily_prices < 50,000 rows → run `backfill_history.py 365`

**Required GitHub Secrets:**
```
ANTHROPIC_API_KEY        — Claude API for AI investor cards
SENDER_EMAIL             — Gmail sender address
SENDER_APP_PASSWORD      — Gmail app password
USER_EMAIL_ID            — Recipient email
TWILIO_ACCOUNT_SID       — WhatsApp delivery
TWILIO_AUTH_TOKEN        — WhatsApp delivery
```

---

## 11. KEY CONSTANTS & THRESHOLDS

```python
# Screening
STAGE1_MIN_DELIVERY     = 40      # %
STAGE1_MIN_PRICE        = 10      # ₹
STAGE1_CIRCUIT_THRESHOLD= 19.9    # %
STAGE3_MAX_OVERRIDES    = 20
STAGE3_MIN_LARGE        = 20      # guaranteed large caps
STAGE3_MIN_MID          = 15      # guaranteed mid caps
STAGE3_MAX_SMALL_MICRO  = 65      # cap on small+micro

# Scoring
AVOID_BELOW             = 38
BUY_MIN   = {LARGE:60, MID:63, SMALL:66, MICRO:70}
WATCH_MIN = {LARGE:50, MID:53, SMALL:56, MICRO:60}
MOS_GATE_FOR_BUY        = -10     # MoS below this → capped at WATCHLIST
MAX_SPIKE_BONUS         = 10      # +2 per trigger

# Fair Value
DDM_DIV_YIELD_MIN       = 0.1     # % (below this = no dividend)
DDM_DIV_YIELD_MAX       = 15.0    # % (above this = unit mismatch)
DCF_GROWTH_CAP          = 30      # % max growth assumption
DDM_DIV_GROWTH_CAP      = 6       # % max dividend growth
GSEC_RATE               = 6.0     # % 10Y benchmark
EQUITY_PREMIUM          = 4.5     # % added to Gsec for req. return

# Backfill
CR_SECOND_PASS_CAP      = 100     # stocks per run for balance_sheet fetch
SUPERTREND_ATR_MULT     = 0.5     # SMA20 ± 0.5×ATR14
BACKFILL_DAYS           = 365

# Priority ranker
VOL_SPIKE_CAP           = 5       # ×average (prevents ETF arb domination)
PRIORITY_W_VOL          = 25
PRIORITY_W_QUALITY      = 30
PRIORITY_W_DELIVERY     = 20
PRIORITY_W_CAP          = 15
PRIORITY_W_TURNOVER     = 10

# Div yield normalisation (master_funnel)
DIV_YIELD_BAD_THRESHOLD = 12      # >12% = unit mismatch (store as pct/100)

# AI batching
AI_BATCH_SIZE           = 12      # stocks per Claude API call
```

---

## 12. ALL FIXES APPLIED (session history)

### Session 1 — Core data fixes
- `total_debt` direct assignment (was blocked by setdefault 0)
- `FCF` direct assignment with 4-tier derivation
- `div_yield` normalisation threshold >12 (bad unit detection)
- DB migration at startup (ALTER TABLE IF NOT EXISTS)
- `PEG` 4-tier fallback (PE/PAT → PE/Rev → PE/SustainableGrowth)
- `P/CF` 4-tier fallback
- `ROE/ROA/ROCE` derivation from available P/E, P/B, margins

### Session 2 — Excel + Alert Log
- `run_time`: captures actual IST time, replaces ALL hardcoded "20:30 IST" (8 locations)
- Alert Log `Prev Score` + `Score Δ`: loads `latest_analysis_results` before saving new results
- Alert Log `Action Required`: 8-way logic (was always "CONSIDER ENTRY")
- FV model zeros shown as "—" (FV_MODEL_KEYS check)
- Glossary `data_type="s"` fix (cells starting with = treated as formula)

### Session 3 — Early Detection fixes
- `Sector Stage`: recomputed AFTER technical data loads (was at L427, tech loads at L1100)
- `Smart Money`: uses FII QoQ + Promoter QoQ + delivery% + RSI zone
- `Early Signals`: added DEEP VALUE, VALUE OPPORTUNITY, FII/PROMOTER ACCUMULATION
- `BS Health`: second re-evaluation pass after FM enrichment (first pass had no data)

### Session 4 — Supertrend + Horizon + Risk
- `Supertrend` formula: corrected from inverted (c > st_up = BUY) to SMA+ATR
- `Time Horizon`: now computed — moved to AFTER verdict/scoring (was before, causing all POSITIONAL)
- `Risk Level`: now computed — uses cap/D/E/beta/pledge/BS (was always MEDIUM)
- `F-Score /9`: proxy 9-point score from available data (was always "—")

### Session 5 — BS Health detailed flags
- `NET CASH COMPANY`, `ZERO DEBT`, `HIGH D/E`, `LOW LIQUIDITY`, `NEGATIVE FCF`, `LOW CASH COVER`, `HIGH PLEDGE`, `LEVERAGED+NEG FCF`
- Status: HEALTHY / WATCH / ALERT with meaningful notes

### Session 6 — Current Ratio 3-bug fix
- Row name mismatch: `_get_bs_row()` with keyword matching (no "Total" prefix required)
- Cap: 30 → 100 stocks per backfill run
- Quick Ratio: `CR × 0.75` → `(CA − Inventory) / CL`
- Tries both `.NS` and `.BO` suffixes
- Tries quarterly balance_sheet as fallback

### Session 7 — Cron + Glossary
- Cron: `30 0 * * 2-6` → `0 0 * * 2-6` (05:30 IST, arrives ~06:00)
- Glossary: 17 missing columns added (F-Score /9, Spike /6, Storm /10, CMP, 52W High/Low, Quick Ratio, Upside %, Chg% 2/4/6/8-Wk, Horizon, P/E, PEG, D/E, Pattern)

### Session 8 — ETF filter + Scoring improvements + Ghost keys
- ETF keyword list: expanded to ~67 patterns (GOLD1, SILVERAG, QNIFTY, MSCIINDIA, MASPTOP50, BANKBEES, ITBEES, ends-INDEX etc.)
- Blank name+sector filter: moved from Stage 1 (crash!) to AFTER FM enrichment
- Hardcoded "20:30 IST": 2 remaining occurrences fixed (Gold strip + Delivery Preview)
- Ghost keys: all 6 derived before storm/sentiment scoring
- Fundamental score: +PAT YoY, +Rev YoY, +FCF Yield
- Safety score: +FCF, +BS Health status
- Technical score: +Stochastic K, +MFI
- Priority ranker: +Supertrend/MACD alignment bonus (+8/+3/-5)

---

## 13. KNOWN ISSUES & LIMITATIONS

| Column | Limitation | Status |
|--------|-----------|--------|
| Current Ratio / Quick Ratio | yfinance missing for ~25-40% of Indian stocks | Fixed balance_sheet 2nd pass; coverage ~60-75% |
| Piotroski F-Score | No free source for true 9-point score | Proxy computed from available data |
| PAT CAGR 3Y / Rev CAGR 3Y | Not in yfinance for Indian stocks | Red headers (no free source) |
| Alert Log Prev Score | Blank on first run | Populates from run 2 onwards |
| Smart Money FII/Promoter | QoQ data depends on shareholding backfill | Improves after 2-3 runs |
| Supertrend in existing DB rows | Old inverted formula | Next backfill recalculates from 365d history |
| BSE SME delivery | Not available from BSE API | NSE delivery used as primary |

---

## 14. PENDING / NEXT ACTIONS

- [ ] Add retry logic for yfinance (currently single attempt per symbol)
- [ ] Add Screener.in scraping for PAT CAGR / Rev CAGR data
- [ ] WhatsApp bot: test ngrok + Twilio integration end-to-end
- [ ] Reduce AI batch size 12→8 (less response truncation risk)
- [ ] FCF-yield based FV model (M8) for capital-light businesses
- [ ] PAT CAGR in fundamental score (needs data source)
- [ ] Verify ETFs = 0 in output after pipeline run

---

## 15. QUICK REFERENCE — KEY FUNCTION LOCATIONS

| Function / Block | File | Approx Line |
|-----------------|------|-------------|
| Gate check | `orchestrator.py` | `gate_check()` |
| Stage 1 ETF filter | `pre_screener.py` | L38–L62 |
| Stage 1 V0B blank filter | REMOVED — was causing Stage 1 to drop all 5150 stocks | — |
| Stage 3 ranker | `priority_ranker.py` | `get_top_100_candidates()` |
| Technical score | `master_funnel.py` | L~1216 |
| Fundamental score | `master_funnel.py` | L~1247 |
| Safety score | `master_funnel.py` | L~1303 |
| Sentiment score | `master_funnel.py` | L~1317 |
| Ghost key derivation | `master_funnel.py` | Before storm score call |
| Sector Stage (2nd pass) | `master_funnel.py` | After L1115 (after tech enrichment) |
| BS Health (2nd pass) | `master_funnel.py` | Before composite score (L~1347) |
| Composite score | `master_funnel.py` | `scoring.calculate_composite_score()` |
| Horizon + Risk Level | `master_funnel.py` | AFTER storm score (L~1479) |
| Blank name/sector filter | `master_funnel.py` | Before Excel generation |
| Fair Value Engine | `fair_value_engine.py` | `FairValueEngine.calculate_all_models()` |
| ExcelGeneratorV6 | `excel_generator.py` | `class ExcelGeneratorV6` |
| Alert Log | `excel_generator.py` | `_alert_log()` |
| Supertrend formula | `backfill_history.py` | L~733 (SMA+ATR approach) |
| CR 2nd pass | `backfill_history.py` | `_get_bs_row()` helper + second pass block |

---

## 16. IMPORTANT DO-NOT-TOUCH RULES

1. **Never add filters based on `company_name` or `sector` in Stage 1** — these fields are empty at Stage 1 time (FM enrichment hasn't run yet). Add such filters only after Section 5 FM enrichment.

2. **Never compute `horizon` or `risk_level` before `calculate_composite_score()`** — `verdict` doesn't exist yet before that call.

3. **Never recompute `Sector Stage` before technical data loads** — RSI/MACD/Supertrend are loaded at Section 5 (~L1100). Any earlier computation uses 0/NEUTRAL defaults.

4. **Never change `FV_MODEL_KEYS`** — this set controls which zero values get shown as "—" in the Excel.

5. **DDM guard: 0.1 < div_yield_pct < 15.0** — values outside this range indicate unit mismatch or no dividend. Do not relax this guard.

6. **`run_time` not `"20:30 IST"`** — all time-sensitive strings in excel_generator use `self.run_time`.

7. **Backfill runs on GitHub Actions** — yfinance rate limits apply. CR second pass capped at 100/run for safety. Do not raise significantly.

---

*Last updated: April 2026 | Maintained by: Claude (Anthropic) working session*