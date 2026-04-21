# -*- coding: utf-8 -*-
"""
reporting/tooltip_formatter.py
────────────────────────────────────────────────────────────────────────────────
Centralised tooltip system for the NSE/BSE Analyser Excel dashboard.

Session 16 (final push): replaces the legacy "💡 short\\n\\nfull" comment style
with a polished, structured hover + visible ⓘ header cue + rich Tooltip
Reference sheet.

Public API:
    TIPS                            — dict[header_name] -> (short, full)
    format_tooltip(header, short, full) -> str
    apply_tooltips(ws, header_row, col_headers,
                   add_cue=True, ref_anchors=None) -> None
    build_reference_sheet(wb, after_sheet=None) -> dict[header -> row]

Design notes:
- Openpyxl writes only the legacy (VML) comment format, so we cannot style
  the hover box itself (yellow background, Tahoma font, callout arrow are
  drawn by Excel). Everything we CAN control — text layout, icons, box
  dimensions — is polished here.
- The visible ⓘ cue on the header tells users a tooltip exists without
  requiring them to hover blindly.
- The Tooltip Reference sheet is the main "modern" visual upgrade — full
  colour, borders, typography — linked from every header via hyperlink.
"""

from __future__ import annotations
from typing import Dict, Iterable, Optional, Tuple

from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ════════════════════════════════════════════════════════════════════════════
# TIP DATA — single source of truth for every column explanation
# ════════════════════════════════════════════════════════════════════════════
TIPS: Dict[str, Tuple[str, str]] = {
    # ── Identity & classification ───────────────────────────────────────────
    "Symbol": ("Stock ticker / trading symbol", "NSE or BSE code used for placing orders."),
    "Company Name": ("Full legal entity name", "Registered company name on the exchange."),
    "Company": ("Full company name", "Legal registered company name."),
    "Sector": ("Industry classification",
               "Used for rotation analysis and peer comparison.\n"
               "Stage 2 sectors offer the best entry opportunities."),
    "Exchange": ("DUAL_LISTED = best liquidity",
                 "DUAL_LISTED: Both NSE + BSE (best liquidity)\n"
                 "NSE_ONLY: Good liquidity\n"
                 "BSE_ONLY: Lower liquidity — check volume before trading\n"
                 "BSE_SME: Very low liquidity — high impact cost\n\n"
                 "Note: When BSE bhavcopy is unavailable (cloud-runner IP\n"
                 "blocking), a curated allowlist of known dual-listed\n"
                 "Nifty 100 + popular mid-cap names is used to tag DUAL_LISTED.\n"
                 "Lesser-known stocks may show NSE_ONLY even if also on BSE."),
    "Cap Category": ("LARGE=safest | MICRO=speculative",
                     "BUY thresholds: LARGE≥60 | MID≥63 | SMALL≥66 | MICRO≥70\n"
                     "LARGE: >₹20,000Cr | MID: ₹5,000–20,000Cr\n"
                     "SMALL: ₹500–5,000Cr | MICRO: <₹500Cr"),
    "BSE Code": ("6-digit BSE scrip code",
                 "Used on BSE terminal. NSE uses Symbol; BSE uses this numeric code.\n"
                 "Helpful when the same company has different tickers across exchanges."),

    # ── Verdicts & composite scores ─────────────────────────────────────────
    "Verdict": ("BUY | OVERVALUED | WATCHLIST | NEUTRAL | AVOID",
                "BUY: Score clears cap-tier threshold + MoS > −10%\n"
                "OVERVALUED: Score clears BUY threshold but MoS gate blocks\n"
                "  (great business, currently expensive, wait for pullback)\n"
                "WATCHLIST: Score in watch band (below BUY threshold)\n"
                "NEUTRAL: Score above AVOID floor but below WATCHLIST min\n"
                "AVOID: Score < 38 (universal floor)\n\n"
                "BUY thresholds: LARGE ≥60 | MID ≥63 | SMALL ≥66 | MICRO ≥70\n"
                "Tech Override: MoS gate relaxes to −20% when Score≥70 + ST=BUY + Stage 2\n\n"
                "Confidence dots (Session 24) indicate distance from threshold:\n"
                "  ●●● HIGH   (≥5 points clear — decisive)\n"
                "  ●●○ MEDIUM (2-5 points clear — solid)\n"
                "  ●○○ LOW    (<2 points — cliff zone, treat with caution)"),
    "Score /100": ("≥70 strong | ≥60 watch | <38 avoid",
                   "Weighted composite (all sub-scores now reach 100):\n"
                   "  Fundamental × 35%  +  Technical × 30%  +  EarlyEntry × 15%\n"
                   "  + Sentiment × 10%  +  Safety × 10%\n"
                   "  + MoS adjustment (−10 to +12)\n"
                   "  + Spike bonus (+2 per trigger, max +10; zeroed if guard active)\n"
                   "  + Early Mover bonus (+5 if EE ≥ 50)\n"
                   "  − Risk penalty (−10 if anti-trigger guard fires)\n\n"
                   "Verdict bands: ≥80 Exceptional | ≥70 Strong BUY | ≥60 Watchlist\n"
                   "<38 universal AVOID floor (regardless of cap tier).\n"
                   "BUY thresholds vary by cap: LARGE≥60 MID≥63 SMALL≥66 MICRO≥70.\n\n"
                   "Session 24 refinements:\n"
                   "• Sentiment redistributes its 10% when no paid/AI signals fired\n"
                   "  (prevents 'free 5 points' for ignorance)\n"
                   "• Spike bonus gated: full +10 only when fundamental_score ≥ 55;\n"
                   "  capped at +3 otherwise (momentum can't mask weak fundamentals)\n"
                   "• Stage 2 baseline reduced (liquidity no longer dominates fundamental)"),
    "Early Entry /100": ("≥50=Early Mover | ≥35=Ahead of Consensus",
                         "Detects stocks 4–12 weeks BEFORE institutional coverage.\n"
                         "Vol Surge+RSI +15 | Trend Confluence +12 | Momentum +10\n"
                         "52W Breakout +10 | Deep Value+BUY +10 | Inst Footprint +10\n"
                         "Score Convergence +8 | FII Accum +8 | Promoter Accum +8 | Dual-Listed +8\n\n"
                         "≥50: EARLY MOVER — accumulate before crowd\n"
                         "≥35: AHEAD OF CONSENSUS | <35: EMERGING\n\n"
                         "Note: low EE on a Gold-sheet stock is not a bug — Gold\n"
                         "includes patient VALUE candidates (high Score + high MoS\n"
                         "+ clean safety) that aren't showing momentum signals yet.\n"
                         "Two legit Gold archetypes: MOMENTUM (high EE) and VALUE (low EE)."),
    "Spike Score /6": ("≥2 notable | ≥4 strong | 6 very rare",
                       "Six momentum triggers — how many fire simultaneously:\n"
                       "T1: CMP within 3% of 52W High + vol>2×\n"
                       "T2: MACD+ST=BUY + vol>1.5×\n"
                       "T3: ADX>25 + delivery>60% + vol>1.5×\n"
                       "T4: RSI 45–65 + vol>2×\n"
                       "T5: vol>3× + delivery>60%\n"
                       "T6: 2w_chg>3% + 2w>4w + vol>1.5×\n\n"
                       "Suppressed to 0 if pledge>20% or Altman/Beneish flags active.\n"
                       "Low Spike on a Gold stock is fine — value candidates\n"
                       "may be accumulating quietly without hot momentum triggers."),
    "Spike /6": ("≥2 notable | ≥4 strong | 6 very rare",
                 "Six momentum triggers — how many fire simultaneously.\n"
                 "Suppressed to 0 if pledge>20% or Altman/Beneish flags active.\n"
                 "Low Spike on Gold is OK — pure-value candidates qualify\n"
                 "on fundamentals + MoS + safety without momentum signals."),
    "Storm Score /10": ("≥8 Storm Safe | ≥5 Moderate | <5 High Risk",
                        "Defensive quality — how safe in a market crash?\n"
                        "Beta<0.8 +2 | D/E<0.3 +2 | FCF positive +2\n"
                        "Div yield>2% +1 | Rev growth>10% +1 | Margin Expansion +1\n"
                        "Promoter QoQ up +1 | FII buying 3Q +1"),
    "Storm /10": ("≥8 Storm Safe | ≥5 Moderate | <5 High Risk",
                  "Defensive quality — higher score = more resilient in downturns."),
    "F-Score /9": ("≥7 strong | ≤3 weak",
                   "Piotroski F-Score — 9 criteria across Profitability (4),\n"
                   "Leverage/Liquidity (3), and Operating Efficiency (2)."),

    # ── Price / momentum ────────────────────────────────────────────────────
    "CMP (₹)": ("Current market price",
                "Compare with CFV. CMP<CFV=undervalued (buying opportunity)."),
    "Day Chg %": ("Today's move | >+3% strong | <−3% weak",
                  ">5% may trigger circuit rules on some counters."),
    "52W High (₹)": ("52-week peak — CMP near here = breakout",
                    "CMP near 52W High = breakout territory (T1 spike fires)."),
    "52W Low (₹)": ("52-week floor — value or continued decline",
                   "Verify fundamentals before buying near 52W Low."),
    "Vol Spike (×50D)": ("≥2× unusual | ≥3× institutional",
                         "1×: Normal | 1.5–2×: Above avg | 2–3×: Unusual | >3×: Major event"),
    "Delivery %": ("≥60% institutional | <40% speculative",
                   "Share of traded volume actually delivered (not intraday).\n"
                   "≥70%: Strong institutional conviction\n"
                   "40–70%: Mixed | <40%: Mostly speculative — caution\n"
                   "Sentiment score impact:\n"
                   "  >70%: +4 | >60%: +2 | <30%: −3"),
    "Beta": ("<0.8 defensive | >1.2 volatile",
            "Beta<0.8: Less volatile — good in downturns (+2 Storm)."),
    "Chg% [2-Weekly]": ("2-week return | >3% strong momentum",
                        ">2%: EE Momentum signal fires. 2W>4W = accelerating (bullish)."),
    "Chg% [4-Weekly]": ("4-week return | 2W>4W = acceleration",
                        "2W>4W = accelerating | 2W<4W = decelerating"),
    "Chg% [6-Weekly]": ("6-week trend",
                        "Positive + rising across 2W/4W/6W/8W = sustained uptrend."),
    "Chg% [8-Weekly]": ("8-week return | best for trend direction",
                        "Consistently positive = confirmed uptrend."),
    "Chg% [2-Wk]": ("2-week return | >3% strong",
                    ">2%: EE Momentum signal fires."),
    "Chg% [4-Wk]": ("4-week return | 2W>4W=acceleration",
                    "2W>4W=accelerating | 2W<4W=decelerating."),
    "Chg% [6-Wk]": ("6-week trend",
                    "Positive + rising across 2W/4W/6W/8W = sustained uptrend."),
    "Chg% [8-Wk]": ("8-week return | best for trend direction",
                    "Consistently positive = confirmed uptrend."),

    # ── Fair value & valuation ──────────────────────────────────────────────
    "CFV (₹)": ("Composite Fair Value (7 models)",
                "M1 DCF 30% | M2 Graham 15% | M3 PE 20% | M4 PB 15%\n"
                "M5 EV/EBITDA 10% | M6 DDM 5% | M7 PEG 5%\n"
                "CMP<CFV = undervalued | CMP>CFV = overvalued\n\n"
                "Safety caps (Session 19):\n"
                "  M1 DCF individually capped at 4× CMP\n"
                "  Composite CFV capped at 3× CMP\n"
                "These prevent a single model misbehaving (e.g., low-beta\n"
                "stock with tiny terminal-value denominator) from producing\n"
                "implausible 5× or 10× fair values."),
    "FV Low (₹)": ("Conservative FV = CFV × 0.85",
                  "CMP below FV Low = very deeply undervalued."),
    "FV High (₹)": ("Optimistic FV = CFV × 1.15",
                   "CMP above FV High = significantly overvalued."),
    "MoS %": (">25% strong buy | <−15% overvalued",
              "Margin of Safety = (CFV − CMP) / CMP × 100\n"
              ">40%: Exceptional (+12) | >25%: Strong (+8) | 10–25%: Adequate (+4)\n"
              "−15 to −30%: Overvalued (−5) | <−30%: Significant premium (−10)\n\n"
              "Note: MoS is effectively capped near 200% because CFV is\n"
              "capped at 3× CMP (Session 19 safety net). If you see MoS\n"
              "at ~200%, treat it as 'model says deeply undervalued —\n"
              "verify inputs' rather than a guaranteed bargain."),
    "MoS Label": ("Valuation summary",
                  "EXCEPTIONAL >40% | STRONG >25% | ADEQUATE >10% | THIN 0-10%\n"
                  "SLIGHT PREMIUM −10% to 0% | SIGNIFICANT PREMIUM <−10%"),
    "Upside to FV %": (">20% = meaningful upside remaining",
                       "Percentage price can rise to reach fair value.\n"
                       "Identical to MoS % (same formula, different label).\n"
                       "Session 23: removed from Gold/Trade Summary to avoid\n"
                       "duplication; kept on Full Dashboard for trader-style\n"
                       "interpretation. MoS % is the single source of truth."),
    # 'Upside %' kept for backward compat in case any old cell still references it
    "Upside %": ("Same as MoS % (removed from Gold/Trade Summary)",
                 "Identical to MoS %. Column removed in Session 23 to avoid duplication."),
    "M1: DCF FV (₹)": ("Discounted Cash Flow fair value — 30% weight in CFV",
                       "Projects 10 years of free cash flow and discounts at WACC.\n"
                       "Best for mature, cash-generating businesses.\n\n"
                       "Session 19 cap: M1 is limited to 4× CMP to prevent unrealistic\n"
                       "valuations when low-beta inputs (e.g., SBIN with β=0.2) inflate\n"
                       "the DCF output to absurd levels. Capping here also protects the\n"
                       "composite CFV (which is separately capped at 3× CMP)."),
    "M2: Graham FV (₹)": ("Benjamin Graham formula — 15% weight in CFV",
                          "Classic value formula: FV = √(22.5 × EPS × BVPS)\n"
                          "Conservative — rewards stable earnings + book value."),
    "M3: PE FV (₹)": ("Sector-relative P/E fair value — 20% weight in CFV",
                      "FV = EPS × sector median P/E.\n"
                      "Shows 0 when EPS negative."),
    "M4: PB FV (₹)": ("Price/Book fair value — 15% weight in CFV",
                      "FV = Book Value × sector median P/B.\n"
                      "Most useful for banks, NBFCs, and asset-heavy businesses."),
    "M5: EV FV (₹)": ("EV/EBITDA fair value — 10% weight in CFV",
                      "Capital-structure neutral — useful when debt levels vary widely."),
    "M6: DDM FV (₹)": ("Dividend Discount Model — 5% weight in CFV",
                       "Only meaningful for consistent dividend payers."),
    "M7: PEG FV (₹)": ("Growth-adjusted P/E fair value — 5% weight in CFV",
                       "Rewards genuine growth companies at reasonable multiples.\n"
                       "Shows 0 when earnings growth negative."),

    # ── Ratios ──────────────────────────────────────────────────────────────
    "P/E TTM": ("<20 cheap | 20–40 fair | >40 expensive",
                "Score: ≤20 = +12 | ≤40 = +7 | >60 = −8"),
    "P/E": ("<20 cheap | 20–40 fair | >40 expensive",
            "Price to Earnings (TTM). Score: ≤20 = +12 | ≤40 = +7 | >60 = −8"),
    "Earn Yield %": (">6% undervalued vs bonds",
                     "EPS/CMP × 100. >6%: Cheap. Compare to 10Y bond yield."),
    "P/CF": ("<15 value | >25 expensive",
            "More reliable than P/E (cash harder to fake)."),
    "PEG Ratio": ("<1 undervalued | >2 expensive",
                  "P/E / Growth. <1: Undervalued (Peter Lynch favourite)."),
    "PEG": ("<1 undervalued | >2 expensive",
            "P/E / Growth. <1: Undervalued."),
    "P/B": ("<2 value | >5 expensive",
            "<1: Below asset value | >5: Only justified by very high ROE."),
    "P/S": ("<3 cheap | >10 expensive", "Useful when P/E unavailable."),
    "EV/EBITDA": ("<12 value | >20 expensive",
                  "IT: 20 | Pharma: 18 | FMCG: 30 | Banks: 12 | Metals: 8"),
    "ROE %": (">20% excellent | <10% weak",
             "Score: >20% = +12 | >10% = +6 | <5% = −5"),
    "ROCE %": (">15% good capital allocation",
               "Return on Capital Employed. ROCE > cost of capital = value-creating."),
    "ROA %": (">10% efficient | <5% poor",
             "Low for Banks/Utilities is normal."),
    "Gross Mgn %": (">40% strong moat | >20% decent",
                    "Score: >40% = +8 | >20% = +4"),
    "EBITDA Mgn %": (">25% excellent | >15% good",
                     ">30%: Excellent | >20%: Good | <10%: Tight"),
    "NPM %": (">15% excellent | <5% thin",
             "Score: >15% = +8 | >5% = +4 | <0% = −8"),
    "NPM Q1 %": ("Most recent quarter margin vs TTM",
                 "Q1 > NPM(TTM): margins accelerating."),
    "NPM Q2 %": ("Previous quarter margin", "Track Q3→Q2→Q1 trend."),
    "NPM Q3 %": ("3rd quarter — rising = Margin Expansion",
                 "Rising Q3→Q2→Q1 triggers Margin Expansion = YES."),
    "Margin Expansion": ("YES = 3 consecutive qtrs of rising NPM",
                         "Score: Fundamental +5 | Safety +3 | Storm +1."),

    # ── Growth ──────────────────────────────────────────────────────────────
    "Rev CAGR 1Y %": (">20% high growth | >10% good",
                      "1Y > 3Y CAGR = growth accelerating."),
    "Rev CAGR 3Y %": (">15% strong | >8% decent",
                      "Score: >15% = +5 | >8% = +3."),
    "PAT CAGR 1Y %": (">20% strong earnings momentum",
                      "1Y > 3Y = accelerating profitability."),
    "PAT CAGR 3Y %": (">20% compounder | >10% good",
                      "Score: >20% = +8 | >10% = +4."),
    "EBITDA CAGR 1Y %": (">15% strong operating growth",
                         "Score: >15% = +4 | >8% = +2"),
    "Rev YoY %": (">10% growing | <0% declining",
                  "Score: >15% = +5 | >8% = +3 | <−5% = −4"),
    "PAT YoY %": (">20% strong | >10% good",
                  "Score: >20% = +8 | >10% = +4 | >0% = +2 | <−10% = −7"),
    "Q3 Rev (₹Cr)": ("Latest quarter revenue", "Rising QoQ = business growing."),
    "Q3 PAT (₹Cr)": ("Latest quarter net profit",
                     "Positive and growing = healthy earnings."),
    "Q3 EBITDA (₹Cr)": ("Latest quarter operating profit",
                        "Q3 EBITDA Margin = Q3 EBITDA / Q3 Rev — compare vs TTM."),

    # ── Balance sheet ───────────────────────────────────────────────────────
    "D/E Ratio": ("<0.3 excellent | >2 risky | >3 danger",
                  "<0.3: +8 Fundamental, +2 Storm | 0.3–1: +4 | >2: −10 | >3: BS ALERT"),
    "D/E": ("<0.3 excellent | >2 risky",
            "<0.3: +8 Fundamental, +2 Storm | >2: −10 | >3: BS ALERT"),
    "ND/EBITDA": ("<2 safe | >4 risky",
                  "Net Debt / EBITDA = years to repay debt from operating cash.\n"
                  "Safety score impact:\n"
                  "  <1 (nearly debt-free): +5\n"
                  "  <0: Net cash position (separate +6 via Cash vs Debt check)"),
    "Int Coverage": (">5 safe | <2 danger",
                     "EBIT / Interest Expense. Higher = easier to service debt.\n"
                     "Safety score impact:\n"
                     "  >10: Very safe → +5\n"
                     "  5–10: Safe → +2\n"
                     "  <2: Danger | <1: Critical"),
    "Current Ratio": (">2 healthy | 1–2 adequate | <1 risky",
                      "Score: >2 = +6 | >1.5 = +3 | <1 = −7"),
    "Quick Ratio": (">1 safe | <0.5 risky",
                    "(Current Assets − Inventory) / Current Liabilities"),
    "Cash (₹Cr)": ("Higher = stronger safety net",
                   "Safety score impact:\n"
                   "  Cash > Total Debt (NET CASH COMPANY) → +6\n"
                   "Cash reserves are the first line of defence in a downturn."),
    "Total Debt (₹Cr)": ("Lower = better | 0 = ideal",
                          "Compare with Cash and EBITDA."),
    "FCF (₹Cr)": (">0 cash generator | <0 cash consuming",
                  "Score: >0 = +2 Storm, +3 Safety | <0: cash consuming."),
    "FCF Yield %": (">6% undervalued | >3% fair",
                    "Score: >6% = +6 | >3% = +3 | <0% = −5"),
    "CCC Days": ("Lower / negative = more efficient",
                 "Cash Conversion Cycle = DIO + DSO − DPO.\n"
                 "Negative CCC = collects cash before paying suppliers."),
    "Div Yield %": (">2% good income | >4% check sustainability",
                    ">2%: +1 Storm Score"),
    "Payout Ratio %": ("40–60% balanced | >80% unsustainable",
                       "30–60%: Balanced | >80%: Check FCF coverage."),
    "Capex / Rev %": ("<5% asset-light | >15% capital-heavy",
                      "<3%: Asset-light = high FCF."),

    # ── Ownership ───────────────────────────────────────────────────────────
    "Promoter %": (">50% aligned | <20% concern",
                   "Score: >50% = +5 | >35% = +2 | <20% = −3"),
    "Pro QoQ Δ": (">0.3% buying signal | negative = selling",
                  "Promoter shareholding change vs previous quarter.\n"
                  "Sentiment score impact:\n"
                  "  >+0.5%: Promoters buying → +5\n"
                  "  <−0.5%: Promoters selling → −5\n"
                  "Storm score also +1 if >0.3%"),
    "Pledge %": ("0% ideal | >10% watch | >20% RED FLAG",
                 "Safety score impact:\n"
                 "  0%: Clean cap structure → +4 (rewarded, not just neutral)\n"
                 "  10–20%: Watch → −7\n"
                 "  >20%: RED FLAG → −15, plus suppresses ALL spike signals"),
    "Pledge Direction": ("FALLING = positive | RISING = risk",
                         "Trend in promoter pledge levels.\n"
                         "Sentiment score impact:\n"
                         "  FALLING: Promoters deleveraging → +3\n"
                         "  RISING: More pledging / stress → −5\n"
                         "  STABLE: No change → 0"),
    "FII %": (">15% institutional backed",
              ">25%: High global interest | Rising FII = strong signal."),
    "FII QoQ Δ": (">1% accumulation | <−1% selling",
                  "+8 EE if >1% | +1 Storm if >0.3%."),
    "DII %": (">10% domestic confidence",
              "Rising DII + FII = dual institutional accumulation = bullish."),
    "DII QoQ Δ": (">0.5% domestic accumulation",
                  "Domestic institutional (MF/insurance) holding change.\n"
                  "Sentiment score impact:\n"
                  "  >+0.5%: Strong DII accumulation → +6\n"
                  "  +0.3–0.5%: Moderate accumulation → +4\n"
                  "  <−0.3%: DII distribution → −3"),
    "Public Float %": (">50% good liquidity | <20% manipulation risk",
                       "<20%: Volatile, easier to manipulate."),

    # ── Forensics ───────────────────────────────────────────────────────────
    "Piotroski F /9": ("≥7 strong | ≤3 weak",
                       "9 criteria: Profitability (4) + Leverage/Liquidity (3) + Efficiency (2)\n"
                       "8–9: Excellent | 6–7: Good | ≤3: Avoid\n"
                       "Safety score impact:\n"
                       "  F ≥ 7 → +6\n"
                       "  F = 5–6 → +3\n"
                       "Computed from free yfinance data (Session 14+20); typical\n"
                       "distribution on a real run: 4–8 range, with most quality\n"
                       "stocks scoring 6–8."),
    "Altman Z": (">2.99 safe | <1.81 distress zone",
                 "<1.81: Triggers anti-trigger guard (Spike suppressed).\n"
                 "Requires balance-sheet inputs (working capital, retained\n"
                 "earnings, EBIT, total assets, total liabilities) which\n"
                 "yfinance free data doesn't provide. Column displays '—'\n"
                 "(em-dash) for stocks without the required BS feed."),
    "Beneish M": ("<−2.22 honest | >−2.22 possible manipulation",
                  ">−2.22: Triggers anti-trigger guard (Spike suppressed).\n"
                  "Requires net income, cash flow from operations, and total\n"
                  "assets from the balance sheet — yfinance free data doesn't\n"
                  "provide these. Column displays '—' (em-dash) for stocks\n"
                  "without the required BS feed."),
    "Earn Quality": ("HIGH = cash-backed earnings",
                     "HIGH: Cash flow matches profits | LOW: Accounting concern."),

    # ── Catalysts ───────────────────────────────────────────────────────────
    "OB/Bill Ratio": (">1 strong pipeline | >3 excellent visibility",
                      ">3: 3+ year revenue visibility. For infra/defence/engineering."),
    "Pipeline Vis": ("HIGH = strong revenue visibility",
                     "HIGH: Strong order book or recurring revenue."),
    "L1 Wins 90D": ("Recent govt contract wins", ">3: Active and winning bidder."),
    "L1 Est (₹Cr)": ("Estimated govt contract value",
                     "Higher = more near-term revenue locked in."),
    "New Mkt Entry": ("YES = new revenue stream potential",
                      "YES: New geography or product launch."),

    # ── Signals ─────────────────────────────────────────────────────────────
    "Early Signals": ("Signals fired today — more = higher conviction",
                      "EE + spike signals: VOL SURGE + RSI | TREND CONFLUENCE\n"
                      "TECHNICAL BREAKOUT | INSTITUTIONAL FOOTPRINT | 52W BREAKOUT"),
    "Sector Stage": ("Stage 2 = best entry | Stage 4 = avoid / exit",
                     "STAGE 1 EARLY ACCUM: Smart money entering\n"
                     "STAGE 2 CONFIRMED UPTREND: All signals aligned (BEST ENTRY)\n"
                     "STAGE 3 MOMENTUM PEAK: Overbought (caution)\n"
                     "STAGE 4 DISTRIBUTION: Smart money exiting (avoid)"),
    "Smart Money": ("ACCUMULATION = institutional buying",
                    "Possible values:\n"
                    "  HIGH DELIVERY BUYING — delivery >70% with good volume\n"
                    "  RSI ACCUMULATION ZONE — RSI in 50-65 with sideways action\n"
                    "  INST ACCUMULATION — block deals + delivery uptick\n"
                    "  INSIDER BUYING — promoter/director buys on record\n"
                    "  FII INCREASING — FII QoQ holding up (paid data)\n"
                    "  PROMOTER BUYING — promoter QoQ up (paid data)\n"
                    "  NEUTRAL — none of above"),

    # ── Technical ───────────────────────────────────────────────────────────
    "SMA 200": ("CMP > SMA200 = bull trend confirmed",
                "200-Day SMA — the long-term trend anchor.\n"
                "Technical score impact (new):\n"
                "  CMP > SMA200 × 1.02 (clearly above) → +3\n"
                "  CMP < SMA200 × 0.98 (clearly below) → −3\n"
                "Golden Cross (50>200) = major buy | Death Cross = major sell."),
    "Supertrend": ("BUY=uptrend | SELL=downtrend | NEUTRAL=sideways",
                   "BUY: Price > SMA20 + 0.5×ATR14 | SELL: Price < SMA20 − 0.5×ATR14"),
    "ADX": (">25 strong trend | <20 weak / sideways",
            "Measures TREND STRENGTH (not direction).\n"
            "Technical score impact:\n"
            "  >30: Established trend → +7\n"
            "  25–30: Strong → +5\n"
            "  20–25: Moderate → +2\n"
            "  <20: Weak / sideways → 0"),
    "RSI (14)": ("45–65 sweet spot | >70 overbought | <30 oversold",
                 "Technical score impact:\n"
                 "  60–70: SWEET SPOT → +10 (highest reward — strong + not overbought)\n"
                 "  >70: Overbought → +8 (rewarded but capped)\n"
                 "  50–60: Mildly bullish → +4\n"
                 "  40–50: Mildly bearish → −4 | <40: Bearish → −8\n"
                 "  45–65 also the ideal entry zone for spike triggers (T4)"),
    "MACD Signal": ("BUY = bullish crossover | SELL = bearish",
                    "BUY: +6 Technical | SELL: −6 Technical"),
    "Stoch %K": ("20–40 accumulation zone | >80 overbought",
                 "20–40: Accum zone (+5) | >80: Overbought (−3)"),
    "MFI": (">60 money inflow | <30 outflow",
            ">60: +4 Technical | <30: −3 Technical"),
    "OBV Signal": ("RISING = accumulation | FALLING = distribution",
                   "RISING: +4 Technical | FALLING: −4 Technical"),
    "Above VWAP": ("YES = institutional support | NO = weak",
                   "YES: +4 Technical | NO: −2 Technical"),
    "Chart Pattern": ("Today's candle pattern from OHLC",
                      "Detected from open/high/low/close + previous close.\n"
                      "Possible values:\n"
                      "  BULLISH CANDLE — close > open > prev close +1%\n"
                      "  BEARISH CANDLE — close < open < prev close -1%\n"
                      "  DOJI — body very small (indecision, possible reversal)\n"
                      "  HAMMER — long lower wick (bullish reversal signal)\n"
                      "  HANGING MAN — long lower wick at top (bearish reversal)\n"
                      "  SHOOTING STAR — long upper wick (bearish reversal)\n"
                      "  UPPER CIRCUIT — hit upper price band (no trading room left)\n"
                      "  LOWER CIRCUIT — hit lower price band (forced sellers stuck)\n"
                      "  NEUTRAL — none of above; sideways action\n"
                      "  '—' — OHLC data incomplete"),
    "Pattern": ("Today's candle pattern from OHLC",
                "Same as Chart Pattern, abbreviated for Gold sheet.\n"
                "Values: BULLISH CANDLE | BEARISH CANDLE | DOJI | HAMMER\n"
                "       HANGING MAN | SHOOTING STAR | NEUTRAL\n"
                "       UPPER CIRCUIT | LOWER CIRCUIT | '—'"),

    # ── Support / resistance ────────────────────────────────────────────────
    "Support 1 (₹)": ("Nearest support = buy zone floor",
                      "20-day rolling low. Breach = bearish. Used for Stop Loss."),
    "Support 2 (₹)": ("Deeper support level",
                      "40-day rolling low. Next level if Support 1 breaks."),
    "Resist 1 (₹)": ("First resistance = Target 1",
                     "20-day rolling high. Breakout with volume = bullish."),
    "Resist 2 (₹)": ("Stronger resistance = Target 2",
                     "40-day rolling high. Used as Target 2."),

    # ── Balance sheet health ────────────────────────────────────────────────
    "BS Health Flag": ("HEALTHY = safe | WATCH = monitor | ALERT = danger",
                       "HEALTHY: No red flags\n"
                       "WATCH: One concern (D/E>2 or low liq or neg FCF)\n"
                       "ALERT: Serious (pledge>20% or D/E>3 or leveraged + neg FCF)"),
    "BS Health Note": ("Explains the health flag",
                       "Examples: NET CASH COMPANY | HIGH D/E 2.5× | NEGATIVE FCF | HIGH PLEDGE"),

    # ── Trade plan ──────────────────────────────────────────────────────────
    "Entry Range (₹)": ("Ideal buy zone = CMP ± 0.5 × ATR",
                         "Avoid chasing if CMP moves significantly above upper bound."),
    "Stop Loss (₹)": ("Exit if CMP closes below this level",
                       "Never risk >2–3% of portfolio per trade."),
    "Target 1 (₹)": ("First target = Resistance 1",
                     "Book 30–50% of position here. R:R should be >1:2."),
    "Target 2 (₹)": ("Second target = Resistance 2",
                     "Hold remainder after Target 1."),
    "Target 3 (₹)": ("Final target = Fair Value (CFV)",
                     "High MoS stocks can give 20–50% upside."),
    "Time Horizon": ("How long to hold",
                     "SHORT TERM: 2–4 weeks (BUY + Spike≥2)\n"
                     "POSITIONAL: 1–3 months (Score≥68 + ST=BUY)\n"
                     "LONG TERM: 3–12 months (Score≥72 + no spike)"),
    "Horizon": ("How long to hold",
                "SHORT TERM: 2–4 weeks | POSITIONAL: 1–3 mo | LONG TERM: 3–12 mo"),
    "Risk Level": ("LOW=safest | VERY HIGH=speculative only",
                   "LOW: High score + low beta + low D/E + no pledge\n"
                   "MEDIUM: Acceptable | HIGH: Small/micro | VERY HIGH: Speculative"),
    "R:R Ratio": ("Aim for >1:2 | Higher = better risk/reward",
                  "R:R = (T1 − Entry mid) / (Entry mid − SL)\n"
                  ">2: Excellent | 1–2: Acceptable | <1: Avoid\n\n"
                  "Session 22: T1 is now auto-derived to ensure R:R ≥ 2.0:\n"
                  "  T1 = max(Entry + 2×risk_distance, CFV-weighted target)\n"
                  "Stocks with high CFV will get value-anchored targets;\n"
                  "stocks without CFV get pure risk-symmetric targets."),

    # ── Narrative / AI ──────────────────────────────────────────────────────
    "Key Catalyst": ("Primary near-term growth driver",
                     "Product launch, order win, policy tailwind, expansion."),
    "News Sentiment": ("POSITIVE = tailwind | NEGATIVE = headwind",
                       "AI-analysed sentiment from recent company news.\n"
                       "Sentiment score impact:\n"
                       "  POSITIVE: Favourable → +4\n"
                       "  NEUTRAL: No news movement → 0\n"
                       "  NEGATIVE: Headwinds → −5"),
    "Primary Risk": ("Biggest downside risk", "Always read before investing."),
    "SEBI Flags": ("NONE = clean | Any flag = investigate first",
                   "Any flag = investigate before buying."),
    "View Analysis Summary": ("Claude AI investor narrative (150–250 words)",
                              "Business quality, ratios, risks, catalysts, verdict rationale.\n"
                              "Generated fresh each trading day."),

    # ── Alert Log specific ──────────────────────────────────────────────────
    "Date": ("Date alert was generated", "Trading day of the alert."),
    "Time (IST)": ("Time pipeline ran",
                   "Typically 05:00–05:30 IST on trading days."),
    "Alert Type": ("Type of signal that fired",
                   "SPIKE FIRED | EARLY MOVER | SMART MONEY | SCORE DEGRADED | EXIT ALERT"),
    "Trigger Detail": ("Full details of the alert",
                       "Shows all signals fired and current score context."),
    "Prev Score": ("Yesterday's composite score",
                   "Compare with New Score to see improvement / decline."),
    "New Score": ("Today's composite score",
                  "Rising = improving | Falling = deteriorating."),
    "Score Δ": ("Score change vs yesterday | +ve = improving",
                "+ve: Getting stronger | −ve: Getting weaker."),
    "Action Required": ("What to do with this stock today",
                        "CONSIDER ENTRY | MONITOR CLOSELY\n"
                        "BUY BUT OVERVALUED — WAIT | SCORE IMPROVED | SCORE DEGRADED"),

    # Session 19: reference-only entries (not actual Excel column headers).
    # These don't get hover tooltips attached to any cell, but they DO show up
    # as reference cards on the 📖 Tooltip Reference sheet, giving the user
    # a quick-reference card for concepts that the Glossary also documents.
    # Keeping Glossary + Tooltip Reference sheet in sync matters — both are
    # discovery surfaces for the same knowledge.
    "Gold-Tier Filter": (
        "8-condition filter for the Gold – Early Movers sheet",
        "ALL 8 conditions must be true for Gold qualification:\n"
        "  1. Verdict = BUY (not WATCHLIST or weaker)\n"
        "  2. Composite Score ≥ 70\n"
        "  3. 15% ≤ MoS ≤ 100% (real upside, not phantom)\n"
        "  4. Storm Score ≥ 5 (defensively sound)\n"
        "  5. RSI ≤ 70 (not already overbought)\n"
        "  6. BS Health Flag ≠ ALERT\n"
        "  7. Pledge % ≤ 10 (clean cap structure)\n"
        "  8. Not spike-suppressed (no anti-trigger guard fire)\n\n"
        "Daily count will vary: some days 0-3 stocks, some days 8+. Filter "
        "reflects market reality, not a fixed quota."),
    "CFV Safety Cap": (
        "Composite Fair Value capped at 3× CMP",
        "CFV is capped at 3× Current Market Price as a safety net, which\n"
        "means MoS never exceeds approximately 200%. Prevents any single\n"
        "model misbehaving (e.g., DCF on a low-beta stock with tiny\n"
        "terminal-value denominator) from distorting the composite.\n\n"
        "If you see MoS near 200%, treat it as 'deeply undervalued by\n"
        "model — verify inputs' rather than a guaranteed bargain."),
    "M1 DCF Safety Cap": (
        "M1 DCF individually capped at 4× CMP",
        "M1 DCF (30% weight in CFV) has two guardrails:\n"
        "  1. WACC floor at 10%: prevents low-beta stocks producing\n"
        "     tiny (WACC − terminal_growth) denominators that blow up\n"
        "     the terminal value calculation.\n"
        "  2. Output capped at 4× CMP: catches residual extreme cases\n"
        "     after the WACC floor.\n\n"
        "Reflects that Indian equity discount rates below 10% are\n"
        "unrealistic given the ~6.8% risk-free rate and true equity\n"
        "risk premium (typically 6-8%, not the default 5.5%)."),
}


# ════════════════════════════════════════════════════════════════════════════
# GROUP-HEADER TOOLTIPS (section-level, above data-column tips)
# ════════════════════════════════════════════════════════════════════════════
# Hover over the merged section headers in Full Dashboard row 3 and Gold
# sheet row 4 (IDENTITY / SCORES / FAIR VALUE / PROFITABILITY / etc.) and
# explain what the whole section is about. Complementary to per-column tips.

GROUP_TIPS: Dict[str, Tuple[str, str]] = {
    "IDENTITY": ("Who the stock is",
                 "Symbol, company name, sector, exchange, cap category.\n"
                 "Identifies the stock and its segment — not scored."),
    "SCORES": ("Core conviction scores — read this first",
               "Verdict • Composite /100 • Early Entry /100 • Spike /6 • Storm /10.\n"
               "Summarises the whole analysis in one strip."),
    "PRICE & MARKET": ("Current price, volume, liquidity, volatility",
                       "CMP, Day Chg, 52W range, volume spike, delivery %, beta.\n"
                       "What the market is doing with this stock TODAY."),
    "PRICE": ("Current market price",
              "CMP — most recent close. Compare against FAIR VALUE columns."),
    "WEEKLY CHANGE %": ("Multi-window returns — momentum direction",
                        "2/4/6/8-week returns. 2W>4W = accelerating (bullish).\n"
                        "All positive + rising = sustained uptrend."),
    "FAIR VALUE": ("What the stock SHOULD be worth",
                   "Composite Fair Value (CFV) from 7 models + MoS % + Upside.\n"
                   "Individual models: M1 DCF (30%), M2 Graham (15%), M3 PE (20%),\n"
                   "M4 PB (15%), M5 EV/EBITDA (10%), M6 DDM (5%), M7 PEG (5%).\n"
                   "CMP < CFV → undervalued (buying opportunity)."),
    "VALUATION": ("Traditional valuation ratios",
                  "P/E, Earnings Yield, P/CF, PEG, P/B, P/S, EV/EBITDA.\n"
                  "Lower = cheaper. Compare against sector medians."),
    "PROFITABILITY": ("How well the business makes money",
                      "ROE, ROCE, ROA, Gross/EBITDA/Net margins, quarterly NPM trend.\n"
                      "Higher + improving = higher fundamental score."),
    "GROWTH": ("Revenue & earnings trajectory",
               "1-year & 3-year CAGRs, YoY growth, last quarter absolute numbers.\n"
               "PAT growing faster than revenue = operating leverage."),
    "FIN HEALTH": ("Balance sheet safety",
                   "D/E, ND/EBITDA, int coverage, liquidity ratios, cash, debt,\n"
                   "FCF, CCC days, dividend yield. Strong here = survives downturns."),
    "CAP ALLOC": ("How management deploys cash",
                  "Dividend yield, payout ratio, capex/revenue ratio.\n"
                  "Low capex + high FCF = asset-light compounder."),
    "SHAREHOLDING": ("Who owns the stock — and how that's changing",
                     "Promoter %, pledge %, FII %, DII %, public float + QoQ deltas.\n"
                     "Rising institutional ownership = conviction signal."),
    "QUALITY SCORES": ("Forensic & accounting-quality checks",
                       "Piotroski F-Score, Altman Z, Beneish M, earnings quality.\n"
                       "Red flags here trigger the anti-trigger guard."),
    "PIPELINE / OB": ("Forward revenue visibility",
                      "Order book ratio, pipeline visibility, L1 contract wins/value,\n"
                      "new market entry. Relevant for infra / defence / engineering."),
    "EARLY DETECTION": ("Signals firing NOW — catch stocks before consensus",
                        "Early Signals list, Smart Money flow, Sector Stage.\n"
                        "Stage 2 + signals firing = classic accumulation zone."),
    "TECHNICAL": ("Chart & momentum indicators",
                  "SMA 200, Supertrend, ADX, RSI, MACD, Stoch %K, MFI, OBV, VWAP,\n"
                  "chart pattern. Confirms entry timing for a qualifying BUY."),
    "BALANCE SHEET": ("Balance sheet health flag",
                      "HEALTHY / WATCH / ALERT status + explanatory note.\n"
                      "ALERT suppresses spike signals and penalises safety score."),
    "TRADE PLAN": ("Actionable entry / stop / targets",
                   "Entry range, Stop Loss, Target 1/2/3, Time Horizon, Risk Level.\n"
                   "Only act when CMP is inside the entry range — don't chase."),
    "NEWS & RISK": ("Narrative context for the trade",
                    "Key catalyst, news sentiment, primary risk, SEBI flags.\n"
                    "Read before entering — catches things the numbers miss."),
    "NEWS": ("Sentiment + risk summary",
             "Key catalyst, news sentiment, primary risk."),
    "ANALYSIS SUMMARY": ("Full AI-written investor memo",
                         "150–250-word narrative from Claude AI covering quality,\n"
                         "ratios, risks, catalysts, and verdict rationale.\n"
                         "Generated fresh each trading day."),
    "KEY METRICS": ("Essential ratios for gold-tier candidates",
                    "P/E, PEG, ROE, D/E, PAT YoY, Piotroski F.\n"
                    "Quick-glance fundamentals for early-mover candidates."),
}


# ════════════════════════════════════════════════════════════════════════════
# CONTEXT-APPROPRIATE ICONS PER METRIC FAMILY
# ════════════════════════════════════════════════════════════════════════════
_ICON_FAMILIES = {
    "🎯": {"Verdict", "Score /100", "Early Entry /100", "Spike Score /6", "Spike /6",
           "Storm Score /10", "Storm /10", "F-Score /9", "Action Required",
           "Gold-Tier Filter"},
    "💰": {"CFV (₹)", "FV Low (₹)", "FV High (₹)", "MoS %", "MoS Label",
           "Upside to FV %", "Upside %", "P/E TTM", "P/E", "Earn Yield %",
           "P/CF", "PEG Ratio", "PEG", "P/B", "P/S", "EV/EBITDA",
           "Div Yield %", "Payout Ratio %",
           "M1: DCF FV (₹)", "M2: Graham FV (₹)", "M3: PE FV (₹)",
           "M4: PB FV (₹)", "M5: EV FV (₹)", "M6: DDM FV (₹)", "M7: PEG FV (₹)",
           "CFV Safety Cap", "M1 DCF Safety Cap"},
    "📈": {"CMP (₹)", "Day Chg %", "52W High (₹)", "52W Low (₹)",
           "Chg% [2-Weekly]", "Chg% [4-Weekly]", "Chg% [6-Weekly]", "Chg% [8-Weekly]",
           "Chg% [2-Wk]", "Chg% [4-Wk]", "Chg% [6-Wk]", "Chg% [8-Wk]"},
    "📊": {"SMA 200", "Supertrend", "ADX", "RSI (14)", "MACD Signal",
           "Stoch %K", "MFI", "OBV Signal", "Above VWAP", "Chart Pattern", "Pattern",
           "Vol Spike (×50D)", "Delivery %", "Beta"},
    "🏛": {"Promoter %", "Pro QoQ Δ", "FII %", "FII QoQ Δ", "DII %", "DII QoQ Δ",
           "Public Float %", "Smart Money"},
    "🛡": {"D/E Ratio", "D/E", "ND/EBITDA", "Int Coverage", "Current Ratio",
           "Quick Ratio", "Cash (₹Cr)", "Total Debt (₹Cr)", "FCF (₹Cr)",
           "FCF Yield %", "CCC Days", "Pledge %", "Pledge Direction",
           "BS Health Flag", "BS Health Note", "Altman Z", "Piotroski F /9"},
    "⚠": {"Beneish M", "Primary Risk", "SEBI Flags", "Risk Level", "Earn Quality"},
    "🚀": {"Rev CAGR 1Y %", "Rev CAGR 3Y %", "PAT CAGR 1Y %", "PAT CAGR 3Y %",
           "EBITDA CAGR 1Y %", "Rev YoY %", "PAT YoY %", "Margin Expansion",
           "Q3 Rev (₹Cr)", "Q3 PAT (₹Cr)", "Q3 EBITDA (₹Cr)",
           "Gross Mgn %", "EBITDA Mgn %", "NPM %", "NPM Q1 %", "NPM Q2 %", "NPM Q3 %",
           "ROE %", "ROCE %", "ROA %", "Early Signals", "Sector Stage",
           "OB/Bill Ratio", "Pipeline Vis", "L1 Wins 90D", "L1 Est (₹Cr)",
           "New Mkt Entry", "Capex / Rev %", "Key Catalyst"},
    "🎚": {"Entry Range (₹)", "Stop Loss (₹)", "Target 1 (₹)", "Target 2 (₹)",
           "Target 3 (₹)", "Time Horizon", "Horizon", "R:R Ratio",
           "Support 1 (₹)", "Support 2 (₹)", "Resist 1 (₹)", "Resist 2 (₹)"},
    "🏷": {"Symbol", "Company Name", "Company", "Sector", "Exchange",
           "Cap Category", "Date", "Time (IST)", "Alert Type", "Trigger Detail",
           "Prev Score", "New Score", "Score Δ", "BSE Code",
           "IDENTITY"},
    "📰": {"News Sentiment", "View Analysis Summary",
           "NEWS", "NEWS & RISK", "ANALYSIS SUMMARY"},
}

# Group-name → icon family mapping (supplements _ICON_MAP above for section tips)
_GROUP_ICON_EXTRA = {
    "SCORES":          "🎯",
    "PRICE & MARKET":  "📈",
    "PRICE":           "📈",
    "WEEKLY CHANGE %": "📈",
    "FAIR VALUE":      "💰",
    "VALUATION":       "💰",
    "PROFITABILITY":   "🚀",
    "GROWTH":          "🚀",
    "KEY METRICS":     "🚀",
    "FIN HEALTH":      "🛡",
    "BALANCE SHEET":   "🛡",
    "CAP ALLOC":       "🛡",
    "SHAREHOLDING":    "🏛",
    "QUALITY SCORES":  "⚠",
    "PIPELINE / OB":   "🚀",
    "EARLY DETECTION": "🚀",
    "TECHNICAL":       "📊",
    "TRADE PLAN":      "🎚",
}

_ICON_MAP: Dict[str, str] = {}
for icon, headers in _ICON_FAMILIES.items():
    for h in headers:
        _ICON_MAP[h] = icon


# ════════════════════════════════════════════════════════════════════════════
# COLOR ACCENTS PER FAMILY
# ════════════════════════════════════════════════════════════════════════════
_FAMILY_COLORS = {
    "🎯": ("B45309", "FEF3C7"),
    "💰": ("065F46", "D1FAE5"),
    "📈": ("1D4ED8", "DBEAFE"),
    "📊": ("6366F1", "EEF2FF"),
    "🏛": ("7C3AED", "EDE9FE"),
    "🛡": ("0F766E", "CCFBF1"),
    "⚠": ("B91C1C", "FEE2E2"),
    "🚀": ("DB2777", "FCE7F3"),
    "🎚": ("0891B2", "CFFAFE"),
    "🏷": ("475569", "F1F5F9"),
    "📰": ("92400E", "FED7AA"),
    "💡": ("64748B", "F8FAFC"),
}


def _icon(header: str) -> str:
    # Session 17: check _ICON_MAP (per-column) first, then group-name extras,
    # then fall back to the neutral 💡 glyph. This way section-header tips
    # render with a family-appropriate icon (💰 for FAIR VALUE, 🛡 for FIN
    # HEALTH, 📊 for TECHNICAL, etc.) instead of the generic bulb.
    if header in _ICON_MAP:
        return _ICON_MAP[header]
    if header in _GROUP_ICON_EXTRA:
        return _GROUP_ICON_EXTRA[header]
    return "💡"


# ════════════════════════════════════════════════════════════════════════════
# TIER 1 — POLISHED HOVER TEXT
# ════════════════════════════════════════════════════════════════════════════
_DIVIDER = "━" * 28


def _bulletise_line(line: str) -> list:
    line = line.strip()
    if not line:
        return []
    if "|" in line:
        parts = [p.strip() for p in line.split("|") if p.strip()]
        return [f"  › {p}" for p in parts]
    return [f"  {line}"]


def format_tooltip(header: str, short: str, full: str) -> str:
    """Render the polished Tier-1 hover text for a single header."""
    icon = _icon(header)
    short_lines = _bulletise_line(short) or [f"  {short.strip()}"]

    full_lines = []
    for raw in full.split("\n"):
        full_lines.extend(_bulletise_line(raw))

    body_parts = [f"{icon}  {header.upper()}", _DIVIDER, "QUICK READ", *short_lines]
    if full_lines:
        body_parts += ["", "DETAIL", *full_lines]
    body_parts += [_DIVIDER, "NSE/BSE Analyser · hover cue"]
    return "\n".join(body_parts)


# ════════════════════════════════════════════════════════════════════════════
# TIER 1+2 — APPLY TO WORKSHEET HEADERS
# ════════════════════════════════════════════════════════════════════════════
_CUE = " ⓘ"


def _comment(text: str, width: int = 380, height: int = 260) -> Comment:
    c = Comment(text, "NSE/BSE Analyser")
    line_count = text.count("\n") + 1
    c.width = width
    # Session 20: height ceiling raised from 420→540 so post-Session-16
    # tooltips (Piotroski, Score /100, Early Entry /100 — all gained scoring
    # detail in their "DETAIL" sections) render fully without clipping.
    c.height = max(height, min(18 * line_count + 40, 540))
    return c


def apply_tooltips(
    ws,
    header_row: int,
    col_headers: Iterable[str],
    *,
    add_cue: bool = True,
    ref_anchors: Optional[Dict[str, int]] = None,
    ref_sheet_name: str = "📖 Tooltip Reference",
) -> None:
    """Attach polished tooltips + ⓘ cue + optional ref-link to every header
    cell that has an entry in TIPS. Headers without entries are skipped (no
    error — keeps backward compatibility with legacy _apply_col_tips behavior).
    """
    for ci, h in enumerate(col_headers, 1):
        if h not in TIPS:
            continue
        short, full = TIPS[h]
        cell = ws.cell(header_row, ci)

        # Tier 1: polished hover
        cell.comment = _comment(format_tooltip(h, short, full))

        # Tier 2: visible ⓘ cue on header text
        if add_cue and isinstance(cell.value, str) and not cell.value.endswith(_CUE):
            cell.value = f"{cell.value}{_CUE}"

        # Tier 3: hyperlink to reference row
        if ref_anchors and h in ref_anchors:
            cell.hyperlink = f"#'{ref_sheet_name}'!A{ref_anchors[h]}"


# ════════════════════════════════════════════════════════════════════════════
# TIER 3 — REFERENCE SHEET BUILDER
# ════════════════════════════════════════════════════════════════════════════

def _fill(hex_): return PatternFill("solid", fgColor=hex_)
def _font(bold=False, color="1F2937", size=10, italic=False):
    return Font(name="Segoe UI", bold=bold, color=color, size=size, italic=italic)
def _thin(color="D1D5DB"):
    s = Side(style="thin", color=color)
    return Border(left=s, right=s, top=s, bottom=s)


_FAMILY_ORDER = ["🎯", "💰", "📈", "📊", "🚀", "🛡", "🏛", "⚠", "🎚", "🏷", "📰", "💡"]
_FAMILY_LABEL = {
    "🎯": "Verdicts & Composite Scores",
    "💰": "Valuation & Fair Value",
    "📈": "Price & Momentum",
    "📊": "Technical Indicators",
    "🚀": "Growth & Margins",
    "🛡": "Balance Sheet & Safety",
    "🏛": "Ownership & Institutional",
    "⚠": "Risk & Forensics",
    "🎚": "Trade Plan",
    "🏷": "Identity & Metadata",
    "📰": "Narrative & Sentiment",
    "💡": "Other",
}


def build_reference_sheet(wb, sheet_name: str = "📖 Tooltip Reference") -> Dict[str, int]:
    """Create the rich Tooltip Reference sheet. Returns {header -> row}
    mapping so callers can pass it as ref_anchors to apply_tooltips()."""
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(sheet_name)
    ws.sheet_properties.tabColor = "475569"
    ws.sheet_view.showGridLines = False

    for i, w in enumerate([3, 34, 78, 14], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # Title banner
    ws.merge_cells("A1:D1")
    t = ws.cell(1, 1, "TOOLTIP REFERENCE  ·  Full explanations for every analyser metric")
    t.fill = _fill("1E3A8A"); t.font = _font(True, "FFFFFF", 13)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34

    # Sub-instructions
    ws.merge_cells("A2:D2")
    s = ws.cell(2, 1,
                "Every column header in the dashboard is explained here. "
                "Click the ⓘ on any header to jump straight to its card.")
    s.fill = _fill("F1F5F9"); s.font = _font(False, "334155", 9, italic=True)
    s.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 28

    by_family: Dict[str, list] = {k: [] for k in _FAMILY_ORDER}
    for h in TIPS.keys():
        fam = _icon(h)
        by_family.setdefault(fam, []).append(h)

    anchors: Dict[str, int] = {}
    row = 4

    for fam in _FAMILY_ORDER:
        headers = sorted(by_family.get(fam, []))
        if not headers:
            continue
        accent, pale = _FAMILY_COLORS[fam]

        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        c = ws.cell(row, 1, f"  {fam}   {_FAMILY_LABEL[fam]}  ({len(headers)} metric{'s' if len(headers) != 1 else ''})")
        c.fill = _fill(accent); c.font = _font(True, "FFFFFF", 12)
        c.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[row].height = 28
        row += 1

        for h in headers:
            short, full = TIPS[h]
            anchors[h] = row

            lbl = ws.cell(row, 2, h)
            lbl.fill = _fill(pale); lbl.font = _font(True, accent, 11)
            lbl.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True, indent=1)
            lbl.border = _thin(accent)

            body = f"{short}\n\n{full}" if full else short
            ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=3)
            cnt = ws.cell(row, 3, body)
            cnt.fill = _fill("FFFFFF"); cnt.font = _font(False, "1F2937", 10)
            cnt.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True, indent=1)
            cnt.border = _thin()

            nav = ws.cell(row, 4, "↑ top")
            nav.fill = _fill("FFFFFF"); nav.font = _font(False, accent, 9, italic=True)
            nav.alignment = Alignment(horizontal="center", vertical="top")
            nav.border = _thin()
            nav.hyperlink = f"#'{sheet_name}'!A1"

            line_count = body.count("\n") + max(1, len(body) // 80)
            ws.row_dimensions[row].height = max(38, min(18 * line_count + 10, 180))
            row += 1

        row += 1

    ws.freeze_panes = "A4"
    return anchors


# ════════════════════════════════════════════════════════════════════════════
# GROUP-HEADER TOOLTIP APPLICATION
# ════════════════════════════════════════════════════════════════════════════

def apply_group_tooltips(ws, header_row: int,
                          groups) -> None:
    """Attach a hover tooltip to each group (section) header.

    `groups` is an iterable of (start_col, name, color, span) tuples —
    same shape as FULL_GROUPS / GOLD_GROUPS in excel_generator. The tooltip
    is placed on the FIRST cell of each merged group (where the label lives).

    Session 17: added so section headers (IDENTITY, SCORES, FAIR VALUE, etc.)
    hover with an explanation of what the whole section covers. Complementary
    to per-column tooltips — the group tip is an orientation aid when scanning
    left-to-right across the wide dashboard.
    """
    for sc, nm, _color, _span in groups:
        if nm not in GROUP_TIPS:
            continue
        short, full = GROUP_TIPS[nm]
        cell = ws.cell(header_row, sc)
        cell.comment = _comment(format_tooltip(nm, short, full),
                                 width=340, height=200)