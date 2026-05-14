"""
analysis/risk_metrics.py — v16.0 Risk-Adjusted Return Metrics
==============================================================

Pure-function module that computes institutional-standard risk-adjusted
return metrics from a set of closed-position P&L outcomes:

  • Sharpe Ratio  — (mean return - risk-free rate) / std-dev of returns
  • Sortino Ratio — (mean return - risk-free rate) / std-dev of NEGATIVE returns
  • Calmar Ratio  — annualized return / max drawdown observed

Plus supporting statistics: trade count, mean / median / std-dev,
win rate, average win / loss, profit factor, expectancy.

DESIGN PRINCIPLES
─────────────────
1. ZERO external dependencies (no scipy/numpy required) — pure Python math.
   This makes the module trivially testable and free of version drift.
2. Pure functions: each takes inputs, returns outputs, no side effects.
   No database, no Excel, no logging.
3. SAFE on empty / sparse inputs: returns None or 0.0 rather than dividing
   by zero, raising exceptions, or returning NaN.
4. CLEAR semantics: returns are P&L percentages as floats (e.g. 12.5 = +12.5%),
   NOT decimals (0.125). This matches the screener's existing convention.

RISK-FREE RATE
──────────────
Default: 6.5% annualized — approximately India's 91-day T-bill rate as of
2026. Per-trade rate is scaled by holding period (days_held / 365.25).
Configurable via DEFAULT_RISK_FREE_RATE_PCT.

CALIBRATION NOTE
────────────────
With <30 closed positions, the standard deviation estimates are noisy
and Sharpe / Sortino confidence intervals are wide. The module computes
the metrics anyway but the caller should interpret them with awareness
of sample-size limitations. After 60-90 days of pipeline runs, you'll
have enough closed positions for these to be reliable.

USAGE
─────
    from analysis.risk_metrics import compute_risk_metrics

    closed_positions = [
        {"pnl_pct": 12.5, "days_held": 45, "max_drawdown_pct": -3.2},
        {"pnl_pct": -7.0, "days_held": 22, "max_drawdown_pct": -7.0},
        # ... etc
    ]
    metrics = compute_risk_metrics(closed_positions)
    # → {"n_trades": 2, "sharpe": 0.32, "sortino": 0.58, ...}
"""
import math
from typing import List, Dict, Optional, Any

# India 91-day T-bill ≈ 6.5% (2026). Used as the risk-free benchmark.
# Configurable: pass risk_free_rate_pct=X to compute_risk_metrics() to override.
DEFAULT_RISK_FREE_RATE_PCT = 6.5

# Trading days per year (used to annualize per-trade returns)
TRADING_DAYS_PER_YEAR = 252


def _safe_mean(xs: List[float]) -> float:
    """Mean of a list. Returns 0.0 on empty input."""
    if not xs:
        return 0.0
    return sum(xs) / len(xs)


def _safe_median(xs: List[float]) -> float:
    """Median of a list. Returns 0.0 on empty."""
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _safe_std(xs: List[float], ddof: int = 1) -> float:
    """Sample standard deviation (ddof=1 by default, unbiased estimator).

    Returns 0.0 if fewer than (1 + ddof) samples — std is undefined.
    """
    n = len(xs)
    if n <= ddof:
        return 0.0
    mu = _safe_mean(xs)
    var = sum((x - mu) ** 2 for x in xs) / (n - ddof)
    return math.sqrt(var)


def compute_risk_metrics(
    closed_positions: List[Dict[str, Any]],
    risk_free_rate_pct: float = DEFAULT_RISK_FREE_RATE_PCT,
) -> Dict[str, Any]:
    """Compute Sharpe, Sortino, Calmar + supporting statistics.

    Parameters
    ----------
    closed_positions : list of dicts, each with at minimum:
        - "pnl_pct"          : realised P&L as a percentage (e.g. 12.5)
        - "days_held"        : integer days from entry to outcome
        - "max_drawdown_pct" : worst running DD as percentage (e.g. -8.5)
        - Optional: "outcome_type" — e.g. "T1_HIT", "SL_HIT", "EXPIRED"

    risk_free_rate_pct : annualized risk-free rate as percentage.

    Returns
    -------
    dict with the following keys:
        n_trades            : int
        mean_return_pct     : float (simple average of P&L%)
        median_return_pct   : float
        std_return_pct      : float (sample std dev, unbiased)
        win_rate_pct        : float (fraction with pnl_pct > 0, × 100)
        avg_win_pct         : float (mean of pnl_pct where pnl_pct > 0)
        avg_loss_pct        : float (mean of pnl_pct where pnl_pct < 0)
        profit_factor       : Optional[float] (sum_wins / abs(sum_losses))
        expectancy_pct      : float (mean P&L per trade)
        max_drawdown_pct    : float (worst single-trade drawdown observed)
        avg_days_held       : float

        # ── Risk-adjusted metrics ──
        sharpe_ratio        : Optional[float] — annualized
        sortino_ratio       : Optional[float] — annualized
        calmar_ratio        : Optional[float]

    Returns None for any ratio that can't be computed (insufficient samples,
    zero variance, zero drawdown, etc.) rather than raising.

    NOTE on annualization: per-trade Sharpe is computed as
       (mean - rf_per_period) / std × sqrt(periods_per_year)
    where periods_per_year ≈ 252 / avg_days_held. This is the standard
    method for converting trade-frequency Sharpe into annualized form.
    """
    n = len(closed_positions)
    if n == 0:
        return {
            "n_trades": 0,
            "mean_return_pct": 0.0,
            "median_return_pct": 0.0,
            "std_return_pct": 0.0,
            "win_rate_pct": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "profit_factor": None,
            "expectancy_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_days_held": 0.0,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "calmar_ratio": None,
            "_caveat": "No closed positions — metrics not computable",
        }

    # Extract series — be defensive about None / missing keys
    def _f(d: dict, k: str, default: float = 0.0) -> float:
        v = d.get(k, default)
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    pnls       = [_f(p, "pnl_pct") for p in closed_positions]
    days_held  = [_f(p, "days_held") for p in closed_positions]
    drawdowns  = [_f(p, "max_drawdown_pct") for p in closed_positions]

    # Basic stats
    mean_ret    = _safe_mean(pnls)
    median_ret  = _safe_median(pnls)
    std_ret     = _safe_std(pnls)
    avg_days    = _safe_mean(days_held)

    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate    = (len(wins) / n) * 100 if n > 0 else 0.0
    avg_win     = _safe_mean(wins)
    avg_loss    = _safe_mean(losses)

    # Profit factor = gross profit / abs(gross loss). None when no losses.
    sum_wins   = sum(wins)
    sum_losses = sum(losses)
    profit_factor: Optional[float]
    if sum_losses < 0:
        profit_factor = sum_wins / abs(sum_losses)
    elif sum_wins > 0:
        # No losses, only wins — infinite profit factor; we report None
        # rather than inf to keep the metric numerically sane.
        profit_factor = None
    else:
        profit_factor = None

    expectancy = mean_ret  # mean P&L per trade IS expectancy by definition

    # Max drawdown is the WORST single-trade DD observed across the set.
    # (Different from portfolio-equity DD, which would require equity-curve walks.)
    # Drawdowns are stored as negative numbers, so "worst" is the MIN.
    max_dd = min(drawdowns) if drawdowns else 0.0

    # ── Risk-adjusted metrics ──
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    calmar: Optional[float] = None

    if n >= 2 and std_ret > 0 and avg_days > 0:
        # Annualization factor: each trade represents avg_days days of risk.
        # Periods-per-year = 252 / avg_days_held (trading days basis).
        # rf per trade-period = risk_free_pct × (avg_days / 365.25).
        # Then annualize the Sharpe by × sqrt(periods_per_year).
        rf_per_period = risk_free_rate_pct * (avg_days / 365.25)
        periods_per_year = TRADING_DAYS_PER_YEAR / avg_days if avg_days > 0 else 0
        if periods_per_year > 0:
            sharpe = (mean_ret - rf_per_period) / std_ret * math.sqrt(periods_per_year)

        # Sortino: same numerator, downside-deviation denominator.
        # Downside deviation uses ONLY negative returns (or returns below rf).
        # Standard convention: deviation of returns below the risk-free rate.
        downside_returns = [r - rf_per_period for r in pnls if r < rf_per_period]
        if len(downside_returns) >= 2:
            # Sample std of downside deviations
            down_std = _safe_std(downside_returns)
            if down_std > 0 and periods_per_year > 0:
                sortino = (mean_ret - rf_per_period) / down_std * math.sqrt(periods_per_year)

    # Calmar = annualized return / abs(max drawdown).
    if max_dd < 0 and avg_days > 0:
        periods_per_year = TRADING_DAYS_PER_YEAR / avg_days
        annualized_return = mean_ret * periods_per_year
        calmar = annualized_return / abs(max_dd)

    out: Dict[str, Any] = {
        "n_trades": n,
        "mean_return_pct": round(mean_ret, 2),
        "median_return_pct": round(median_ret, 2),
        "std_return_pct": round(std_ret, 2),
        "win_rate_pct": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "expectancy_pct": round(expectancy, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_days_held": round(avg_days, 1),
        "sharpe_ratio": round(sharpe, 2) if sharpe is not None else None,
        "sortino_ratio": round(sortino, 2) if sortino is not None else None,
        "calmar_ratio": round(calmar, 2) if calmar is not None else None,
    }

    # Add a sample-size caveat for transparency.
    if n < 30:
        out["_caveat"] = (
            f"Sample size n={n} < 30 — ratios are statistically noisy. "
            f"Confidence intervals widen rapidly below ~30 trades."
        )

    return out


# ─────────────────────────────────────────────────────────────────────
# DD Duration helpers (Item 2 of v16.0)
# ─────────────────────────────────────────────────────────────────────

def summarize_dd_duration(closed_positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize Max DD Duration across closed positions.

    Each position dict should have:
      - dd_duration_days : longest consecutive days underwater
      - dd_recovered     : 1 if recovered above entry before close, 0 otherwise

    Returns dict with mean/median/max duration and recovery rate.
    """
    n = len(closed_positions)
    if n == 0:
        return {
            "n_trades": 0,
            "avg_dd_duration_days": 0.0,
            "max_dd_duration_days": 0,
            "recovery_rate_pct": 0.0,
        }

    def _i(d: dict, k: str, default: int = 0) -> int:
        v = d.get(k, default)
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    durations = [_i(p, "dd_duration_days") for p in closed_positions]
    recovered = [_i(p, "dd_recovered") for p in closed_positions]
    n_recovered = sum(1 for r in recovered if r == 1)
    return {
        "n_trades": n,
        "avg_dd_duration_days": round(_safe_mean(durations), 1),
        "max_dd_duration_days": max(durations) if durations else 0,
        "recovery_rate_pct": round((n_recovered / n) * 100, 1),
    }