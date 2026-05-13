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


def fetch_bulk_pledge_data(session, target_date: Optional[datetime.date] = None,
                           max_retries: int = 3) -> Dict[str, float]:
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

    # v15.2.1: NSE bulk pledge endpoints are increasingly blocked on cloud IPs
    # (GitHub Actions, AWS, GCP). Try both legacy URL and the newer hyphenated
    # variant before giving up. On free-tier this commonly fails — log once,
    # politely, not 3 noisy retries. Real fix is paid feed (Trendlyne ~$30/mo)
    # or manual CSV import; see CHANGES.md for details.
    urls_to_try = [
        "https://www.nseindia.com/api/corporates-pledgedata?index=equities",
        "https://www.nseindia.com/api/corporate-filings-pledgedata?index=equities",
    ]

    last_err: Optional[Exception] = None
    for url in urls_to_try:
        try:
            r = session.get(url, timeout=20)
            if r.status_code != 200:
                last_err = RuntimeError(f"HTTP {r.status_code}")
                continue

            data = r.json()
            # NSE wraps payload either as raw list or under 'data' key
            records = data if isinstance(data, list) else data.get("data", [])

            out: Dict[str, float] = {}
            for rec in records:
                try:
                    sym = str(rec.get("symbol") or rec.get("Symbol") or "").strip().upper()
                    if not sym:
                        continue

                    # Prefer the SEBI-standard `pctEncumbered` field
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

    # Both endpoints failed — single honest log line (no retries, no alarm).
    # This is the expected free-tier behavior on cloud IPs and is documented.
    # Pledge % column will show "—" with tooltip explaining paid-tier fallback.
    print(f"   ℹ️  NSE bulk pledge: free endpoint unavailable on this IP "
          f"(known limitation; paid-tier fallback expected)")
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