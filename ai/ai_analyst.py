"""
ai_analyst.py
SECTION 0D & 7 — AI Batch Analysis Engine (v7 FINAL)

Switched from Anthropic Claude to Google Gemini (google-genai SDK).
Master prompt v7 goes into the system_instruction parameter.
Batch data goes into the `contents` parameter.
"""

import os
import time
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from dotenv import load_dotenv
from analysis.fundamental_engine import FundamentalEngine


def _sf(val, default=0.0):
    """Safe float — handles '—', None, '', non-numeric strings."""
    if val is None or val == "" or str(val) in ("—", "--", "N/A"):
        return float(default)
    try:
        return float(val)
    except (ValueError, TypeError):
        return float(default)

load_dotenv()

# ── Gemini API key resolution ─────────────────────────────────────────────────
# The google-genai SDK auto-picks up GEMINI_API_KEY or GOOGLE_API_KEY from env.
# We still validate explicitly so we fail fast with a clear error message.
_gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not _gemini_key:
    raise ValueError(
        "CRITICAL ERROR: GEMINI_API_KEY (or GOOGLE_API_KEY) is missing from "
        "environment secrets. Add it to GitHub Secrets or your .env file. "
        "Get a key at https://aistudio.google.com/apikey"
    )

client = genai.Client(api_key=_gemini_key)

# Gemini model — 2.5 Pro is the strongest reasoning model for equity analysis.
# Swap to "gemini-2.5-flash" if you want faster/cheaper runs at some quality cost.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

MASTER_PROMPT_PATH = "master_prompt/NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt"


def _load_master_prompt() -> str:
    try:
        with open(MASTER_PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"⚠️  Master prompt not found at {MASTER_PROMPT_PATH}. Using minimal fallback.")
        return (
            "You are a senior equity research analyst. "
            "For each stock in the data batch, produce a concise investor card "
            "with: verdict, fair value estimate, key strengths, key risks, "
            "and a 150-word analysis summary."
        )


def _is_quota_error(err: Exception) -> bool:
    """Detect Gemini quota/billing exhaustion — no point retrying these."""
    err_str = str(err).lower()
    quota_markers = (
        "resource_exhausted",
        "quota",
        "billing",
        "insufficient",
        "permission_denied",
        "429",
    )
    return any(m in err_str for m in quota_markers)


def get_ai_analysis(stock_list_df) -> str:
    """
    SECTION 0D & 3: Grounded Batch Processing via Google Gemini.

    Pre-calculates Graham Number, PEG Ratio, and CFV using FundamentalEngine
    so the AI uses our computed values rather than estimating them.

    Batch size: 10–15 stocks per API call (Section 0D).
    Rate limiting: 2s delay between batches.
    Timeout/retry: 1 retry per failed batch.
    """
    all_investor_cards = []
    batch_size = 12  # 10-15 per Section 0D

    engine       = FundamentalEngine()
    master_prompt = _load_master_prompt()

    # ── Pre-calculation: Inject hard math into the DataFrame ──────────────────
    print("🧮 Running Python Fundamental Engine pre-calculations...")
    import pandas as pd
    if not isinstance(stock_list_df, pd.DataFrame):
        stock_list_df = pd.DataFrame(stock_list_df)

    for index, row in stock_list_df.iterrows():
        stock_list_df.at[index, "Graham_No"] = engine.calculate_graham_number(
            _sf(row.get("eps", 0)), _sf(row.get("bvps", 0)),
        )
        stock_list_df.at[index, "PEG_Ratio"] = engine.calculate_peg_ratio(
            _sf(row.get("pe", 0)),
            _sf(row.get("pat_cagr_3y", row.get("growth_rate", 0))),
        )
        models_data = {
            "DCF": _sf(row.get("M1_DCF", row.get("dcf_val", 0))),
            "PE":  _sf(row.get("M3_PE",  row.get("pe_val",  0))),
            "PEG": _sf(row.get("M7_PEG", row.get("peg_val", 0))),
        }
        stock_list_df.at[index, "Calculated_CFV"] = engine.calculate_composite_fair_value(
            str(row.get("symbol", "")),
            str(row.get("sector", "General")),
            models_data,
        )

    def _fmt_stock_card(row):
        """
        Build a rich, structured per-stock context card for the AI.
        This replaces the raw DataFrame.to_string() dump with clearly
        labelled, human-readable data that guides quality Block H rationale.
        """
        def v(k, d="—"): return row.get(k, d) or d

        sym   = v("symbol")
        co    = v("company_name")
        sec   = v("sector")
        cap   = v("cap_category")
        exch  = v("exchange_tag")
        cmp   = v("close")
        h52   = v("high_52w"); l52 = v("low_52w")
        chg   = v("day_change", v("day_chg", "—"))
        vol_r = v("vol_ratio")
        deliv = v("delivery_pct")
        chg2w = v("2w_chg"); chg4w = v("4w_chg")
        cfv   = v("cfv"); fvl = v("cfv_low"); fvh = v("cfv_high")
        mos   = v("mos_pct"); up = v("upside"); mos_lbl = v("mos_label")
        pe    = v("pe"); pb = v("pb"); peg = v("peg")
        ey    = v("earnings_yield"); roe = v("roe"); npm = v("npm")
        de    = v("debt_equity"); cr  = v("current_ratio")
        cash  = v("cash"); fcf = v("fcf"); div = v("div_yield")
        score = v("composite_score"); verdict = v("verdict")
        fs    = v("fundamental_score"); ts = v("technical_score")
        es    = v("early_entry_score"); e_lbl = v("early_label")
        rsi   = v("rsi"); macd = v("macd_signal"); st = v("supertrend"); adx = v("adx")
        entry = v("entry_range"); sl = v("stop_loss")
        t1    = v("t1"); t2 = v("t2"); t3 = v("t3")
        hor   = v("horizon"); risk = v("risk_level")
        bs    = v("bs_status"); bs_note = v("bs_flags")
        sigs  = v("early_signals"); sm = v("smart_money_signals")
        rot   = v("rotation_stage"); sel_rsn = v("selection_reason")
        m1    = v("M1_DCF"); m2 = v("M2_Graham"); m3 = v("M3_PE")
        m4    = v("M4_PB"); m5 = v("M5_EV"); cfv_calc = v("Calculated_CFV")
        graham = v("Graham_No"); peg_calc = v("PEG_Ratio")
        intel = v("intel_queries")
        gm    = v("gross_margin"); em = v("ebitda_margin")
        promo = v("promoter_pct"); fii = v("fii_pct")
        ps    = v("ps"); ev = v("ev_ebitda")

        return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STOCK: {sym} | {co}
Sector: {sec} | Cap: {cap} | Exchange: {exch}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHY SELECTED (pipeline reason): {sel_rsn}

PRICE & MOMENTUM
  CMP: ₹{cmp} ({chg}% today) | 52W: ₹{l52}–₹{h52}
  Vol spike: {vol_r}× avg | Delivery: {deliv}%
  2W: {chg2w}% | 4W: {chg4w}%

FAIR VALUE
  CFV (composite): ₹{cfv} | Range: ₹{fvl}–₹{fvh}
  Calc CFV (engine): ₹{cfv_calc} | Graham Number: ₹{graham}
  MoS: {mos}% [{mos_lbl}] | Upside to FV: {up}%
  Models: DCF ₹{m1} | Graham ₹{m2} | PE-FV ₹{m3} | PB-FV ₹{m4} | EV-FV ₹{m5}

VALUATION
  PE: {pe}x | PB: {pb}x | PS: {ps}x | EV/EBITDA: {ev}x
  PEG: {peg} (calc: {peg_calc}) | Earnings Yield: {ey}% | Div Yield: {div}%

PROFITABILITY
  ROE: {roe}% | Gross Margin: {gm}% | EBITDA Margin: {em}% | Net Margin: {npm}%

FINANCIAL HEALTH
  D/E: {de} | Current Ratio: {cr} | Cash: ₹{cash}Cr | FCF: ₹{fcf}Cr
  Promoter: {promo}% | FII: {fii}%

SCORES
  Overall: {score}/100 [{verdict}] | Fundamental: {fs} | Technical: {ts}
  Early Entry: {es}/100 [{e_lbl}]

TECHNICALS
  RSI: {rsi} | MACD: {macd} | Supertrend: {st} | ADX: {adx}
  Early signals: {sigs}

SECTOR & SMART MONEY
  Rotation stage: {rot} | Smart money: {sm}

BALANCE SHEET
  Status: {bs} | {bs_note}

TRADE PLAN
  Entry: ₹{entry} | SL: ₹{sl} | T1: ₹{t1} | T2: ₹{t2} | T3: ₹{t3}
  Horizon: {hor} | Risk: {risk}

CATALYST SEARCH QUERIES (use for Block H grounding):
  {intel}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    # ── Batch execution ───────────────────────────────────────────────────────
    batches = [
        stock_list_df.iloc[i: i + batch_size]
        for i in range(0, len(stock_list_df), batch_size)
    ]
    total_batches = len(batches)

    grounding_instruction = (
        "You are a senior Indian equity research analyst. "
        "For EACH stock card below, produce a crisp institutional-grade analysis "
        "following the Section 8 format from your system prompt.\n\n"
        "CRITICAL RULES:\n"
        "1. USE the provided Calculated_CFV and Graham_No — do not recalculate.\n"
        "2. For Block H Analysis Summary (150-250 words): write like the research widget "
        "   in our conversation — cite the WHY SELECTED reason, the strongest "
        "   fundamental/technical signal, the key sector tailwind, and the primary risk. "
        "   Use absolute facts and ₹ figures where available. No vague statements.\n"
        "3. Cap-adjusted verdict: LARGE CAP needs score ≥60 for BUY, MICRO CAP needs ≥70.\n"
        "4. If a stock has NO compelling reason (low score, no signals), say so honestly — "
        "   WATCHLIST or NEUTRAL is fine. Quality over hype.\n"
        "5. The Analysis_Summary_Block_H field in your output becomes the last Excel column "
        "   'View Analysis Summary' — make it worth reading.\n"
        "6. v10.17: respect the engine's verdict exactly. If the verdict reads "
        "   'WATCHLIST (thin data)', this is a v10.17 quality-guard demotion — "
        "   the score qualified for BUY but data was too sparse (<3 of 5 sub-score "
        "   dimensions actually fired). Do NOT upgrade it back to BUY in your "
        "   narrative; instead acknowledge that data gaps blocked a confident BUY call."
    )

    # Gemini generation config — master prompt goes into system_instruction.
    # max_output_tokens is the Gemini equivalent of Anthropic's max_tokens.
    gen_config = types.GenerateContentConfig(
        system_instruction=master_prompt,
        max_output_tokens=4096,
        temperature=0.7,
    )

    quota_exhausted = False   # flag to abort all batches on quota error

    for idx, batch in enumerate(batches):
        # Skip remaining batches if quota exhausted
        if quota_exhausted:
            # v12.6 (#14): standardised "[AI <verb> — <reason>]" format.
            all_investor_cards.append(
                f"[AI skipped — Gemini API quota exhausted (batch {idx + 1}). "
                f"Check quota/billing at https://aistudio.google.com/apikey]"
            )
            continue

        print(f"🤖 Processing batch {idx + 1}/{total_batches} "
              f"({len(batch)} stocks) via {GEMINI_MODEL}...")

        # Build rich per-stock cards instead of raw DataFrame dump
        cards = []
        for _, row in batch.iterrows():
            cards.append(_fmt_stock_card(row.to_dict()))
        stock_data_text = "\n".join(cards)

        user_message = (
            f"{grounding_instruction}\n\n"
            f"BATCH {idx + 1}/{total_batches} — {len(batch)} stocks:\n"
            f"{stock_data_text}"
        )

        # Try once; NO retry on quota errors (pointless — quota won't refill mid-run)
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_message,
                config=gen_config,
            )
            card_text = response.text or ""
            if not card_text.strip():
                # Gemini occasionally returns empty text if output was blocked by
                # safety filters or the finish reason is MAX_TOKENS with no parts.
                finish = getattr(
                    getattr(response, "candidates", [None])[0], "finish_reason", "UNKNOWN"
                ) if getattr(response, "candidates", None) else "UNKNOWN"
                # v12.6 (#14): standardised "[AI <verb> — <reason>]" format.
                card_text = (
                    f"[AI unavailable — Gemini returned empty response for batch "
                    f"{idx + 1} (finish_reason={finish}). The batch may have been "
                    f"blocked by safety filters or truncated.]"
                )
            all_investor_cards.append(card_text)
            print(f"   ✅ Batch {idx + 1} complete.")
            # Section 0D: Rate limiting — 2s between successful batches
            if idx < total_batches - 1:
                time.sleep(2)
        except Exception as e:
            # Detect quota exhaustion — no point retrying
            if _is_quota_error(e):
                quota_exhausted = True
                print(f"   ⚠️  Gemini quota exhausted — skipping all remaining batches.")
                print(f"      Check quota at: https://aistudio.google.com/apikey")
                all_investor_cards.append(
                    f"[AI unavailable — Gemini API quota exhausted. "
                    f"Please check your quota/billing at aistudio.google.com/apikey. "
                    f"All other Excel data is complete and accurate.]"
                )
            else:
                # Non-quota error — retry once
                print(f"   ⚠️  Batch {idx + 1} attempt 1 failed: {e}. Retrying in 5s...")
                time.sleep(5)
                try:
                    response = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=user_message,
                        config=gen_config,
                    )
                    card_text = response.text or ""
                    if not card_text.strip():
                        card_text = f"[Batch {idx + 1}: empty response after retry]"
                    all_investor_cards.append(card_text)
                    print(f"   ✅ Batch {idx + 1} complete (retry).")
                    if idx < total_batches - 1:
                        time.sleep(2)
                except Exception as e2:
                    print(f"   ❌ Batch {idx + 1} failed after retry: {e2}")
                    all_investor_cards.append(
                        f"[Batch {idx + 1} analysis unavailable: {e2}]"
                    )

    return "\n\n".join(all_investor_cards)