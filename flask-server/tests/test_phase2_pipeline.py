"""
Phase 2 tests: universe seeding, ingestion, the analytics job (versioning,
atomic current-pointer flip, retention, covariance shape/PSD), and the
store-backed request-path read.

Runs against a throwaway SQLite database with a deterministic FAKE price source
(no network, no TensorFlow). The same paths were verified against real Postgres
during development.
"""

import zlib
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest


@pytest.fixture
def store_db(monkeypatch, tmp_path):
    """SQLite-backed DB with Phase 2 tables created; yields the db module."""
    import db

    db_url = f"sqlite+pysqlite:///{tmp_path / 'store.db'}"
    db.reset_engine_for_tests()
    db.init_engine(db_url)
    import db.models  # noqa: F401  (register models)
    db.Base.metadata.create_all(db.get_engine())
    yield db
    db.reset_engine_for_tests()


class _FakeSource:
    """Deterministic random-walk OHLCV source (no network)."""

    def __init__(self, days=80, seed=0):
        self.days = days
        self.seed = seed

    def fetch_ohlcv(self, ticker, period="5y"):
        rng = np.random.default_rng((zlib.crc32(ticker.encode()) % (2**32)) ^ self.seed)
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        price = 100.0
        rows = []
        for d in range(self.days):
            price *= 1 + rng.normal(0.0007, 0.015)
            rows.append(
                {
                    "date": base + timedelta(days=d),
                    "open": price, "high": price * 1.01, "low": price * 0.99,
                    "close": price, "volume": 1000.0,
                }
            )
        return rows


def _seed_three(store_db):
    from jobs.seed import seed_instruments

    seed_instruments(
        {
            "Indian Equity": ["RELIANCE.NS", "TCS.NS", "INFY.NS"],
        }
    )


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #

class TestSeed:
    def test_seed_is_idempotent(self, store_db):
        from jobs.seed import seed_instruments

        groups = {"Indian Equity": ["RELIANCE.NS", "TCS.NS"], "Gold": ["GOLDBEES.NS"]}
        r1 = seed_instruments(groups)
        assert r1["inserted"] == 3 and r1["total"] == 3
        r2 = seed_instruments(groups)
        assert r2["inserted"] == 0 and r2["total"] == 3

    def test_seed_infers_country_currency(self, store_db):
        from db.models import Instrument
        from jobs.seed import seed_instruments

        seed_instruments({"Indian Equity": ["RELIANCE.NS"], "International ETF": ["QQQ"]})
        s = store_db.get_session()
        ns = s.get(Instrument, "RELIANCE.NS")
        us = s.get(Instrument, "QQQ")
        assert ns.country == "IN" and ns.currency == "INR"
        assert us.country is None


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #

class TestIngest:
    def test_ingest_writes_rows_and_isolates_failures(self, store_db):
        _seed_three(store_db)
        from db.models import PriceSeries
        from jobs.ingest import ingest

        class PartlyBad(_FakeSource):
            def fetch_ohlcv(self, ticker, period="5y"):
                if ticker == "TCS.NS":
                    raise RuntimeError("provider down")
                return super().fetch_ohlcv(ticker, period)

        result = ingest(source=PartlyBad(days=40), tickers=["RELIANCE.NS", "TCS.NS", "INFY.NS"])
        assert result["ok"] == 2
        assert result["failed"] == 1
        assert result["failures"][0]["ticker"] == "TCS.NS"
        s = store_db.get_session()
        assert s.query(PriceSeries).filter(PriceSeries.ticker == "RELIANCE.NS").count() == 40

    def test_ingest_is_idempotent(self, store_db):
        _seed_three(store_db)
        from db.models import PriceSeries
        from jobs.ingest import ingest

        src = _FakeSource(days=40)
        ingest(source=src, tickers=["RELIANCE.NS"])
        ingest(source=src, tickers=["RELIANCE.NS"])
        s = store_db.get_session()
        assert s.query(PriceSeries).filter(PriceSeries.ticker == "RELIANCE.NS").count() == 40


# --------------------------------------------------------------------------- #
# Analytics job
# --------------------------------------------------------------------------- #

class TestAnalyze:
    def _prepare(self, store_db, days=80):
        _seed_three(store_db)
        from jobs.ingest import ingest

        ingest(source=_FakeSource(days=days), tickers=["RELIANCE.NS", "TCS.NS", "INFY.NS"])

    def test_publishes_version_with_metrics_and_covariance(self, store_db):
        self._prepare(store_db)
        from db.models import CovarianceArtifact, InstrumentMetric, ModelMetricsVersion
        from jobs.analyze import analyze

        result = analyze(min_observations=30)
        assert result["instruments"] == 3
        assert result["covariance_dim"] == 3

        s = store_db.get_session()
        current = s.query(ModelMetricsVersion).filter(ModelMetricsVersion.is_current.is_(True)).all()
        assert len(current) == 1
        metrics = s.query(InstrumentMetric).filter(InstrumentMetric.version_id == current[0].id).all()
        assert len(metrics) == 3
        for m in metrics:
            assert m.volatility_pct is not None and m.volatility_pct > 0
            assert m.risk_profile in {"Low", "Medium", "High"}

        cov = s.get(CovarianceArtifact, current[0].id)
        M = np.array(cov.matrix_inline)
        assert M.shape == (3, 3)
        assert np.allclose(M, M.T), "covariance must be symmetric"
        assert np.linalg.eigvalsh(M).min() >= -1e-10, "covariance must be PSD"
        assert cov.shrinkage and cov.shrinkage.startswith("ledoit_wolf")
        assert cov.ticker_order == sorted(["RELIANCE.NS", "TCS.NS", "INFY.NS"])

    def test_atomic_flip_keeps_exactly_one_current(self, store_db):
        self._prepare(store_db)
        from db.models import ModelMetricsVersion
        from jobs.analyze import analyze

        analyze(min_observations=30, retain=5)
        analyze(min_observations=30, retain=5)
        analyze(min_observations=30, retain=5)
        s = store_db.get_session()
        assert s.query(ModelMetricsVersion).filter(ModelMetricsVersion.is_current.is_(True)).count() == 1
        assert s.query(ModelMetricsVersion).count() == 3

    def test_retention_trims_old_versions(self, store_db):
        self._prepare(store_db)
        from db.models import ModelMetricsVersion
        from jobs.analyze import analyze

        for _ in range(4):
            analyze(min_observations=30, retain=2)
        s = store_db.get_session()
        assert s.query(ModelMetricsVersion).count() == 2
        assert s.query(ModelMetricsVersion).filter(ModelMetricsVersion.is_current.is_(True)).count() == 1

    def test_analyze_requires_price_data(self, store_db):
        _seed_three(store_db)  # instruments but no prices
        from jobs.analyze import analyze

        with pytest.raises(RuntimeError):
            analyze(min_observations=30)


# --------------------------------------------------------------------------- #
# Store-backed read path
# --------------------------------------------------------------------------- #

class TestStoreRead:
    def _prepare_current_version(self, store_db, days=80):
        _seed_three(store_db)
        from jobs.analyze import analyze
        from jobs.ingest import ingest

        ingest(source=_FakeSource(days=days), tickers=["RELIANCE.NS", "TCS.NS", "INFY.NS"])
        analyze(min_observations=30)

    def test_current_metrics_dataframe_legacy_shape(self, store_db):
        self._prepare_current_version(store_db)
        from jobs.store import current_metrics_dataframe

        df = current_metrics_dataframe()
        assert df is not None
        assert list(df.columns) == [
            "Stock Name", "Asset Class", "Annual Return (%)", "Volatility (%)",
            "Beta", "Sharpe Ratio", "Risk Profile",
        ]
        assert len(df) == 3

    def test_fetch_stock_data_uses_store_without_network(self, store_db, monkeypatch):
        self._prepare_current_version(store_db)
        import investment_strategy as eng

        # If the store path is taken, yfinance must NOT be called. Make any
        # yfinance access explode so a fallback would fail the test.
        class Boom:
            def __getattr__(self, _):
                raise AssertionError("yfinance should not be called when store is populated")

        monkeypatch.setattr(eng, "yf", Boom())
        df = eng.fetch_stock_data(["RELIANCE.NS", "TCS.NS", "INFY.NS"])
        assert df is not None and len(df) == 3
        assert "Volatility (%)" in df.columns

    def test_recommendation_uses_store_data(self, store_db):
        self._prepare_current_version(store_db)
        import investment_strategy as eng

        np.random.seed(1)
        df = eng.fetch_stock_data(["RELIANCE.NS", "TCS.NS", "INFY.NS"])
        # Pick the risk band that matches the fake data's ~20-25% volatility.
        result = eng.suggest_investment(df, 30, 10_000_000, 15_000, "medium")
        assert "error" not in result
        assert len(result["Investment Suggestions"]) >= 1

    def test_store_empty_returns_none(self, store_db):
        # Tables exist but no current version -> store read returns None.
        from jobs.store import current_metrics_dataframe

        assert current_metrics_dataframe() is None
