"""
ingestion/nse_pledge.py — v13.0

Fetch promoter pledge percentages from NSE's bulk corporate-pledge-data
endpoint. ONE API call returns the latest pledge filing for every listed
company (≈5,000 stocks), so this is dramatically cheaper than per-symbol
calls and avoids rate-limit issues.

Data source:
  Page : https://www.nseindia.com/companies-listing/corporate-filings-pledged-data
  API  : https://www.nseindia.com/api/corporates-pledgedata?index=equities
  License: NSE makes this data public for free (pledged disclosures are SEBI-mandated)

What this populates:
  - shareholding.pledge_pct      (the % of promoter holding pledged)
  - Used by spike_screener anti-trigger guard (suppresses Spike when > 20%)
  - Used by Gold-tier filter (≤ 10% required)

Direction logic:
  - Comparing today's pledge_pct against last stored pledge_pct in the
    `shareholding` table tells us IMPROVING (down) / DETERIORATING (up) /
    STABLE (same). That logic stays in master_funnel.py:785-808 — this
    module just fetches the current snapshot.
"""

import datetime
import time
from typing import Dict, Optional


def _norm_name(n) -> str:
    """Normalise a company name for matching: lowercase, strip corporate
    suffixes and punctuation. '360 ONE WAM LIMITED' == '360 ONE WAM Ltd.'."""
    import re as _re
    n = str(n or "").lower()
    n = _re.sub(r"\b(limited|ltd\.?|private|pvt\.?|company|co\.?)\b", " ", n)
    n = _re.sub(r"[^a-z0-9]+", " ", n)
    return " ".join(n.split())


def fetch_bulk_pledge_data(session, target_date: Optional[datetime.date] = None,
                           max_retries: int = 3,
                           name_map: Optional[Dict[str, str]] = None) -> Dict[str, float]:
    """
    Fetch the bulk pledge report from NSE.

    Args:
        session : Authenticated NSE session (from _make_nse_session())
        target_date : Date for which to fetch. None = latest available.
        max_retries : Network retry budget (default 3).

    Returns:
        Dict mapping NSE symbol → promoter pledge % (e.g. {"VEDL": 38.42, ...})
        Empty dict on failure (caller falls back to per-symbol or zero).

    Notes:
        - The endpoint returns a JSON array of company-level pledge records.
        - Field names per NSE schema: `symbol`, `companyName`, `noOfSharesPromoter`,
          `noOfSharesPledged`, `submissionDate`, `pctEncumbered`.
        - We extract `pctEncumbered` (the official SEBI-standardized %).
        - When pctEncumbered is missing, derive: noOfSharesPledged / noOfSharesPromoter * 100.
    """
    if target_date is None:
        target_date = datetime.date.today()

    # These endpoints return HTTP 404 from an ordinary residential IP, which
    # means the path has been moved or retired - NOT that cloud IPs are
    # blocked, as this comment previously claimed. The difference decides the
    # remedy: a block is worked around with a session or a proxy, a 404 is not
    # worked around at all. Anyone chasing the old diagnosis loses an
    # afternoon to headers and user agents.
    #
    # Both URLs are kept because they cost one request each and a retired path
    # occasionally returns. When every one fails the pledge column shows an
    # honest —: exchange data or nothing.
    urls_to_try = [
        # SINGULAR "corporate-". Confirmed live 17 Sep 2026 from the page's own
        # network tab: 200 OK, ~121 kB, no query parameters. The two below it
        # are the ones this module tried for a year and are why every run
        # reported a 404 - `corporates-` with an s, and a `corporate-filings-`
        # prefix that does not exist. One character.
        "https://www.nseindia.com/api/corporate-pledgedata",
        # v17.13.3: the plural and corporate-filings- variants are confirmed
        # 404 (probe 18-Sep-2026); retired so a failure reports the live path.
    ]

    last_err: Optional[Exception] = None
    for url in urls_to_try:
        try:
            # v17.13.1: the homepage warm-up carried browser headers but this
            # API call sent requests' defaults ("python-requests/x.y",
            # Accept */*), which NSE's edge rejects even from a residential IP.
            # Send the same headers the page itself sends for this XHR.
            r = session.get(url, timeout=20, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/124.0.0.0 Safari/537.36"),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-pledged-data",
                "X-Requested-With": "XMLHttpRequest",
            })
            if r.status_code != 200:
                last_err = RuntimeError(f"HTTP {r.status_code}")
                continue

            data = r.json()
            # NSE wraps the payload differently per endpoint, and a wrapper it
            # does not recognise reads as "zero records" - the same as a
            # failure, and indistinguishable from one in the logs. Try each
            # known key and take the first that yields a non-empty list.
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                records = []
                for _k in ("data", "records", "pledgeData", "companyPledgeData"):
                    _v = data.get(_k)
                    if isinstance(_v, list) and _v:
                        records = _v
                        break
                else:
                    # Nothing matched. Say what the payload DID contain, or the
                    # next person debugging this has only "0 symbols" to go on.
                    print(f"   ℹ️  Pledge: 200 OK but no known record key. "
                          f"Payload keys: {sorted(data.keys())[:8]}")
            else:
                records = []

            out: Dict[str, float] = {}
            for rec in records:
                try:
                    # v17.13.3: NSE's live payload has NO symbol field — only comName.
                    # Map company name -> symbol via name_map (built by the caller from
                    # the NIFTY 500 CSV). Fall back to any symbol field if present.
                    sym = str(rec.get("symbol") or rec.get("Symbol") or "").strip().upper()
                    if not sym and name_map:
                        sym = name_map.get(_norm_name(rec.get("comName", "")), "")
                    if not sym:
                        continue

                    # v17.13.3: NSE's LIVE field is `percSharesPledged` (confirmed from
                    # the real payload, 18-Sep-2026). The three names below it were
                    # never in the response — they are why "200 OK" still yielded 0
                    # records. Kept as fallbacks only.
                    pct = rec.get("percSharesPledged")
                    if pct is None:
                        pct = rec.get("pctEncumbered")
                    if pct is None:
                        pct = rec.get("pctOfPledged")
                    if pct is None:
                        pct = rec.get("percentageOfPledgedShares")

                    if pct is not None:
                        try:
                            pct_f = float(str(pct).replace(",", "").strip())
                        except (ValueError, TypeError):
                            continue
                    else:
                        # Derive from share counts as fallback
                        pledged = rec.get("noOfSharesPledged", 0) or 0
                        promoter = rec.get("noOfSharesPromoter", 0) or 0
                        try:
                            pledged_f = float(str(pledged).replace(",", "") or 0)
                            promoter_f = float(str(promoter).replace(",", "") or 0)
                        except (ValueError, TypeError):
                            continue
                        if promoter_f <= 0:
                            continue
                        pct_f = (pledged_f / promoter_f) * 100.0

                    # Sanity clamp [0, 100]
                    if pct_f < 0:   pct_f = 0.0
                    if pct_f > 100: pct_f = 100.0

                    # Keep highest if same symbol appears multiple times
                    # (e.g. multiple pledge events on same day)
                    if sym in out:
                        out[sym] = max(out[sym], round(pct_f, 2))
                    else:
                        out[sym] = round(pct_f, 2)

                except (KeyError, AttributeError, TypeError):
                    # Skip malformed records, keep going
                    continue

            # If we got a non-empty response, return it
            if out:
                return out
            # Empty response but HTTP 200 — try the next URL
            last_err = RuntimeError("empty pledge response (NSE may have restructured endpoint)")

        except Exception as e:
            last_err = e

    # Every endpoint failed — one honest line, no retries, no alarm.
    print(f"   ℹ️  NSE bulk pledge: no endpoint returned records "
          f"(last error: {last_err}). "
          "If this is HTTP 404 the path has moved again - open the NSE "
          "pledged-data page, DevTools > Network > Fetch/XHR, and read "
          "the live URL off it. Pledge % shows \u2014 for this run, which is "
          "the honest answer: a hand-maintained figure would look like data "
          "and feed the spike guard while quietly going out of date.")
    return {}
def merge_pledge_into_rows(rows: list, pledge_map: Dict[str, float]) -> int:
    """
    Update each row dict in-place with pledge_pct from the bulk map.

    Args:
        rows : list of stock dicts (must have "symbol" key)
        pledge_map : {symbol: pledge_pct} from fetch_bulk_pledge_data()

    Returns:
        Count of rows that received non-zero pledge updates.

    Behaviour:
        - Only OVERWRITES pledge_pct if bulk data has a non-zero value.
        - Does NOT touch other fields (pledge_dir is computed by master_funnel
          via historical comparison, not here).
        - Symbol matching is case-insensitive.
    """
    if not pledge_map:
        return 0

    updated = 0
    for row in rows:
        sym = str(row.get("symbol", "")).strip().upper()
        if not sym:
            continue
        if sym in pledge_map and pledge_map[sym] > 0:
            row["pledge_pct"] = pledge_map[sym]
            updated += 1

    return updated