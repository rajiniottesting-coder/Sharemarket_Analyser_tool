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
    NEWS_MAX_HEADLINES       optional, default 15
    NEWS_LOOKBACK_DAYS       optional, default 14
    AI_CACHE / NEWS_CACHE    optional, default "1" — either set to "0" turns the
                             per-stock reply cache off (v17.22.3, see below)

REPLY CACHE (v17.22.3)
  Every LLM reply that parsed as JSON is kept in downloads/ai_cache/news/, one
  file per prompt, keyed on sha1(model + reasoning effort + system prompt +
  the exact user message — i.e. the symbol and every headline with its date
  and source). The same headlines are never sent twice: a re-run of the
  pipeline, or the next day for a stock whose headlines did not change, reads
  the stored reply instead of calling the LLM. New or changed headlines change
  the key, so the stock is judged again. Failed or non-JSON replies are never
  stored. Files unused for NEWS_CACHE_KEEP_DAYS (lookback + 1) are deleted.
  The post-processing below (confidence floor, evidence rule) runs on the
  stored reply exactly as on a fresh one.

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
import hashlib
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

# v17.22.3: per-prompt reply cache (see module docstring).
_NEWS_CACHE_ENABLED = (os.getenv("AI_CACHE", "1").strip() != "0"
                       and os.getenv("NEWS_CACHE", "1").strip() != "0")
_NEWS_CACHE_DIR = os.path.join("downloads", "ai_cache", "news")
NEWS_CACHE_KEEP_DAYS = _LOOKBACK_DAYS + 1   # a prompt older than the lookback cannot recur
_NEWS_CACHE_VERSION = "v17.22.3"            # bump if the stored payload changes shape


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
        "news_llm_source": "",   # v17.22.3: no LLM reply involved
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


def _user_message(symbol: str, headlines: list) -> str:
    """The exact user message sent to the LLM — also the cache key's input."""
    lines = "\n".join(
        f"- [{h.get('published','')[:16]}] {h['title']} ({h.get('source','')})"
        for h in headlines
    )
    return f"Stock: {symbol}\nRecent headlines:\n{lines}\n\nReturn the JSON."


# ── 2b. Reply cache (v17.22.3) ───────────────────────────────────────────────
def _news_cache_key(symbol: str, headlines: list) -> str:
    """sha1 of everything that decides the reply: model, reasoning effort,
    system prompt and the exact user message. '' if it cannot be built."""
    try:
        blob = json.dumps({"v": _NEWS_CACHE_VERSION, "model": _MODEL,
                           "effort": _REASONING_EFFORT, "system": _SYSTEM,
                           "user": _user_message(symbol, headlines)},
                          sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def _news_cache_get(key: str):
    """(parsed, raw) for a stored reply, else None. Never raises. A hit
    refreshes the file's time so the prune keeps replies still in use."""
    if not (_NEWS_CACHE_ENABLED and key):
        return None
    path = os.path.join(_NEWS_CACHE_DIR, f"{key}.json")
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        parsed = payload.get("parsed")
        if payload.get("key") != key or not isinstance(parsed, dict):
            return None
        try:
            os.utime(path, None)
        except OSError:
            pass
        return parsed, str(payload.get("raw", "") or "")
    except Exception:
        return None


def _news_cache_put(key: str, symbol: str, parsed: dict, raw: str) -> bool:
    """Store a reply that parsed as JSON. Atomic (temp file + replace), so an
    interrupted run never leaves half a file. Never raises."""
    if not (_NEWS_CACHE_ENABLED and key and isinstance(parsed, dict)):
        return False
    path = os.path.join(_NEWS_CACHE_DIR, f"{key}.json")
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(_NEWS_CACHE_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"key": key, "symbol": symbol, "model": _MODEL,
                       "stored": _dt.datetime.now().isoformat(timespec="seconds"),
                       "parsed": parsed, "raw": str(raw or "")[:4000]},
                      fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def _news_cache_prune(keep_days: int = None) -> int:
    """Delete stored replies not used for keep_days. Returns the count."""
    if not _NEWS_CACHE_ENABLED:
        return 0
    keep_days = NEWS_CACHE_KEEP_DAYS if keep_days is None else keep_days
    cutoff = time.time() - keep_days * 86400
    n = 0
    try:
        for name in os.listdir(_NEWS_CACHE_DIR):
            p = os.path.join(_NEWS_CACHE_DIR, name)
            try:
                if name.endswith((".json", ".tmp")) and os.path.getmtime(p) < cutoff:
                    os.remove(p); n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n


def _call_llm(symbol: str, headlines: list) -> tuple:
    """Returns (parsed_dict_or_None, raw_text). Never raises."""
    user = _user_message(symbol, headlines)
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

    # v17.22.3: the same prompt is never sent twice — a stored reply for these
    # exact headlines is used instead (and the polite delay is skipped).
    _key = _news_cache_key(symbol, headlines)
    _hit = _news_cache_get(_key)
    if _hit is not None:
        parsed, raw = _hit
        _source = "cache"
    else:
        time.sleep(_RATE_LIMIT_S)
        parsed, raw = _call_llm(symbol, headlines)
        _source = "llm"
        if parsed:
            _news_cache_put(_key, symbol, parsed, raw)   # failures are never stored
    if not parsed:
        _r = str(raw or "")
        _why = (_r[:45] if _r.startswith(("HTTP", "EXC", "empty reply")) else "reply was not JSON")
        _res = _neutral(f"LLM: {_why}", headlines, raw)
        _res["news_llm_source"] = _source
        return _res

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
        "news_llm_source": _source,   # v17.22.3: "llm" = called now · "cache" = stored reply
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
    _pruned = _news_cache_prune()
    if _pruned:
        log_fn(f"   📰 News reply cache: removed {_pruned} reply(ies) unused for "
               f"{NEWS_CACHE_KEEP_DAYS}+ days")
    informed = 0
    for st in stocks:
        try:
            sym = str(st.get("symbol", "") or "").strip()
            if not sym:
                continue
            # v17.22.5: a row the v15.8 name check already marked as an ETF /
            # fund is pruned before anything renders or saves it, so judging
            # its headlines only spent an LLM call (8 such rows on 26-Nov-2026,
            # each sent when Google News had headlines for it).
            if st.get("_v158_etf_filtered"):
                res = _neutral(_NOT_SENT_ETF)
            else:
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
    _det = lambda st: (st.get("news_detail") or {})
    # v17.22.3: how many LLM calls this run actually made.
    # v17.22.5: and how many of them failed (a failed reply is never stored,
    # so that stock is asked again on the next run).
    _failed = []
    try:
        _src = [str(_det(st).get("news_llm_source", "") or "") for st in stocks]
        _n_llm, _n_cache = _src.count("llm"), _src.count("cache")
        _failed = [st for st in stocks
                   if _det(st).get("news_llm_source") == "llm"
                   and str(_det(st).get("news_reason", "")).startswith("LLM:")]
        log_fn(f"   📰 News LLM calls: {_n_llm} · failed: {len(_failed)} · from cache: {_n_cache} · "
               f"no LLM needed: {len(stocks) - _n_llm - _n_cache}"
               + ("" if _NEWS_CACHE_ENABLED else " · cache OFF (AI_CACHE/NEWS_CACHE=0)"))
    except Exception:
        pass
    # v17.16: show why the rest were not informed, so a silent LLM failure
    # (e.g. output cut off) is visible instead of looking like "no news".
    # v17.22.5: EVERY reason is listed (most_common(4) hid 2 of 84 stocks on
    # 26-Nov-2026 — exactly where a failure would sit); the per-confidence
    # downgrades are one bucket so the line stays short.
    try:
        from collections import Counter as _C
        _why = _C(_reason_bucket(_det(st).get("news_reason", "?")) for st in stocks
                  if not _det(st).get("news_informed"))
        if _why:
            log_fn(f"   📰   not informed ({sum(_why.values())}): "
                   + " · ".join(f"{k} ×{v}" for k, v in _why.most_common()))
        if _failed:
            _names = ", ".join(f"{st.get('symbol', '?')} ({str(_det(st).get('news_reason', ''))[5:45]})"
                               for st in _failed[:10])
            log_fn(f"   ⚠️  News LLM failed for {len(_failed)} stock(s): {_names}"
                   f"{' …' if len(_failed) > 10 else ''} — not stored; asked again next run")
    except Exception:
        pass
    return informed


_NOT_SENT_ETF = "ETF/fund row — not sent to the LLM"


def _reason_bucket(reason) -> str:
    """Group a news_reason for the run-log summary (v17.22.5)."""
    r = str(reason or "?")
    if r == "ok":   # headlines were read and judged NEUTRAL (routine news) — not a failure
        return "headlines neutral"
    if r.startswith("downgraded (conf"):
        return "downgraded (confidence < 0.6)"
    return r[:60]