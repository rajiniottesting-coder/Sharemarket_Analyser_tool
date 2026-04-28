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


def record_dual_listed_observations(reconciled_df, today_iso: Optional[str] = None,
                                     hardcoded_allowlist: Optional[Set[str]] = None) -> int:
    """
    Scan a reconciled DataFrame and persist any newly-observed DUAL_LISTED
    symbols that aren't already on the hardcoded allowlist.

    Parameters
    ----------
    reconciled_df : pd.DataFrame
        Output of reconcile_exchanges() — must have 'exchange_tag' column.
    today_iso : str, optional
        ISO date string. Defaults to today.
    hardcoded_allowlist : set, optional
        The frozenset from reconciler. If provided, symbols already in it
        are skipped (no point duplicating). If None, all observed
        DUAL_LISTED symbols are recorded.

    Returns the number of NEW symbols inserted (existing-symbol updates
    don't count as new). Returns 0 on any error.
    """
    today = today_iso or _today_iso()

    if reconciled_df is None or len(reconciled_df) == 0:
        return 0
    if "exchange_tag" not in reconciled_df.columns:
        return 0

    # Find the symbol column — in post-merge dfs it can be 'symbol_NSE' or 'symbol'
    sym_col = None
    for cand in ("symbol_NSE", "symbol", "symbol_BSE"):
        if cand in reconciled_df.columns:
            sym_col = cand
            break
    if sym_col is None:
        return 0

    try:
        # Filter to DUAL_LISTED rows
        mask = reconciled_df["exchange_tag"].astype(str).str.upper() == "DUAL_LISTED"
        observed = (
            reconciled_df.loc[mask, sym_col]
            .dropna()
            .astype(str)
            .str.strip()
            .str.upper()
        )
        observed = {s for s in observed if s and s != "NAN"}
    except Exception:
        return 0

    if not observed:
        return 0

    # Filter out symbols already on the hardcoded allowlist (no point duplicating)
    if hardcoded_allowlist:
        observed = observed - set(hardcoded_allowlist)

    if not observed:
        return 0

    new_count = 0
    try:
        conn = sqlite3.connect(_DB_PATH)
        try:
            _ensure_table(conn)
            cur = conn.cursor()
            for sym in sorted(observed):
                # Insert if new, update last_seen_date if existing
                cur.execute(f"""
                    INSERT INTO {_TABLE} (symbol, first_seen_date, last_seen_date, source)
                    VALUES (?, ?, ?, 'bse_merge')
                    ON CONFLICT(symbol) DO UPDATE SET last_seen_date = excluded.last_seen_date
                """, (sym, today, today))
                if cur.rowcount and cur.lastrowid:
                    # rowcount is 1 for both insert and conflict-update; check if it was actually new
                    pass
            # Count true insertions
            cur.execute(f"SELECT COUNT(*) FROM {_TABLE} WHERE first_seen_date = ?", (today,))
            row = cur.fetchone()
            new_count = int(row[0]) if row else 0
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return 0

    return new_count


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