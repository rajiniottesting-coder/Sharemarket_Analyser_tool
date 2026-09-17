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
from dotenv import load_dotenv
from analysis.fundamental_engine import FundamentalEngine

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


import os as _os
from pathlib import Path as _Path


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
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


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



# ─────────────────────────────────────────────────────────────────────────
# DAY-SCOPED CACHE
#
# Every LLM call is billed. The pipeline is re-run several times a day -
# after a data fix, to regenerate a sheet, to retry a failed step - and each
# run re-generated every narrative card from scratch, paying again for text
# that had not changed. The cards are a view of the day's picks: if the picks
# are the same, the cards are the same.
#
# Keyed on the trading DATE plus a hash of the exact input. The date alone
# would serve yesterday's cards after midnight; the hash alone would serve
# stale cards if a pick list happened to repeat. Both together mean: same day
# AND same stocks -> reuse; a changed pick list on the same day regenerates,
# because those are genuinely different stocks.
# ─────────────────────────────────────────────────────────────────────────
_CACHE_DIR = _Path(__file__).resolve().parent.parent / "downloads" / "ai_cache"


def _cache_key(stock_list_df) -> str:
    """A fingerprint of what the AI is being asked about.

    Symbol plus the handful of fields the cards actually turn on. Reading the
    whole row would make the key change on any trivial numeric drift - a price
    tick - and defeat the cache entirely.
    """
    import hashlib
    rows = []
    try:
        for _, r in stock_list_df.iterrows():
            rows.append("|".join(str(r.get(k, "")) for k in (
                "symbol", "composite_score", "quick_pick_label",
                "entry_range", "stop_loss", "t1", "horizon")))
    except Exception:                                          # noqa: BLE001
        return ""
    return hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()[:16]


def _cache_path(key: str):
    from datetime import date
    return _CACHE_DIR / f"{date.today().isoformat()}_{key}.txt"


def _cache_read(key: str):
    """Today's cards for this exact input, or None."""
    if not key or _os.getenv("AI_CACHE", "1").strip().lower() in ("0", "false", "no"):
        return None
    try:
        path = _cache_path(key)
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        pass
    return None


def _cache_write(key: str, text: str) -> None:
    """Best-effort. A cache that cannot be written must never fail a run."""
    if not key or not text:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(text, encoding="utf-8")
        # Keep only today's files. Yesterday's can never be served - the date
        # is in the name - so they are dead weight from the moment midnight
        # passes.
        from datetime import date
        today = date.today().isoformat()
        for old in _CACHE_DIR.glob("*.txt"):
            if not old.name.startswith(today):
                old.unlink(missing_ok=True)
    except Exception:                                          # noqa: BLE001
        pass


def get_ai_analysis(stock_list_df) -> str:
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

    # Same day, same stocks - reuse rather than pay again.
    _key = _cache_key(stock_list_df)
    _hit = _cache_read(_key)
    if _hit is not None:
        print(f"💾 AI cards served from today's cache ({_key}) — no API calls, no cost.")
        return _hit

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

    # v17.10: master_prompt is passed as the system message to _llm_complete().

    quota_exhausted = False   # flag to abort all batches on quota error

    for idx, batch in enumerate(batches):
        # Skip remaining batches if quota exhausted
        if quota_exhausted:
            # v12.6 (#14): standardised "[AI <verb> — <reason>]" format.
            all_investor_cards.append(
                f"[AI skipped — LLM quota exhausted (batch {idx + 1}). "
                f"Check quota/billing at https://aistudio.google.com/apikey]"
            )
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
                all_investor_cards.append(
                    f"[AI unavailable — LLM {_reason}. "
                    f"Check OPENAI_API_KEY / OPENAI_API_BASE / LLM_MODEL. "
                    f"All other Excel data is complete and accurate.]"
                )
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
                    all_investor_cards.append(
                        f"[Batch {idx + 1} analysis unavailable: {e2}]"
                    )

    _out = "\n\n".join(all_investor_cards)
    # Only a real result is cached. Caching a partial or failed batch would
    # pin the failure for the rest of the day and cost a debugging session to
    # notice, since a cache hit looks exactly like a successful run.
    if all_investor_cards and "[AI unavailable" not in _out:
        _cache_write(_key, _out)
    return _out