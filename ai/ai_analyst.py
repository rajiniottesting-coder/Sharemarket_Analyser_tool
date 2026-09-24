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

_LLM_TIMEOUT_S = 90


def _llm_complete(system_prompt: str, user_message: str,
                  max_tokens: int = 4096, temperature: float = 0.7) -> str:
    """Single OpenAI-compatible /chat/completions call. Returns the text, or
    raises on HTTP/transport error so the caller's existing abort logic can
    classify it (auth vs quota vs transient)."""
    payload = {
        "model": LLM_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
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
    if not content.strip():
        fr = choices[0].get("finish_reason")
        usage = (data or {}).get("usage")
        raise RuntimeError(f"LLM returned empty content (finish_reason={fr}, usage={usage})")
    return content


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
        "   narrative; instead acknowledge that data gaps blocked a confident BUY call.\n"
        "7. OUTPUT FORMAT (mandatory): begin EACH stock's analysis with a line that is "
        "   exactly `=== CARD: <SYMBOL> ===` using the symbol given in its data card, "
        "   then the analysis. One marker per stock, in any order. No text before the "
        "   first marker."
    )

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
            card_text = _llm_complete(master_prompt, user_message)
            if not card_text.strip():
                # v12.6 (#14): standardised "[AI <verb> — <reason>]" format.
                card_text = (
                    f"[AI unavailable — LLM returned empty response for batch "
                    f"{idx + 1}. The batch may have been truncated or filtered.]"
                )
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
                    card_text = _llm_complete(master_prompt, user_message)
                    if not card_text.strip():
                        card_text = f"[Batch {idx + 1}: empty response after retry]"
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
# v17.12 — PER-DAY AI CARD CACHE
#
# WHY: every LLM call is billed, and the pipeline is re-run several times a
# day (after a data fix, to regenerate a sheet, to retry a step). Without a
# cache each re-run regenerated every card from scratch.
#
# KEY = date + hash(input). Keyed on the ACTUAL input — symbol, score, label,
# entry, SL, target, horizon — not on price ticks (hashing every field would
# change the key on any drift and defeat the cache).
#   same day, same picks     -> served from cache, zero API calls
#   same day, picks changed  -> regenerates (different stocks = different key)
#   next day                 -> regenerates; yesterday's files are purged
#   AI_CACHE=0               -> bypass, for when fresh output is wanted
#
# FAILED BATCHES ARE NEVER CACHED. Pinning a failure for a day costs a
# debugging session, because a cache hit looks exactly like a success.
# Cache lives in downloads/ai_cache/ (gitignored). Any cache error falls back
# to a normal generate — the cache can only save cost, never break a run.
# =============================================================================
_AI_CACHE_ENABLED = os.getenv("AI_CACHE", "1").strip() != "0"
_AI_CACHE_DIR     = os.path.join("downloads", "ai_cache")


def _ai_cache_key(stock_list_df) -> str:
    """date + sha1 of the identity-defining input fields, sorted by symbol so
    row order never changes the key. Returns '' if the input is unusable."""
    try:
        rows = []
        for _, r in stock_list_df.iterrows():
            g = lambda k: str(r.get(k, "") if hasattr(r, "get") else "").strip()
            rows.append("|".join([
                g("symbol"),
                f"{_sf(r.get('composite_score', 0)):.1f}",
                g("quick_pick_label") or g("label"),
                f"{_sf(r.get('close', 0)):.2f}",
                f"{_sf(r.get('stop_loss', 0)):.2f}",
                f"{_sf(r.get('t1', 0)):.2f}",
                g("time_horizon") or g("horizon"),
            ]))
        rows.sort()
        # v17.14: include the card output-format version, so a change to the
        # prompt/output contract (e.g. the per-symbol markers) never serves
        # cached text produced under the previous format.
        rows.append(f"fmt={_CARD_FORMAT_VERSION}")
        today = _dt.date.today().strftime("%Y%m%d")
        digest = hashlib.sha1(("\n".join(rows)).encode("utf-8")).hexdigest()[:12]
        return f"{today}_{digest}"
    except Exception:
        return ""


def _ai_cache_purge_old(today: str) -> int:
    """Delete cache files not from today. Returns count removed."""
    n = 0
    try:
        for f in glob.glob(os.path.join(_AI_CACHE_DIR, "*.json")):
            if not os.path.basename(f).startswith(today + "_"):
                os.remove(f); n += 1
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
_CARD_FORMAT_VERSION = "v17.14-symbol-markers"


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


def get_ai_analysis(stock_list_df) -> str:
    """PUBLIC entry point (v17.12): cache-aware wrapper around
    _generate_ai_analysis(). Same signature and return value as before, so
    every caller is unchanged."""
    if not _AI_CACHE_ENABLED or stock_list_df is None or len(stock_list_df) == 0:
        return _generate_ai_analysis(stock_list_df)

    key = _ai_cache_key(stock_list_df)
    if not key:
        return _generate_ai_analysis(stock_list_df)
    today = key.split("_", 1)[0]
    path = os.path.join(_AI_CACHE_DIR, f"{key}.json")

    # ── cache hit ──
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            text = payload.get("cards", "")
            if not _ai_output_is_failure(text):
                print(f"   💾 AI cards served from today's cache ({key}) — "
                      f"no API calls, no cost.")
                return text
    except Exception as e:
        print(f"   ⚠️  AI cache read failed ({e}) — regenerating.")

    # ── cache miss: generate, then store only if it succeeded ──
    text = _generate_ai_analysis(stock_list_df)
    try:
        os.makedirs(_AI_CACHE_DIR, exist_ok=True)
        purged = _ai_cache_purge_old(today)
        if _ai_output_is_failure(text):
            print("   ℹ️  AI cards not cached (failed/skipped output is never pinned).")
        else:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"key": key, "generated": _dt.datetime.now().isoformat(),
                           "n_stocks": int(len(stock_list_df)), "cards": text}, fh)
            print(f"   💾 AI cards cached for today ({key})"
                  + (f" · purged {purged} stale file(s)" if purged else ""))
    except Exception as e:
        print(f"   ⚠️  AI cache write failed ({e}) — output still returned.")
    return text