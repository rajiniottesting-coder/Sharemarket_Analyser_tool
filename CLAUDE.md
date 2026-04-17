# NSE/BSE Stock Analyser — Claude Code Knowledge Base
# Version: v7 FINAL | Updated: April 17, 2026
# READ THIS ENTIRE FILE before making any changes to the project.

---

## 1. PROJECT GOAL

Build a fully automated institutional-grade equity research pipeline that:
1. Runs every trading day at 06:00 IST (00:30 UTC) via GitHub Actions
2. Downloads bhav data for 5,000+ stocks from NSE + BSE
3. Filters to top 100 quality stocks through a 3-stage funnel
4. Enriches with fundamentals (yfinance), technicals (DB), fair value (7 models)
5. Generates a 6-sheet Excel dashboard + text report + AI analyst notes per stock
6. Emails deliverables automatically

**The output must look like professional equity research.** Stocks like TATAMOTORS,
RELIANCE, BEL, HAL, PERSISTENT, KAYNES, SUZLON should appear in the final 100
with clear rationale. ETFs (LIQUIDBEES, EGOLD, MAKEINDIA), penny stocks, and
alphabetical junk must never appear.

---

## 2. REPOSITORY LAYOUT

```
Sharemarket_Analyser_tool/
├── master_funnel.py           1315  # MAIN ENTRY POINT — orchestrates everything
├── backfill_history.py        1430  # Historical fetch + yfinance 25-field enrichment
├── pre_screener.py             259  # Stage 1 + Stage 2 filters
├── priority_ranker.py          248  # Stage 3: top-100 selection with cap diversity
├── scoring_engine.py           132  # Composite score + cap-adjusted 3-verdict system
├── fair_value_engine.py        128  # 7-model FV: DCF/Graham/PE/PB/EV/DDM/PEG
├── ai_analyst.py               286  # AI batch analysis via Anthropic claude-sonnet-4-5
├── excel_generator.py          563  # 6-sheet Excel dashboard (ExcelGeneratorV6)
├── data_bridge.py              796  # SQLite layer + stream consolidation
├── harvester.py                357  # NSE/BSE bhav download (requests + cloudscraper)
├── orchestrator.py             192  # Gate check: holidays/data availability
├── email_service.py            120  # Gmail SMTP delivery
├── v7_analysis_engine.py       131  # Section 3A/C valuation + growth engines
├── bs_engine.py                 72  # Balance sheet health (HEALTHY/WATCH/ALERT)
├── forensics_engine.py         108  # Piotroski/Altman/Beneish (placeholders)
├── fundamental_engine.py        91  # Graham number, PEG, Altman Z maths
├── intel_fetcher.py             50  # Catalyst search query builder per sector
├── early_detection_engine.py    37  # Early mover signal detection
├── spike_screener.py            41  # Volume spike scoring (0-6)
├── technical_engine.py          55  # RSI/MACD/ADX/Supertrend calculations
├── rotation_engine.py           29  # Sector rotation stage (1-4)
├── reconciler.py               171  # NSE+BSE cross-exchange ISIN dedup
├── ownership_tracker.py         35  # Promoter/FII quarterly trend
├── smart_money.py               38  # Bulk deal + insider sentiment
├── report_formatter.py          89  # Text investor card formatter
├── daily_report_generator.py    95  # Daily research report builder
├── market_context.py            50  # Nifty/Sensex/VIX context helpers
├── database_manager.py         108  # DB initialisation helpers
├── db_maintenance.py           137  # Rolling 400-day vacuum + prune
├── whatsapp_gateway.py          32  # Twilio WhatsApp Flask bot
├── chat_interface.py            81  # Interactive CLI interface
├── command_parser.py            65  # "why RELIANCE", "early movers" commands
├── orchestrator.py             192  # Gate check + holiday calendar
├── requirements.txt                 # See section 6
├── master_prompt/
│   └── NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt   # 83KB system prompt for AI
└── .github/workflows/
    ├── market_run.yml               # cron: '30 0 * * 2-6' (06:00 IST Tue-Sat)
    └── keep_alive.yml               # Prevents GitHub 60-day auto-disable
```

---

## 3. PIPELINE FLOW (master_funnel.py — follow this sequence)

```
[Gate Check] orchestrator.py
  C1 Weekday | C2 Not holiday | C3 NSE bhav available
  C4 BSE bhav available | C5 Data integrity

[Section 1] Harvest
  NSE bhav   →  nsearchives.nseindia.com  ~2455 EQ rows
  BSE bhav   →  bse package + cloudscraper  ~4970 rows
  NSE deliv  →  nseindia delivery file  ~3156 rows
  NSE F&O    →  participant data (5 rows)

[Section 1.2] DB Sync  →  daily_prices table

[Section 0] 3-Stage Pre-Screening Funnel
  Stage 1: 5195 → ~1400   (ETFs excluded, price≥₹10, delivery≥40%)
  Stage 2: ~1400 → ~700   (bhav-only quality: delivery≥50%, turnover≥₹10L)
  Stage 3: ~700 → 100     (cap-diversified top-100, min LARGE≥20, MID≥15)

[Section 4B] NSE Fundamentals (backfill_history.fetch_nse_fundamentals)
  EQUITY_L.csv      → company names  (no bot protection, always works)
  NSE index CSVs    → sector mapping (ind_nifty500list.csv etc.)
  yfinance .NS      → 25 fields per stock (PE, ROE, margins, D/E, FCF…)

[Section 5] DB Enrichment per stock
  symbol_master     → company_name, sector, cap_category, eps, mcap
  fundamental_metrics → pe_ttm, pb, roe, de_ratio, margins, fcf_cr…
  shareholding      → promoter_pct, fii_pct, pledge_pct
  technical_indicators → RSI, MACD, ADX, Supertrend, OBV, VWAP
  weekly_momentum   → 2w/4w/6w/8w pct changes, beta_90d

[Section 5B] Fair Value Engine  (fair_value_engine.calculate_all_models)
  M1 DCF | M2 Graham | M3 PE | M4 PB | M5 EV/EBITDA | M6 DDM | M7 PEG
  → cfv, cfv_low (×0.85), cfv_high (×1.15), mos_pct, upside, mos_label

[Section 6] Per-stock scoring loop
  fundamental_score  from PE/ROE/D-E/CR/margins  (real yfinance data)
  technical_score    from RSI/MACD/ADX/Supertrend
  early_entry_score  from EarlyDetectionEngine + spike signals
  sentiment_score    from FII trend + smart money
  safety_score       from pledge + D/E + beta
  composite_score    weighted sum → cap-adjusted verdict
  selection_reason   why this stock passed (feeds AI rationale)
  trade plan         entry_range, stop_loss, t1/t2/t3, horizon, risk_level

[Section 7/8] AI Analyst  (ai_analyst.py)
  Sends 9 batches of 12 stocks each to Anthropic claude-sonnet-4-5
  Each stock = rich structured card (NOT raw DataFrame dump)
  Output = Block H: 150-250 word research note → "View Analysis Summary" col in Excel
  Credit exhaustion: detected on first failure → skip all remaining instantly

[Section 9/10] Deliverables
  ExcelGeneratorV6  → NSE_BSE_Full_Dashboard_{YYYYMMDD}.xlsx  (6 sheets)
  DailyReportGenerator → Daily_Analysis_Report_{YYYYMMDD}.txt
  email_service     → Gmail SMTP to USER_EMAIL_ID
```

---

## 4. DATABASE SCHEMA  (market_data.db, SQLite, ~400 MB)

```
daily_prices          1.7M+ rows, 400-day rolling window
  symbol, bse_code, isin, date, open, high, low, close, prev_close,
  volume, turnover, delivery_pct, exchange, exchange_tag

fundamental_metrics   yfinance data per symbol
  pe_ttm, eps, pb, beta, mcap_cr, div_yield, roe, roa,
  de_ratio,           ← CRITICAL: NOT "debt_equity"
  current_ratio, quick_ratio,
  gross_margin, ebitda_margin, net_margin,
  rev_yoy, pat_yoy,
  total_debt_cr,      ← CRITICAL: NOT "total_debt"
  cash_cr,            ← CRITICAL: NOT "cash"
  fcf_cr, fcf_yield, payout_ratio,
  ps, ev_ebitda, peg, nd_ebitda, int_coverage

symbol_master
  symbol, company_name, sector, cap_category, face_value, isin,
  updated_on  ← contains embedded tags: |eps=X|mcap=X|pe=X|div=X

shareholding
  symbol, promoter_pct, promoter_qoq, pledge_pct, pledge_dir,
  fii_pct, fii_qoq, dii_pct, dii_qoq, public_float

technical_indicators
  symbol, date, rsi_14, macd_signal, adx_14, supertrend,
  obv_signal, vwap, stoch_k, stoch_d, mfi_14

weekly_momentum
  symbol, 2w_chg, 4w_chg, 6w_chg, 8w_chg, beta_90d, sma_200

delivery_stats, fo_participant_data, bulk_deals, insider_trades,
run_stats, latest_analysis_results, watchlist, market_holidays
```

---

## 5. STAGE FILTER LOGIC

### Stage 1  (pre_screener.stage_1_filter)

```python
# V0: ETF/MF exclusion — MUST run first
sc_group in ("EF","MF","IF","IR","BE")  → DROP
sym.startswith / endswith patterns:
    "LIQUID","BEES","GOLDETF","GOLDBEES","SILVERETF","LIQUIDETF",
    "SBILIQ","ABSLLIQ","HDFCLIQ","KOTAKLIQ","IVZIN","AONELIQ",
    "EQUAL50","MAKEINDIA","LIQUIDPLUS","LIQUIDSHRI" etc.  → DROP
CMP between 995-1005 AND "LIQ"/"CASH" in symbol  → DROP (liquid fund NAV)

# V1: volume = 0 → DROP
# V7: |price_change| ≥ 19.9% (circuit) → DROP
# V4: close < ₹10 → DROP (penny)
# V8: suspended → DROP
# V3: delivery_pct < 40% → DROP
# V9: BSE SME + turnover < ₹5L → DROP
```

### Stage 2  (pre_screener.stage_2_fundamental_scorer)
**Uses ONLY bhav data** (no fundamentals at this point).

Hard drops: turnover<₹2L | delivery<30% | close<₹20

Scoring (5 pts each, max 35):
- B1 delivery≥50%  B2 delivery≥65%
- B3 turnover≥₹10L  B4 turnover≥₹50L
- B5 close≥₹50  B6 close≥₹200
- B7 DUAL_LISTED

Threshold: score < 15 → DROP

Cap_category estimate (for Stage 3 use):
- turnover ≥ ₹50Cr → LARGE CAP
- turnover ≥ ₹10Cr → MID CAP
- turnover ≥  ₹1Cr → SMALL CAP
- else             → MICRO CAP

### Stage 3  (priority_ranker.get_top_100_candidates)

Priority score formula:
```
P = (vol_spike/5 × 25)        # capped at 5×, weight 25
  + (s2_score/35 × 30)        # Stage 2 quality, weight 30
  + (delivery/100 × 20)       # institutional interest, weight 20
  + (cap_bonus × 15)          # LARGE=1.0 MID=0.67 SMALL=0.33 MICRO=0.0
  + (turnover_bonus × 10)     # ≥₹50Cr=1.0 ≥₹10Cr=0.6 ≥₹1Cr=0.3
```

Guaranteed slots (enforced):
- LARGE CAP: minimum 20 slots
- MID CAP:   minimum 15 slots
- SMALL+MICRO: maximum 65 slots

Override rules (O1-O4 active, O5 DISABLED):
- O1 Watchlist stocks  |  O2 Corporate announcement  |  O3 Spike pre-trigger
- O4 Score deterioration  |  **O5 DISABLED** (fires for all stocks otherwise)
- Override cap: max 20 slots

Each stock gets `selection_reason` field: human-readable explanation of why selected.
This feeds directly into the AI analyst prompt.

---

## 6. SCORING ENGINE  (scoring_engine.py)

### Composite score formula
```python
base  = fundamental_score × 0.35
      + technical_score   × 0.30
      + early_entry_score × 0.15
      + sentiment_score   × 0.10
      + safety_score      × 0.10
final = base + mos_adjustment + spike_bonus(cap ±10) + early_mover_bonus(+5 if es≥70)
final = clamp(0, 100)
```

### Cap-adjusted verdict thresholds (3 verdicts + AVOID floor)

| Score | LARGE CAP | MID CAP | SMALL CAP | MICRO CAP |
|-------|-----------|---------|-----------|-----------|
| ≥ 70  | BUY       | BUY     | BUY       | BUY       |
| ≥ 66  | BUY       | BUY     | BUY       | WATCHLIST |
| ≥ 63  | BUY       | BUY     | WATCHLIST | WATCHLIST |
| ≥ 60  | BUY       | WATCHLIST | WATCHLIST | WATCHLIST |
| ≥ 56  | WATCHLIST | WATCHLIST | WATCHLIST | NEUTRAL   |
| ≥ 53  | WATCHLIST | WATCHLIST | NEUTRAL   | NEUTRAL   |
| ≥ 50  | WATCHLIST | NEUTRAL | NEUTRAL   | NEUTRAL   |
| < 38  | AVOID     | AVOID   | AVOID     | AVOID     |
| else  | NEUTRAL   | NEUTRAL | NEUTRAL   | NEUTRAL   |

Logic: lower bar for large caps (less risky), highest bar for micro caps.

Valid verdict strings (must match VERDICT_STYLES in excel_generator.py):
BUY, WATCHLIST, NEUTRAL, AVOID, AVOID / EXIT, EXIT,
DEEP VALUE, EARLY MOVER, DEEP VALUE EARLY MOVER, BUY / EARLY MOVER

---

## 7. YFINANCE FIELDS AND UNIT RULES

### 25 fields fetched (backfill_history.py, stored in fundamental_metrics)
```
pe_ttm, eps, pb, beta, mcap_cr, div_yield,
roe, roa,                  ← stored as fractions (0.16), need ×100 when displayed
gross_margin, ebitda_margin, net_margin,  ← same, fractions
de_ratio,                  ← yfinance gives ×100 (e.g. 31), need ÷100 to get ratio
current_ratio, quick_ratio,
total_debt_cr, cash_cr, fcf_cr, fcf_yield,
rev_yoy, pat_yoy, payout_ratio,
ps, ev_ebitda, peg
promoter_pct (→ shareholding table)
```

### Unit conversion helpers in master_funnel.py
```python
_pct(v)   : if abs(v) < 2.0 → v × 100   # fraction to %  (ROE, margins)
_ratio(v) : if abs(v) > 2.0 → v / 100   # ×100 to ratio  (D/E)
_sf(v, d) : safe float — handles "—", None, "", non-numeric → returns d
```

**Always use `_sf()` instead of `float(x or 0)` anywhere x could be "—".**

---

## 8. FAIR VALUE ENGINE  (fair_value_engine.py)

```python
M1_DCF   : 3-stage DCF (5yr at growth, 5yr at growth/2, terminal 4.5%)
M2_Graham: sqrt(22.5 × EPS × BVPS)  — BVPS derived from PB × close if missing
M3_PE    : EPS × sector_median_PE
M4_PB    : BVPS × sector_median_PB
M5_EV    : CMP × sector_median_EVEBITDA / stock_EVEBITDA
M6_DDM   : DPS×(1+g)/(req_return−g)  — only if div_yield > 0
M7_PEG   : EPS × adj_growth_rate

cfv      : weighted average of available models
cfv_low  : cfv × 0.85   (bear case)
cfv_high : cfv × 1.15   (bull case)
```

Returns: cfv, cfv_low, cfv_high, mos_pct, upside, mos_label, score_adjustment

---

## 9. AI ANALYST  (ai_analyst.py)

### What each stock card looks like (sent to Claude API)
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STOCK: TATAMOTORS | Tata Motors Ltd | Automobiles | LARGE CAP | DUAL_LISTED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHY SELECTED (pipeline reason): Large-cap institutional quality;
  strong institutional delivery 62%; volume surge 2.4× avg

PRICE: ₹780 (1.2% today) | 52W ₹620–₹1050 | Vol 2.4× | Delivery 62.5%
MOMENTUM: 2W 3.1% | 4W 5.8%
FAIR VALUE: CFV ₹920 (₹782–₹1058) | MoS 15.2% [ADEQUATE] | Upside 17.9%
  Models: DCF ₹880 | Graham ₹760 | PE-FV ₹940
VALUATION: PE 12.5x | PB 2.1x | ROE 18% | Net Margin 4.2%
HEALTH:    D/E 1.1 | CR 1.3 | Cash ₹8400Cr | FCF ₹3200Cr
SCORE: 68.5/100 [BUY] | F:70 T:72 E:25 [MOMENTUM BUILDING]
TECH:  RSI 62 | MACD BUY | ST BUY | ADX 28
SIGNALS: VOL SURGE + RSI ACCUMULATION | TREND CONFLUENCE
SMART MONEY: FII NET BUYER 3 SESSIONS | Sector: STAGE 2 - ACCUMULATION
TRADE: Entry ₹762–795 | SL ₹720 | T1 ₹850 T2 ₹920 T3 ₹1000
CATALYST QUERIES: [TATAMOTORS JLR profitability FY26, ...]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Config
- Model: `claude-sonnet-4-5`
- Batch size: 12 stocks per call | 9 batches for 100 stocks
- Credit exhaustion: detected on batch 1 failure → all remaining skipped instantly
- Output → "View Analysis Summary" column (col 124) in Full Dashboard sheet

### What Block H should say (quality target)
> "TATAMOTORS was selected as a large-cap institutional quality stock with 62%
> institutional delivery and 2.4× volume surge. Trading at PE 12.5x — a 30% discount
> to the auto sector average — with JLR profitability at record levels in FY26,
> generating ₹3,200Cr FCF. MACD BUY + Supertrend BUY confluence signals institutional
> accumulation. EV transition adds structural re-rating catalyst. FV ₹920 implies
> 17.9% upside. Primary risk: JLR volume slowdown in UK/Europe."

---

## 10. EXCEL OUTPUT  (excel_generator.py — ExcelGeneratorV6)

### 6 sheets
1. `📊 Full Dashboard`      — all 100 stocks, 124 columns, autofilter row 4
2. `⭐ Gold – Early Movers` — stocks with early_entry_score≥70 OR (MoS≥25%+score≥70)
3. `📊 Trade Summary`
4. `🔔 Alert Log`
5. `📱 Delivery Preview`
6. `📖 Glossary`

### Verdict colour coding
```
DEEP VALUE EARLY MOVER / EARLY MOVER  → Gold   #FAC775
DEEP VALUE / BUY / BUY EARLY MOVER    → Blue   #DBEAFE
WATCHLIST / NEUTRAL                   → Amber  #FEF3C7
AVOID / AVOID EXIT / EXIT             → Red    #FEE2E2
```

### Column sections (124 cols, row 3 headers, row 4 subheaders, data from row 5)
IDENTITY | SCORES | PRICE & MARKET | WEEKLY CHANGE | FAIR VALUE |
VALUATION | PROFITABILITY | GROWTH | FIN HEALTH | CAP ALLOC |
SHAREHOLDING | QUALITY SCORES | PIPELINE/OB | EARLY DETECTION |
TECHNICAL | BALANCE SHEET | TRADE PLAN | NEWS & RISK | ANALYSIS SUMMARY

---

## 11. DATA SOURCES RELIABILITY

| Source | What | Works on GitHub Actions? |
|--------|------|--------------------------|
| nsearchives.nseindia.com | NSE bhav ZIP | ✅ Always (static CDN) |
| bseindia.com + bse package | BSE bhav | ✅ cloudscraper works |
| EQUITY_L.csv (NSE static) | Company names | ✅ Always |
| NSE index CSVs | Sector mapping | ✅ Always |
| yfinance (.NS suffix) | 25 fundamental fields | ✅ 93-100% |
| nseindia.com/api/quote-equity | Full JSON fundamentals | ❌ Akamai blocks GH IPs |
| Screener.in / BSE XBRL | Quarterly P&L, CAGR | ❌ Not implemented |

---

## 12. ENVIRONMENT VARIABLES (GitHub Secrets)

```
ANTHROPIC_API_KEY     Claude API (claude-sonnet-4-5 for Block H analysis)
SENDER_EMAIL          Gmail address to send from
SENDER_APP_PASSWORD   Gmail app password (NOT account password)
USER_EMAIL_ID         Recipient email
TWILIO_ACCOUNT_SID    WhatsApp bot (optional)
TWILIO_AUTH_TOKEN     WhatsApp bot (optional)
```

---

## 13. REQUIREMENTS

```
pandas numpy openpyxl requests pytz anthropic
twilio python-dotenv flask bse cloudscraper yfinance
```

---

## 14. GITHUB ACTIONS SCHEDULE

```yaml
market_run.yml   cron: '30 0 * * 2-6'    # 06:00 IST Tue–Sat
keep_alive.yml   cron: '0 10 * * 3'      # Wed 10:00 UTC  — prevents 60-day disable
                 cron: '0 10 * * 0'      # Sun 10:00 UTC
```

keep_alive.yml commits a timestamp AND explicitly re-enables market_run.yml
via GitHub API if it was auto-disabled.

---

## 15. ALL BUGS FIXED (never reintroduce)

### Bug 1 — O5 override fired for ALL 1914 stocks
`days_since_analysis` never set → defaulted to 99 → O5 (≥7 days) true for everyone
→ first 100 alphabetically selected, not by quality. AFFLE, ABINFRA, ENTERO...
**Fix**: O5 disabled. `stock["days_since_analysis"] = 0` set in Section 6 loop.
Override cap = 20 slots max.

### Bug 2 — Stage 2 F5 free points for missing PE
`if pe == 0 or pe < 80: score += 5` — pe=0 (absent) → 5 free pts every stock.
**Fix**: `if 0 < pe < 80: score += 5`

### Bug 3 — Stage 2 phantom data
Stage 2 checked net_profit, debt_equity, promoter_holding — all 0 at Stage 2 time
(yfinance enrichment runs AFTER Stage 3). Everything passed → useless filter.
**Fix**: Stage 2 uses ONLY bhav data (close, volume, turnover, delivery_pct, exchange_tag).

### Bug 4 — ETFs flooding top 100
LIQUIDBEES delivery=92%, vol_spike=5× → ranked #1 every day.
No ETF filter existed. 19+ ETFs in the final 100.
**Fix**: sc_group=EF/MF/IF → hard drop Stage 1. Symbol pattern matching for NSE ETFs.
Liquid fund NAV (CMP≈₹1000) pattern also caught.

### Bug 5 — Large caps excluded by vol_spike formula
Vol spike weight was 40/100. LIQUIDBEES=8× scored 32pts. TATAMOTORS=0.8× scored 3pts.
**Fix**: vol spike weight → 25, capped at 5×. Cap bonus added (LARGE cap = 15pts).

### Bug 6 — cap_category not available at Stage 3 time
cap_category was set in Section 5 (AFTER Stage 3). Ranker couldn't use it.
**Fix**: Stage 2 now estimates cap_category from daily turnover thresholds.
Section 5 overwrites with correct yfinance-based mcap value later.

### Bug 7 — No large cap minimum guarantee
Nothing stopped 60+ micro caps filling all 100 slots.
**Fix**: MIN_LARGE=20, MIN_MID=15, MAX_SMALL_MICRO=65 enforced in priority_ranker.

### Bug 8 — DB column name mismatch
Code queried `debt_equity`, `total_debt`, `cash` but DB columns are
`de_ratio`, `total_debt_cr`, `cash_cr`. All NULL reads.
**Fix**: Use correct column names everywhere.

### Bug 9 — yfinance unit mismatches
ROE=0.16 displayed as 0.16% not 16%. D/E=31 displayed not 0.31.
**Fix**: `_pct()` multiplies fractions ×100. `_ratio()` divides ×100 by 100.

### Bug 10 — float("—") crashes
`float("—" or 0)` → "—" is truthy → float("—") → ValueError crash.
**Fix**: `_sf(val, default)` helper handles "—", "--", None, "", non-numeric.
Used throughout master_funnel, ai_analyst, fair_value_engine, excel_generator.

### Bug 11 — AI receiving raw DataFrame dump
`batch.to_string()` dumps all 100+ messy columns. AI wrote generic garbage.
**Fix**: Each stock formatted as a rich labelled card with WHY SELECTED reason,
fair value range, key metrics, technicals, trade plan, catalyst queries.

### Bug 12 — M2_Graham always 0
BVPS never populated. Graham = √(22.5 × EPS × BVPS) → always 0.
**Fix**: Derive BVPS = PB × close inside fair_value_engine if BVPS absent.

### Bug 13 — cfv_low/cfv_high never returned
Excel FV Low/High columns always "—".
**Fix**: cfv_low = cfv×0.85, cfv_high = cfv×1.15 returned from FV engine.

### Bug 14 — GitHub 60-day auto-disable
GitHub disables scheduled workflows after 60 days of no commits.
**Fix**: keep_alive.yml commits timestamp + calls GitHub API to re-enable.

### Bug 15 — AI credit exhaustion wasting 3 minutes
9 batches × 2 attempts × 10s sleep = 3+ min of dead API calls.
**Fix**: First credit error → `credit_exhausted=True` → skip all remaining instantly.

### Bug 16 — Empty Excel (0 data rows)
Excel was generated from the first (empty) run before Stage 2 fix.
Added guard: if `final_100_list` is empty at Excel generation time → crash loudly.
Also confirmed: the pipeline log showing 100 stocks and the empty Excel were from
two different runs (the user had downloaded the old empty file).

### Bug 17 — All verdicts NEUTRAL/MILD SELL
Old verdict scale: STRONG BUY≥90, BUY≥75, MILD BUY≥60... 
Real scores were 37-68 → everything NEUTRAL or MILD SELL.
MILD BUY and STRONG BUY strings also not in VERDICT_STYLES → no colour.
**Fix**: Calibrated to real score range. Only 3 verdicts: BUY/WATCHLIST/NEUTRAL/AVOID.
Cap-adjusted thresholds: LARGE cap BUY at ≥60, MICRO cap BUY at ≥70.

---

## 16. EXPECTED OUTPUT — NEXT PIPELINE RUN

### Pipeline log should show:
```
Stage 1: 5195 → ~1400  (ETFs=0, penny<₹10 removed)
Stage 2: ~1400 → ~700  (delivery≥50%, turnover≥₹10L)
Stage 3: ~700 → 100    (LARGE≥20, MID≥15, SMALL+MICRO≤65, ETFs=0)
Cap mix: LARGE=20-25, MID=15-20, SMALL/MICRO=55-65
```

### Top 20 ranked (on a normal trading day):
Large caps with highest delivery% + vol spike + turnover:
RELIANCE, TCS, HDFCBANK, TATAMOTORS, SBIN, ICICIBANK, BEL, HAL,
SUNPHARMA, DRREDDY, LT, BAJFINANCE, CIPLA, NTPC, ONGC, TATAPOWER

Followed by high-performing mid caps:
MAZDOCK, RVNL, PERSISTENT, CHOLAFIN, APOLLOHOSP, TRENT, MPHASIS

### Verdict distribution (normal day):
```
BUY:       10-20 stocks  (cap-adjusted — LARGE qualifies at ≥60)
WATCHLIST: 30-40 stocks
NEUTRAL:   35-45 stocks
AVOID:      5-10 stocks  (score < 38, universal floor)
```

### View Analysis Summary column (with API credits):
Each stock gets a 150-250 word research note explaining:
- Why it was selected (selection_reason)
- Strongest fundamental/technical signal
- Key sector tailwind
- Fair value rationale
- Primary risk

---

## 17. HOW TO SAFELY MAKE CHANGES

### Before ANY change
```bash
# Read the file first
cat filename.py | head -100

# Syntax check after change
python3 -c "import ast; ast.parse(open('filename.py').read()); print('OK')"

# Run pipeline simulation
python3 -c "
import sys; sys.path.insert(0, '.')
from pre_screener import stage_1_filter, stage_2_fundamental_scorer
from priority_ranker import get_top_100_candidates
import pandas as pd
# ... build test stocks ... 
s1 = stage_1_filter(stocks)
s2 = stage_2_fundamental_scorer(pd.DataFrame(s1))
s3 = get_top_100_candidates(s2)
print(f'{len(stocks)} -> {len(s1)} -> {len(s2)} -> {len(s3)}')
"
```

### Safe float pattern (ALWAYS use _sf, NEVER float(x or 0))
```python
# WRONG — crashes when x = "—"
val = float(x or 0)

# RIGHT
val = _sf(x, 0)    # _sf defined in master_funnel.py, copy to other files as needed
```

### Adding a new Excel column
1. Add to `FULL_COLS` in excel_generator.py: `("Column Name", width, "dict_key")`
2. Add to correct `FULL_GROUPS` section (column range tuple)
3. Ensure key is set in master_funnel.py Section 6: `stock["dict_key"] = value`
4. If from DB: add to the SELECT query around line 579 in master_funnel.py

### Debugging a field showing "—" in Excel
```python
# Check if key is set anywhere in master_funnel
grep -n '"your_key"' master_funnel.py

# Check DB column name
grep -n 'your_key\|your_col' data_bridge.py

# Check yfinance mapping
grep -n 'your_key' backfill_history.py
```

### Stage 2 returning 0 stocks
- Uses ONLY bhav data — check threshold (currently 15/35)
- Check hard drops: turnover<₹2L | delivery<30% | close<₹20
- Verify `cap_category` not blocking — it's set via `setdefault`, not a filter

### ETFs reappearing
- Add new ETF symbol to `_etf_kw` tuple in pre_screener.py Stage 1 V0
- Check if sc_group="EF" is passing through from data_bridge.py consolidation

### No large caps in top 100
- Check MIN_LARGE=20 in priority_ranker.py is being enforced
- Check cap_category is set in Stage 2 from turnover thresholds
- Check vol_spike is capped at 5× (not 10×) and weight is 25 (not 40)

### Verdict strings must match VERDICT_STYLES
Valid strings (case-sensitive, space-sensitive):
"BUY", "WATCHLIST", "NEUTRAL", "AVOID", "AVOID / EXIT", "EXIT",
"DEEP VALUE", "EARLY MOVER", "DEEP VALUE EARLY MOVER", "BUY / EARLY MOVER"
Any other string → grey default colour → looks broken in Excel.

---

## 18. FILE HASHES (April 17, 2026 — verified state)

These line counts confirm the correct versions:
```
pre_screener.py           259 lines
priority_ranker.py        248 lines
scoring_engine.py         132 lines
ai_analyst.py             286 lines
master_funnel.py         1315 lines
backfill_history.py      1430 lines
fair_value_engine.py      128 lines
excel_generator.py        563 lines
```

If your files have different line counts, you may have an older version.
The 8 files above are the definitive final versions with all 17 bugs fixed.

---

## 19. WHAT TO WORK ON NEXT

### Immediate priorities (top up Anthropic credits first):
1. **Trigger a manual GitHub Actions run** to verify the full pipeline with new code
2. **Verify ETFs=0** in the next Excel output
3. **Verify large caps in top 20** (RELIANCE, TCS, TATAMOTORS etc.)
4. **Verify "View Analysis Summary"** column has quality research notes

### Medium-term improvements:
5. **Quarterly earnings data** — add Screener.in scraping for P&L CAGR, quarterly PAT
   (currently shows "—" for rev_cagr_1y, pat_cagr_1y, pat_cagr_3y)
6. **52W high/low from DB** — compute from daily_prices rolling window instead of "—"
7. **Sector PE/PB benchmarks** — improve M3/M4 FV model accuracy with fresh NSE data
8. **WhatsApp bot** — test ngrok + Twilio integration for "why RELIANCE" queries
9. **Piotroski F-Score** — implement actual calculation (currently placeholder)

### Structural improvements:
10. **Reduce batch size** for AI from 12 → 8 stocks (less truncation risk per batch)
11. **Add retry logic** for yfinance (currently 1 attempt, some stocks fail transiently)
12. **EDGAR/MCA filing integration** for promoter pledge real-time data

---

*End of CLAUDE.md — this file is the single source of truth for Claude Code.*
*When in doubt about any design decision, the answer is in sections 14-17.*

---

## 20. QUICK REFERENCE ALIASES

The following terms appear throughout the codebase under these exact names:

- **CAP_THRESHOLDS** — class constant in `scoring_engine.ScoringEngine`:
  `{"LARGE":(60,50), "MID":(63,53), "SMALL":(66,56), "MICRO":(70,60)}`

- **_fmt_stock_card(row)** — function in `ai_analyst.py` that converts a stock
  dict into a rich labelled text card sent to the Claude API (replaces to_string())

- **ETF exclusion** — implemented as V0 in `pre_screener.stage_1_filter`:
  checks sc_group, symbol patterns, and NAV≈₹1000 pattern

- **vol spike cap 5×** — in `priority_ranker.calculate_priority_score`:
  `vol_spike_ratio = min(current_vol / avg_vol, 5)` — was 10× before fix