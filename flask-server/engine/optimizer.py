"""
MPT optimizer: max-Sharpe, min-variance, and risk-parity over the precomputed
expected-return vector and Ledoit-Wolf covariance matrix.

Baseline constraints always applied:
  - long-only            (w_i >= 0)
  - fully invested       (sum w_i = 1)
  - per-asset cap        (w_i <= per_asset_cap, default 0.35) to avoid degenerate
                          single-asset portfolios (the production analogue of the
                          old MIN/MAX portfolio-size diversification intent)

Additional linear asset-class caps (L_c <= sum_{i in c} w_i <= H_c) are passed
through from the constraint mapper.

Objective selection:
  explicit `objective` arg wins; otherwise derived from risk category:
    low    -> min_variance   (mu-robust)
    medium -> max_sharpe
    high   -> max_sharpe      (higher per-asset cap applied upstream)

Infeasibility raises engine.InfeasibleError (API -> 422).

Units: mu is annual fraction, sigma is annualised covariance (fraction^2).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

from engine import InfeasibleError

logger = logging.getLogger(__name__)

VALID_OBJECTIVES = {"max_sharpe", "min_variance", "risk_parity"}
DEFAULT_PER_ASSET_CAP = 0.35
RISK_FREE_RATE_DEFAULT = 0.07  # annual fraction


def objective_for_risk(risk_category: str) -> str:
    rc = (risk_category or "").strip().lower()
    if rc == "low":
        return "min_variance"
    return "max_sharpe"  # medium / high / unknown


def resolve_objective(objective: Optional[str], risk_category: str) -> str:
    if objective:
        obj = objective.strip().lower()
        if obj not in VALID_OBJECTIVES:
            raise ValueError(f"objective must be one of {sorted(VALID_OBJECTIVES)}")
        return obj
    return objective_for_risk(risk_category)


def _risk_parity_weights(sigma: np.ndarray, bounds: tuple, max_iter: int = 500) -> np.ndarray:
    """
    Equal-risk-contribution portfolio via a simple, robust iterative algorithm
    (cyclical coordinate descent on the standard RP fixed point), then clipped to
    bounds and renormalised. Long-only. Avoids a cvxpy nonconvex formulation.
    """
    n = sigma.shape[0]
    w = np.ones(n) / n
    lo, hi = bounds
    for _ in range(max_iter):
        marginal = sigma @ w  # marginal risk contributions direction
        # target: w_i proportional to 1 / marginal_i (risk-balancing update)
        marginal = np.where(np.abs(marginal) < 1e-12, 1e-12, marginal)
        w_new = 1.0 / marginal
        w_new = np.clip(w_new, 0.0, None)
        s = w_new.sum()
        if s <= 0:
            break
        w_new = w_new / s
        if np.max(np.abs(w_new - w)) < 1e-8:
            w = w_new
            break
        w = w_new
    # Apply per-asset cap then renormalise (keeps long-only + fully invested).
    w = np.clip(w, lo, hi)
    if w.sum() <= 0:
        raise InfeasibleError("Risk-parity produced a degenerate (zero) portfolio.")
    return w / w.sum()


def optimize(
    tickers: List[str],
    mu: List[float],
    sigma: List[List[float]],
    objective: str,
    per_asset_cap: float = DEFAULT_PER_ASSET_CAP,
    asset_class_of: Optional[Dict[str, str]] = None,
    asset_class_caps: Optional[Dict[str, dict]] = None,
    risk_free_rate: float = RISK_FREE_RATE_DEFAULT,
) -> Dict[str, float]:
    """
    Solve for portfolio weights. Returns {ticker: weight} (weights >= 0, sum≈1).

    Raises InfeasibleError if the problem can't be solved or has no assets, and
    ValueError on bad objective.
    """
    if objective not in VALID_OBJECTIVES:
        raise ValueError(f"objective must be one of {sorted(VALID_OBJECTIVES)}")
    n = len(tickers)
    if n == 0:
        raise InfeasibleError("No instruments available to optimize after filters.")

    mu_arr = np.asarray(mu, dtype=float)
    sigma_arr = np.asarray(sigma, dtype=float)

    # A single asset: trivial full allocation (cap can't force diversification).
    if n == 1:
        return {tickers[0]: 1.0}

    # Per-asset cap must at least allow a feasible fully-invested long-only
    # portfolio: n * cap >= 1. If too tight, relax to the minimum feasible cap.
    min_feasible_cap = 1.0 / n
    cap = max(per_asset_cap, min_feasible_cap)
    bounds = (0.0, cap)

    if objective == "risk_parity":
        w = _risk_parity_weights(sigma_arr, bounds)
        return {t: float(w[i]) for i, t in enumerate(tickers)}

    # max_sharpe / min_variance via PyPortfolioOpt.
    import pandas as pd
    from pypfopt import EfficientFrontier

    mu_s = pd.Series(mu_arr, index=tickers)
    cov_df = pd.DataFrame(sigma_arr, index=tickers, columns=tickers)

    def _build_ef():
        """Fresh EfficientFrontier (cvxpy problems are single-use) with the
        same baseline bounds + asset-class caps applied."""
        ef = EfficientFrontier(mu_s, cov_df, weight_bounds=bounds)
        if asset_class_caps and asset_class_of:
            for cls, limits in asset_class_caps.items():
                idx = [i for i, t in enumerate(tickers) if asset_class_of.get(t) == cls]
                if not idx:
                    continue
                lo = limits.get("min")
                hi = limits.get("max")
                if hi is not None:
                    ef.add_constraint(lambda w, idx=idx, hi=hi: sum(w[i] for i in idx) <= hi)
                if lo is not None:
                    ef.add_constraint(lambda w, idx=idx, lo=lo: sum(w[i] for i in idx) >= lo)
        return ef

    def _min_variance():
        ef = _build_ef()
        ef.min_volatility()
        return ef.clean_weights()

    cleaned = None
    if objective == "max_sharpe":
        # max_sharpe is undefined if no asset's return exceeds the risk-free
        # rate; use min_variance in that degenerate case.
        if np.nanmax(mu_arr) <= risk_free_rate:
            logger.info("All expected returns <= risk-free; using min_variance.")
            cleaned = _min_variance()
        else:
            try:
                ef = _build_ef()
                ef.max_sharpe(risk_free_rate=risk_free_rate)
                cleaned = ef.clean_weights()
            except Exception as exc:  # noqa: BLE001
                # A max-Sharpe SOLVER failure (DCP / convergence on a particular
                # solver build) is NOT user infeasibility — fall back to the
                # robust min-variance portfolio rather than returning a 422.
                logger.warning("max_sharpe solve failed (%s); falling back to min_variance.", exc)
                try:
                    cleaned = _min_variance()
                except Exception as exc2:  # noqa: BLE001
                    raise InfeasibleError(
                        "The optimizer could not find a feasible portfolio under the given "
                        "constraints. Try relaxing asset-class caps or exclusions."
                    ) from exc2
    else:  # min_variance (and risk_parity handled earlier)
        try:
            cleaned = _min_variance()
        except Exception as exc:  # noqa: BLE001
            raise InfeasibleError(
                "The optimizer could not find a feasible portfolio under the given "
                "constraints. Try relaxing asset-class caps or exclusions."
            ) from exc

    weights = {t: float(cleaned.get(t, 0.0)) for t in tickers}
    total = sum(weights.values())
    if total <= 0:
        raise InfeasibleError("Optimization produced an empty portfolio.")
    # Normalise to exactly 1 (clean_weights can leave tiny residuals).
    return {t: w / total for t, w in weights.items()}
