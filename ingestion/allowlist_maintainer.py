"""
ingestion/allowlist_maintainer.py
─────────────────────────────────
v11.0.2 — Runtime DUAL_LISTED allowlist maintainer.

PURPOSE
    The hardcoded DUAL_LISTED_ALLOWLIST in reconciler.py covers ~233 widely-
    traded symbols. New IPOs and re-discovered dual-listed stocks need to be
    added to keep tagging accurate. This module observes the FACT of dual-
    listing whenever the BSE bhavcopy succeeds (~30-40% of GitHub Actions
    runs) and persists those observations to a SQLite-backed runtime table.

    The reconciler's effective allowlist becomes:
        hardcoded DUAL_LISTED_ALLOWLIST  ∪  dual_listed_runtime table

PRINCIPLE
    Quality has ZERO influence on this maintenance. A stock either is or
    isn't dual-listed, and that's the only question this module answers.
    Symbols are added when observed on both NSE and BSE, and removed only
    when the symbol stops trading entirely (NSE absence ≥ 30 days).

CONTRACT
    record_dual_listed_observations(df, today_iso=None) → int
        Called by data_bridge.consolidate_market_data() after reconcile_exchanges
        succeeds. Writes new DUAL_LISTED symbols to the table. Idempotent.

    prune_runtime_allowlist(today_iso=None, ttl_days=30) → int
        Removes runtime entries whose last_seen_date < today - ttl_days.
        Called once per pipeline run (typically end-of-run).

    get_runtime_allowlist() → set[str]
        Returns the union snapshot of all live runtime entries. Used by
        reconciler.get_effective_allowlist() to merge with the hardcoded set.

GRACEFUL DEGRADATION
    Every public function is wrapped in try/except: if the SQLite file or
    table doesn't exist, we return safe defaults (empty set, 0 inserts,
    0 removals) without raising. This keeps fresh-install runs healthy.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Set


_DB_PATH = "market_data.db"
_TABLE = "dual_listed_runtime"


def _today_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create the runtime table if it doesn't exist. Idempotent."""
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            symbol           TEXT PRIMARY KEY,
            first_seen_date  TEXT NOT NULL,
            last_seen_date   TEXT NOT NULL,
            source           TEXT DEFAULT 'bse_merge'
        )
    """)


def get_runtime_allowlist() -> Set[str]:
    """
    Return the union of all symbols currently in the runtime table.
    Returns an empty set on any error (fresh install, missing DB, etc.).
    """
    try:
        conn = sqlite3.connect(_DB_PATH)
        try:
            _ensure_table(conn)
            cur = conn.execute(f"SELECT symbol FROM {_TABLE}")
            return {row[0] for row in cur.fetchall() if row and row[0]}
        finally:
            conn.close()
    except Exception:
        return set()


# ── v17.17: write-time guards ────────────────────────────────────────────────
# Replaces the nightly "v12.1 self-healing" workflow step, which deleted this
# table every run: its limits (700 rows, a fixed "bad ticker" list) predated
# the v12.1 reconciler fix. After that fix, ~2,200 genuinely dual-listed NSE
# stocks is normal, ETFs really are listed on both exchanges, and RBA is a
# real company. Instead of wiping good data after the fact, only trustworthy
# observations are ever written:
#   1. only when dual listings were matched on real ISINs (never the
#      symbol-name fallback, never the allowlist-driven BSE-down path);
#   2. only rows actually present on BOTH exchanges (not the allowlist's own
#      "promote NSE_ONLY back to DUAL_LISTED" override — that would be circular);
#   3. never ETFs / mutual funds / index products;
#   4. refuse the whole run if the data carries the old Cartesian-merge
#      fingerprint: the SAME NSE symbol matched to several BSE rows. A correct
#      ISIN merge is one-to-one, so duplicates are ~0; the v11 cross-join
#      produced them in bulk. A ratio backstop (> 99% of NSE rows dual) also
#      applies. Neither depends on a row count, so neither goes stale.
#      (Today ~91% of NSE rows are genuinely dual-listed, so a 95% cap would
#      have been too tight.)
_MAX_DUP_SHARE = 0.01       # > 1% duplicated NSE symbols among dual rows
_MAX_DUAL_SHARE = 0.99      # v11 bug tagged ~99% of rows dual
_PRODUCT_SYMBOL_SUFFIXES = ("BEES", "ETF")   # not GOLD/SILVER: SKYGOLD etc. are real equities
_PRODUCT_SYMBOL_PREFIXES = ("LIQUID", "GILT", "NIFTY", "SENSEX", "BANKNIFTY",
                            "FINNIFTY", "MIDCPNIFTY")
_PRODUCT_NAME_MARKERS = (" ETF", "ETF -", "MUTUAL FUND", "INDEX FUND",
                         "FUND OF FUND", "BEES", "EXCHANGE TRADED")


def _is_fund_or_index(symbol: str, name: str = "") -> bool:
    """True for ETFs, mutual funds and index products — never equities."""
    s = (symbol or "").strip().upper()
    n = " " + (name or "").strip().upper() + " "
    if not s:
        return True
    if s.endswith(_PRODUCT_SYMBOL_SUFFIXES) or s.startswith(_PRODUCT_SYMBOL_PREFIXES):
        return True
    return any(m in n for m in _PRODUCT_NAME_MARKERS)


def _col(df, *names):
    for c in names:
        if c in df.columns:
            return df[c]
    return None


def _present(series):
    t = series.astype(str).str.strip()
    return series.notna() & (t != "") & (t.str.upper() != "NAN") & (t.str.upper() != "NONE")


def record_dual_listed_observations(reconciled_df, today_iso: Optional[str] = None,
                                     hardcoded_allowlist: Optional[Set[str]] = None) -> int:
    """
    Persist dual-listed symbols OBSERVED on both NSE and BSE today.

    v17.17: guarded at write time (see block comment above). Every refusal is
    printed with its reason — this function never fails silently and never
    deletes valid rows. It also removes any fund/index rows already stored
    (self-cleaning, idempotent).

    Returns the number of NEW symbols inserted. Returns 0 on refusal or error.
    """
    today = today_iso or _today_iso()
    if reconciled_df is None or len(reconciled_df) == 0:
        return 0
    if "exchange_tag" not in reconciled_df.columns:
        return 0

    # Guard 1 — how were dual listings identified?
    method = str(getattr(reconciled_df, "attrs", {}).get("dual_match_method", "") or "")
    has_both_cols = ("symbol_NSE" in reconciled_df.columns and
                     "symbol_BSE" in reconciled_df.columns)
    if method and method != "isin":
        print(f"   ℹ️  Allowlist recorder: skipped — dual listings were identified by "
              f"'{method}', not by ISIN (nothing reliable to learn today)")
        return 0
    if not method and not has_both_cols:
        # Older reconciler without the attrs flag: only the ISIN merge
        # produces separate NSE/BSE symbol columns.
        print("   ℹ️  Allowlist recorder: skipped — no ISIN-merge evidence in the data")
        return 0

    try:
        sym_nse = _col(reconciled_df, "symbol_NSE", "symbol")
        sym_bse = _col(reconciled_df, "symbol_BSE")
        # Security name from either exchange (BSE bhav carries it; NSE may not)
        name_nse = _col(reconciled_df, "company_name_NSE", "name_NSE", "company_name", "name")
        name_bse = _col(reconciled_df, "company_name_BSE", "name_BSE")
        is_dual = reconciled_df["exchange_tag"].astype(str).str.upper() == "DUAL_LISTED"

        # Guard 2 — present on BOTH exchanges (excludes allowlist promotions)
        on_nse = _present(sym_nse) if sym_nse is not None else is_dual & False
        on_bse = _present(sym_bse) if sym_bse is not None else is_dual & False
        both = is_dual & on_nse & on_bse

        # Guard 4 — Cartesian-bug fingerprint
        n_nse = int(on_nse.sum())
        n_both = int(both.sum())
        _syms_both = sym_nse[both].astype(str).str.strip().str.upper()
        n_dup = int(_syms_both.duplicated(keep=False).sum())
        if n_both > 0 and n_dup / n_both > _MAX_DUP_SHARE:
            print(f"   ⚠️  Allowlist recorder: REFUSED — {n_dup:,} of {n_both:,} dual rows repeat "
                  f"the same NSE symbol (one-to-many match = the old cross-join bug). "
                  f"Nothing written.")
            return 0
        if n_nse > 0 and n_both / n_nse > _MAX_DUAL_SHARE:
            print(f"   ⚠️  Allowlist recorder: REFUSED — {n_both:,}/{n_nse:,} NSE rows "
                  f"({n_both / n_nse:.0%}) tagged dual-listed; > {_MAX_DUAL_SHARE:.0%} "
                  f"matches the old cross-join bug. Nothing written.")
            return 0

        observed = {}
        for i in reconciled_df.index[both]:
            sym = str(sym_nse.at[i]).strip().upper()
            nm = " ".join(str(c.at[i]) for c in (name_nse, name_bse) if c is not None)
            observed[sym] = nm
    except Exception as _e:
        print(f"   ⚠️  Allowlist recorder: skipped (error: {_e})")
        return 0

    # Guard 3 — no ETFs / funds / index products
    n_before = len(observed)
    observed = {s for s, nm in observed.items() if not _is_fund_or_index(s, nm)}
    n_products = n_before - len(observed)

    if hardcoded_allowlist:
        observed = observed - set(hardcoded_allowlist)

    new_count = 0
    try:
        conn = sqlite3.connect(_DB_PATH)
        try:
            _ensure_table(conn)
            cur = conn.cursor()
            # Self-clean: drop fund/index rows stored before these guards.
            cur.execute(f"SELECT symbol FROM {_TABLE}")
            _stale = [r[0] for r in cur.fetchall() if _is_fund_or_index(r[0])]
            if _stale:
                cur.executemany(f"DELETE FROM {_TABLE} WHERE symbol = ?", [(x,) for x in _stale])
            cur.execute(f"SELECT COUNT(*) FROM {_TABLE} WHERE first_seen_date = ?", (today,))
            _pre_today = int((cur.fetchone() or [0])[0])
            for sym in sorted(observed):
                cur.execute(f"""
                    INSERT INTO {_TABLE} (symbol, first_seen_date, last_seen_date, source)
                    VALUES (?, ?, ?, 'bse_merge')
                    ON CONFLICT(symbol) DO UPDATE SET last_seen_date = excluded.last_seen_date
                """, (sym, today, today))
            cur.execute(f"SELECT COUNT(*) FROM {_TABLE} WHERE first_seen_date = ?", (today,))
            new_count = int((cur.fetchone() or [0])[0]) - _pre_today
            cur.execute(f"SELECT COUNT(*) FROM {_TABLE}")
            _total = int((cur.fetchone() or [0])[0])
            conn.commit()
        finally:
            conn.close()
        print(f"   🔗 Allowlist recorder: {len(observed):,} dual-listed seen on both exchanges "
              f"(ISIN) · {max(new_count, 0)} new · {_total:,} stored"
              + (f" · {n_products} ETF/fund/index skipped" if n_products else "")
              + (f" · {len(_stale)} old fund/index rows removed" if _stale else ""))
    except Exception as _e:
        print(f"   ⚠️  Allowlist recorder: DB write skipped (error: {_e})")
        return 0
    return max(new_count, 0)


def prune_runtime_allowlist(today_iso: Optional[str] = None,
                             ttl_days: int = 30) -> int:
    """
    Remove runtime entries whose last_seen_date < today - ttl_days.
    Returns the number of rows removed. Returns 0 on any error.

    NOTE: This is the only legitimate removal mechanism. It fires when a
    symbol has been absent from NSE bhavcopy for ttl_days consecutive runs,
    which strongly indicates delisting or symbol-rename. Quality of the
    stock is irrelevant.
    """
    today = today_iso or _today_iso()
    try:
        cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=ttl_days)).strftime("%Y-%m-%d")
    except Exception:
        return 0

    try:
        conn = sqlite3.connect(_DB_PATH)
        try:
            _ensure_table(conn)
            cur = conn.execute(f"DELETE FROM {_TABLE} WHERE last_seen_date < ?", (cutoff,))
            removed = cur.rowcount or 0
            conn.commit()
            return removed
        finally:
            conn.close()
    except Exception:
        return 0


def update_last_seen(symbols, today_iso: Optional[str] = None) -> int:
    """
    Touch last_seen_date for symbols still observed today.
    Used to keep an existing runtime entry alive even when BSE merge
    fails — because the SYMBOL is still trading on NSE, even if BSE
    side wasn't visible this run.

    Returns the number of rows updated.
    """
    today = today_iso or _today_iso()
    if not symbols:
        return 0
    try:
        conn = sqlite3.connect(_DB_PATH)
        try:
            _ensure_table(conn)
            placeholders = ",".join("?" * len(symbols))
            cur = conn.execute(
                f"UPDATE {_TABLE} SET last_seen_date = ? WHERE symbol IN ({placeholders})",
                [today] + list(symbols),
            )
            updated = cur.rowcount or 0
            conn.commit()
            return updated
        finally:
            conn.close()
    except Exception:
        return 0