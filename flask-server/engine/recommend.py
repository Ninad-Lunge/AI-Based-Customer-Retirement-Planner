"""
Recommendation orchestrator (Phase 3).

Ties the engine together into the full response contract:
    store inputs + request
      -> build_constraints (filters/caps/tilt/warnings)
      -> resolve objective (constraint override wins over request/risk-category)
      -> apply_black_litterman (optional, off by default)
      -> optimize (MPT weights)
      -> build legacy "Investment Suggestions" rows + raw weights
      -> backtest (walk-forward / full-window)
      -> montecarlo.project (portfolio-level)
      -> explain
      -> assemble response: legacy keys + additive fields

Raises engine.InfeasibleError (API -> 422) when the universe/constraints are
infeasible. Does NOT import TensorFlow.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Dict, Optional

from engine import blacklitterman, constraints, explain, montecarlo, optimizer

logger = logging.getLogger(__name__)

DISCLAIMER_VERSION = "2026-10-01"
DISCLAIMER_TEXT = (
    "This tool is for educational and illustrative purposes only and does not "
    "constitute investment, financial, legal, or tax advice, nor a recommendation "
    "or solicitation to buy or sell any security. Projections are model estimates "
    "based on historical data. Past performance is not indicative of future results. "
    "Consult a qualified, registered financial adviser before making decisions."
)


def _fv_annuity(monthly_payment: float, annual_return_pct: float, months: int, step_up_percent: float = 0.0) -> float:
    r = annual_return_pct / 100.0 / 12.0
    step = step_up_percent / 100.0
    if step == 0.0:
        if r == 0:
            return monthly_payment * months
        return monthly_payment * (((1 + r) ** months - 1) / r)
    # Step-up SIP: escalate the payment each year, accumulate year by year.
    balance = 0.0
    payment = monthly_payment
    months_left = months
    year = 0
    while months_left > 0:
        if year > 0:
            payment *= (1 + step)
        m = min(12, months_left)
        if r == 0:
            balance += payment * m
        else:
            balance = balance * ((1 + r) ** m) + payment * (((1 + r) ** m - 1) / r)
        months_left -= m
        year += 1
    return balance


def recommend(
    inputs: dict,
    *,
    years: int,
    target_fund: float,
    monthly_investment: float,
    risk_category: str,
    objective: Optional[str] = None,
    profile: Optional[dict] = None,
    preferences: Optional[dict] = None,
    options: Optional[dict] = None,
    price_returns=None,
    risk_free_rate: float = 0.07,
    rng_seed: Optional[int] = None,
    step_up_percent: float = 0.0,
) -> dict:
    """Produce the full recommendation response from store inputs + request.

    `rng_seed` makes the Monte Carlo deterministic and is recorded in the
    response (`projection.rngSeed`) + optimizer_config so a recommendation can
    be reproduced exactly from the audit log. If None, a seed is generated and
    recorded.
    """
    options = options or {}
    profile = profile or {}
    months = years * 12

    # Deterministic, recorded RNG seed for exact reproducibility (Phase 4).
    if rng_seed is None:
        rng_seed = int(options.get("rngSeed")) if options.get("rngSeed") is not None else secrets.randbelow(2**31)

    # 1. Constraints: filter universe + caps + tilt + risk-capacity + warnings.
    cres = constraints.build_constraints(inputs, preferences, profile)

    # 2. Objective: a risk-capacity override wins; else explicit; else risk band.
    resolved_objective = cres.objective_override or optimizer.resolve_objective(objective, risk_category)

    # 3. Optional Black-Litterman view (off by default).
    apply_view = bool(options.get("applyLstmView", False))
    mu_bl, lstm_applied = blacklitterman.apply_black_litterman(
        cres.tickers, cres.mu, cres.sigma, inputs.get("lstm_views"), apply_view, risk_free_rate
    )

    # 4. Optimize.
    weights = optimizer.optimize(
        cres.tickers, mu_bl, cres.sigma, resolved_objective,
        per_asset_cap=cres.per_asset_cap, asset_class_of=cres.asset_class_of,
        asset_class_caps=cres.asset_class_caps, risk_free_rate=risk_free_rate,
    )

    # 5. Legacy "Investment Suggestions" rows (per-asset %, return, risk, FV).
    #    Build a quick lookup of annual return (%) and a crude per-asset risk
    #    profile from the diagonal of the covariance (annualised vol).
    import numpy as np

    sigma_arr = np.asarray(cres.sigma, dtype=float)
    suggestions = []
    for i, t in enumerate(cres.tickers):
        w = weights.get(t, 0.0)
        if w <= 1e-6:
            continue
        annual_ret_pct = float(mu_bl[i] * 100.0)
        vol_pct = float(np.sqrt(max(sigma_arr[i, i], 0.0)) * 100.0)
        risk_profile = "Low" if vol_pct < 10 else "Medium" if vol_pct < 20 else "High"
        suggestions.append(
            {
                "Stock Name": t,
                "Asset Class": cres.asset_class_of.get(t) or "Other",
                "Investment Percentage": round(w * 100.0, 2),
                "Annual Return (%)": round(annual_ret_pct, 2),
                "Risk Profile": risk_profile,
                "Future Value (INR)": round(_fv_annuity(monthly_investment * w, annual_ret_pct, months, step_up_percent), 2),
            }
        )
    suggestions.sort(key=lambda s: s["Investment Percentage"], reverse=True)

    # 6. Backtest (walk-forward if enough history, else full-window; None if no data).
    bt = None
    try:
        from engine import backtest as backtest_mod

        bt = backtest_mod.backtest(
            price_returns, weights, resolved_objective,
            per_asset_cap=cres.per_asset_cap, asset_class_of=cres.asset_class_of,
            asset_class_caps=cres.asset_class_caps, risk_free_rate=risk_free_rate,
        )
    except Exception:
        logger.warning("Backtest failed; continuing without it.", exc_info=True)

    # 7. Monte Carlo projection (portfolio-level, via covariance). The recorded
    #    rng_seed makes this deterministic for reproducibility.
    sims = int(options.get("monteCarloSims", montecarlo.DEFAULT_SIMS))
    mc = montecarlo.project(
        weights, cres.tickers, mu_bl, cres.sigma,
        monthly_investment=monthly_investment, months=months,
        target_fund=target_fund, simulations=sims, seed=rng_seed,
        step_up_percent=step_up_percent,
    )
    mc["projection"]["rngSeed"] = rng_seed

    # 8. Applied-constraints echo (transparency).
    applied_constraints = {
        "excludedTickers": (preferences or {}).get("excluded_tickers") or [],
        "excludedSectors": (preferences or {}).get("excluded_sectors") or [],
        "esgOnly": bool((preferences or {}).get("esg_only")),
        "shariahOnly": bool((preferences or {}).get("shariah_only")),
        "asset_class_caps": cres.asset_class_caps,
        "perAssetCap": round(cres.per_asset_cap, 4),
    }

    # optimizer_config captures everything needed to reproduce the result, plus
    # a stable hash for the audit trail.
    optimizer_config = {
        "objective": resolved_objective,
        "perAssetCap": round(cres.per_asset_cap, 6),
        "assetClassCaps": cres.asset_class_caps,
        "riskFreeRate": risk_free_rate,
        "simulations": sims,
        "rngSeed": rng_seed,
        "lstmViewApplied": lstm_applied,
        "stepUpPercent": step_up_percent,
    }
    optimizer_config["configHash"] = hashlib.sha256(
        json.dumps(optimizer_config, sort_keys=True).encode()
    ).hexdigest()[:16]

    # 9. Explanation.
    expl = explain.build_explanation(
        resolved_objective, profile.get("experience_level"), weights, bt,
        applied_constraints, lstm_applied, cres.warnings,
    )

    # ---- Assemble response: legacy keys first, then additive fields. ----
    avg_annual_return_pct = round(float(sum(mu_bl[i] * weights.get(t, 0.0) for i, t in enumerate(cres.tickers)) * 100), 2)

    response: Dict = {
        # Legacy keys (frontend contract).
        "Investment Suggestions": suggestions,
        "Average Annual Return (%)": avg_annual_return_pct,
        **{k: v for k, v in mc.items() if k != "projection"},  # legacy MC keys
        # Additive v1 fields.
        "apiVersion": "v1",
        "objective": resolved_objective,
        "weights": [{"ticker": t, "weight": round(weights.get(t, 0.0), 6)} for t in cres.tickers if weights.get(t, 0.0) > 1e-6],
        "backtest": bt,
        "projection": mc["projection"],
        "explanation": expl,
        "appliedConstraints": applied_constraints,
        "optimizerConfig": optimizer_config,
        "lstmViewApplied": lstm_applied,
        "metricsVersion": inputs.get("version_id"),
        "disclaimer": {"version": DISCLAIMER_VERSION, "text": DISCLAIMER_TEXT},
    }
    return response


def reproduce(audit_row: dict, inputs: dict, price_returns=None) -> dict:
    """
    Re-run a recommendation from a stored audit row to verify reproducibility.

    `audit_row` provides the recorded inputs + rng_seed + optimizer_config;
    `inputs` must be the SAME model/metrics version referenced by the audit row
    (caller is responsible for loading the correct version). Returns the fresh
    recommendation; a caller compares its weights + projection to the original.
    """
    snap = audit_row.get("input_snapshot") or {}
    cfg = audit_row.get("optimizer_config") or {}
    seed = audit_row.get("rng_seed")
    if seed is None:
        seed = cfg.get("rngSeed")

    return recommend(
        inputs,
        years=int(snap["years"]),
        target_fund=float(snap["desiredFund"]),
        monthly_investment=float(snap["monthlyInvestment"]),
        risk_category=snap.get("riskCategory", "medium"),
        objective=snap.get("objective"),
        profile=snap.get("profile") or {},
        preferences=snap.get("preferences") or {},
        options=snap.get("options") or {},
        price_returns=price_returns,
        rng_seed=seed,
        step_up_percent=float(snap.get("stepUpPercent", cfg.get("stepUpPercent", 0.0)) or 0.0),
    )
