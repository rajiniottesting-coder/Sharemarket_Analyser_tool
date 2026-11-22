#!/usr/bin/env python3
"""
backtest/replay_picks.py — v17.21 · replay every Gold pick on real prices

WHY
The Performance sheet had 6 closed picks on 19-Oct-2026 because two resets
(31-Aug, 17-Sep) deleted 26 open picks before they closed. Their entry, stop
and Target are still on the old dashboards, and their prices are still in
daily_prices (400-day window). This script walks every pick forward with the
LIVE exit model (track_outcomes._walk_forward: single Target, open-price
fills on gaps, trailing tiers, horizon expiry) and then measures four entry
rules that were written down BEFORE looking at the results:

    R1 trend     close > 50-day average AND 63-day return > Nifty's
    R2 regime    Nifty close ≥ its 20-day average on the pick date
    R3 cooldown  no stop-loss exit on the same symbol in the 28 calendar
                 days (≈ 20 trading days) before the pick
    R4 MoS cap   not (MoS > 60% on a LARGE / MID cap) — treats an extreme
                 "discount" on a liquid stock as a fair-value model error

It only READS the database. Nothing in the pipeline uses its output.

INPUTS
    --picks CSV   picks rebuilt from the dashboards (backtest/picks_2026H2.csv):
                  symbol, recommendation_date, cmp_at_recommendation,
                  stop_loss, t1, time_horizon, quick_pick_label,
                  composite_score, sector, cap_category, mos_pct, source
    --db PATH     market_data.db (default ./market_data.db). Rows in
                  gold_recommendations are merged in; a DB row wins over a
                  CSV row for the same (symbol, date).
    --exit        logged  — each pick's own logged stop / Target (default)
                  uniform — logged stop, Target = 1.5 × stop distance for
                            every pick (one exit rule across rule eras)
    --out PATH    per-pick CSV (default replay_results.csv); a Markdown
                  summary is written next to it (…_summary.md)
    --no-nifty    skip the yfinance ^NSEI download (R1/R2 → n/a)

RUN (needs the DB, so normally on GitHub Actions via
.github/workflows/replay.yml):
    python3 backtest/replay_picks.py --picks backtest/picks_2026H2.csv

READING THE RESULT
With ~50 picks a 10-point difference in win rate is still noise-level. Adopt
a rule only if it improves BOTH win rate and mean P&L, keeps enough picks,
and the gap survives the next 30 closes (tag it with a new GOLD_RULE_VERSION,
do not reset the tracker).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

CLOSED = ("SL_HIT", "TRAIL_SL", "T1_HIT", "T2_HIT", "T3_HIT", "EXPIRED")
HORIZON_EXPIRY = {"SHORT TERM": 30, "POSITIONAL": 90, "LONG TERM": 270}
COOLDOWN_DAYS = 28
MOS_CAP_PCT = 60.0
UNIFORM_TARGET_MULT = 1.5


def _f(v, default=None):
    try:
        x = float(v)
        return default if x != x else x
    except (TypeError, ValueError):
        return default


def load_picks(csv_path: str | None, db_path: str) -> pd.DataFrame:
    """CSV picks merged with gold_recommendations (DB wins on duplicates)."""
    frames = []
    if csv_path and os.path.exists(csv_path):
        c = pd.read_csv(csv_path)
        c["origin"] = "dashboard"
        frames.append(c)
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            try:
                d = pd.read_sql_query(
                    "SELECT symbol, recommendation_date, cmp_at_recommendation, stop_loss, t1, "
                    "time_horizon, quick_pick_label, composite_score, sector, cap_category, "
                    "mos_pct FROM gold_recommendations", conn)
            finally:
                conn.close()
            d["source"] = "gold_recommendations"
            d["origin"] = "db"
            frames.append(d)
        except Exception as e:
            print(f"⚠️  gold_recommendations not read: {e}")
    if not frames:
        return pd.DataFrame()
    allp = pd.concat(frames, ignore_index=True)
    allp["recommendation_date"] = allp["recommendation_date"].astype(str).str[:10]
    allp["_pri"] = (allp["origin"] == "db").astype(int)
    allp = (allp.sort_values("_pri", ascending=False)
                .drop_duplicates(["symbol", "recommendation_date"], keep="first")
                .drop(columns="_pri")
                .sort_values(["recommendation_date", "symbol"])
                .reset_index(drop=True))
    # Missing cap category (most dashboard rows) → symbol_master's current one.
    if os.path.exists(db_path) and allp["cap_category"].isna().any():
        try:
            conn = sqlite3.connect(db_path)
            try:
                caps = dict(conn.execute(
                    "SELECT symbol, cap_category FROM symbol_master "
                    "WHERE COALESCE(cap_category, '') <> ''").fetchall())
            finally:
                conn.close()
            miss = allp["cap_category"].isna() | (allp["cap_category"].astype(str).str.strip() == "")
            allp.loc[miss, "cap_category"] = allp.loc[miss, "symbol"].map(caps)
        except Exception as e:
            print(f"⚠️  symbol_master cap categories not read: {e}")
    return allp


def price_history(conn, symbol: str, upto: str, bars: int = 260) -> pd.DataFrame:
    """NSE closes up to and including `upto` (pick-date features, no look-ahead)."""
    df = pd.read_sql_query(
        "SELECT date, close FROM daily_prices WHERE symbol = ? AND exchange = 'NSE' "
        "AND date <= ? ORDER BY date DESC LIMIT ?", conn, params=(symbol, upto, bars))
    return df.sort_values("date").reset_index(drop=True)


def nifty_series(enabled: bool) -> pd.Series | None:
    """^NSEI daily closes (yfinance — reachable on GitHub Actions)."""
    if not enabled:
        return None
    try:
        import yfinance as yf
        d = yf.download("^NSEI", period="2y", interval="1d", progress=False, auto_adjust=False)
        if d is None or d.empty:
            return None
        s = d["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s.index = pd.to_datetime(s.index).strftime("%Y-%m-%d")
        return s.dropna()
    except Exception as e:
        print(f"⚠️  Nifty history unavailable ({e}) — R1 relative strength and R2 → n/a")
        return None


def features(conn, pick: dict, nifty: pd.Series | None) -> dict:
    out = {"close_vs_sma50_pct": None, "ret_63d_pct": None, "nifty_ret_63d_pct": None,
           "rs_63d_pp": None, "nifty_vs_sma20_pct": None}
    rd = pick["recommendation_date"]
    h = price_history(conn, pick["symbol"], rd)
    if len(h) >= 50:
        sma50 = h["close"].tail(50).mean()
        out["close_vs_sma50_pct"] = round((h["close"].iloc[-1] / sma50 - 1) * 100, 2)
    if len(h) >= 64:
        out["ret_63d_pct"] = round((h["close"].iloc[-1] / h["close"].iloc[-64] - 1) * 100, 2)
    if nifty is not None:
        n = nifty[nifty.index <= rd]
        if len(n) >= 20:
            out["nifty_vs_sma20_pct"] = round((n.iloc[-1] / n.tail(20).mean() - 1) * 100, 2)
        if len(n) >= 64:
            out["nifty_ret_63d_pct"] = round((n.iloc[-1] / n.iloc[-64] - 1) * 100, 2)
    if out["ret_63d_pct"] is not None and out["nifty_ret_63d_pct"] is not None:
        out["rs_63d_pp"] = round(out["ret_63d_pct"] - out["nifty_ret_63d_pct"], 2)
    return out


def walk(pick: dict, exit_mode: str) -> dict:
    """Outcome with the live tracker's exit model."""
    import track_outcomes as to
    cmp_ = _f(pick.get("cmp_at_recommendation"), 0.0)
    sl = _f(pick.get("stop_loss"), 0.0)
    t1 = _f(pick.get("t1"), 0.0)
    if exit_mode == "uniform" or not t1:
        t1 = round(cmp_ + UNIFORM_TARGET_MULT * (cmp_ - sl), 2) if cmp_ > sl > 0 else 0.0
    rec = {"symbol": pick["symbol"], "recommendation_date": pick["recommendation_date"],
           "cmp_at_recommendation": cmp_, "stop_loss": sl, "t1": t1,
           "expiry_days": HORIZON_EXPIRY.get(str(pick.get("time_horizon", "")).upper(), 90)}
    r = to._walk_forward(rec)
    pnl = r["current_pnl_pct"]
    return {"target_used": t1, "outcome": r["outcome_type"], "outcome_date": r["outcome_date"],
            "exit_or_last_price": r["current_price"], "pnl_pct": round(float(pnl), 2),
            "max_runup_pct": r["max_runup_pct"], "max_drawdown_pct": r["max_drawdown_pct"]}


def apply_rules(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["R1_trend"] = [None if (a is None or pd.isna(a) or b is None or pd.isna(b)) else (a > 0 and b > 0)
                      for a, b in zip(df["close_vs_sma50_pct"], df["rs_63d_pp"])]
    df["R2_regime"] = [None if (x is None or pd.isna(x)) else (x >= 0) for x in df["nifty_vs_sma20_pct"]]
    # R3: stop-loss exit on the same symbol in the 28 days before this pick
    sl_exits = df[df["outcome"] == "SL_HIT"][["symbol", "outcome_date"]]
    r3 = []
    for _, p in df.iterrows():
        rd = datetime.strptime(p["recommendation_date"], "%Y-%m-%d")
        hits = sl_exits[(sl_exits["symbol"] == p["symbol"])]
        recent = [d for d in hits["outcome_date"]
                  if d and 0 < (rd - datetime.strptime(str(d)[:10], "%Y-%m-%d")).days <= COOLDOWN_DAYS]
        r3.append(not recent)
    df["R3_cooldown"] = r3
    r4 = []
    for _, p in df.iterrows():
        mos = _f(p.get("mos_pct"))
        cap = str(p.get("cap_category") or "").upper()
        if mos is None or not cap:
            r4.append(None)
        else:
            r4.append(not (mos > MOS_CAP_PCT and ("LARGE" in cap or "MID" in cap)))
    df["R4_mos_cap"] = r4
    return df


def stats(sub: pd.DataFrame) -> dict:
    closed = sub[sub["outcome"].isin(CLOSED)]
    wins = closed[closed["pnl_pct"] > 0]
    losses = closed[closed["pnl_pct"] <= 0]
    gl = -losses["pnl_pct"].sum()
    return {"picks": len(sub), "closed": len(closed),
            "target_hits": int(closed["outcome"].isin(("T1_HIT", "T2_HIT", "T3_HIT")).sum()),
            "sl_hits": int((closed["outcome"] == "SL_HIT").sum()),
            "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if len(closed) else None,
            "mean_pnl_pct": round(sub["pnl_pct"].mean(), 2) if len(sub) else None,
            "mean_closed_pnl_pct": round(closed["pnl_pct"].mean(), 2) if len(closed) else None,
            "profit_factor": round(wins["pnl_pct"].sum() / gl, 2) if gl > 0 else None}


def summarize(df: pd.DataFrame) -> str:
    lines = ["# Gold-pick replay", "",
             f"{len(df)} picks · {int(df['outcome'].isin(CLOSED).sum())} closed · "
             f"exit model = live tracker (single Target, open-price gap fills). "
             "Mean P&L mixes closed (realised) and open (marked to the last close).", "",
             "| Group | Picks | Closed | Target | SL | Win % (closed) | Mean P&L % | Profit factor |",
             "|---|---|---|---|---|---|---|---|"]

    def row(label, sub):
        s = stats(sub)
        lines.append(f"| {label} | {s['picks']} | {s['closed']} | {s['target_hits']} | {s['sl_hits']} | "
                     f"{s['win_rate_pct'] if s['win_rate_pct'] is not None else '—'} | "
                     f"{s['mean_pnl_pct'] if s['mean_pnl_pct'] is not None else '—'} | "
                     f"{s['profit_factor'] if s['profit_factor'] is not None else '—'} |")
    row("All picks", df)
    for rule in ("R1_trend", "R2_regime", "R3_cooldown", "R4_mos_cap"):
        known = df[df[rule].notna()]
        row(f"{rule} kept", known[known[rule] == True])     # noqa: E712
        row(f"{rule} dropped", known[known[rule] == False])  # noqa: E712
        if len(known) < len(df):
            lines.append(f"| {rule} n/a | {len(df) - len(known)} | | | | | | |")
    allr = df[["R1_trend", "R2_regime", "R3_cooldown", "R4_mos_cap"]]
    keep_all = allr.apply(lambda r: all(v is not False for v in r), axis=1)
    row("All four rules kept", df[keep_all])
    row("Dropped by any rule", df[~keep_all])
    for arch, sub in df.groupby(df["quick_pick_label"].fillna("—")):
        row(f"Archetype {arch}", sub)
    lines += ["", "n/a = the input needed for that rule is missing for the pick "
              "(no 63-day history, no Nifty data, no MoS/cap). Small samples: treat "
              "every difference as a lead to re-check on the next 30 closes."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--picks", default=os.path.join(_ROOT, "backtest", "picks_2026H2.csv"))
    ap.add_argument("--db", default="market_data.db")
    ap.add_argument("--exit", dest="exit_mode", choices=("logged", "uniform"), default="logged")
    ap.add_argument("--out", default="replay_results.csv")
    ap.add_argument("--no-nifty", action="store_true")
    a = ap.parse_args(argv)
    if not os.path.exists(a.db):
        print(f"❌ {a.db} not found — the replay needs daily_prices (run it on Actions).")
        return 2
    import track_outcomes as to
    _db_before = to.DB_PATH
    to.DB_PATH = a.db
    try:
        picks = load_picks(a.picks, a.db)
        if picks.empty:
            print("❌ no picks to replay")
            return 2
        nifty = nifty_series(not a.no_nifty)
        conn = sqlite3.connect(a.db)
        rows = []
        try:
            for p in picks.to_dict("records"):
                rec = dict(p)
                rec.update(walk(p, a.exit_mode))
                rec.update(features(conn, p, nifty))
                rows.append(rec)
        finally:
            conn.close()
    finally:
        to.DB_PATH = _db_before
    df = apply_rules(pd.DataFrame(rows))
    df.to_csv(a.out, index=False)
    md = summarize(df)
    md_path = os.path.splitext(a.out)[0] + "_summary.md"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    print(md)
    print(f"\n💾 {a.out}  ·  {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
