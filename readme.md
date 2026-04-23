# NSE / BSE Stock Analyser — v10.6

> **Fully automated · Daily pre-market intelligence for Indian equities**
> 5,000+ stocks → AI-scored Excel dashboard → your inbox by 6:00 AM IST.

A cloud-hosted, zero-intervention research engine for the Indian markets. It harvests NSE and BSE data overnight, funnels 5,000+ stocks down to the top 100 through a three-stage screener, runs fundamental, technical, forensic, and AI analysis on each, and delivers a colour-coded Excel dashboard with investor cards straight to your email — every trading morning.

Single-user personal tool. No UI, no server to manage. Runs free on GitHub Actions + Gmail + Google Gemini API.

---

## Highlights

- **Three-stage funnel** — 5,150 raw rows → ~600 (Stage 1 ETF/junk filter) → ~400 (Stage 2 quality score) → **100 candidates** (Stage 3 priority ranker with cap diversification).
- **7 Fair-Value models** per stock — DCF, Graham, PE, PB, EV/EBITDA, DDM, PEG — weighted into a Composite Fair Value with Margin-of-Safety gate.
- **Composite score /100** with cap-adjusted verdict thresholds, **confidence dots** (●●● HIGH / ●●○ MEDIUM / ●○○ LOW), and a distinct **OVERVALUED** verdict for quality businesses trading above fair value.
- **Forensic guards** — Altman Z, Beneish M, promoter pledge, Piotroski F /9, BS Health status — automatically suppress alerts on distressed names.
- **7-sheet Excel dashboard** — Full Dashboard, Gold (Early Movers), Trade Summary, Alert Log, Delivery Preview, Glossary, Tooltip Reference — with Indian currency formatting and hover tooltips on every metric.
- **AI investor cards** — Google Gemini generates a 150–250 word research note per stock, batched 10–15 per API call, grounded with the engine's pre-computed Graham/PEG/CFV values.
- **Gate-checked execution** — 6 pre-conditions (weekday, holiday calendar, NSE bhav availability, data integrity, DB freshness, minimum rows) must pass before any download or analysis.
- **BSE resilience** — uses the `bse` pip package (Akamai auth handled internally) and falls back to a curated `DUAL_LISTED_ALLOWLIST` of 206 Nifty-100/mid-cap names when BSE downloads fail from cloud IPs.

---

## Project layout

```
Sharemarket_Analyser_tool/
├── master_funnel.py              Core pipeline orchestrator (Sections 0–13)
├── backfill_history.py           365-day historical builder + yfinance enrichment
├── requirements.txt
├── .env                          (local only — secrets go into GitHub Secrets in prod)
│
├── ingestion/
│   ├── orchestrator.py           Gate check — 6 pre-conditions + NSE holiday calendar
│   ├── harvester.py              NSE bhav / delivery / SME / F&O downloaders
│   └── reconciler.py             NSE+BSE merge on ISIN + DUAL_LISTED_ALLOWLIST fallback
│
├── screening/
│   ├── pre_screener.py           Stage 1 (ETF filter, ~67 patterns) + Stage 2 (quality /35)
│   └── priority_ranker.py        Stage 3 (cap diversification, tech alignment bonus)
│
├── analysis/
│   ├── fair_value_engine.py      7 valuation models + composite FV + MoS gate
│   ├── scoring_engine.py         Composite score + verdict + confidence + storm score
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
│   └── db_maintenance.py         90-day rolling circular queue
│
├── ai/
│   └── ai_analyst.py             Google Gemini batch analysis
│
├── reporting/
│   ├── excel_generator.py        7-sheet ExcelGeneratorV6 + dynamic red-header
│   ├── tooltip_formatter.py      Cell / group / reference tooltips
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

**`.github/workflows/market_run.yml` cron:** `0 0 * * 2-6`
→ 00:00 UTC = **05:30 IST**, Tue–Sat (covers Mon–Fri Indian trading days, delivered the morning after).
Email arrives at roughly 06:00–06:30 IST after the GitHub queue delay.

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
Section 5B  Fair Value engine — 7 models + composite FV + MoS
Section 6   Scoring loop per stock
             Technical / Fundamental / Safety / Sentiment / Early-Entry scores
             → Composite score → Verdict + confidence dots
             → Storm score → Spike score → Horizon + Risk level (after verdict)
             → F-Score proxy → Price targets T1/T2/T3 + SL + R:R
Section 7/8 AI investor cards (Gemini, batches of 10–15)
Section 9/10 7-sheet Excel dashboard + text research report
Section 12  Gmail SMTP delivery
Section 13  DB maintenance — 90-day circular queue
```

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

1. **📊 Full Dashboard** — 100 stocks × ~120 columns. Every metric. The full picture.
2. **⭐ Gold / Early Movers** — MOMENTUM archetype (EE ≥ 70) or VALUE archetype (MoS ≥ 25% + Score ≥ 70 + clean safety).
3. **📊 Trade Summary** — Entry / Stop Loss / T1 / T2 / T3 / R:R for Gold stocks only.
4. **🔔 Alert Log** — Today's score vs yesterday's, 8-way **Action Required** logic (`CONSIDER ENTRY`, `MONITOR FOR ENTRY`, `VOLUME ALERT — INVESTIGATE`, `EARLY MOVER — ACCUMULATE`, `SCORE IMPROVING — WATCH`, `SCORE DECLINING — CAUTION`, `REVIEW FOR EXIT`, `MONITOR CLOSELY`).
5. **📱 Delivery Preview** — WhatsApp + Email text preview.
6. **📖 Glossary** — 80+ column definitions.
7. **💡 Tooltip Reference** — Polished hover + ⓘ cue.

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

---

## Credits & licence

Personal algorithmic trading research system — designed, built & maintained by **Rajkumar**.
Internal use only · confidential · not for redistribution.

The master prompt in `master_prompt/` encodes Section 0–13 of the v7 design spec. AI output is generated by Google Gemini via the official `google-genai` SDK.