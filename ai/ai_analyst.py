"""
ai_analyst.py
SECTION 0D & 7 — AI Batch Analysis Engine (v7 FINAL)

v17.10: Switched from Google Gemini to the company's OpenAI-compatible LLM
endpoint (Sonnet), sharing the SAME three credentials as the v17.9 news
sentiment engine — one LLM, one key, one endpoint for the whole project.
Master prompt v7 goes into the system message; batch data into the user
message. All batching, card formatting, and the v17.6 fail-safe /
abort-on-first-auth-failure logic are unchanged.

v17.10 scope: investor cards are generated ONLY for Gold picks plus
currently-held (OPEN) positions — not all 100 dashboard stocks. See
master_funnel Section 7/8 for the subset selection.
"""

import os
import re
import time
import json
import glob
import hashlib
import datetime as _dt
from dotenv import load_dotenv
from analysis.fundamental_engine import FundamentalEngine

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


def _sf(val, default=0.0):
    """Safe float — handles '—', None, '', non-numeric strings."""
    if val is None or val == "" or str(val) in ("—", "--", "N/A"):
        return float(default)
    try:
        return float(val)
    except (ValueError, TypeError):
        return float(default)

load_dotenv()

# ── LLM credentials (OpenAI-compatible; shared with analysis/news_sentiment) ─
# v17.6 semantics preserved: WARN, do not raise. A missing key must NOT break
# the module — only the optional narrative cards need it. get_ai_analysis()
# checks _AI_ENABLED and returns a clean skip message when absent.
_API_KEY  = os.getenv("OPENAI_API_KEY", "").strip()
_API_BASE = os.getenv("OPENAI_API_BASE", "").strip().rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
_AI_ENABLED = bool(_API_KEY and _API_BASE and LLM_MODEL) and requests is not None
client = None   # kept for backward-compat with any external reference
if not _AI_ENABLED:
    print("   ⚠️  LLM not configured (OPENAI_API_KEY / OPENAI_API_BASE / LLM_MODEL) "
          "— AI narrative cards will be skipped. All other analysis (screening, "
          "fair value, SL/targets, outcome tracking, Excel, delivery) runs normally.")

_LLM_TIMEOUT_S = 180
# v17.16: the configured model is a REASONING model — its hidden reasoning
# tokens count against max_tokens. On 25-Sep a 4,096 budget was spent entirely
# on reasoning (reasoning_tokens=4096, text_tokens=0 -> empty reply), and the
# retry got 2,621 reasoning + 1,475 text (cut off). Cards now get a budget that
# leaves room for both; override with LLM_MAX_TOKENS.
LLM_CARD_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "16000") or 16000)
# Optional: ask the gateway for less reasoning ("low"/"medium"/"high"). Sent
# ONLY when set — not every OpenAI-compatible gateway accepts the field.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "").strip()
_LAST_CALL = {}   # v17.14: finish_reason + usage of the most recent call (diagnostics)


def _llm_complete(system_prompt: str, user_message: str,
                  max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """Single OpenAI-compatible /chat/completions call. Returns the text, or
    raises on HTTP/transport error so the caller's existing abort logic can
    classify it (auth vs quota vs transient)."""
    payload = {
        "model": LLM_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **({"reasoning_effort": LLM_REASONING_EFFORT} if LLM_REASONING_EFFORT else {}),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
    }
    r = requests.post(
        f"{_API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {_API_KEY}",
                 "Content-Type": "application/json"},
        json=payload, timeout=_LLM_TIMEOUT_S,
    )
    if r.status_code != 200:
        # Surface status in the message so _is_auth_error/_is_quota_error match.
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    # v17.14: a gateway can answer HTTP 200 with an error body, no choices, a
    # list-form content, or finish_reason="length" and empty text. Previously all
    # of these collapsed into "" -> "[AI unavailable — LLM returned empty"
    # response]" with the real cause lost. Now: accept list-form content, and
    # raise with the actual reason so the batch log states what happened.
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"LLM error body (HTTP 200): {str(data.get('error'))[:300]}")
    choices = (data or {}).get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM returned no choices: {str(data)[:300]}")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):   # [{"type":"text","text":...}, ...]
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    content = content or ""
    fr = choices[0].get("finish_reason")
    usage = (data or {}).get("usage")
    _LAST_CALL.update({"finish_reason": fr, "usage": usage})
    if not content.strip():
        raise RuntimeError(f"LLM returned empty content (finish_reason={fr}, usage={usage})")
    if str(fr).lower() in ("length", "max_tokens"):
        print(f"   ⚠️  LLM output was CUT OFF by the token limit (finish_reason={fr}, usage={usage}) "
              f"— missing cards will be re-requested one stock at a time.")
    return content


MASTER_PROMPT_PATH = "master_prompt/NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt"


def _load_card_system_prompt() -> str:
    """v17.16: the cards need the analyst ROLE and the BLOCK H spec only.

    Sending the whole 84 KB master prompt cost ~44,000 input tokens per call
    (x6 calls on 25-Sep with recovery), and its Section-8 rule "No paragraphs
    in default output (Blocks A-G only)" directly contradicts the Block H
    request — one reason the model kept producing full investor cards. The
    two relevant sections are extracted from the master prompt (still the
    single source of truth); falls back to the full prompt if the section
    markers are ever renamed."""
    full = _load_master_prompt()
    try:
        a = full.index("SYSTEM ROLE")
        b = full.index("SECTION 1A", a)
        c = full.index("BLOCK H — ANALYSIS SUMMARY")
        d = full.index("LIST VIEW sort", c)
        role = full[a:b].rsplit("═", 1)[0].strip()
        blockh = full[c:d].strip()
        return (role + "\n\n" + blockh +
                "\n\nIn this task you write ONLY Block H notes, for the stocks given, "
                "using ONLY the data provided in each stock's data card.")
    except ValueError:
        return full


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
    """Detect LLM quota/billing exhaustion — no point retrying these."""
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


def _is_auth_error(err: Exception) -> bool:
    """v17.6: detect an INVALID or MISSING API key (as opposed to quota).

    An invalid key fails EVERY batch identically, so retrying each of ~8
    batches (5s wait + 2 attempts apiece) just burns ~80s and spams the log
    with useless calls. Detecting it lets us abort ALL remaining cards after
    the FIRST failed batch — the behaviour requested: 'if no api key
    configured, don't run any further cards.'
    """
    err_str = str(err).lower()
    auth_markers = (
        "api_key_invalid", "api key not valid", "invalid api key",
        "unauthenticated", "unauthorized", "401", "invalid_argument",
        "missing", "no api key",
    )
    return any(m in err_str for m in auth_markers)


def _generate_ai_analysis(stock_list_df) -> str:
    """
    SECTION 0D & 3: Grounded Batch Processing via the company LLM (Sonnet).

    Pre-calculates Graham Number, PEG Ratio, and CFV using FundamentalEngine
    so the AI uses our computed values rather than estimating them.

    Batch size: 10–15 stocks per API call (Section 0D).
    Rate limiting: 2s delay between batches.
    Timeout/retry: 1 retry per failed batch.
    """
    # v17.6: hard skip when no key — return one clean card, run zero batches.
    if not _AI_ENABLED:
        return ("[AI unavailable — LLM not configured. "
                "Cards skipped; all other Excel data is complete and accurate. "
                "Set OPENAI_API_KEY / OPENAI_API_BASE / LLM_MODEL to enable AI narratives.]")

    all_investor_cards = []
    batch_size = 4   # v17.16: was 12 — smaller batches keep each reply well inside the budget

    engine       = FundamentalEngine()
    master_prompt = _load_card_system_prompt()   # v17.16: role + Block H only

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
        t1    = v("t1")   # v17.21: the single live Target; T2/T3 are not sent
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

        # v17.10.1: if this is a currently-held position (not a fresh pick), tell
        # the model so the card is hold/trim/exit guidance, not a buy thesis.
        _held = row.get("_held_context", "") or ""
        _held_line = f"\n⚠ {_held}\n" if _held else ""
        return f"""{_held_line}
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
  Entry: ₹{entry} | SL: ₹{sl} | Target: ₹{t1} (the only exit target — there is no T2/T3)
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
        "For EACH stock card below, write ONLY its Block H Analysis Summary "
        "(150-250 words, a concise analyst note — no other blocks, no tables, "
        "no price/metric strips; those are already in the spreadsheet). "
        "v17.14: the full Section 8 card overran the output limit after the "
        "first stock, so the other stocks received nothing.\n\n"
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
        "   narrative; instead acknowledge that data gaps blocked a confident BUY call.\n"
        "7. OUTPUT FORMAT (mandatory): begin EACH stock's analysis with a line that is "
        "   exactly `=== CARD: <SYMBOL> ===` using the symbol given in its data card, "
        "   then the analysis. One marker per stock, in any order. No text before the "
        "   first marker."
    )

    def _recover_missing(card_text, batch, idx):
        """v17.14: if a batch reply lacks cards for some stocks (output cut off,
        or the model skipped one), request each missing stock on its own. One
        small call per missing stock; failures get a per-stock placeholder."""
        have = _parse_marked(card_text)
        missing = [r for _, r in batch.iterrows()
                   if str(r.get("symbol", "")).strip().upper() not in have]
        if not missing:
            return card_text
        print(f"   ↻ Batch {idx + 1}: {len(missing)}/{len(batch)} card(s) missing "
              f"(last finish_reason={_LAST_CALL.get('finish_reason')}) — requesting individually")
        extra = []
        for r in missing:
            sym = str(r.get("symbol", "")).strip()
            msg = (f"{grounding_instruction}\n\n"
                   f"SINGLE STOCK — 1 stock:\n{_fmt_stock_card(r.to_dict())}")
            try:
                t = _llm_complete(master_prompt, msg, max_tokens=LLM_CARD_MAX_TOKENS)
                one = _parse_marked(t).get(sym.upper()) or t.strip()
                extra.append(CARD_MARKER.format(sym=sym) + "\n" + one)
                print(f"      ✅ {sym} recovered")
            except Exception as e:
                extra.append(CARD_MARKER.format(sym=sym) + "\n"
                             + f"[AI unavailable — {sym} single-stock request failed: {str(e)[:160]}]")
                print(f"      ❌ {sym}: {str(e)[:160]}")
        return (card_text.rstrip() + "\n\n" + "\n\n".join(extra)).strip()

    # v17.10: master_prompt is passed as the system message to _llm_complete().

    quota_exhausted = False   # flag to abort all batches on quota error

    for idx, batch in enumerate(batches):
        # Skip remaining batches if quota exhausted
        if quota_exhausted:
            # v12.6 (#14): standardised "[AI <verb> — <reason>]" format.
            all_investor_cards.append(_mark_batch(batch,
                f"[AI skipped — LLM quota exhausted or key invalid (batch {idx + 1}). "
                f"Check OPENAI_API_KEY / OPENAI_API_BASE / LLM_MODEL.]"
            ))
            continue

        print(f"🤖 Processing batch {idx + 1}/{total_batches} "
              f"({len(batch)} stocks) via {LLM_MODEL}...")

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
            card_text = _llm_complete(master_prompt, user_message, max_tokens=LLM_CARD_MAX_TOKENS)
            if not card_text.strip():
                # v12.6 (#14): standardised "[AI <verb> — <reason>]" format.
                card_text = (
                    f"[AI unavailable — LLM returned empty response for batch "
                    f"{idx + 1}. The batch may have been truncated or filtered.]"
                )
            card_text = _recover_missing(card_text, batch, idx)
            all_investor_cards.append(card_text)
            print(f"   ✅ Batch {idx + 1} complete.")
            # Section 0D: Rate limiting — 2s between successful batches
            if idx < total_batches - 1:
                time.sleep(2)
        except Exception as e:
            # Detect quota exhaustion — no point retrying
            if _is_quota_error(e) or _is_auth_error(e):
                quota_exhausted = True   # reuse the same abort flag for both
                _reason = ("quota exhausted" if _is_quota_error(e)
                           else "API key invalid or not configured")
                # v17.6: on the FIRST bad-key/quota batch, stop trying the rest.
                # Requested behaviour: don't run any further cards once we know
                # the key won't work — every subsequent batch would fail the same.
                print(f"   ⚠️  LLM {_reason} — skipping ALL remaining batches "
                      f"(no point retrying).")
                print(f"      Check OPENAI_API_KEY / OPENAI_API_BASE / LLM_MODEL and your gateway quota.")
                all_investor_cards.append(_mark_batch(batch,
                    f"[AI unavailable — LLM {_reason}. "
                    f"Check OPENAI_API_KEY / OPENAI_API_BASE / LLM_MODEL. "
                    f"All other Excel data is complete and accurate.]"
                ))
            else:
                # Non-quota error — retry once
                print(f"   ⚠️  Batch {idx + 1} attempt 1 failed: {e}. Retrying in 5s...")
                time.sleep(5)
                try:
                    card_text = _llm_complete(master_prompt, user_message, max_tokens=LLM_CARD_MAX_TOKENS)
                    if not card_text.strip():
                        card_text = f"[Batch {idx + 1}: empty response after retry]"
                    card_text = _recover_missing(card_text, batch, idx)
                    all_investor_cards.append(card_text)
                    print(f"   ✅ Batch {idx + 1} complete (retry).")
                    if idx < total_batches - 1:
                        time.sleep(2)
                except Exception as e2:
                    print(f"   ❌ Batch {idx + 1} failed after retry: {e2}")
                    all_investor_cards.append(_mark_batch(batch,
                        f"[AI unavailable — batch {idx + 1} failed: {str(e2)[:200]}]"
                    ))

    return "\n\n".join(all_investor_cards)


# =============================================================================
# AI CARD CACHE — v17.12, per stock and per trading date since v17.22.3
#
# WHY: every LLM call is billed, and the pipeline is re-run (after a data fix,
# to regenerate a sheet, "Re-run jobs" on Actions). A re-run must not pay for
# cards it already has.
#
# ONE FILE PER TRADING DATE: downloads/ai_cache/cards/<YYYYMMDD>.json, holding
# one entry per stock. The date is the trading day the run processes (passed
# in by master_funnel), not the runner's clock — before v17.22.3 the key used
# date.today() on the runner (UTC), so a re-run after 00:00 UTC missed.
#
# STOCK KEY = sha1(symbol, score, label, close, SL, target, horizon, held
# context, card format version, model, system prompt). Identity fields, not
# every input — hashing every field would change the key on any drift.
#   re-run, same stocks       -> every card from the cache, zero LLM calls
#   one stock added/changed   -> only that stock is generated
#   next trading date         -> regenerated; other dates' files are deleted
#   AI_CACHE=0                -> bypass, for when fresh output is wanted
# (v17.12 hashed the whole stock list into one key, so one changed stock
# regenerated every card.)
#
# PLACEHOLDERS ARE NEVER CACHED: only a real card ("[AI …" text is a
# placeholder) is stored, so a failed stock is retried on the next run while
# the stocks that worked are not paid for twice. Any cache error falls back to
# a normal generate — the cache can only save cost, never break a run.
# =============================================================================
_AI_CACHE_ENABLED = os.getenv("AI_CACHE", "1").strip() != "0"
_AI_CACHE_DIR     = os.path.join("downloads", "ai_cache")
_CARD_CACHE_DIR   = os.path.join(_AI_CACHE_DIR, "cards")
_PROMPT_SIG = None   # sha1 of model + card system prompt, computed once


def _card_trade_key(trade_date=None) -> str:
    """YYYYMMDD of the trading date (date, datetime, 'YYYY-MM-DD' or
    'YYYYMMDD'). Falls back to the local date only when none is given."""
    if trade_date is None or trade_date == "":
        return _dt.date.today().strftime("%Y%m%d")
    if isinstance(trade_date, (_dt.date, _dt.datetime)):
        return trade_date.strftime("%Y%m%d")
    t = str(trade_date).strip()
    for text, fmt in ((t[:10], "%Y-%m-%d"), (t[:8], "%Y%m%d")):
        try:
            return _dt.datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    raise ValueError(f"unreadable trade_date: {trade_date!r}")


def _card_prompt_sig() -> str:
    """Model + system prompt: a change to either must not serve old cards."""
    global _PROMPT_SIG
    if _PROMPT_SIG is None:
        try:
            _PROMPT_SIG = hashlib.sha1(
                (LLM_MODEL + "\n" + _load_card_system_prompt()).encode("utf-8")).hexdigest()[:12]
        except Exception:
            _PROMPT_SIG = "noprompt"
    return _PROMPT_SIG


def _card_stock_key(row) -> str:
    """sha1 of the fields that define one stock's card."""
    g = lambda k: str(row.get(k, "") if row.get(k, "") is not None else "").strip()
    parts = [
        g("symbol").upper(),
        f"{_sf(row.get('composite_score', 0)):.1f}",
        g("quick_pick_label") or g("label"),
        f"{_sf(row.get('close', 0)):.2f}",
        f"{_sf(row.get('stop_loss', 0)):.2f}",
        f"{_sf(row.get('t1', 0)):.2f}",
        g("time_horizon") or g("horizon"),
        g("_held_context"),
        f"fmt={_CARD_FORMAT_VERSION}",
        f"prompt={_card_prompt_sig()}",
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _card_cache_load(path: str) -> dict:
    """{stock_key: {"symbol", "card", "stored"}} for one trading date."""
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        cards = payload.get("cards", {})
        return cards if isinstance(cards, dict) else {}
    except Exception as e:
        print(f"   ⚠️  AI card cache unreadable ({e}) — starting it again.")
        return {}


def _card_cache_save(path: str, trade_key: str, cards: dict) -> None:
    """Atomic write (temp file + replace)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"trade_date": trade_key, "format": _CARD_FORMAT_VERSION,
                   "cards": cards}, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _card_cache_purge(keep_path: str) -> int:
    """Delete other trading dates' card files and the v17.12 files
    (downloads/ai_cache/<date>_<hash>.json). The news/ folder is untouched."""
    n = 0
    try:
        for f in (glob.glob(os.path.join(_CARD_CACHE_DIR, "*.json"))
                  + glob.glob(os.path.join(_CARD_CACHE_DIR, "*.tmp"))
                  + glob.glob(os.path.join(_AI_CACHE_DIR, "*.json"))):
            if os.path.abspath(f) != os.path.abspath(keep_path):
                try:
                    os.remove(f); n += 1
                except OSError:
                    pass
    except Exception:
        pass
    return n


def _ai_output_is_failure(text: str) -> bool:
    """True if the generated output represents a failed/skipped run. Such output
    must NEVER be cached (a cached failure is indistinguishable from success)."""
    if not text or not text.strip():
        return True
    t = text.strip()
    # Whole-output failure markers used by _generate_ai_analysis
    if t.startswith("[AI unavailable") or t.startswith("[AI skipped"):
        return True
    return False


CARD_MARKER = "=== CARD: {sym} ==="
_CARD_RE = re.compile(r"^\s*=== CARD:\s*([^=\n]+?)\s*===\s*$", re.M)


def _parse_marked(text: str) -> dict:
    """v17.14: {SYMBOL: body} from '=== CARD: SYMBOL ===' delimited text."""
    parts = _CARD_RE.split(text or "")
    out = {}
    for k in range(1, len(parts) - 1, 2):
        sym, body = parts[k].strip().upper(), parts[k + 1].strip()
        if sym and body and sym not in out:
            out[sym] = body
    return out


def _cards_complete(text: str, df) -> bool:
    """True only if EVERY stock in df has a real (non-placeholder) card."""
    try:
        got = _parse_marked(text)
        for _, row in df.iterrows():
            b = got.get(str(row.get("symbol", "")).strip().upper(), "")
            if not b or b.startswith("[AI"):
                return False
        return True
    except Exception:
        return False
_CARD_FORMAT_VERSION = "v17.21-single-target"


def _mark_batch(batch, text: str) -> str:
    """v17.14: attach a placeholder to EVERY stock in a failed/skipped batch,
    each under its own symbol marker, so the caller maps results by symbol
    instead of by position (one placeholder used to land on the first stock
    only, and the rest showed 'Analysis pending')."""
    out = []
    try:
        for _, row in batch.iterrows():
            out.append(CARD_MARKER.format(sym=str(row.get("symbol", "?")).strip()) + "\n" + text)
    except Exception:
        return text
    return "\n\n".join(out) if out else text


def get_ai_analysis(stock_list_df, trade_date=None) -> str:
    """PUBLIC entry point: cache-aware wrapper around _generate_ai_analysis().

    v17.22.3: cached per stock for the trading date `trade_date` (the date the
    run processes; master_funnel passes target_date). Only the stocks with no
    stored card are sent to the LLM. Returns the same '=== CARD: SYMBOL ==='
    text as before; with nothing in the cache the generator's text is returned
    unchanged."""
    if not _AI_CACHE_ENABLED or stock_list_df is None or len(stock_list_df) == 0:
        return _generate_ai_analysis(stock_list_df)
    try:
        import pandas as pd
        df = stock_list_df if isinstance(stock_list_df, pd.DataFrame) else pd.DataFrame(stock_list_df)
        df = df.reset_index(drop=True)
        trade_key = _card_trade_key(trade_date)
        path = os.path.join(_CARD_CACHE_DIR, f"{trade_key}.json")
        keys = [_card_stock_key(r) for _, r in df.iterrows()]
        syms = [str(r.get("symbol", "")).strip() for _, r in df.iterrows()]
        store = _card_cache_load(path)
    except Exception as e:
        print(f"   ⚠️  AI card cache unavailable ({e}) — generating without it.")
        return _generate_ai_analysis(stock_list_df)

    def _real(body) -> bool:
        return bool(body) and isinstance(body, str) and not body.strip().startswith("[AI")

    hits = {i: store[k]["card"] for i, k in enumerate(keys)
            if isinstance(store.get(k), dict) and _real(store[k].get("card"))}
    _date_txt = f"{trade_key[:4]}-{trade_key[4:6]}-{trade_key[6:]}"
    if len(hits) == len(df):
        print(f"   💾 AI cards: all {len(df)} from the cache for trading date {_date_txt} "
              f"— no LLM calls.")
        return "\n\n".join(CARD_MARKER.format(sym=syms[i]) + "\n" + hits[i]
                            for i in range(len(df)))

    miss_idx = [i for i in range(len(df)) if i not in hits]
    gen = _generate_ai_analysis(df.iloc[miss_idx].copy().reset_index(drop=True))
    parsed = _parse_marked(gen)

    # Store every real card just generated; placeholders are never stored.
    stored = 0
    for i in miss_idx:
        body = parsed.get(syms[i].upper())
        if _real(body):
            store[keys[i]] = {"symbol": syms[i], "card": body,
                              "stored": _dt.datetime.now().isoformat(timespec="seconds")}
            stored += 1
    try:
        if stored:
            _card_cache_save(path, trade_key, store)
        purged = _card_cache_purge(path)
    except Exception as e:
        purged = 0
        print(f"   ⚠️  AI card cache write failed ({e}) — output still returned.")
    print(f"   💾 AI cards (trading date {_date_txt}): {len(hits)} from cache · "
          f"{len(miss_idx)} not in the cache → {stored} new card(s) stored"
          + (f" · {len(miss_idx) - stored} placeholder/missing, not stored (retried next run)"
             if stored < len(miss_idx) else "")
          + (f" · removed {purged} old cache file(s)" if purged else ""))

    if not hits:
        return gen   # cold cache: exactly what the generator returned
    # Mixed: cached cards + the generated ones, each under its own marker.
    _gen_placeholder = gen.strip() if (not parsed and gen.strip().startswith("[AI")) else ""
    out = []
    for i in range(len(df)):
        body = hits.get(i) or parsed.get(syms[i].upper()) or _gen_placeholder
        if body:
            out.append(CARD_MARKER.format(sym=syms[i]) + "\n" + body)
    return "\n\n".join(out)
