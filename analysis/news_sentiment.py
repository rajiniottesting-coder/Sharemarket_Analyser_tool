"""
v17.9 — NEWS / CATALYST SENTIMENT ENGINE
=========================================

PURPOSE
The composite score reserves a 10% "sentiment" weight, and one of its inputs
is `news_sentiment` (POSITIVE / NEGATIVE / NEUTRAL). Until v17.9 that field
was NEVER populated — it always fell through to NEUTRAL, so the scoring engine
correctly detected "no informed sentiment" and REDISTRIBUTED the 10% across the
other factors (see scoring_engine.calculate_composite_score, Fix #1).

This module fills that empty slot with a real signal:

    1. Fetch recent headlines for the stock from a FREE, keyless source
       (Google News RSS). No paid news API required.
    2. Send the headlines to the company's OpenAI-compatible LLM endpoint
       (Sonnet) and ask for a strict-JSON sentiment verdict.
    3. Return {sentiment, confidence, catalyst, risk, headline_count}.

Once `news_sentiment` is POSITIVE or NEGATIVE, scoring_engine's
`sentiment_is_informed` flips to True and the CANONICAL 10% weight is used
automatically. No change to the scoring engine is needed — this module only
supplies the missing input. Redistribution remains the fallback for stocks
with no news.

CONFIG (environment / .env — OpenAI-compatible, ANY provider):
    OPENAI_API_KEY    required — bearer token
    OPENAI_API_BASE   required — e.g. https://your-llm-endpoint.example.com/v1
    LLM_MODEL         required — e.g. a Sonnet model id on that gateway
    NEWS_SENTIMENT_ENABLED   optional, default "1" — set "0" to disable
    NEWS_SENTIMENT_SHADOW    optional, default "0" — "1" = compute + log but
                             DO NOT feed the score (observe-first mode)
    NEWS_MAX_HEADLINES       optional, default 8
    NEWS_LOOKBACK_DAYS       optional, default 7

DESIGN RULES (accuracy)
  · temperature=0 and a strict JSON schema → as deterministic as an LLM gets.
  · Every raw LLM response is returned in the result dict so the caller can
    LOG it to the DB. A score change must always be traceable to what the
    model actually saw and said.
  · Fully fail-safe: ANY error (no key, network, bad JSON, timeout) returns a
    NEUTRAL/uninformed result so the pipeline never breaks and the scoring
    engine simply falls back to redistribution — exactly today's behaviour.
  · No app-specific vocabulary; stock symbol is the only input that varies.
"""

import os
import re
import json
import time
import datetime as _dt
import xml.etree.ElementTree as _ET
from urllib.parse import quote_plus

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

# ── Configuration ────────────────────────────────────────────────────────────
_API_KEY   = os.getenv("OPENAI_API_KEY", "").strip()
_API_BASE  = os.getenv("OPENAI_API_BASE", "").strip().rstrip("/")
_MODEL     = os.getenv("LLM_MODEL", "").strip()
_ENABLED   = os.getenv("NEWS_SENTIMENT_ENABLED", "1").strip() == "1"
_SHADOW    = os.getenv("NEWS_SENTIMENT_SHADOW", "0").strip() == "1"
_MAX_HEADLINES = int(os.getenv("NEWS_MAX_HEADLINES", "15") or 15)   # v17.11: was 8
_LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "14") or 14)  # v17.11: was 7
_TIMEOUT_S = 60
# v17.16: the gateway model is a REASONING model; hidden reasoning tokens count
# against max_tokens. The old 300-token cap could be consumed entirely by
# reasoning, leaving no JSON — the stock then silently fell back to NEUTRAL
# ("LLM call failed or returned non-JSON"). Budget now leaves room for both.
_NEWS_MAX_TOKENS = int(os.getenv("NEWS_MAX_TOKENS", "3000") or 3000)
_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "").strip()
_RATE_LIMIT_S = 0.4   # polite gap between LLM calls

NEWS_AVAILABLE = bool(_API_KEY and _API_BASE and _MODEL) and _ENABLED and requests is not None


def _neutral(reason: str, headlines=None, raw: str = "") -> dict:
    """Uninformed result — scoring engine will redistribute the weight."""
    return {
        "news_sentiment": "NEUTRAL",
        "news_confidence": 0.0,
        "news_catalyst": "",
        "news_risk": "",
        "news_headline_count": len(headlines or []),
        "news_informed": False,
        "news_reason": reason,
        "news_raw_llm": raw,
        "news_shadow": _SHADOW,
        "news_insider": "NONE", "news_insider_evidence": "",
        "news_bulk_deal": "NONE", "news_bulk_deal_evidence": "",
        "news_regulatory_flag": False, "news_regulatory_evidence": "",
        "news_results_tone": "NONE",
    }


# ── 1. Headline fetch (free, keyless) ────────────────────────────────────────
def fetch_headlines(symbol: str, company_name: str = "", max_items: int = None) -> list:
    """Recent headlines from Google News RSS. Returns list of
    {'title','source','published'} dicts, newest first. Never raises."""
    max_items = max_items or _MAX_HEADLINES
    if requests is None:
        return []
    # Prefer company name (better recall) but include the ticker.
    q = f'"{company_name}" OR {symbol} stock' if company_name else f"{symbol} stock NSE"
    url = (f"https://news.google.com/rss/search?q={quote_plus(q)}"
           f"+when:{_LOOKBACK_DAYS}d&hl=en-IN&gl=IN&ceid=IN:en")
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or not r.text:
            return []
        root = _ET.fromstring(r.text)
        out = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            src   = (item.findtext("source") or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()
            if title:
                # Google appends " - Source" to titles; strip it.
                title = re.sub(r"\s+-\s+[^-]+$", "", title)
                out.append({"title": title, "source": src, "published": pub})
            if len(out) >= max_items:
                break
        return out
    except Exception:
        return []


# ── 2. LLM sentiment (OpenAI-compatible /chat/completions) ───────────────────
_SYSTEM = (
    "You are an equity-news analyst for Indian listed companies. Given recent "
    "headlines for ONE stock, extract FACTS that are explicitly stated in the "
    "headlines. Respond with ONLY a JSON object and nothing else, using exactly "
    "these keys:\n"
    '{"sentiment": "POSITIVE"|"NEGATIVE"|"NEUTRAL", '
    '"confidence": <number 0.0-1.0>, '
    '"catalyst": "<one short line: main positive driver, or empty>", '
    '"risk": "<one short line: main negative driver, or empty>", '
    '"insider_activity": "BUY"|"SELL"|"NONE", '
    '"insider_evidence": "<the headline that shows insider/promoter buying or selling, or empty>", '
    '"bulk_deal": "BUY"|"SELL"|"NONE", '
    '"bulk_deal_evidence": "<the headline showing a bulk/block deal, or empty>", '
    '"regulatory_flag": true|false, '
    '"regulatory_evidence": "<the headline showing SEBI/regulator/court/auditor action, or empty>", '
    '"results_tone": "BEAT"|"MISS"|"INLINE"|"NONE"}\n'
    "STRICT RULES:\n"
    "1. Every non-empty *_evidence field MUST quote or closely paraphrase an actual "
    "headline from the list. Never infer an event that is not stated.\n"
    "2. NEVER output a number, percentage, price, or ratio that does not appear "
    "verbatim in a headline. If a figure is not stated, leave it out.\n"
    "3. insider_activity = BUY only for promoter/insider/director PURCHASES stated in "
    "the headlines; SELL only for stated sales or pledge INCREASES. Otherwise NONE.\n"
    "4. bulk_deal = BUY/SELL only if a headline names a bulk or block deal.\n"
    "5. regulatory_flag = true only for a stated SEBI/RBI/court/auditor/fraud action.\n"
    "6. NEUTRAL sentiment if headlines are routine, mixed, or irrelevant. POSITIVE "
    "only for a concrete favourable catalyst; NEGATIVE only for a concrete adverse "
    "event. Ignore generic market-wide noise. Be conservative."
)


def _call_llm(symbol: str, headlines: list) -> tuple:
    """Returns (parsed_dict_or_None, raw_text). Never raises."""
    lines = "\n".join(
        f"- [{h.get('published','')[:16]}] {h['title']} ({h.get('source','')})"
        for h in headlines
    )
    user = f"Stock: {symbol}\nRecent headlines:\n{lines}\n\nReturn the JSON."
    payload = {
        "model": _MODEL,
        "temperature": 0,
        "max_tokens": _NEWS_MAX_TOKENS,
        **({"reasoning_effort": _REASONING_EFFORT} if _REASONING_EFFORT else {}),
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": user},
        ],
    }
    try:
        r = requests.post(
            f"{_API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {_API_KEY}",
                     "Content-Type": "application/json"},
            json=payload, timeout=_TIMEOUT_S,
        )
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:300]}"
        data = r.json()
        _ch = (data.get("choices") or [{}])[0]
        raw = _ch.get("message", {}).get("content", "") or ""
        if isinstance(raw, list):
            raw = "".join(p.get("text", "") for p in raw if isinstance(p, dict))
        if not raw.strip():
            return None, f"empty reply (finish_reason={_ch.get('finish_reason')})"
        # Strip code fences if the model added them despite instructions.
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.S)
        m = re.search(r"\{.*\}", clean, flags=re.S)
        if not m:
            return None, raw
        parsed = json.loads(m.group(0))
        return parsed, raw
    except Exception as e:
        return None, f"EXC: {e}"


# ── 3. Public entry point ────────────────────────────────────────────────────
def get_news_sentiment(symbol: str, company_name: str = "") -> dict:
    """Fetch + score news for one stock. ALWAYS returns a dict with the keys
    shown in _neutral(); never raises. If NEWS_AVAILABLE is False the result
    is uninformed NEUTRAL and the scoring engine redistributes as before."""
    if not NEWS_AVAILABLE:
        return _neutral("news engine not configured (OPENAI_API_KEY/BASE/LLM_MODEL) or disabled")

    headlines = fetch_headlines(symbol, company_name)
    if not headlines:
        return _neutral("no recent headlines found", headlines)

    time.sleep(_RATE_LIMIT_S)
    parsed, raw = _call_llm(symbol, headlines)
    if not parsed:
        _r = str(raw or "")
        _why = (_r[:45] if _r.startswith(("HTTP", "EXC", "empty reply")) else "reply was not JSON")
        return _neutral(f"LLM: {_why}", headlines, raw)

    sent = str(parsed.get("sentiment", "NEUTRAL")).upper().strip()
    if sent not in ("POSITIVE", "NEGATIVE", "NEUTRAL"):
        sent = "NEUTRAL"
    try:
        conf = float(parsed.get("confidence", 0) or 0)
        conf = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        conf = 0.0

    # Low-confidence directional calls are downgraded to NEUTRAL so a hesitant
    # model cannot swing the score. Threshold 0.6 is a plain sanity floor.
    if sent != "NEUTRAL" and conf < 0.6:
        sent_effective = "NEUTRAL"
    else:
        sent_effective = sent

    def _enum(k, allowed, default):
        v = str(parsed.get(k, default) or default).upper().strip()
        return v if v in allowed else default
    _ins  = _enum("insider_activity", ("BUY", "SELL", "NONE"), "NONE")
    _bulk = _enum("bulk_deal", ("BUY", "SELL", "NONE"), "NONE")
    _res  = _enum("results_tone", ("BEAT", "MISS", "INLINE", "NONE"), "NONE")
    _reg  = bool(parsed.get("regulatory_flag", False))
    # A fact without evidence is not a fact — drop it (guards against invention).
    if _ins != "NONE" and not str(parsed.get("insider_evidence", "") or "").strip():
        _ins = "NONE"
    if _bulk != "NONE" and not str(parsed.get("bulk_deal_evidence", "") or "").strip():
        _bulk = "NONE"
    if _reg and not str(parsed.get("regulatory_evidence", "") or "").strip():
        _reg = False

    return {
        "news_sentiment": sent_effective,
        "news_confidence": round(conf, 2),
        "news_catalyst": str(parsed.get("catalyst", "") or "")[:200],
        "news_risk": str(parsed.get("risk", "") or "")[:200],
        # v17.11 structured facts — each backed by the headline that states it
        "news_insider": _ins,
        "news_insider_evidence": str(parsed.get("insider_evidence", "") or "")[:200],
        "news_bulk_deal": _bulk,
        "news_bulk_deal_evidence": str(parsed.get("bulk_deal_evidence", "") or "")[:200],
        "news_regulatory_flag": _reg,
        "news_regulatory_evidence": str(parsed.get("regulatory_evidence", "") or "")[:200],
        "news_results_tone": _res,
        "news_headline_count": len(headlines),
        "news_informed": sent_effective != "NEUTRAL",
        "news_reason": "ok" if sent_effective == sent else f"downgraded (conf {conf:.2f} < 0.6)",
        "news_raw_llm": raw[:1000],
        "news_shadow": _SHADOW,
        "news_headlines": [h["title"] for h in headlines],
    }


def enrich_stocks_with_news(stocks: list, log_fn=print) -> int:
    """Apply get_news_sentiment() to every stock dict in place. Sets
    stock['news_sentiment'] ONLY when not in shadow mode; the full result is
    always stored under stock['news_detail'] for logging/display.
    Returns the number of stocks that received an INFORMED (non-neutral)
    signal. Never raises."""
    if not NEWS_AVAILABLE:
        log_fn("   ℹ️  News sentiment: engine not configured — skipping "
               "(scores will use redistributed weights as before)")
        return 0
    mode = "SHADOW (log only, score untouched)" if _SHADOW else "LIVE (feeds score)"
    log_fn(f"   📰 News sentiment: {mode} · model={_MODEL} · {len(stocks)} stocks")
    informed = 0
    for st in stocks:
        try:
            sym = str(st.get("symbol", "") or "").strip()
            if not sym:
                continue
            res = get_news_sentiment(sym, str(st.get("company_name", "") or ""))
            st["news_detail"] = res
            if not _SHADOW:
                st["news_sentiment"] = res["news_sentiment"]
                if res.get("news_catalyst"):
                    st["key_catalyst"] = res["news_catalyst"]
                if res.get("news_risk"):
                    st["primary_risk"] = res["news_risk"]
                # v17.11: news-evidenced insider BUY upgrades the (often starved)
                # SAST-derived alert. STRICTLY ADDITIVE: only NO -> YES, never
                # downgrade a real SAST YES, and only with a supporting headline.
                if res.get("news_insider") == "BUY" and res.get("news_insider_evidence"):
                    if str(st.get("insider_buy_alert", "NO")).upper() != "YES":
                        st["insider_buy_alert"] = "YES"
                        st["insider_buy_source"] = "news"
                # Regulatory action is a hard fact worth surfacing on the row.
                if res.get("news_regulatory_flag"):
                    st["regulatory_flag"] = "YES"
                # Lift the structured facts onto the row for the dashboard columns.
                st["news_insider"]   = res.get("news_insider", "NONE")
                st["news_bulk_deal"] = res.get("news_bulk_deal", "NONE")
                st.setdefault("regulatory_flag", "NO")
            if res.get("news_informed"):
                informed += 1
        except Exception as e:
            st["news_detail"] = _neutral(f"enrich error: {e}")
    log_fn(f"   📰 News sentiment: {informed}/{len(stocks)} stocks got an informed signal")
    # v17.16: show why the rest were not informed, so a silent LLM failure
    # (e.g. output cut off) is visible instead of looking like "no news".
    try:
        from collections import Counter as _C
        def _lbl(st):
            r = str((st.get("news_detail") or {}).get("news_reason", "?"))[:60]
            # "ok" = headlines were read and judged NEUTRAL (routine news) — not a failure
            return "headlines neutral" if r == "ok" else r
        _why = _C(_lbl(st) for st in stocks
                  if not (st.get("news_detail") or {}).get("news_informed"))
        if _why:
            log_fn("   📰   not informed: " + " · ".join(f"{k} ×{v}" for k, v in _why.most_common(4)))
    except Exception:
        pass
    return informed