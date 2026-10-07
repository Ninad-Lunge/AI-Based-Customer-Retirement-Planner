"""
Black-Litterman view integration (optional, OFF by default).

The LSTM is NOT the decision-maker. When enabled, its per-instrument forward
return "views" are blended into the prior expected returns via Black-Litterman,
weighted by each view's stated confidence and by the covariance structure. When
disabled (default) or when there are no views, the posterior equals the prior
exactly (mu_BL == mu).

Toggle precedence:
    1. global kill-switch env LSTM_VIEW_ENABLED=false  -> always off
    2. request options.applyLstmView (default False)   -> per-request opt-in

Units: mu and view returns are annual fractions; sigma is annualised covariance.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _kill_switch_on() -> bool:
    """Global kill-switch: LSTM_VIEW_ENABLED=false disables BL entirely."""
    return os.environ.get("LSTM_VIEW_ENABLED", "true").strip().lower() not in ("0", "false", "no")


def apply_black_litterman(
    tickers: List[str],
    mu: List[float],
    sigma: List[List[float]],
    lstm_views: Optional[Dict[str, dict]],
    apply_view: bool,
    risk_free_rate: float = 0.07,
) -> Tuple[List[float], bool]:
    """
    Return (mu_posterior, applied).

    `applied` is True only if the view was actually incorporated. When False,
    mu_posterior is exactly the input mu (identity), so callers can rely on
    "off => unchanged".
    """
    # Off by default / kill-switched / no views => identity.
    if not apply_view or not _kill_switch_on() or not lstm_views:
        return list(mu), False

    # Build absolute views (Q) and confidences only for tickers we have views on.
    view_dict: Dict[str, float] = {}
    confidences: List[float] = []
    for t in tickers:
        v = lstm_views.get(t)
        if not v or v.get("view_return") is None:
            continue
        view_dict[t] = float(v["view_return"])
        conf = v.get("confidence")
        confidences.append(float(conf) if conf is not None else 0.5)

    if not view_dict:
        return list(mu), False

    try:
        import pandas as pd
        from pypfopt import BlackLittermanModel

        idx = list(tickers)
        cov_df = pd.DataFrame(np.asarray(sigma, dtype=float), index=idx, columns=idx)
        pi = pd.Series(np.asarray(mu, dtype=float), index=idx)  # use historical mu as prior

        bl = BlackLittermanModel(
            cov_df,
            pi=pi,
            absolute_views=view_dict,
            omega="idzorek",
            view_confidences=np.array(confidences, dtype=float),
            risk_free_rate=risk_free_rate,
        )
        posterior = bl.bl_returns()  # pandas Series indexed by ticker
        mu_post = [float(posterior.get(t, mu[i])) for i, t in enumerate(tickers)]
        logger.info("Applied Black-Litterman view to %d instrument(s).", len(view_dict))
        return mu_post, True
    except Exception:
        # Any BL failure must NOT break the recommendation; fall back to prior.
        logger.warning("Black-Litterman computation failed; using prior mu.", exc_info=True)
        return list(mu), False
