"""
Portfolio-level Monte Carlo corpus projection.

Unlike the legacy per-asset-independent sampling, this samples the OPTIMIZED
PORTFOLIO's aggregate return/vol derived from the covariance matrix, so cross-
asset correlation is respected (and it is faster):

    mu_p    = w . mu                      (annual fraction)
    sigma_p = sqrt(w' Sigma w)            (annual fraction)

For each of N paths: sample an annual return ~ Normal(mu_p, sigma_p), convert to
a monthly compounding rate ((1+a)^(1/12) - 1, with a clipped to >= -0.99 for
safety), and run the future-value-of-annuity over n months for a fixed monthly
SIP. Outputs percentiles + goal probability, plus the legacy response keys.

Limitation (documented): normal-return sampling understates fat tails; a
Student-t / historical-bootstrap option is a planned enhancement.
"""

from __future__ import annotations

import logging
from typing import Dict, List

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SIMS = 10000
MIN_SIMS = 1000
MAX_SIMS = 50000


def portfolio_moments(weights: Dict[str, float], tickers: List[str], mu: List[float], sigma: List[List[float]]):
    """Return (mu_p, sigma_p) as annual fractions from the optimized weights."""
    w = np.array([weights.get(t, 0.0) for t in tickers], dtype=float)
    s = w.sum()
    if s > 0:
        w = w / s
    mu_arr = np.asarray(mu, dtype=float)
    sigma_arr = np.asarray(sigma, dtype=float)
    mu_p = float(w @ mu_arr)
    var_p = float(w @ sigma_arr @ w)
    sigma_p = float(np.sqrt(max(var_p, 0.0)))
    return mu_p, sigma_p


def project(
    weights: Dict[str, float],
    tickers: List[str],
    mu: List[float],
    sigma: List[List[float]],
    monthly_investment: float,
    months: int,
    target_fund: float,
    simulations: int = DEFAULT_SIMS,
    seed: int | None = None,
) -> dict:
    """
    Run the portfolio-level Monte Carlo projection.

    Returns a dict with both the new `projection` fields and the legacy
    response keys so the API response stays backward compatible.
    """
    sims = int(max(MIN_SIMS, min(MAX_SIMS, simulations)))
    mu_p, sigma_p = portfolio_moments(weights, tickers, mu, sigma)

    rng = np.random.default_rng(seed)
    annual = rng.normal(loc=mu_p, scale=max(sigma_p, 1e-9), size=sims)
    annual = np.clip(annual, -0.99, None)
    monthly_rate = (1.0 + annual) ** (1.0 / 12.0) - 1.0
    monthly_rate = np.where(monthly_rate == 0, 1e-12, monthly_rate)

    # FV of annuity: P * ((1+r)^n - 1) / r
    fv = monthly_investment * (((1.0 + monthly_rate) ** months - 1.0) / monthly_rate)

    total_invested = monthly_investment * months
    p5 = float(np.percentile(fv, 5))
    p50 = float(np.percentile(fv, 50))
    p90 = float(np.percentile(fv, 90))
    mean = float(np.mean(fv))
    goal_prob = float(np.mean(fv >= target_fund))

    var_pct = ((p5 - total_invested) / total_invested * 100.0) if total_invested > 0 else 0.0

    # "Total Future Value" uses the deterministic portfolio mean return for a
    # stable headline figure (consistent with the legacy single-number corpus).
    det_monthly = (1.0 + max(mu_p, -0.99)) ** (1.0 / 12.0) - 1.0
    if det_monthly == 0:
        det_monthly = 1e-12
    total_fv = monthly_investment * (((1.0 + det_monthly) ** months - 1.0) / det_monthly)

    return {
        # New additive projection object.
        "projection": {
            "p5": round(p5, 2),
            "p50": round(p50, 2),
            "p90": round(p90, 2),
            "mean": round(mean, 2),
            "simulations": sims,
            "goalProbability": round(goal_prob, 4),
            "portfolioAnnualReturnPct": round(mu_p * 100, 2),
            "portfolioAnnualVolatilityPct": round(sigma_p * 100, 2),
        },
        # Legacy keys preserved for the current frontend.
        "Total Future Value (INR)": round(total_fv, 2),
        "Average Value from Simulation (INR)": round(mean, 2),
        "Value at Risk (5th percentile) (INR)": round(p5, 2),
        "Value at Risk (5th percentile) (%)": round(var_pct, 2),
        "Optimistic Value (90th percentile) (INR)": round(p90, 2),
        "Goal Achievable": bool(total_fv >= target_fund),
    }
