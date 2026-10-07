"""
Ingestion job: pull OHLCV history for active instruments into price_series.

Data source is PLUGGABLE via the ``PriceSource`` protocol so a licensed
provider (Phase 4) can replace yfinance without touching the job. yfinance is
the dev/default source and is imported lazily inside the source class so the
rest of the job (and the analytics job) stay import-light.

Per-ticker failures are isolated: one bad symbol never aborts the run. Writes
are idempotent per (ticker, date) via upsert-style merge.
"""

from __future__ import annotations

import logging
from datetime import timezone
from typing import List, Protocol

logger = logging.getLogger(__name__)


class PriceSource(Protocol):
    """Interface a market-data source must implement."""

    def fetch_ohlcv(self, ticker: str, period: str) -> list[dict]:
        """Return a list of {date, open, high, low, close, volume} dicts."""
        ...


class YFinanceSource:
    """Dev/default source backed by yfinance (personal-use; dev only)."""

    def fetch_ohlcv(self, ticker: str, period: str = "5y") -> list[dict]:
        import yfinance as yf  # lazy import; keeps the module light

        hist = yf.Ticker(ticker).history(period=period)
        rows = []
        for idx, row in hist.iterrows():
            # idx is a pandas Timestamp (tz-aware for .NS); normalise to UTC.
            dt = idx.to_pydatetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            close = row.get("Close")
            if close is None or (isinstance(close, float) and close != close):  # NaN guard
                continue
            rows.append(
                {
                    "date": dt.astimezone(timezone.utc),
                    "open": _f(row.get("Open")),
                    "high": _f(row.get("High")),
                    "low": _f(row.get("Low")),
                    "close": float(close),
                    "volume": _f(row.get("Volume")),
                }
            )
        return rows


def _f(v):
    try:
        if v is None:
            return None
        v = float(v)
        return v if v == v else None  # drop NaN
    except (TypeError, ValueError):
        return None


def ingest(source: PriceSource | None = None, period: str = "5y", tickers: List[str] | None = None) -> dict:
    """
    Fetch OHLCV for active instruments and upsert into price_series.

    Args:
        source: price data source (defaults to YFinanceSource).
        period: history window to request from the source.
        tickers: optional explicit subset; defaults to all active instruments.

    Returns a summary: {"ok": n, "failed": m, "rows": total_rows, "failures": [...]}.
    """
    import db
    from db.models import Instrument, PriceSeries

    if not db.is_enabled():
        raise RuntimeError("Database is not configured (set DATABASE_URL).")

    source = source or YFinanceSource()
    session = db.get_session()

    if tickers is None:
        tickers = [
            t for (t,) in session.query(Instrument.ticker).filter(Instrument.active.is_(True)).all()
        ]

    ok = failed = rows_written = 0
    failures: list[dict] = []

    for ticker in tickers:
        try:
            rows = source.fetch_ohlcv(ticker, period)
            if not rows:
                raise ValueError("no rows returned")
            for r in rows:
                session.merge(
                    PriceSeries(
                        ticker=ticker,
                        date=r["date"],
                        open=r["open"],
                        high=r["high"],
                        low=r["low"],
                        close=r["close"],
                        volume=r["volume"],
                    )
                )
            session.commit()
            ok += 1
            rows_written += len(rows)
            logger.info("Ingested %d rows for %s.", len(rows), ticker)
        except Exception as exc:  # noqa: BLE001 - isolate per-ticker failures
            session.rollback()
            failed += 1
            failures.append({"ticker": ticker, "error": str(exc)})
            logger.warning("Ingestion failed for %s: %s", ticker, exc)

    logger.info("Ingestion complete: ok=%d, failed=%d, rows=%d.", ok, failed, rows_written)
    return {"ok": ok, "failed": failed, "rows": rows_written, "failures": failures}
