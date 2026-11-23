"""
database/price_date_repair.py — v17.22 · one-time repair of daily_prices dates

WHY
Until v17.22, save_to_database() stamped each daily run's price rows with
date.today() on the GitHub runner (UTC clock). Every run that started after
00:00 UTC — 86 of the 100 runs up to 21-Nov-2026, all of them since 26-Aug —
stored the bars one calendar day late (Friday's bar as Saturday, Monday's as
Tuesday). Backfilled and gap-filled rows carry the bhavcopy date and are
correct. The outcome tracker walks bars dated AFTER the pick date, so for
those picks its first bar was the pick day's own bar, whose high and low came
before the entry at the close.

HOW
Each stored date label holds the rows of exactly one run (save_to_database
deletes the label before inserting). So the true trading date of a label can
be read from a few liquid stocks: their closes stored under the label are
compared with Yahoo Finance closes (correct exchange dates) of every trading
day up to LOOKBACK_DAYS before it. The trading day whose closes match — median
difference ≤ MATCH_MAX_PCT, clearly better than the next-best day — is the
label's true date. Every label that moves is moved for all exchanges; labels
whose match is unclear are left as they are. When two labels belong to the
same trading day (the same day run twice), the copy whose label was already
right is kept.

Every decision is written to price_date_repair_log (label, true date, status,
match quality, rows), so the repair can be checked and undone. The one-time
marker (pipeline_migrations 'v17_22_price_dates') is set only after a
successful run; a failed fetch leaves everything unchanged and retries on the
next run. master_funnel stores new rows under the trading date only once this
marker is set, so old and new rows never mix two date conventions.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from statistics import median

MIGRATION_ID = "v17_22_price_dates"
ANCHORS = ("RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
           "SBIN", "BHARTIARTL", "ITC", "LT", "AXISBANK")
LOOKBACK_DAYS = 6          # a label is at most a long weekend after its trading day
MIN_ANCHORS = 3            # anchors needed to judge a label
MATCH_MAX_PCT = 0.30       # best day's median |close difference| must be ≤ this
SEPARATION_RATIO = 2.0     # runner-up must be ≥ 2× worse ...
SEPARATION_MIN_PP = 0.15   # ... and ≥ 0.15 percentage points worse


def _iso(d) -> str:
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]


def fetch_anchor_closes(symbols, start_iso: str, end_iso: str) -> dict:
    """{symbol: {date_iso: close}} from Yahoo Finance (.NS, unadjusted for
    dividends). {} on any failure. Reachable on GitHub Actions."""
    try:
        import pandas as pd
        import yfinance as yf
        end_excl = _iso(datetime.strptime(end_iso, "%Y-%m-%d").date() + timedelta(days=2))
        tickers = [f"{s}.NS" for s in symbols]
        d = yf.download(tickers, start=start_iso, end=end_excl, interval="1d",
                        progress=False, auto_adjust=False, group_by="column", threads=False)
        if d is None or d.empty:
            return {}
        close = d["Close"] if "Close" in d.columns.get_level_values(0) else d
        out = {}
        for s, t in zip(symbols, tickers):
            if isinstance(close, pd.DataFrame) and t in close.columns:
                col = close[t]
            elif isinstance(close, pd.Series) and len(tickers) == 1:
                col = close
            else:
                continue
            col = col.dropna()
            out[s] = {pd.Timestamp(ix).strftime("%Y-%m-%d"): float(v) for ix, v in col.items()}
        return out
    except Exception as e:
        print(f"   ⚠️  price-date repair: Yahoo anchor closes unavailable ({type(e).__name__}: {e})")
        return {}


def _db_anchor_closes(conn, symbols) -> dict:
    ph = ",".join("?" * len(symbols))
    rows = conn.execute(
        f"SELECT symbol, date, close FROM daily_prices WHERE exchange = 'NSE' "
        f"AND symbol IN ({ph}) AND close > 0", tuple(symbols)).fetchall()
    out: dict = {}
    for sym, d, c in rows:
        out.setdefault(str(d)[:10], {})[sym] = float(c)
    return out


def plan_repair(conn, anchor_closes: dict) -> list:
    """One decision per stored date label:
    {label, true_date, status, anchors, best_pct, second_pct, rows}
    status: SAME (label is right) · MOVE (label → true_date) · UNCLEAR."""
    labels = [r[0] for r in conn.execute(
        "SELECT date, COUNT(*) FROM daily_prices GROUP BY date ORDER BY date").fetchall()]
    counts = dict(conn.execute("SELECT date, COUNT(*) FROM daily_prices GROUP BY date").fetchall())
    db_close = _db_anchor_closes(conn, ANCHORS)
    yf_dates = sorted({d for s in anchor_closes.values() for d in s})
    plan = []
    for label in labels:
        lab = str(label)[:10]
        rec = {"label": lab, "true_date": lab, "status": "UNCLEAR", "anchors": 0,
               "best_pct": None, "second_pct": None, "rows": int(counts.get(label, 0))}
        try:
            ld = datetime.strptime(lab, "%Y-%m-%d").date()
        except ValueError:
            plan.append(rec)
            continue
        lo = _iso(ld - timedelta(days=LOOKBACK_DAYS))
        cands = [d for d in yf_dates if lo <= d <= lab]
        mine = db_close.get(lab, {})
        scored = []
        for cand in cands:
            diffs = []
            for sym, c in mine.items():
                y = anchor_closes.get(sym, {}).get(cand)
                if y and y > 0:
                    diffs.append(abs(c - y) / y * 100)
            if len(diffs) >= MIN_ANCHORS:
                scored.append((median(diffs), cand, len(diffs)))
        if not scored:
            plan.append(rec)
            continue
        scored.sort()
        best, best_d, n = scored[0]
        second = scored[1][0] if len(scored) > 1 else None
        rec.update(anchors=n, best_pct=round(best, 3),
                   second_pct=None if second is None else round(second, 3))
        clear = best <= MATCH_MAX_PCT and (
            second is None
            or (second >= SEPARATION_RATIO * best and second - best >= SEPARATION_MIN_PP))
        if clear:
            rec["true_date"] = best_d
            rec["status"] = "SAME" if best_d == lab else "MOVE"
        plan.append(rec)
    return plan


def apply_repair(conn, plan: list) -> dict:
    """Move the MOVE labels to their true dates (all exchanges) in one
    transaction. When several labels end up on the same trading day, one
    copy is kept — a label that was already right (SAME) first, then a moved
    one (earliest label first), an UNCLEAR one last — and the others are
    deleted. UNCLEAR labels themselves are never moved."""
    moves = {p["label"]: p["true_date"] for p in plan if p["status"] == "MOVE"}
    if not moves:
        return {"moved_labels": 0, "moved_rows": 0, "dropped_labels": 0, "dropped_rows": 0}
    status = {p["label"]: p["status"] for p in plan}
    # who ends up on each true date
    owners: dict = {}
    for p in plan:
        tgt = p["true_date"] if p["status"] == "MOVE" else p["label"]
        owners.setdefault(tgt, []).append(p["label"])
    prio = {"SAME": 0, "MOVE": 1, "UNCLEAR": 2}
    losers = []
    for tgt, labs in owners.items():
        if len(labs) > 1:
            labs = sorted(labs, key=lambda l: (prio.get(status.get(l), 3), l))
            losers.extend(labs[1:])
    moved_rows = dropped_rows = 0
    cur = conn.cursor()
    try:
        # one implicit transaction (sqlite3 opens it at the first DELETE/UPDATE)
        for l in losers:
            cur.execute("DELETE FROM daily_prices WHERE date = ?", (l,))
            dropped_rows += cur.rowcount
        live_moves = {l: t for l, t in moves.items() if l not in losers}
        # two steps so a chain (Sat→Fri while Fri→Thu) never hits the primary key
        for l in live_moves:
            cur.execute("UPDATE daily_prices SET date = ? WHERE date = ?", ("~" + l, l))
        for l, t in live_moves.items():
            cur.execute("UPDATE daily_prices SET date = ? WHERE date = ?", (t, "~" + l))
            moved_rows += cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"moved_labels": len(live_moves), "moved_rows": moved_rows,
            "dropped_labels": len(losers), "dropped_rows": dropped_rows,
            "losers": losers}


def _write_log(conn, plan: list, losers) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_date_repair_log (
            label        TEXT PRIMARY KEY,
            true_date    TEXT,
            status       TEXT,
            anchors      INTEGER,
            best_pct     REAL,
            second_pct   REAL,
            rows         INTEGER,
            dropped      INTEGER DEFAULT 0,
            logged_at    TEXT
        )""")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lose = set(losers or ())
    conn.executemany(
        "INSERT OR REPLACE INTO price_date_repair_log "
        "(label, true_date, status, anchors, best_pct, second_pct, rows, dropped, logged_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(p["label"], p["true_date"], p["status"], p["anchors"], p["best_pct"],
          p["second_pct"], p["rows"], 1 if p["label"] in lose else 0, now) for p in plan])
    conn.commit()


def run_price_date_repair(db_path: str = "market_data.db", fetch=fetch_anchor_closes) -> dict:
    """Run the one-time repair if it has not run yet.
    Returns {"ok": bool, "log_lines": [...], ...}. ok=True means the dates in
    daily_prices follow the trading-date convention (now or from an earlier
    run), so new rows may be stored under the trading date."""
    from database.data_bridge import mark_migration, migration_applied
    if migration_applied(MIGRATION_ID):
        return {"ok": True, "log_lines": []}
    conn = sqlite3.connect(db_path)
    try:
        span = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_prices").fetchone()
        if not span or not span[2]:
            mark_migration(MIGRATION_ID, 0)
            return {"ok": True, "log_lines": ["   🗓️  v17.22 price-date repair: no stored prices — nothing to repair"]}
        start = _iso(datetime.strptime(str(span[0])[:10], "%Y-%m-%d").date() - timedelta(days=10))
        closes = fetch(ANCHORS, start, str(span[1])[:10]) or {}
        if sum(1 for s in closes.values() if s) < MIN_ANCHORS:
            return {"ok": False, "log_lines": [
                "   ⚠️  v17.22 price-date repair NOT run: Yahoo anchor closes unavailable — "
                "dates unchanged, retry next run (new rows keep the old date stamp until then)"]}
        plan = plan_repair(conn, closes)
        res = apply_repair(conn, plan)
        _write_log(conn, plan, res.get("losers"))
        n = {k: sum(1 for p in plan if p["status"] == k) for k in ("SAME", "MOVE", "UNCLEAR")}
        mark_migration(MIGRATION_ID, res["moved_rows"])
        # trading days Yahoo knows but daily_prices now lacks (bars lost when
        # two runs wrote the same label before v17.22)
        have = {r[0] for r in conn.execute("SELECT DISTINCT date FROM daily_prices").fetchall()}
        first_moved = min((p["label"] for p in plan if p["status"] == "MOVE"), default=None)
        yf_days = sorted({d for s in closes.values() for d in s})
        missing = [d for d in yf_days if first_moved and d >= first_moved and d not in have]
        lines = [f"   🗓️  v17.22 price-date repair: {len(plan)} stored dates checked · "
                 f"{n['SAME']} already right · {n['MOVE']} moved to their trading day "
                 f"({res['moved_rows']:,} rows) · {n['UNCLEAR']} unclear (left as they were) · "
                 f"{res['dropped_labels']} duplicate day(s) removed — details in price_date_repair_log"]
        if missing:
            lines.append(f"   🗓️  v17.22 price-date repair: {len(missing)} trading day(s) since "
                         f"{first_moved} have no bar (overwritten before v17.22): "
                         f"{', '.join(missing[:12])}{' …' if len(missing) > 12 else ''}")
        return {"ok": True, "log_lines": lines, "plan": plan, "result": res,
                "counts": n, "missing_days": missing}
    finally:
        conn.close()
