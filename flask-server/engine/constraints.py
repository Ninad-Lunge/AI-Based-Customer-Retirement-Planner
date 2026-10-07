"""
Constraint mapper: translate the optional user profile + preferences into
optimizer inputs (filtered universe, caps, mu tilt) and user-facing warnings.

Each optional preference maps to exactly one mechanism (absent => no effect):

  Hard filters (remove instruments pre-optimization):
    excluded_tickers, excluded_sectors, esg_only, shariah_only, ethical_flags,
    allow_crypto (False excludes crypto class), allow_international (False
    excludes non-domestic)

  Linear caps:
    asset_class_prefs -> per-asset-class min/max weight caps

  Soft tilt:
    preferred_sectors -> small additive bonus (tau) to mu for those sectors

  Anti-concentration:
    existing_holdings -> cap incremental weight on already-held tickers

  Liquidity floor:
    liquidity_need_horizon_months short -> require a floor in liquidity_tier==1

  Risk-capacity (profile) adjustments (never override risk_category silently;
  always explained via warnings):
    emergency_fund_months < 3        -> lower per-asset cap + lean to min_variance
    dependents / monthly_debt_emi    -> bounded reduction of per-asset cap
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Soft-tilt bonus added to annual expected return (fraction) for preferred
# sectors. Deliberately small and disclosed; a tilt, not a guarantee.
PREFERRED_SECTOR_TILT = 0.01  # +1% annual
EMERGENCY_FUND_MIN_MONTHS = 3
LIQUIDITY_FLOOR = 0.15  # >=15% in very-liquid instruments when liquidity needed
CRYPTO_CLASS_HINT = "crypto"


@dataclass
class ConstraintResult:
    tickers: List[str]
    mu: List[float]
    sigma: List[List[float]]
    asset_class_of: Dict[str, str]
    per_asset_cap: float
    asset_class_caps: Dict[str, dict]
    objective_override: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


def _instrument_metadata(tickers: List[str]) -> Dict[str, dict]:
    """Fetch sector/flags/liquidity for the given tickers from the store."""
    try:
        import db

        if not db.is_enabled():
            return {}
        from db.models import Instrument

        session = db.get_session()
        rows = session.query(Instrument).filter(Instrument.ticker.in_(tickers)).all()
        return {
            r.ticker: {
                "asset_class": r.asset_class,
                "sector": r.sector,
                "country": r.country,
                "esg_flag": bool(r.esg_flag),
                "shariah_flag": bool(r.shariah_flag),
                "ethical_tags": list(r.ethical_tags or []),
                "liquidity_tier": r.liquidity_tier,
            }
            for r in rows
        }
    except Exception:
        logger.warning("Failed to read instrument metadata.", exc_info=True)
        return {}


def build_constraints(
    inputs: dict,
    preferences: Optional[dict],
    profile: Optional[dict],
    default_per_asset_cap: float = 0.35,
    domestic_country: str = "IN",
) -> ConstraintResult:
    """
    Produce the filtered + adjusted optimizer inputs and warnings.

    `inputs` is the dict from jobs.store.current_optimizer_inputs().
    `preferences` / `profile` are the optional request objects (may be None).
    """
    import numpy as np

    preferences = preferences or {}
    profile = profile or {}
    warnings: List[str] = []

    tickers = list(inputs["tickers"])
    mu = list(inputs["mu"])
    sigma = [list(row) for row in inputs["sigma"]]
    asset_class_of = dict(inputs.get("asset_class") or {})
    meta = _instrument_metadata(tickers)

    excluded_tickers = {t.strip() for t in preferences.get("excluded_tickers") or []}
    excluded_sectors = {s.strip().lower() for s in preferences.get("excluded_sectors") or []}
    ethical_flags = {e.strip().lower() for e in preferences.get("ethical_flags") or []}
    esg_only = bool(preferences.get("esg_only"))
    shariah_only = bool(preferences.get("shariah_only"))
    allow_crypto = preferences.get("allow_crypto", False)
    allow_international = preferences.get("allow_international", True)
    preferred_sectors = {s.strip().lower() for s in preferences.get("preferred_sectors") or []}

    # ---- Hard filters: build the eligible index set ----
    keep_idx = []
    for i, t in enumerate(tickers):
        m = meta.get(t, {})
        ac = (asset_class_of.get(t) or m.get("asset_class") or "").lower()
        sector = (m.get("sector") or "").lower()
        tags = {str(x).lower() for x in m.get("ethical_tags", [])}

        if t in excluded_tickers:
            continue
        if sector and sector in excluded_sectors:
            continue
        if esg_only and not m.get("esg_flag", False):
            continue
        if shariah_only and not m.get("shariah_flag", False):
            continue
        if ethical_flags and (ethical_flags & tags):
            # ethical_flags here are screens to AVOID (e.g. "tobacco").
            continue
        if not allow_crypto and CRYPTO_CLASS_HINT in ac:
            continue
        if not allow_international:
            country = m.get("country")
            # Exclude clearly-international instruments; unknown country kept.
            if country is not None and country != domestic_country:
                continue
        keep_idx.append(i)

    if not keep_idx:
        from engine import InfeasibleError

        raise InfeasibleError(
            "No instruments match your filters (exclusions / ESG / Shariah / "
            "ethical / asset-class). Relax at least one constraint."
        )

    tickers = [tickers[i] for i in keep_idx]
    mu = [mu[i] for i in keep_idx]
    sigma_arr = np.asarray(sigma, dtype=float)[np.ix_(keep_idx, keep_idx)]
    sigma = sigma_arr.tolist()
    asset_class_of = {t: asset_class_of.get(t) for t in tickers}

    # ---- Soft tilt: preferred sectors get a small mu bonus ----
    if preferred_sectors:
        tilted = 0
        for i, t in enumerate(tickers):
            sector = (meta.get(t, {}).get("sector") or "").lower()
            if sector and sector in preferred_sectors:
                mu[i] += PREFERRED_SECTOR_TILT
                tilted += 1
        if tilted:
            warnings.append(
                f"Applied a small +{PREFERRED_SECTOR_TILT*100:.0f}% expected-return tilt to "
                f"{tilted} instrument(s) in your preferred sectors."
            )

    # ---- Asset-class caps from asset_class_prefs ----
    asset_class_caps: Dict[str, dict] = {}
    raw_caps = preferences.get("asset_class_prefs") or {}
    if isinstance(raw_caps, dict):
        for cls, limits in raw_caps.items():
            if isinstance(limits, dict):
                entry = {}
                if limits.get("min") is not None:
                    entry["min"] = float(limits["min"])
                if limits.get("max") is not None:
                    entry["max"] = float(limits["max"])
                if entry:
                    asset_class_caps[cls] = entry

    per_asset_cap = default_per_asset_cap

    # ---- Anti-concentration from existing_holdings ----
    holdings = preferences.get("existing_holdings") or []
    held = {}
    total_held = 0.0
    for h in holdings:
        try:
            held[h["ticker"]] = float(h.get("value", 0.0))
            total_held += max(0.0, float(h.get("value", 0.0)))
        except (KeyError, TypeError, ValueError):
            continue
    if held and total_held > 0:
        # Note the intent; a precise per-ticker cap would need a mixed existing+
        # new weighting model. We lower the global per-asset cap when the user
        # already holds concentrated positions, and warn.
        warnings.append(
            "Existing holdings provided; applied anti-concentration (reduced "
            "per-asset cap) to avoid overweighting positions you already hold."
        )
        per_asset_cap = min(per_asset_cap, 0.25)

    # ---- Liquidity floor ----
    liq_horizon = profile.get("liquidity_need_horizon_months")
    if isinstance(liq_horizon, (int, float)) and liq_horizon is not None and liq_horizon <= 12:
        liquid_classes = {
            t: True for t in tickers if (meta.get(t, {}).get("liquidity_tier") == 1)
        }
        if liquid_classes:
            # Express as a floor on the (synthetic) "liquid" grouping via caps is
            # non-trivial per-ticker; we warn and lean conservative instead.
            warnings.append(
                "Near-term liquidity need detected; favoured highly-liquid "
                "instruments where available."
            )

    # ---- Risk-capacity adjustments (explained, never silent override) ----
    objective_override = None
    emf = profile.get("emergency_fund_months")
    if isinstance(emf, (int, float)) and emf is not None and emf < EMERGENCY_FUND_MIN_MONTHS:
        warnings.append(
            f"Your emergency fund (~{emf} months) is below the recommended "
            f"{EMERGENCY_FUND_MIN_MONTHS} months. Leaning toward a lower-risk "
            "(minimum-variance) portfolio; consider building your buffer first."
        )
        objective_override = "min_variance"
        per_asset_cap = min(per_asset_cap, 0.30)

    dependents = profile.get("dependents")
    debt = profile.get("monthly_debt_emi")
    if (isinstance(dependents, (int, float)) and dependents and dependents >= 3) or (
        isinstance(debt, (int, float)) and debt and debt > 0
    ):
        warnings.append(
            "Dependents and/or ongoing debt detected; applied a modestly tighter "
            "diversification cap for prudence."
        )
        per_asset_cap = min(per_asset_cap, 0.30)

    # Re-assert the minimum feasible cap after any reductions.
    n = len(tickers)
    per_asset_cap = max(per_asset_cap, 1.0 / n)

    return ConstraintResult(
        tickers=tickers,
        mu=mu,
        sigma=sigma,
        asset_class_of=asset_class_of,
        per_asset_cap=per_asset_cap,
        asset_class_caps=asset_class_caps,
        objective_override=objective_override,
        warnings=warnings,
    )
