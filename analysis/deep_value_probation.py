"""
analysis/deep_value_probation.py — v17.22 · DEEP VALUE picks on probation

WHY
The Gold-pick replay of 23-Nov-2026 (backtest/replay_picks.py, 58 independent
picks 20-Jul → 21-Nov-2026, 45 closed) found one consistent pattern: Gold
picks labelled DEEP VALUE (margin of safety above 25 % without an early-entry
signal) lost 17.2 R on 32 closed trades (22 % won), while all other Gold
picks made 6.0 R on 13 (62 % won). The pattern was first seen on 19-Oct and
re-checked on largely the same picks, so it is a lead, not proof.

RE-CHECK ON CORRECTED PRICE DATES (24-Nov-2026, v17.22.2)
The 23-Nov replay ran on price bars stored one day late (fixed in v17.22).
Re-run on the re-dated bars (prices to 23-Nov-2026, runs 45353469993 logged /
45353482214 uniform): DEEP VALUE 34 closed, 24 % won, −15.4 R; all other
picks unchanged at 13 closed, 62 % won, +6.0 R (one-sided Fisher p 0.018;
uniform 1.5× targets p 0.046). Only one DEEP VALUE outcome changed with the
dates (SHREERAMA: a −15.6 % stop that was its own pick-day open became a
+10.5 % target); BLACKROSE and KKCL closed at their stops on 23-Nov. The
conclusion stands; the decision rule below is unchanged.

WHAT CHANGES
Display only. The 15 Gold gates, the outcome logging, the tracker, the AI-card
scope and GOLD_RULE_VERSION are unchanged, so every DEEP VALUE Gold pick is
still logged and tracked exactly as before. The Gold sheet, Trade Summary and
Delivery Preview show these picks in a separate "on probation — tracked, not
recommended" block, and the Performance sheet shows the forward test.

THE DECISION RULE (fixed on 23-Nov-2026, before any forward data)
Forward window = Gold picks with recommendation_date ≥ PROBATION_SINCE. Once
PROBATION_MIN_CLOSED of them have closed, with at least PROBATION_MIN_PER_GROUP
closed in each group, compare the average R of closed DEEP VALUE picks with
the average R of all other closed picks:
    GATE    DEEP VALUE avg R < 0 and below the others → make the exclusion a
            Gold gate under a new GOLD_RULE_VERSION (never a reset)
    END     DEEP VALUE avg R ≥ the others → end the probation
    EXTEND  anything else (or a group too small) → another 30 closes
R = (exit price − entry) / (entry − original stop): the loss or gain in units
of the risk taken. Risk-parity sizing risks about 1 % of capital per R.

Pure Python, no I/O — imported by reporting/excel_generator.py,
master_funnel.py and backtest/replay_picks.py so all three agree.
"""
from __future__ import annotations

from typing import Iterable

PROBATION_LABEL = "DEEP VALUE"
PROBATION_SINCE = "2026-11-23"     # the replay used prices up to 20-Nov-2026 (a Friday);
                                   # picks from 23-Nov on were never seen by it
PROBATION_MIN_CLOSED = 30
PROBATION_MIN_PER_GROUP = 5

# Evidence the decision was taken on (shown on the sheets; do not update — a
# new replay result belongs in a new note, not in this one).
PROBATION_EVIDENCE = ("Replay of 23-Nov-2026 (58 Gold picks, 20-Jul → 21-Nov-2026): "
                      "DEEP VALUE 32 closed, 22 % won, −17.2 R · all other picks "
                      "13 closed, 62 % won, +6.0 R")

# v17.22.2: the same replay re-run on the corrected price dates (the 23-Nov
# run read bars stored one day late). A new note, as the comment above asks.
PROBATION_RECHECK = ("Re-checked 24-Nov-2026 on corrected price dates (prices to "
                     "23-Nov-2026): DEEP VALUE 34 closed, 24 % won, −15.4 R · all "
                     "other picks 13 closed, 62 % won, +6.0 R")


def probation_evidence_text() -> str:
    """The evidence line shown on the sheets: the decision-time result, then
    every later re-check, oldest first."""
    return f"{PROBATION_EVIDENCE}. {PROBATION_RECHECK}"


def is_on_probation(label) -> bool:
    """True for the plain DEEP VALUE quick-pick label only.

    'DEEP VALUE EARLY MOVER' (the same discount WITH an early-entry signal) is
    not on probation; neither is a missing label.
    """
    if label is None:
        return False
    try:
        if label != label:          # NaN
            return False
    except Exception:
        return False
    return str(label).strip().upper() == PROBATION_LABEL


def r_multiple(entry, stop, exit_price):
    """(exit − entry) / (entry − stop); None when the inputs cannot give one."""
    try:
        e, s, x = float(entry), float(stop), float(exit_price)
    except (TypeError, ValueError):
        return None
    if not (e > 0 and s > 0 and x > 0) or e <= s:
        return None
    return (x - e) / (e - s)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def forward_verdict(dv_r: Iterable, other_r: Iterable) -> dict:
    """Apply the pre-registered rule to the closed R values of the window."""
    dv = [x for x in dv_r if x is not None]
    ot = [x for x in other_r if x is not None]
    n = len(dv) + len(ot)
    out = {"closed": n, "dv_closed": len(dv), "other_closed": len(ot),
           "dv_avg_r": _mean(dv), "other_avg_r": _mean(ot),
           "dv_win_pct": (sum(1 for x in dv if x > 0) / len(dv) * 100) if dv else None,
           "other_win_pct": (sum(1 for x in ot if x > 0) / len(ot) * 100) if ot else None}
    if n < PROBATION_MIN_CLOSED:
        out["code"] = "WAIT"
        out["text"] = f"{n} of {PROBATION_MIN_CLOSED} closed — no decision yet"
        return out
    if len(dv) < PROBATION_MIN_PER_GROUP or len(ot) < PROBATION_MIN_PER_GROUP:
        out["code"] = "EXTEND"
        out["text"] = (f"{n} closed, but a group has fewer than {PROBATION_MIN_PER_GROUP} "
                       f"(DEEP VALUE {len(dv)}, others {len(ot)}) — extend by another "
                       f"{PROBATION_MIN_CLOSED} closes")
        return out
    d, o = out["dv_avg_r"], out["other_avg_r"]
    if d < 0 and d < o:
        out["code"] = "GATE"
        out["text"] = (f"rule met: DEEP VALUE avg R {d:+.2f} vs others {o:+.2f} — make the "
                       f"DEEP VALUE exclusion a Gold gate under a new GOLD_RULE_VERSION")
    elif d >= o:
        out["code"] = "END"
        out["text"] = (f"rule met: DEEP VALUE avg R {d:+.2f} vs others {o:+.2f} — end the "
                       f"probation")
    else:
        out["code"] = "EXTEND"
        out["text"] = (f"inconclusive: DEEP VALUE avg R {d:+.2f} vs others {o:+.2f} — extend "
                       f"by another {PROBATION_MIN_CLOSED} closes")
    return out
