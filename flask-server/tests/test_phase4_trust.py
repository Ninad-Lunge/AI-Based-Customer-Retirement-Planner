"""
Phase 4 tests: audit trail + reproducibility, rate limiting, and
right-to-be-forgotten (account deletion with audit de-identification).

Runs against a throwaway SQLite store populated via the Phase 2 jobs with a
deterministic fake price source. No network, no TensorFlow in the engine path.
"""

import zlib
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest


class _FakeSource:
    def __init__(self, days=400):
        self.days = days

    def fetch_ohlcv(self, ticker, period="5y"):
        rng = np.random.default_rng(zlib.crc32(ticker.encode()) % (2**32))
        base = datetime(2022, 1, 1, tzinfo=timezone.utc)
        p = 100.0
        rows = []
        drift = {"RELIANCE.NS": 0.0009, "TCS.NS": 0.0007, "INFY.NS": 0.0008, "ITC.NS": 0.0005}.get(ticker, 0.0006)
        for _ in range(self.days):
            p *= 1 + rng.normal(drift, 0.014)
            rows.append({"date": base + timedelta(days=len(rows)), "open": p, "high": p, "low": p, "close": p, "volume": 1000})
        return rows


@pytest.fixture
def trust_client(monkeypatch, tmp_path):
    """SQLite store (seeded+ingested+analyzed) + Flask client with persistence."""
    import db
    import server
    from settings import get_settings

    db_url = f"sqlite+pysqlite:///{tmp_path / 'trust.db'}"
    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    db.reset_engine_for_tests()
    db.init_engine(db_url)
    import db.models  # noqa: F401
    db.Base.metadata.create_all(db.get_engine())

    from jobs.analyze import analyze
    from jobs.ingest import ingest
    from jobs.seed import seed_instruments

    seed_instruments({"Indian Equity": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "ITC.NS"]})
    ingest(source=_FakeSource(days=400), tickers=["RELIANCE.NS", "TCS.NS", "INFY.NS", "ITC.NS"])
    analyze(min_observations=30)

    server.app.config.update(TESTING=True)
    yield server.app.test_client()

    import ratelimit

    ratelimit.reset()
    db.reset_engine_for_tests()
    get_settings.cache_clear()


_BASE = {
    "currentAge": 30, "retirementAge": 60, "desiredFund": 10_000_000,
    "monthlyInvestment": 15000, "riskCategory": "medium",
}


# --------------------------------------------------------------------------- #
# Audit trail + reproducibility
# --------------------------------------------------------------------------- #

class TestAuditAndReproducibility:
    def test_audit_row_written_with_data_source_and_no_pii(self, trust_client):
        import db
        from db.models import AuditLog

        r = trust_client.post("/v1/investment-strategy", json=_BASE, headers={"X-Request-ID": "p4-audit-1"})
        assert r.status_code == 200
        rows = db.get_session().query(AuditLog).filter(AuditLog.request_id == "p4-audit-1").all()
        assert len(rows) == 1
        row = rows[0]
        assert row.data_source == "price_series"
        assert row.rng_seed is not None
        assert row.disclaimer_version == "2026-10-01"
        # PII-minimised: snapshot has financial inputs, never contact PII.
        assert set(row.input_snapshot).issuperset({"years", "desiredFund", "riskCategory"})
        assert not any(k in row.input_snapshot for k in ("email", "name", "display_name"))

    def test_reproduce_from_audit_row_matches_exactly(self, trust_client):
        import db
        from db.models import AuditLog
        from engine import recommend
        from jobs import store

        orig = trust_client.post("/v1/investment-strategy", json=_BASE, headers={"X-Request-ID": "p4-repro"}).get_json()
        row = db.get_session().query(AuditLog).filter(AuditLog.request_id == "p4-repro").one()

        inputs = store.current_optimizer_inputs()
        price_returns = store.price_returns_frame(inputs["tickers"])
        audit = {"input_snapshot": row.input_snapshot, "optimizer_config": row.optimizer_config, "rng_seed": row.rng_seed}
        repro = recommend.reproduce(audit, inputs, price_returns=price_returns)

        assert repro["weights"] == orig["weights"]
        assert repro["projection"] == orig["projection"]

    def test_same_seed_is_deterministic(self, trust_client):
        from engine import recommend
        from jobs import store

        inputs = store.current_optimizer_inputs()
        pr = store.price_returns_frame(inputs["tickers"])
        kw = dict(years=30, target_fund=1e7, monthly_investment=15000, risk_category="medium", price_returns=pr)
        a = recommend.recommend(inputs, rng_seed=777, **kw)
        b = recommend.recommend(inputs, rng_seed=777, **kw)
        assert a["projection"] == b["projection"]
        assert a["weights"] == b["weights"]

    def test_authenticated_recommendation_attributed_to_user(self, trust_client):
        import db
        from db.models import AuditLog

        reg = trust_client.post("/v1/auth/register", json={"email": "au@example.com", "password": "password123"})
        uid = reg.get_json()["user"]["id"]
        h = {"Authorization": f"Bearer {reg.get_json()['access_token']}", "X-Request-ID": "p4-authed"}
        trust_client.post("/v1/investment-strategy", json=_BASE, headers=h)
        row = db.get_session().query(AuditLog).filter(AuditLog.request_id == "p4-authed").one()
        assert str(row.user_id) == uid


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #

class TestRateLimiting:
    def test_recommendation_rate_limit_429(self, trust_client, monkeypatch):
        import ratelimit
        import server
        from settings import get_settings

        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
        get_settings.cache_clear()
        server.app.config.update(RATE_LIMIT_IN_TESTS=True)
        ratelimit.reset()

        codes = [
            trust_client.post("/v1/investment-strategy", json=_BASE,
                              environ_overrides={"REMOTE_ADDR": "7.7.7.7"}).status_code
            for _ in range(5)
        ]
        assert codes.count(200) == 3
        assert codes.count(429) == 2

        last = trust_client.post("/v1/investment-strategy", json=_BASE, environ_overrides={"REMOTE_ADDR": "7.7.7.7"})
        assert last.status_code == 429
        assert "Retry-After" in last.headers

        server.app.config.update(RATE_LIMIT_IN_TESTS=False)
        get_settings.cache_clear()

    def test_disabled_under_testing_by_default(self, trust_client):
        # No RATE_LIMIT_IN_TESTS -> limiter off; many requests all succeed.
        codes = [trust_client.post("/v1/investment-strategy", json=_BASE).status_code for _ in range(6)]
        assert 429 not in codes


# --------------------------------------------------------------------------- #
# Right-to-be-forgotten
# --------------------------------------------------------------------------- #

class TestRightToBeForgotten:
    def test_delete_account_purges_and_deidentifies(self, trust_client):
        import uuid

        import db
        import db.models as M

        reg = trust_client.post("/v1/auth/register", json={"email": "forget@example.com", "password": "password123"})
        uid = reg.get_json()["user"]["id"]
        h = {"Authorization": f"Bearer {reg.get_json()['access_token']}"}

        trust_client.put("/v1/profile", json={"annual_income": 800000}, headers=h)
        trust_client.put("/v1/preferences", json={"esg_only": True}, headers=h)
        trust_client.post("/v1/plans", json={"name": "P", "input_payload": {"x": 1}}, headers=h)
        # Authed recommendation -> audit row attributed to the user.
        trust_client.post("/v1/investment-strategy", json=_BASE, headers={**h, "X-Request-ID": "p4-del-audit"})

        U = uuid.UUID(uid)
        s = db.get_session()
        assert s.query(M.AuditLog).filter_by(user_id=U).count() >= 1
        total_audit_before = s.query(M.AuditLog).count()

        d = trust_client.delete("/v1/account", headers=h)
        assert d.status_code == 200
        erased = d.get_json()["erased"]
        assert erased["user_deleted"] is True
        assert erased["audit_deidentified"] >= 1

        s2 = db.get_session()
        assert s2.query(M.FinancialProfile).filter_by(user_id=U).count() == 0
        assert s2.query(M.Preferences).filter_by(user_id=U).count() == 0
        assert s2.query(M.SavedPlan).filter_by(user_id=U).count() == 0
        assert s2.query(M.UserCredential).filter_by(user_id=U).count() == 0
        assert s2.query(M.User).filter_by(id=U).count() == 0
        # Audit rows retained (de-identified) — count unchanged, user link gone.
        assert s2.query(M.AuditLog).count() == total_audit_before
        assert s2.query(M.AuditLog).filter_by(user_id=U).count() == 0

    def test_login_fails_after_deletion(self, trust_client):
        trust_client.post("/v1/auth/register", json={"email": "gone@example.com", "password": "password123"})
        login = trust_client.post("/v1/auth/login", json={"email": "gone@example.com", "password": "password123"})
        h = {"Authorization": f"Bearer {login.get_json()['access_token']}"}
        trust_client.delete("/v1/account", headers=h)
        after = trust_client.post("/v1/auth/login", json={"email": "gone@example.com", "password": "password123"})
        assert after.status_code == 401

    def test_delete_requires_auth(self, trust_client):
        assert trust_client.delete("/v1/account").status_code == 401
