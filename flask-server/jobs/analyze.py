"""
Analytics job: compute per-instrument metrics + Ledoit-Wolf covariance from the
price_series store, publish a new versioned snapshot, and atomically flip the
"current" pointer. Retains the last N versions for audit.

This job is TensorFlow-FREE by design (the hot path and analytics must not pull
in TF). Metric formulas intentionally match the legacy engine so the response
contract is preserved:
    expected_annual_return_pct = mean(daily pct_change) * 252 * 100
    volatility_pct             = std(daily pct_change) * sqrt(252) * 100
    sharpe                     = (expected_return - RISK_FREE_RATE) / volatility
    risk_profile               = Low (<10) / Medium (<20) / High  (by volatility)

Covariance is the Ledoit-Wolf shrinkage estimate over ALIGNED daily returns
(inner-join on dates across the universe), serialized inline for local/dev and
addressable via storage_uri in production.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

RISK_FREE_RATE: float = float(os.environ.get("RISK_FREE_RATE", "7.0"))
TRADING_DAYS = 252
# How many historical versions to keep (including the new current one).
RETAIN_VERSIONS: int = int(os.environ.get("MODEL_RETAIN_VERSIONS", "5"))


def _risk_profile(volatility_pct: float) -> str:
    if volatility_pct < 10:
        return "Low"
    if volatility_pct < 20:
        return "Medium"
    return "High"


def _load_price_frame(session, tickers) -> pd.DataFrame:
    """Return a wide DataFrame of close prices indexed by date, columns=tickers."""
    from db.models import PriceSeries

    rows = (
        session.query(PriceSeries.ticker, PriceSeries.date, PriceSeries.close)
        .filter(PriceSeries.ticker.in_(tickers))
        .all()
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ticker", "date", "close"])
    wide = df.pivot_table(index="date", columns="ticker", values="close").sort_index()
    return wide


def analyze(min_observations: int = 30, retain: int | None = None) -> dict:
    """
    Compute metrics + covariance from price_series, publish a new current version.

    Returns a summary dict with the new version id, instrument count, and the
    covariance matrix dimension.
    """
    import db
    from db.models import (
        CovarianceArtifact,
        Instrument,
        InstrumentMetric,
        ModelMetricsVersion,
    )

    if not db.is_enabled():
        raise RuntimeError("Database is not configured (set DATABASE_URL).")

    retain = RETAIN_VERSIONS if retain is None else retain
    session = db.get_session()

    # Universe = active instruments.
    universe = {
        t: ac
        for (t, ac) in session.query(Instrument.ticker, Instrument.asset_class)
        .filter(Instrument.active.is_(True))
        .all()
    }
    tickers = sorted(universe.keys())
    if not tickers:
        raise RuntimeError("No active instruments to analyze; run the seed job first.")

    prices = _load_price_frame(session, tickers)
    if prices.empty:
        raise RuntimeError("No price data found; run the ingestion job first.")

    # Daily simple returns.
    returns = prices.pct_change().dropna(how="all")

    # Per-instrument metrics (only for tickers with enough observations).
    metrics_rows = []
    usable_tickers = []
    for ticker in tickers:
        if ticker not in returns.columns:
            continue
        series = returns[ticker].dropna()
        if len(series) < min_observations:
            logger.info("Skipping %s: only %d observations.", ticker, len(series))
            continue
        exp_ret = float(series.mean() * TRADING_DAYS * 100)
        vol = float(series.std(ddof=1) * np.sqrt(TRADING_DAYS) * 100)
        sharpe = float((exp_ret - RISK_FREE_RATE) / vol) if vol > 0 else 0.0
        metrics_rows.append(
            {
                "ticker": ticker,
                "asset_class": universe.get(ticker),
                "expected_annual_return_pct": round(exp_ret, 4),
                "volatility_pct": round(vol, 4),
                "beta": 1.0,  # market-proxy beta is a Phase 3 refinement
                "sharpe": round(sharpe, 4),
                "risk_profile": _risk_profile(vol),
            }
        )
        usable_tickers.append(ticker)

    if not metrics_rows:
        raise RuntimeError("No instruments had enough price history to analyze.")

    # Ledoit-Wolf covariance over ALIGNED returns (inner-join dates across the
    # usable universe) so the matrix is consistent and well-conditioned.
    aligned = returns[usable_tickers].dropna(how="any")
    cov_tickers = list(usable_tickers)
    matrix_inline = None
    shrinkage = None
    if len(aligned) >= 2 and len(cov_tickers) >= 2:
        from sklearn.covariance import LedoitWolf

        lw = LedoitWolf().fit(aligned.values)
        cov = lw.covariance_
        matrix_inline = cov.tolist()
        shrinkage = f"ledoit_wolf:{round(float(lw.shrinkage_), 6)}"
    else:
        logger.warning(
            "Insufficient aligned data for covariance (rows=%d, cols=%d); storing empty.",
            len(aligned), len(cov_tickers),
        )
        matrix_inline = []

    checksum = hashlib.sha256(
        json.dumps({"order": cov_tickers, "matrix": matrix_inline}, sort_keys=True).encode()
    ).hexdigest()

    data_window_start = prices.index.min()
    data_window_end = prices.index.max()

    # ---- Publish a new version and ATOMICALLY flip the current pointer ----
    # All within one transaction: create the new version + children, set all
    # other versions is_current=False, set the new one True, commit. A failure
    # rolls back and leaves the previous current intact.
    try:
        version = ModelMetricsVersion(
            computed_at=datetime.now(timezone.utc),
            data_window_start=data_window_start,
            data_window_end=data_window_end,
            data_source="price_series",
            is_current=False,
            notes=shrinkage or "no_covariance",
        )
        session.add(version)
        session.flush()  # get version.id

        for m in metrics_rows:
            session.add(InstrumentMetric(version_id=version.id, **m))

        session.add(
            CovarianceArtifact(
                version_id=version.id,
                storage_uri=None,  # inline for local/dev; S3/GCS in prod
                ticker_order=cov_tickers,
                matrix_inline=matrix_inline,
                shrinkage=shrinkage,
                checksum=checksum,
            )
        )

        # Atomic flip: demote all others, promote the new one.
        session.query(ModelMetricsVersion).filter(
            ModelMetricsVersion.id != version.id
        ).update({ModelMetricsVersion.is_current: False}, synchronize_session=False)
        version.is_current = True

        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Analytics publish failed; current pointer unchanged.")
        raise

    # ---- Retention: delete versions beyond the last N (keep newest, incl current) ----
    deleted = _retain_last_n(session, retain)

    logger.info(
        "Published version %s: %d instruments, cov dim=%d, retained<=%d (deleted %d old).",
        version.id, len(metrics_rows), len(cov_tickers), retain, deleted,
    )
    return {
        "version_id": str(version.id),
        "instruments": len(metrics_rows),
        "covariance_dim": len(cov_tickers),
        "shrinkage": shrinkage,
        "deleted_old_versions": deleted,
    }


def _retain_last_n(session, n: int) -> int:
    """Delete versions beyond the newest N (by computed_at). Returns #deleted."""
    from db.models import ModelMetricsVersion

    all_versions = (
        session.query(ModelMetricsVersion)
        .order_by(ModelMetricsVersion.computed_at.desc())
        .all()
    )
    to_delete = all_versions[n:]
    for v in to_delete:
        session.delete(v)  # cascades to metrics/covariance/lstm_views
    if to_delete:
        session.commit()
    return len(to_delete)
