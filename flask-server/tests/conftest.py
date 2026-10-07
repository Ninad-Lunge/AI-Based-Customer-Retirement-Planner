"""
Shared pytest fixtures for the backend test suite.

Design notes
------------
- Tests never hit the network and never train a model. The only expensive
  thing is the one-time module import of ``investment_strategy`` (which eagerly
  imports keras/tensorflow, ~3s). We pay that once per session; no test calls
  ``fetch_stock_data`` for real.
- The finance core (``suggest_investment`` and its private helpers) operates on
  a plain pandas DataFrame of precomputed metrics, so we feed it synthetic data.
- API contract tests mock ``fetch_stock_data`` so the Flask handler is exercised
  end-to-end without yfinance or TensorFlow.
"""

import os
import sys

import pandas as pd
import pytest

# Make the backend package importable regardless of the pytest invocation CWD.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.dirname(_BACKEND_DIR)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)


# --------------------------------------------------------------------------- #
# Synthetic metrics data
# --------------------------------------------------------------------------- #
# These mirror the exact column shape produced by fetch_stock_data():
#   Stock Name, Asset Class, Annual Return (%), Volatility (%), Beta,
#   Sharpe Ratio, Risk Profile
#
# The values are chosen to populate every risk band (low/medium/high) so the
# risk filter and allocation logic can be exercised deterministically.

def _row(name, asset_class, ret, vol, beta, sharpe, profile):
    return {
        "Stock Name": name,
        "Asset Class": asset_class,
        "Annual Return (%)": ret,
        "Volatility (%)": vol,
        "Beta": beta,
        "Sharpe Ratio": sharpe,
        "Risk Profile": profile,
    }


@pytest.fixture
def synthetic_metrics_df():
    """A diversified metrics table spanning all three volatility bands."""
    rows = [
        # Low-volatility (<= 15%) — bonds / liquid funds
        _row("BOND_A", "Bonds / Fixed Income", 8.0, 5.0, 0.2, 0.20, "Low"),
        _row("BOND_B", "Bonds / Fixed Income", 9.5, 12.0, 0.3, 0.21, "Medium"),
        _row("LIQUID_A", "Indian ETF / Mutual Fund", 7.5, 3.0, 0.1, 0.17, "Low"),
        # Medium-volatility (12% < vol <= 30%) — broad equity / ETFs
        _row("EQ_A", "Indian Equity", 14.0, 18.0, 1.0, 0.39, "Medium"),
        _row("EQ_B", "Indian Equity", 16.0, 22.0, 1.1, 0.41, "High"),
        _row("ETF_A", "International ETF", 12.0, 20.0, 0.9, 0.25, "Medium"),
        # High-volatility (> 25%) — growth / thematic
        _row("GROWTH_A", "Indian Equity (Growth)", 25.0, 35.0, 1.5, 0.51, "High"),
        _row("GROWTH_B", "Indian Equity (Growth)", 30.0, 40.0, 1.7, 0.57, "High"),
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def valid_payload():
    """A valid POST /investment-strategy body (the 5 required base fields)."""
    return {
        "currentAge": 30,
        "retirementAge": 60,
        "desiredFund": 10_000_000,
        "monthlyInvestment": 15_000,
        "riskCategory": "medium",
    }


@pytest.fixture
def flask_client(monkeypatch, synthetic_metrics_df):
    """
    A Flask test client with ``fetch_stock_data`` patched to return synthetic
    metrics, so the endpoint runs without yfinance or TensorFlow.

    Explicitly ensures STATELESS mode (no DB engine) so these tests are robust
    regardless of ordering relative to the db_app fixture.
    """
    import server

    try:
        import db

        db.reset_engine_for_tests()
    except Exception:
        pass

    monkeypatch.setattr(server, "fetch_stock_data", lambda tickers: synthetic_metrics_df)
    server.app.config.update(TESTING=True)
    return server.app.test_client()


# --------------------------------------------------------------------------- #
# Phase 1: database-backed fixtures (SQLite, fast + portable)
# --------------------------------------------------------------------------- #

@pytest.fixture
def db_app(monkeypatch, tmp_path, synthetic_metrics_df):
    """
    A Flask test client with persistence ENABLED against a throwaway SQLite
    database. Creates all tables via metadata (equivalent to migrating), and
    tears the engine down afterwards so tests are isolated.
    """
    import db
    import server
    from settings import get_settings

    # Point settings + engine at a per-test SQLite file.
    db_url = f"sqlite+pysqlite:///{tmp_path / 'test.db'}"
    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("JWT_SECRET", "test-secret")

    db.reset_engine_for_tests()
    db.init_engine(db_url)
    db.Base.metadata.create_all(db.get_engine())

    monkeypatch.setattr(server, "fetch_stock_data", lambda tickers: synthetic_metrics_df)
    server.app.config.update(TESTING=True)

    yield server.app.test_client()

    db.reset_engine_for_tests()
    get_settings.cache_clear()


@pytest.fixture
def auth_headers(db_app):
    """Register a user and return (client, headers) with a valid access token."""
    resp = db_app.post(
        "/v1/auth/register",
        json={"email": "tester@example.com", "password": "password123"},
    )
    assert resp.status_code == 201, resp.get_json()
    token = resp.get_json()["access_token"]
    return db_app, {"Authorization": f"Bearer {token}"}
