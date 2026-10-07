"""
Phase 3 tests: MPT optimizer, constraint mapping, Black-Litterman view,
backtest, portfolio Monte Carlo, and the expanded /v1 API (optional-degradation,
legacy-key preservation, 422 on infeasible).

Engine-unit tests run on in-memory numpy data (no DB). Integration tests use a
SQLite store populated via the Phase 2 seed/ingest/analyze jobs with a
deterministic fake price source (no network, no TensorFlow in the engine path).
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

# --------------------------------------------------------------------------- #
# Optimizer (pure, no DB)
# --------------------------------------------------------------------------- #

class TestOptimizer:
    def _inputs(self):
        tickers = ["A", "B", "C", "D"]
        mu = [0.15, 0.12, 0.09, 0.06]
        rng = np.random.default_rng(0)
        base = rng.normal(0, 0.01, (300, 4))
        sigma = (np.cov(base, rowvar=False) + np.diag([0.04, 0.05, 0.03, 0.02])).tolist()
        ac = {"A": "equity", "B": "equity", "C": "bond", "D": "bond"}
        return tickers, mu, sigma, ac

    def test_all_objectives_sum_to_one_and_respect_cap(self):
        from engine import optimizer as opt

        tickers, mu, sigma, ac = self._inputs()
        for obj in ("max_sharpe", "min_variance", "risk_parity"):
            w = opt.optimize(tickers, mu, sigma, obj, per_asset_cap=0.4)
            assert abs(sum(w.values()) - 1.0) < 1e-6
            assert max(w.values()) <= 0.4 + 1e-6
            assert all(v >= -1e-9 for v in w.values())  # long-only

    def test_objective_resolution(self):
        from engine import optimizer as opt

        assert opt.resolve_objective(None, "low") == "min_variance"
        assert opt.resolve_objective(None, "medium") == "max_sharpe"
        assert opt.resolve_objective(None, "high") == "max_sharpe"
        assert opt.resolve_objective("risk_parity", "low") == "risk_parity"

    def test_invalid_objective_raises(self):
        from engine import optimizer as opt

        with pytest.raises(ValueError):
            opt.resolve_objective("nonsense", "low")

    def test_asset_class_cap_binds(self):
        from engine import optimizer as opt

        tickers, mu, sigma, ac = self._inputs()
        w = opt.optimize(
            tickers, mu, sigma, "max_sharpe", per_asset_cap=0.6,
            asset_class_of=ac, asset_class_caps={"equity": {"max": 0.5}},
        )
        equity = sum(v for k, v in w.items() if ac[k] == "equity")
        assert equity <= 0.5 + 1e-6

    def test_infeasible_cap_raises(self):
        from engine import InfeasibleError
        from engine import optimizer as opt

        tickers, mu, sigma, ac = self._inputs()
        with pytest.raises(InfeasibleError):
            opt.optimize(
                tickers, mu, sigma, "max_sharpe", per_asset_cap=0.3,
                asset_class_of=ac, asset_class_caps={"equity": {"max": 0.1}},
            )


# --------------------------------------------------------------------------- #
# Black-Litterman (pure, no DB)
# --------------------------------------------------------------------------- #

class TestBlackLitterman:
    def _data(self):
        t = ["A", "B", "C"]
        mu = [0.10, 0.12, 0.08]
        sigma = (np.diag([0.04, 0.05, 0.03]) + 0.005).tolist()
        views = {"A": {"view_return": 0.25, "confidence": 0.8}}
        return t, mu, sigma, views

    def test_off_by_default_is_identity(self):
        from engine import blacklitterman as bl

        t, mu, sigma, views = self._data()
        out, applied = bl.apply_black_litterman(t, mu, sigma, views, apply_view=False)
        assert applied is False
        assert np.allclose(out, mu)

    def test_no_views_is_identity(self):
        from engine import blacklitterman as bl

        t, mu, sigma, _ = self._data()
        out, applied = bl.apply_black_litterman(t, mu, sigma, {}, apply_view=True)
        assert applied is False
        assert np.allclose(out, mu)

    def test_on_with_view_changes_mu(self):
        from engine import blacklitterman as bl

        t, mu, sigma, views = self._data()
        out, applied = bl.apply_black_litterman(t, mu, sigma, views, apply_view=True)
        assert applied is True
        assert not np.allclose(out, mu)
        assert out[0] > mu[0]  # bullish view on A raises its posterior

    def test_kill_switch_forces_off(self, monkeypatch):
        from engine import blacklitterman as bl

        monkeypatch.setenv("LSTM_VIEW_ENABLED", "false")
        t, mu, sigma, views = self._data()
        out, applied = bl.apply_black_litterman(t, mu, sigma, views, apply_view=True)
        assert applied is False
        assert np.allclose(out, mu)


# --------------------------------------------------------------------------- #
# Backtest (pure, no DB)
# --------------------------------------------------------------------------- #

class TestBacktest:
    def test_full_window_metrics_sane(self):
        import pandas as pd

        from engine import backtest as bt

        rng = np.random.default_rng(1)
        idx = pd.date_range("2022-01-01", periods=400, freq="D")
        R = pd.DataFrame({c: rng.normal(0.0005, 0.012, 400) for c in ["A", "B", "C"]}, index=idx)
        res = bt.backtest(R, {"A": 0.4, "B": 0.35, "C": 0.25}, "max_sharpe", per_asset_cap=0.5,
                          asset_class_of={"A": "e", "B": "e", "C": "b"})
        assert res["method"] == "full_window"
        assert res["maxDrawdown"] <= 0
        assert res["volatility"] >= 0
        assert "rolling" in res and isinstance(res["rolling"], list)

    def test_walk_forward_engages_with_long_history(self):
        import pandas as pd

        from engine import backtest as bt

        rng = np.random.default_rng(2)
        idx = pd.date_range("2019-01-01", periods=1000, freq="D")
        R = pd.DataFrame({c: rng.normal(0.0005, 0.012, 1000) for c in ["A", "B", "C"]}, index=idx)
        res = bt.backtest(R, {"A": 0.4, "B": 0.35, "C": 0.25}, "max_sharpe", per_asset_cap=0.5,
                          asset_class_of={"A": "e", "B": "e", "C": "b"})
        assert res["method"] == "walk_forward"
        assert res["observations"] > 0

    def test_none_returns_none(self):
        from engine import backtest as bt

        assert bt.backtest(None, {"A": 1.0}, "max_sharpe") is None


# --------------------------------------------------------------------------- #
# Monte Carlo (pure, no DB)
# --------------------------------------------------------------------------- #

class TestMonteCarlo:
    def _args(self):
        t = ["A", "B", "C"]
        mu = [0.12, 0.10, 0.08]
        sigma = (np.diag([0.04, 0.05, 0.03]) + 0.005).tolist()
        w = {"A": 0.4, "B": 0.35, "C": 0.25}
        return w, t, mu, sigma

    def test_percentile_ordering_and_goal_prob(self):
        from engine import montecarlo as mc

        w, t, mu, sigma = self._args()
        r = mc.project(w, t, mu, sigma, monthly_investment=15000, months=360,
                       target_fund=10_000_000, seed=42)
        pj = r["projection"]
        assert pj["p5"] <= pj["p50"] <= pj["p90"]
        assert 0.0 <= pj["goalProbability"] <= 1.0

    def test_legacy_keys_present(self):
        from engine import montecarlo as mc

        w, t, mu, sigma = self._args()
        r = mc.project(w, t, mu, sigma, 15000, 360, 10_000_000, seed=1)
        for k in (
            "Total Future Value (INR)", "Value at Risk (5th percentile) (INR)",
            "Optimistic Value (90th percentile) (INR)", "Average Value from Simulation (INR)",
            "Goal Achievable",
        ):
            assert k in r

    def test_simulations_bounded(self):
        from engine import montecarlo as mc

        w, t, mu, sigma = self._args()
        assert mc.project(w, t, mu, sigma, 15000, 360, 1e7, simulations=10)["projection"]["simulations"] == 1000
        assert mc.project(w, t, mu, sigma, 15000, 360, 1e7, simulations=10**9)["projection"]["simulations"] == 50000


# --------------------------------------------------------------------------- #
# Integration: expanded /v1 API against a populated store
# --------------------------------------------------------------------------- #

class _FakeSource:
    def __init__(self, days=400):
        self.days = days

    def fetch_ohlcv(self, ticker, period="5y"):
        rng = np.random.default_rng(abs(hash(ticker)) % (2**32))
        base = datetime(2022, 1, 1, tzinfo=timezone.utc)
        p = 100.0
        rows = []
        drift = {"RELIANCE.NS": 0.0009, "TCS.NS": 0.0007, "INFY.NS": 0.0008, "ITC.NS": 0.0005}.get(ticker, 0.0006)
        for d in range(self.days):
            p *= 1 + rng.normal(drift, 0.014)
            rows.append({"date": base + timedelta(days=d), "open": p, "high": p, "low": p, "close": p, "volume": 1000})
        return rows


@pytest.fixture
def mpt_client(monkeypatch, tmp_path):
    """SQLite store seeded+ingested+analyzed, with a Flask client wired to it."""
    import db
    import server
    from settings import get_settings

    db_url = f"sqlite+pysqlite:///{tmp_path / 'mpt.db'}"
    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", db_url)
    db.reset_engine_for_tests()
    db.init_engine(db_url)
    import db.models  # noqa: F401
    db.Base.metadata.create_all(db.get_engine())

    from jobs.analyze import analyze
    from jobs.ingest import ingest
    from jobs.seed import seed_instruments

    # Seed with sectors/esg so filter tests work.
    seed_instruments({"Indian Equity": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "ITC.NS"]})
    from db.models import Instrument

    s = db.get_session()
    for t, sec, esg in [("RELIANCE.NS", "energy", False), ("TCS.NS", "technology", True),
                        ("INFY.NS", "technology", True), ("ITC.NS", "fmcg", False)]:
        inst = s.get(Instrument, t)
        inst.sector = sec
        inst.esg_flag = esg
    s.commit()

    ingest(source=_FakeSource(days=400), tickers=["RELIANCE.NS", "TCS.NS", "INFY.NS", "ITC.NS"])
    analyze(min_observations=30)

    server.app.config.update(TESTING=True)
    yield server.app.test_client()

    db.reset_engine_for_tests()
    get_settings.cache_clear()


_BASE = {
    "currentAge": 30, "retirementAge": 60, "desiredFund": 10_000_000,
    "monthlyInvestment": 15000, "riskCategory": "medium",
}


class TestMptApi:
    def test_base_request_uses_mpt_and_keeps_legacy_keys(self, mpt_client):
        r = mpt_client.post("/v1/investment-strategy", json=_BASE)
        assert r.status_code == 200
        b = r.get_json()
        # Additive MPT markers.
        for k in ("apiVersion", "objective", "weights", "backtest", "projection", "explanation", "disclaimer"):
            assert k in b
        # Legacy keys preserved.
        for k in ("Investment Suggestions", "Total Future Value (INR)", "Goal Achievable", "Average Annual Return (%)"):
            assert k in b
        assert abs(sum(w["weight"] for w in b["weights"]) - 1.0) < 1e-4

    def test_explicit_objective_honoured(self, mpt_client):
        r = mpt_client.post("/v1/investment-strategy", json={**_BASE, "objective": "min_variance"})
        assert r.status_code == 200
        assert r.get_json()["objective"] == "min_variance"

    def test_excluded_ticker_camelcase_honoured(self, mpt_client):
        r = mpt_client.post("/v1/investment-strategy", json={**_BASE, "preferences": {"excludedTickers": ["ITC.NS"]}})
        assert r.status_code == 200
        tickers = [w["ticker"] for w in r.get_json()["weights"]]
        assert "ITC.NS" not in tickers

    def test_over_restrictive_filter_returns_422(self, mpt_client):
        # Shariah-only with none flagged -> empty universe -> 422.
        r = mpt_client.post("/v1/investment-strategy", json={**_BASE, "preferences": {"shariahOnly": True}})
        assert r.status_code == 422
        assert "error" in r.get_json()

    def test_lstm_view_flag_off_by_default(self, mpt_client):
        r = mpt_client.post("/v1/investment-strategy", json=_BASE)
        assert r.get_json()["lstmViewApplied"] is False

    def test_invalid_objective_returns_422(self, mpt_client):
        r = mpt_client.post("/v1/investment-strategy", json={**_BASE, "objective": "nonsense"})
        assert r.status_code == 422

    def test_base_fields_still_validated(self, mpt_client):
        # Missing required base field -> 400 (validation runs before MPT path).
        r = mpt_client.post("/v1/investment-strategy", json={"currentAge": 30})
        assert r.status_code == 400


class TestStatelessDegradation:
    def test_v1_falls_back_to_legacy_without_store(self, flask_client):
        # flask_client fixture has NO persistence; must still serve the base flow
        # via the legacy path (no MPT markers).
        r = flask_client.post("/v1/investment-strategy", json=_BASE)
        assert r.status_code == 200
        b = r.get_json()
        assert "Investment Suggestions" in b
        assert "apiVersion" not in b
