"""
Store repository: read the CURRENT model/metrics version from the DB and
return it in the legacy DataFrame shape the engine/response contract expects.

This is the fast, deterministic request-path read introduced in Phase 2:
no market-data network call and no model training happen here. If the store is
empty or persistence is disabled, callers fall back to the live source.

Legacy DataFrame columns (unchanged contract):
    Stock Name, Asset Class, Annual Return (%), Volatility (%), Beta,
    Sharpe Ratio, Risk Profile
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

_LEGACY_COLUMNS = [
    "Stock Name",
    "Asset Class",
    "Annual Return (%)",
    "Volatility (%)",
    "Beta",
    "Sharpe Ratio",
    "Risk Profile",
]


def current_metrics_rows() -> List[dict]:
    """
    Return the current version's per-instrument metrics as a list of dicts in
    the legacy shape, or [] if no current version / persistence disabled.
    """
    try:
        import db

        if not db.is_enabled():
            return []
        from db.models import InstrumentMetric, ModelMetricsVersion

        session = db.get_session()
        current = (
            session.query(ModelMetricsVersion)
            .filter(ModelMetricsVersion.is_current.is_(True))
            .one_or_none()
        )
        if current is None:
            return []
        rows = (
            session.query(InstrumentMetric)
            .filter(InstrumentMetric.version_id == current.id)
            .all()
        )
        out = []
        for m in rows:
            out.append(
                {
                    "Stock Name": m.ticker,
                    "Asset Class": m.asset_class or "Other",
                    "Annual Return (%)": round(float(m.expected_annual_return_pct or 0.0), 2),
                    "Volatility (%)": round(float(m.volatility_pct or 0.0), 2),
                    "Beta": round(float(m.beta if m.beta is not None else 1.0), 2),
                    "Sharpe Ratio": round(float(m.sharpe or 0.0), 2),
                    "Risk Profile": m.risk_profile or "Medium",
                }
            )
        return out
    except Exception:
        logger.warning("Failed to read current metrics from store.", exc_info=True)
        return []


def current_metrics_dataframe() -> Optional["object"]:
    """
    Return the current metrics as a pandas DataFrame (legacy columns), or None
    if the store is empty / unavailable. Imported pandas lazily.
    """
    rows = current_metrics_rows()
    if not rows:
        return None
    import pandas as pd

    return pd.DataFrame(rows, columns=_LEGACY_COLUMNS)


def current_optimizer_inputs() -> Optional[dict]:
    """
    Return the current version's MPT optimizer inputs, or None if unavailable.

    Shape:
        {
          "version_id": str,
          "tickers": [str, ...],              # covariance ticker order
          "mu": [float, ...],                 # expected ANNUAL return (fraction, e.g. 0.12)
          "sigma": [[float, ...], ...],       # annualised covariance matrix (fraction^2)
          "asset_class": {ticker: str},
          "lstm_views": {ticker: {"view_return": float, "confidence": float}},
        }

    Notes:
    - Metrics are stored as PERCENT (expected_annual_return_pct, volatility_pct).
      The covariance matrix_inline is computed from DAILY simple returns in the
      analytics job. We annualise it here (×252) and express mu as a fraction so
      the optimizer and Monte Carlo work in consistent annual-fraction units.
    """
    try:
        import db

        if not db.is_enabled():
            return None
        from db.models import (
            CovarianceArtifact,
            InstrumentMetric,
            LstmView,
            ModelMetricsVersion,
        )

        session = db.get_session()
        current = (
            session.query(ModelMetricsVersion)
            .filter(ModelMetricsVersion.is_current.is_(True))
            .one_or_none()
        )
        if current is None:
            return None

        cov = session.get(CovarianceArtifact, current.id)
        if cov is None or not cov.ticker_order or not cov.matrix_inline:
            return None

        tickers = list(cov.ticker_order)

        metrics = {
            m.ticker: m
            for m in session.query(InstrumentMetric)
            .filter(InstrumentMetric.version_id == current.id)
            .all()
        }
        # mu as annual fraction (percent / 100), aligned to covariance order.
        mu = [float((metrics[t].expected_annual_return_pct or 0.0) / 100.0) if t in metrics else 0.0 for t in tickers]
        asset_class = {t: (metrics[t].asset_class if t in metrics else None) for t in tickers}

        # Daily covariance -> annualised (×252), aligned to ticker_order.
        import numpy as np

        daily_cov = np.array(cov.matrix_inline, dtype=float)
        sigma = (daily_cov * 252.0).tolist()

        views = {
            v.ticker: {
                "view_return": float((v.view_return_pct or 0.0) / 100.0),
                "confidence": float(v.confidence) if v.confidence is not None else None,
            }
            for v in session.query(LstmView).filter(LstmView.version_id == current.id).all()
        }

        return {
            "version_id": str(current.id),
            "tickers": tickers,
            "mu": mu,
            "sigma": sigma,
            "asset_class": asset_class,
            "lstm_views": views,
        }
    except Exception:
        logger.warning("Failed to read optimizer inputs from store.", exc_info=True)
        return None


def price_returns_frame(tickers: list) -> Optional["object"]:
    """
    Return a wide DataFrame of DAILY simple returns (index=date, columns=tickers)
    built from price_series, for the given tickers. Used by the backtester.
    Returns None if persistence is disabled or there is no price data.
    """
    try:
        import db

        if not db.is_enabled():
            return None
        import pandas as pd

        from db.models import PriceSeries

        session = db.get_session()
        rows = (
            session.query(PriceSeries.ticker, PriceSeries.date, PriceSeries.close)
            .filter(PriceSeries.ticker.in_(list(tickers)))
            .all()
        )
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
        wide = df.pivot_table(index="date", columns="ticker", values="close").sort_index()
        returns = wide.pct_change().dropna(how="all")
        return returns
    except Exception:
        logger.warning("Failed to read price returns from store.", exc_info=True)
        return None
