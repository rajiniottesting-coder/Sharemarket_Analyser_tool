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
_MAX_HEADLINES = int(os.getenv("NEWS_MAX_HEADLINES", "8") or 8)
_LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "7") or 7)
_TIMEOUT_S = 25
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
    "You are an equity-news analyst for Indian listed companies. "
    "Given recent headlines for ONE stock, classify the net near-term "
    "sentiment for its share price. Respond with ONLY a JSON object and "
    "nothing else, using exactly these keys:\n"
    '{"sentiment": "POSITIVE"|"NEGATIVE"|"NEUTRAL", '
    '"confidence": <number 0.0-1.0>, '
    '"catalyst": "<one short line: the main positive driver, or empty>", '
    '"risk": "<one short line: the main negative driver, or empty>"}\n'
    "Rules: NEUTRAL if headlines are routine/mixed/irrelevant. POSITIVE only "
    "for a concrete favourable catalyst (order win, approval, strong results, "
    "upgrade). NEGATIVE only for a concrete adverse event (fraud probe, "
    "regulatory action, downgrade, results miss, promoter pledge/exit). "
    "Ignore generic market-wide noise. Be conservative."
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
        "max_tokens": 300,
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
        raw = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
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
        return _neutral("LLM call failed or returned non-JSON", headlines, raw)

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

    return {
        "news_sentiment": sent_effective,
        "news_confidence": round(conf, 2),
        "news_catalyst": str(parsed.get("catalyst", "") or "")[:200],
        "news_risk": str(parsed.get("risk", "") or "")[:200],
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
            if res.get("news_informed"):
                informed += 1
        except Exception as e:
            st["news_detail"] = _neutral(f"enrich error: {e}")
    log_fn(f"   📰 News sentiment: {informed}/{len(stocks)} stocks got an informed signal")
    return informed