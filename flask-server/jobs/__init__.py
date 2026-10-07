"""
Offline jobs package (Phase 2).

Contains the scheduled, out-of-request-path work:
- seed:    load the investment universe (tickers.json) into the instruments table
- ingest:  pull OHLCV for active instruments into the price_series store
- analyze: compute per-instrument metrics + Ledoit-Wolf covariance, publish a
           new versioned snapshot, and atomically flip the "current" pointer

These jobs run as separate processes (CLI: python -m jobs <cmd>) and connect to
the same database as the API. The analytics job does NOT import TensorFlow — the
LSTM retraining job (Phase 3) is isolated in its own image.
"""
