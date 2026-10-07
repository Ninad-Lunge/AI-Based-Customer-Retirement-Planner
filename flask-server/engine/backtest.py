"""
Backtesting: weight-replay over historical daily returns, with standard metrics.

Two protocols:
  - full_window: apply the FINAL weights to the whole history (what-if the
    recommended portfolio had been held).
  - walk_forward: rolling re-optimization (train window -> hold window), stitch
    the out-of-sample segments to reduce look-ahead bias. Falls back to the
    full-window result when history is too short for even one walk-forward step.

Metrics (annualised where applicable; r_f is an annual fraction):
  CAGR, max_drawdown, volatility, Sharpe, Sortino, plus a rolling cumulative
  return series (resampled monthly for the response payload).

Assumptions (stated in the response/docs): periodic rebalancing to target
weights, NO transaction costs/taxes/slippage. Returns are daily simple returns.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TRADING_DAYS = 252
TRAIN_DAYS = 756  # ~3 years
HOLD_DAYS = 63  # ~3 months


def _portfolio_returns(returns: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    """Daily portfolio returns = sum_i w_i * r_i, aligned on available columns."""
    cols = [t for t in weights if t in returns.columns]
    if not cols:
        return pd.Series(dtype=float)
    w = np.array([weights[t] for t in cols], dtype=float)
    s = w.sum()
    if s > 0:
        w = w / s
    sub = returns[cols].fillna(0.0)
    return sub.values @ w  # ndarray; wrap below


def _metrics_from_daily(daily: np.ndarray, risk_free_rate: float) -> dict:
    """Compute CAGR, max drawdown, vol, Sharpe, Sortino from a daily return array."""
    daily = np.asarray(daily, dtype=float)
    daily = daily[np.isfinite(daily)]
    n = len(daily)
    if n < 2:
        return {
            "cagr": 0.0, "maxDrawdown": 0.0, "volatility": 0.0,
            "sharpe": 0.0, "sortino": 0.0, "observations": int(n),
        }

    cumulative = np.cumprod(1.0 + daily)
    total_return = float(cumulative[-1])
    years = n / TRADING_DAYS
    cagr = (total_return ** (1.0 / years) - 1.0) if years > 0 and total_return > 0 else 0.0

    # Max drawdown from the running peak of the cumulative curve.
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = cumulative / running_max - 1.0
    max_drawdown = float(drawdowns.min())

    vol = float(np.std(daily, ddof=1) * np.sqrt(TRADING_DAYS))

    excess_cagr = cagr - risk_free_rate
    sharpe = (excess_cagr / vol) if vol > 0 else 0.0

    downside = daily[daily < 0]
    downside_dev = float(np.std(downside, ddof=1) * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else 0.0
    sortino = (excess_cagr / downside_dev) if downside_dev > 0 else 0.0

    return {
        "cagr": round(cagr * 100, 2),            # percent
        "maxDrawdown": round(max_drawdown * 100, 2),
        "volatility": round(vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "observations": int(n),
    }


def _rolling_series(index, daily: np.ndarray) -> List[dict]:
    """Monthly-resampled cumulative-return series for the response payload."""
    if len(daily) < 2:
        return []
    s = pd.Series(np.cumprod(1.0 + np.asarray(daily, dtype=float)) - 1.0, index=index[: len(daily)])
    monthly = s.resample("ME").last() if hasattr(s.index, "freq") or True else s
    out = []
    for dt, val in monthly.items():
        if pd.notna(val):
            out.append({"date": pd.Timestamp(dt).strftime("%Y-%m"), "cumulativeReturn": round(float(val), 4)})
    return out


def full_window_backtest(returns: pd.DataFrame, weights: Dict[str, float], risk_free_rate: float = 0.07) -> dict:
    daily = _portfolio_returns(returns, weights)
    metrics = _metrics_from_daily(daily, risk_free_rate)
    window = ""
    if len(returns.index) >= 1:
        window = f"{pd.Timestamp(returns.index.min()).strftime('%Y-%m')}..{pd.Timestamp(returns.index.max()).strftime('%Y-%m')}"
    return {
        **metrics,
        "method": "full_window",
        "window": window,
        "rolling": _rolling_series(returns.index, daily),
        "assumptions": "Periodic rebalancing to target weights; no transaction costs or taxes.",
    }


def walk_forward_backtest(
    returns: pd.DataFrame,
    objective: str,
    per_asset_cap: float,
    asset_class_of: Optional[Dict[str, str]],
    asset_class_caps: Optional[Dict[str, dict]],
    risk_free_rate: float = 0.07,
    train_days: int = TRAIN_DAYS,
    hold_days: int = HOLD_DAYS,
) -> Optional[dict]:
    """
    Rolling re-optimization. Re-estimate mu/Sigma on each training window, solve,
    hold for the next `hold_days`, stitch out-of-sample daily returns. Returns
    None if history is too short for even one train+hold step.
    """
    from engine import InfeasibleError
    from engine.optimizer import optimize

    n = len(returns)
    if n < train_days + hold_days:
        return None

    tickers = list(returns.columns)
    oos_segments: List[np.ndarray] = []
    oos_index = []

    start = 0
    while start + train_days + hold_days <= n:
        train = returns.iloc[start : start + train_days]
        hold = returns.iloc[start + train_days : start + train_days + hold_days]

        mu_tr = (train.mean() * TRADING_DAYS).reindex(tickers).fillna(0.0).tolist()
        sigma_tr = (train.cov() * TRADING_DAYS).reindex(index=tickers, columns=tickers).fillna(0.0).values.tolist()
        try:
            w = optimize(
                tickers, mu_tr, sigma_tr, objective,
                per_asset_cap=per_asset_cap, asset_class_of=asset_class_of,
                asset_class_caps=asset_class_caps, risk_free_rate=risk_free_rate,
            )
        except InfeasibleError:
            # Skip this window if it can't be solved; keep the backtest robust.
            start += hold_days
            continue
        seg = _portfolio_returns(hold, w)
        oos_segments.append(np.asarray(seg, dtype=float))
        oos_index.extend(list(hold.index))
        start += hold_days

    if not oos_segments:
        return None

    daily = np.concatenate(oos_segments)
    metrics = _metrics_from_daily(daily, risk_free_rate)
    idx = pd.DatetimeIndex(oos_index)
    return {
        **metrics,
        "method": "walk_forward",
        "window": f"{idx.min().strftime('%Y-%m')}..{idx.max().strftime('%Y-%m')}",
        "train_days": train_days,
        "hold_days": hold_days,
        "rolling": _rolling_series(idx, daily),
        "assumptions": "Rolling re-optimization; no transaction costs or taxes.",
    }


def backtest(
    returns: Optional[pd.DataFrame],
    weights: Dict[str, float],
    objective: str,
    per_asset_cap: float = 0.35,
    asset_class_of: Optional[Dict[str, str]] = None,
    asset_class_caps: Optional[Dict[str, dict]] = None,
    risk_free_rate: float = 0.07,
) -> Optional[dict]:
    """
    Preferred entry point: walk-forward if there is enough history, else
    full-window. Returns None if there is no usable return data at all.
    """
    if returns is None or returns.empty:
        return None
    wf = walk_forward_backtest(
        returns, objective, per_asset_cap, asset_class_of, asset_class_caps, risk_free_rate
    )
    if wf is not None:
        return wf
    return full_window_backtest(returns, weights, risk_free_rate)
