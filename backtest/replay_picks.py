#!/usr/bin/env python3
"""
backtest/replay_picks.py — v17.22 · replay every Gold pick on real prices

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

and one rule added AFTER the 23-Nov-2026 replay (so it is a lead to confirm
on new picks, not evidence):

    R5 deep value  not a plain DEEP VALUE pick (margin of safety > 25 % with
                   no early-entry signal) — see analysis/deep_value_probation

v17.22 changes (23-Nov-2026 review of the first replay):
  * A pick of a stock that was still open from an earlier pick is listed but
    NOT counted (first appearance only — the live tracker never holds a stock
    twice). The first replay counted 8 of 66 picks twice, e.g. HEROMOTOCO
    8-Sep and 17-Sep, both stopped out on 9-Oct.
  * The summary names the exit mode (both modes printed "live tracker").
  * Averages are over closed picks only; open picks are reported on their
    own line. R (P&L ÷ stop distance) is shown next to P&L %, because the
    risk-parity sizing risks about 1 % of capital per R.
  * Each rule shows whether the kept picks beat the dropped picks on BOTH
    win rate and average R, and a one-sided Fisher exact p-value.
  * A forward window (picks on or after --split-date) applies the DEEP VALUE
    probation decision rule.
  * Breakdowns by archetype, time horizon, margin of safety, stop distance
    and month, labelled as leads.
  * --from-csv re-summarises an earlier result without the database.

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
    --split-date  first pick date of the forward window
                  (default: the DEEP VALUE probation start date)
    --from-csv    re-summarise an earlier per-pick CSV instead of walking
                  the database; writes <csv stem>_resummary.md (or --out's)

RUN (needs the DB, so normally on GitHub Actions via
.github/workflows/replay.yml):
    python3 backtest/replay_picks.py --picks backtest/picks_2026H2.csv
Re-summarise a downloaded result anywhere:
    python3 backtest/replay_picks.py --from-csv replay_results.csv

READING THE RESULT
With ~50 picks a 10-point difference in win rate is still noise-level. Adopt
a rule only if it improves BOTH win rate and the average result, keeps enough
picks, and the gap survives the next 30 closes (tag it with a new
GOLD_RULE_VERSION, do not reset the tracker). All results are gross of
trading costs; entry is assumed at the pick-date close.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from math import comb

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from analysis.deep_value_probation import (  # noqa: E402
    PROBATION_SINCE, forward_verdict, is_on_probation)

CLOSED = ("SL_HIT", "TRAIL_SL", "T1_HIT", "T2_HIT", "T3_HIT", "EXPIRED")
TARGETS = ("T1_HIT", "T2_HIT", "T3_HIT")
HORIZON_EXPIRY = {"SHORT TERM": 30, "POSITIONAL": 90, "LONG TERM": 270}
COOLDOWN_DAYS = 28
MOS_CAP_PCT = 60.0
UNIFORM_TARGET_MULT = 1.5
PRESET_RULES = ("R1_trend", "R2_regime", "R3_cooldown", "R4_mos_cap")
RULE_NAMES = {"R1_trend": "R1 trend", "R2_regime": "R2 regime",
              "R3_cooldown": "R3 cooldown", "R4_mos_cap": "R4 MoS cap",
              "R5_deep_value": "R5 not DEEP VALUE"}
EXIT_TEXT = {"logged": "**logged** — each pick's own stop and Target",
             "uniform": f"**uniform** — logged stop, Target = {UNIFORM_TARGET_MULT} × stop distance"}


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
    return {"exit_mode": exit_mode, "target_used": t1, "outcome": r["outcome_type"],
            "outcome_date": r["outcome_date"],
            "exit_or_last_price": r["current_price"], "pnl_pct": round(float(pnl), 2),
            "max_runup_pct": r["max_runup_pct"], "max_drawdown_pct": r["max_drawdown_pct"]}


def _is_closed(outcome) -> bool:
    return str(outcome) in CLOSED


def mark_overlaps(df: pd.DataFrame) -> pd.DataFrame:
    """First appearance only, as the live tracker logs picks.

    A pick is not counted when an earlier COUNTED pick of the same symbol is
    still open on its date: not closed at all, or closed on or after it (the
    live hook runs before that day's tracker pass, so a position that closes
    on the pick date is still open when the new pick would be logged).
    """
    df = df.sort_values(["recommendation_date", "symbol"]).reset_index(drop=True)
    counted, overlap_of = [], []
    open_until = {}          # symbol → (pick date, close date or None = still open)
    for _, p in df.iterrows():
        sym, rd = p["symbol"], str(p["recommendation_date"])[:10]
        prev = open_until.get(sym)
        if prev is not None and (prev[1] is None or prev[1] >= rd):
            counted.append(False)
            overlap_of.append(prev[0])
            continue
        counted.append(True)
        overlap_of.append("")
        od = p.get("outcome_date")
        closed_on = str(od)[:10] if (_is_closed(p.get("outcome")) and isinstance(od, str) and od) else None
        open_until[sym] = (rd, closed_on)
    df["counted"] = counted
    df["overlap_of"] = overlap_of
    return df


def add_risk(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    risk, rr = [], []
    for _, p in df.iterrows():
        cmp_, sl, pnl = _f(p.get("cmp_at_recommendation")), _f(p.get("stop_loss")), _f(p.get("pnl_pct"))
        if cmp_ and sl and cmp_ > sl > 0:
            rp = (cmp_ - sl) / cmp_ * 100
            risk.append(round(rp, 2))
            rr.append(round(pnl / rp, 3) if pnl is not None else None)
        else:
            risk.append(None)
            rr.append(None)
    df["risk_pct"] = risk
    df["r_multiple"] = rr
    return df


def apply_rules(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "counted" not in df.columns:
        df["counted"] = True
    df["R1_trend"] = [None if (a is None or pd.isna(a) or b is None or pd.isna(b)) else bool(a > 0 and b > 0)
                      for a, b in zip(df["close_vs_sma50_pct"], df["rs_63d_pp"])]
    df["R2_regime"] = [None if (x is None or pd.isna(x)) else bool(x >= 0) for x in df["nifty_vs_sma20_pct"]]
    # R3: stop-loss exit on the same symbol in the 28 days before this pick
    # (counted picks only — a re-pick that the tracker would not have logged
    # cannot have produced a stop-out).
    sl_exits = df[(df["outcome"] == "SL_HIT") & df["counted"].astype(bool)][["symbol", "outcome_date"]]
    r3 = []
    for _, p in df.iterrows():
        rd = datetime.strptime(str(p["recommendation_date"])[:10], "%Y-%m-%d")
        hits = sl_exits[(sl_exits["symbol"] == p["symbol"])]
        recent = [d for d in hits["outcome_date"]
                  if isinstance(d, str) and d
                  and 0 < (rd - datetime.strptime(d[:10], "%Y-%m-%d")).days <= COOLDOWN_DAYS]
        r3.append(not recent)
    df["R3_cooldown"] = r3
    r4 = []
    for _, p in df.iterrows():
        mos = _f(p.get("mos_pct"))
        cap = str(p.get("cap_category") or "").upper()
        if cap == "NAN":
            cap = ""
        if mos is None or not cap:
            r4.append(None)
        else:
            r4.append(not (mos > MOS_CAP_PCT and ("LARGE" in cap or "MID" in cap)))
    df["R4_mos_cap"] = r4
    r5 = []
    for lab in df.get("quick_pick_label", pd.Series([None] * len(df))):
        r5.append(None if (lab is None or (isinstance(lab, float) and lab != lab) or str(lab).strip() == "")
                  else not is_on_probation(lab))
    df["R5_deep_value"] = r5
    return df


def fisher_p(kept_wins: int, kept_n: int, drop_wins: int, drop_n: int):
    """One-sided Fisher exact test: P(kept wins ≥ observed) if the rule made
    no difference to the win rate. None when a group is empty."""
    if kept_n <= 0 or drop_n <= 0:
        return None
    n, wins = kept_n + drop_n, kept_wins + drop_wins
    denom = comb(n, wins)
    top = min(kept_n, wins)
    return sum(comb(kept_n, x) * comb(drop_n, wins - x)
               for x in range(kept_wins, top + 1)) / denom


def stats(sub: pd.DataFrame) -> dict:
    closed = sub[sub["outcome"].isin(CLOSED)]
    open_ = sub[~sub["outcome"].isin(CLOSED)]
    wins = closed[closed["pnl_pct"] > 0]
    losses = closed[closed["pnl_pct"] <= 0]
    gl = -losses["pnl_pct"].sum()
    r = pd.to_numeric(closed.get("r_multiple"), errors="coerce").dropna() if len(closed) else pd.Series(dtype=float)
    return {"picks": len(sub), "closed": len(closed), "wins": len(wins),
            "target_hits": int(closed["outcome"].isin(TARGETS).sum()),
            "sl_hits": int((closed["outcome"] == "SL_HIT").sum()),
            "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if len(closed) else None,
            "avg_closed_pnl_pct": round(closed["pnl_pct"].mean(), 2) if len(closed) else None,
            "avg_r": round(r.mean(), 2) if len(r) else None,
            "total_r": round(r.sum(), 1) if len(r) else None,
            "profit_factor": round(wins["pnl_pct"].sum() / gl, 2) if gl > 0 else None,
            "open": len(open_),
            "avg_open_pnl_pct": round(open_["pnl_pct"].mean(), 2) if len(open_) else None}


def _v(x, fmt="{}"):
    return "—" if x is None else fmt.format(x)


_STATS_HDR = ["| Group | Picks | Closed | Target | SL | Win % | Avg P&L % | Avg R | Total R | PF |",
              "|---|---|---|---|---|---|---|---|---|---|"]


def _stats_row(label: str, sub: pd.DataFrame) -> str:
    s = stats(sub)
    return (f"| {label} | {s['picks']} | {s['closed']} | {s['target_hits']} | {s['sl_hits']} | "
            f"{_v(s['win_rate_pct'])} | {_v(s['avg_closed_pnl_pct'], '{:+.2f}')} | "
            f"{_v(s['avg_r'], '{:+.2f}')} | {_v(s['total_r'], '{:+.1f}')} | {_v(s['profit_factor'])} |")


def _rule_row(label: str, kept: pd.DataFrame, dropped: pd.DataFrame) -> str:
    k, d = stats(kept), stats(dropped)
    p = fisher_p(k["wins"], k["closed"], d["wins"], d["closed"])
    both = None
    if None not in (k["win_rate_pct"], d["win_rate_pct"], k["avg_r"], d["avg_r"]):
        both = k["win_rate_pct"] > d["win_rate_pct"] and k["avg_r"] > d["avg_r"]
    return (f"| {label} | {k['closed']} | {_v(k['win_rate_pct'])} | {_v(k['avg_r'], '{:+.2f}')} | "
            f"{d['closed']} | {_v(d['win_rate_pct'])} | {_v(d['avg_r'], '{:+.2f}')} | "
            f"{'—' if both is None else ('yes' if both else 'no')} | {_v(None if p is None else round(p, 3))} |")


_RULE_HDR = ["| Rule | Kept closed | Kept win % | Kept avg R | Dropped closed | Dropped win % | "
             "Dropped avg R | Kept better on both? | p |",
             "|---|---|---|---|---|---|---|---|---|"]


def _mos_band(m):
    m = _f(m)
    if m is None:
        return "unknown"
    if m <= 25:
        return "≤ 25 %"
    if m <= 40:
        return "25–40 %"
    if m <= 60:
        return "40–60 %"
    return "> 60 %"


def _stop_band(r):
    r = _f(r)
    if r is None:
        return "unknown"
    if r <= 7.0:
        return "≤ 7 %"
    if r <= 9.0:
        return "7–9 %"
    return "> 9 %"


def summarize(df: pd.DataFrame, exit_mode: str, split_date: str = PROBATION_SINCE) -> str:
    c = df[df["counted"].astype(bool)].copy()
    skipped = df[~df["counted"].astype(bool)]
    n_closed = int(c["outcome"].isin(CLOSED).sum())
    lines = ["# Gold-pick replay", "",
             f"Exit mode: {EXIT_TEXT.get(exit_mode, exit_mode)}.", "",
             f"Picks in the file: {len(df)} · counted: {len(c)}"
             + (f" ({len(skipped)} re-picks of a stock that was still open from an earlier pick are "
                f"left out, as the live tracker logs a stock only once — listed at the end)" if len(skipped) else "")
             + f" · closed: {n_closed} · open: {len(c) - n_closed}.", "",
             "R = P&L ÷ the pick's stop distance; the risk-parity sizing risks about 1 % of capital per R. "
             "Results are gross (no brokerage, STT, stamp duty or slippage); entry = the pick-date close. "
             "Averages are over closed picks; open picks are marked to the last close on their own line.", "",
             "## Overall", ""] + _STATS_HDR
    lines.append(_stats_row("Counted picks", c))
    s_open = c[~c["outcome"].isin(CLOSED)]
    if len(s_open):
        ro = pd.to_numeric(s_open["r_multiple"], errors="coerce").dropna()
        lines.append(f"| Open picks (marked to the last close) | {len(s_open)} | | | | | "
                     f"{s_open['pnl_pct'].mean():+.2f} | {_v(round(ro.mean(), 2) if len(ro) else None, '{:+.2f}')} | | |")

    lines += ["", "## Entry rules fixed before the replay (R1–R4)", ""] + _RULE_HDR
    for rule in PRESET_RULES:
        known = c[c[rule].notna()]
        lines.append(_rule_row(RULE_NAMES[rule], known[known[rule] == True],   # noqa: E712
                               known[known[rule] == False]))                   # noqa: E712
    keep_all = c[list(PRESET_RULES)].apply(lambda r: all(v is not False for v in r), axis=1)
    lines.append(_rule_row("All four kept (vs dropped by any)", c[keep_all], c[~keep_all]))
    na = [f"{RULE_NAMES[r]} {int(c[r].isna().sum())}" for r in PRESET_RULES if c[r].isna().any()]
    lines += ["", "p = one-sided Fisher exact test: the chance of the kept picks winning at least this "
              "often if the rule made no difference. Adopt a rule only when the kept picks are better on "
              "BOTH win rate and average R and the gap holds on new picks."
              + (f" Rule input missing (n/a, not counted in that rule's row): {', '.join(na)}." if na else "")]

    lines += ["", "## R5 — DEEP VALUE probation (added 23-Nov-2026 after looking at the data)", "",
              "Confirm on new picks before treating it as evidence (see the forward window).", ""] + _RULE_HDR
    k5 = c[c["R5_deep_value"].notna()]
    lines.append(_rule_row(RULE_NAMES["R5_deep_value"], k5[k5["R5_deep_value"] == True],   # noqa: E712
                           k5[k5["R5_deep_value"] == False]))                              # noqa: E712

    fw = c[c["recommendation_date"].astype(str).str[:10] >= split_date]
    lines += ["", f"## Forward window — picks dated on or after {split_date}", ""]
    if fw.empty:
        lines.append("No picks in the window yet.")
    else:
        dv = fw[fw["quick_pick_label"].apply(is_on_probation)]
        ot = fw[~fw["quick_pick_label"].apply(is_on_probation)]
        lines += _STATS_HDR
        lines.append(_stats_row("DEEP VALUE (probation)", dv))
        lines.append(_stats_row("All other picks", ot))
        lines.append(_stats_row("All picks in the window", fw))

        def _closed_r(sub):
            sc = sub[sub["outcome"].isin(CLOSED)]
            return [x for x in pd.to_numeric(sc["r_multiple"], errors="coerce").tolist() if x == x]
        v = forward_verdict(_closed_r(dv), _closed_r(ot))
        lines += ["", f"Probation decision rule: **{v['code']}** — {v['text']}."]

    lines += ["", "## Breakdowns (found by looking at the data — leads only)"]
    groupings = [
        ("Archetype", c["quick_pick_label"].fillna("—").astype(str)),
        ("Time horizon", c["time_horizon"].fillna("—").astype(str)),
        ("Margin of safety", c["mos_pct"].apply(_mos_band)),
        ("Stop distance", c["risk_pct"].apply(_stop_band)),
        ("Pick month", c["recommendation_date"].astype(str).str[:7]),
    ]
    order = {"Margin of safety": ["≤ 25 %", "25–40 %", "40–60 %", "> 60 %", "unknown"],
             "Stop distance": ["≤ 7 %", "7–9 %", "> 9 %", "unknown"]}
    for title, key in groupings:
        lines += ["", f"### By {title.lower()}", ""] + _STATS_HDR
        groups = dict(tuple(c.groupby(key, sort=True)))
        for g in order.get(title, sorted(groups)):
            if g in groups:
                lines.append(_stats_row(str(g), groups[g]))

    if len(skipped):
        lines += ["", "## Re-picks left out (stock still open from an earlier pick)", "",
                  "| Symbol | Pick date | Still open from | Outcome if it had been counted |",
                  "|---|---|---|---|"]
        for _, p in skipped.iterrows():
            lines.append(f"| {p['symbol']} | {p['recommendation_date']} | {p['overlap_of']} | "
                         f"{p['outcome']} {p['pnl_pct']:+.2f} % |")
    return "\n".join(lines)


def _infer_exit_mode(df: pd.DataFrame) -> str:
    if "exit_mode" in df.columns and df["exit_mode"].notna().any():
        return str(df["exit_mode"].dropna().iloc[0])
    t1 = pd.to_numeric(df.get("t1"), errors="coerce")
    tu = pd.to_numeric(df.get("target_used"), errors="coerce")
    both = t1.notna() & tu.notna()
    if both.any() and ((t1[both] - tu[both]).abs() > 0.01).any():
        return "uniform"
    return "logged"


def finish(df: pd.DataFrame, exit_mode: str, split_date: str):
    """Derived columns (overlaps, R, rules) + Markdown summary."""
    df = mark_overlaps(df)
    df = add_risk(df)
    df = apply_rules(df)
    return df, summarize(df, exit_mode, split_date)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--picks", default=os.path.join(_ROOT, "backtest", "picks_2026H2.csv"))
    ap.add_argument("--db", default="market_data.db")
    ap.add_argument("--exit", dest="exit_mode", choices=("logged", "uniform"), default="logged")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-nifty", action="store_true")
    ap.add_argument("--split-date", default=PROBATION_SINCE)
    ap.add_argument("--from-csv", default=None)
    a = ap.parse_args(argv)

    if a.from_csv:
        if not os.path.exists(a.from_csv):
            print(f"❌ {a.from_csv} not found")
            return 2
        raw = pd.read_csv(a.from_csv)
        raw["recommendation_date"] = raw["recommendation_date"].astype(str).str[:10]
        df, md = finish(raw, _infer_exit_mode(raw), a.split_date)
        md_path = (os.path.splitext(a.out)[0] + "_summary.md" if a.out
                   else os.path.splitext(a.from_csv)[0] + "_resummary.md")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(md + "\n")
        print(md)
        print(f"\n💾 {md_path}")
        return 0

    out = a.out or "replay_results.csv"
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
    df, md = finish(pd.DataFrame(rows), a.exit_mode, a.split_date)
    df.to_csv(out, index=False)
    md_path = os.path.splitext(out)[0] + "_summary.md"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md + "\n")
    print(md)
    print(f"\n💾 {out}  ·  {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
