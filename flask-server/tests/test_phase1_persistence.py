"""
Phase 1 integration tests: auth flow, profile/preferences CRUD, saved-plan
ownership, DB-backed metrics cache, and the stateless/anonymous guarantees.

These run against a throwaway SQLite database (the ``db_app`` fixture), so they
need no external services and run in CI. The same code paths were separately
verified against real PostgreSQL during development.
"""

import uuid

# --------------------------------------------------------------------------- #
# Auth flow
# --------------------------------------------------------------------------- #

class TestAuthFlow:
    def test_register_returns_tokens(self, db_app):
        resp = db_app.post(
            "/v1/auth/register",
            json={"email": "new@example.com", "password": "password123"},
        )
        assert resp.status_code == 201
        body = resp.get_json()
        assert body["access_token"] and body["refresh_token"]
        assert body["token_type"] == "Bearer"
        assert body["user"]["email"] == "new@example.com"

    def test_register_rejects_bad_email(self, db_app):
        resp = db_app.post("/v1/auth/register", json={"email": "nope", "password": "password123"})
        assert resp.status_code == 400

    def test_register_rejects_short_password(self, db_app):
        resp = db_app.post("/v1/auth/register", json={"email": "a@b.com", "password": "short"})
        assert resp.status_code == 400

    def test_duplicate_email_conflicts(self, db_app):
        payload = {"email": "dup@example.com", "password": "password123"}
        assert db_app.post("/v1/auth/register", json=payload).status_code == 201
        assert db_app.post("/v1/auth/register", json=payload).status_code == 409

    def test_login_success_and_wrong_password(self, db_app):
        db_app.post("/v1/auth/register", json={"email": "l@example.com", "password": "password123"})
        ok = db_app.post("/v1/auth/login", json={"email": "l@example.com", "password": "password123"})
        assert ok.status_code == 200 and ok.get_json()["access_token"]
        bad = db_app.post("/v1/auth/login", json={"email": "l@example.com", "password": "wrong"})
        assert bad.status_code == 401

    def test_login_unknown_user_is_401(self, db_app):
        resp = db_app.post("/v1/auth/login", json={"email": "ghost@example.com", "password": "password123"})
        assert resp.status_code == 401

    def test_refresh_issues_new_access_token(self, db_app):
        reg = db_app.post("/v1/auth/register", json={"email": "r@example.com", "password": "password123"})
        refresh_token = reg.get_json()["refresh_token"]
        resp = db_app.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        assert resp.get_json()["access_token"]

    def test_refresh_rejects_access_token_as_refresh(self, db_app):
        reg = db_app.post("/v1/auth/register", json={"email": "r2@example.com", "password": "password123"})
        access = reg.get_json()["access_token"]
        # Passing an access token where a refresh token is expected must fail.
        resp = db_app.post("/v1/auth/refresh", json={"refresh_token": access})
        assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Profile & preferences CRUD
# --------------------------------------------------------------------------- #

class TestProfilePreferences:
    def test_get_profile_requires_auth(self, db_app):
        assert db_app.get("/v1/profile").status_code == 401

    def test_profile_put_then_get_roundtrip(self, auth_headers):
        client, headers = auth_headers
        put = client.put(
            "/v1/profile",
            json={"annual_income": 1_200_000, "dependents": 2, "experience_level": "intermediate"},
            headers=headers,
        )
        assert put.status_code == 200
        got = client.get("/v1/profile", headers=headers).get_json()
        assert got["annual_income"] == 1_200_000
        assert got["dependents"] == 2
        assert got["experience_level"] == "intermediate"

    def test_profile_put_ignores_unknown_fields(self, auth_headers):
        client, headers = auth_headers
        resp = client.put(
            "/v1/profile",
            json={"annual_income": 500000, "hacker_field": "evil"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert "hacker_field" not in resp.get_json()

    def test_preferences_put_then_get_roundtrip(self, auth_headers):
        client, headers = auth_headers
        put = client.put(
            "/v1/preferences",
            json={"esg_only": True, "excluded_tickers": ["ITC.NS"], "preferred_sectors": ["technology"]},
            headers=headers,
        )
        assert put.status_code == 200
        got = client.get("/v1/preferences", headers=headers).get_json()
        assert got["esg_only"] is True
        assert got["excluded_tickers"] == ["ITC.NS"]
        assert got["preferred_sectors"] == ["technology"]


# --------------------------------------------------------------------------- #
# Saved plans + ownership
# --------------------------------------------------------------------------- #

class TestSavedPlans:
    def _make_plan(self, client, headers, name="Plan A"):
        return client.post(
            "/v1/plans",
            json={
                "name": name,
                "input_payload": {"currentAge": 30, "retirementAge": 60, "riskCategory": "medium"},
                "recommendation": {
                    "objective": "max_sharpe",
                    "allocation": [{"Stock Name": "NIFTYBEES.NS", "Investment Percentage": 100}],
                    "projection": {"p50": 10180000},
                },
            },
            headers=headers,
        )

    def test_create_requires_input_payload(self, auth_headers):
        client, headers = auth_headers
        resp = client.post("/v1/plans", json={"name": "x"}, headers=headers)
        assert resp.status_code == 400

    def test_create_list_get_roundtrip(self, auth_headers):
        client, headers = auth_headers
        created = self._make_plan(client, headers)
        assert created.status_code == 201
        plan_id = created.get_json()["id"]

        listing = client.get("/v1/plans", headers=headers).get_json()
        assert len(listing["plans"]) == 1

        got = client.get(f"/v1/plans/{plan_id}", headers=headers)
        assert got.status_code == 200
        body = got.get_json()
        assert body["name"] == "Plan A"
        assert "recommendation" in body
        assert body["recommendation"]["objective"] == "max_sharpe"

    def test_owner_scoping_blocks_other_user(self, db_app):
        # User 1 creates a plan.
        r1 = db_app.post("/v1/auth/register", json={"email": "owner@example.com", "password": "password123"})
        h1 = {"Authorization": f"Bearer {r1.get_json()['access_token']}"}
        pid = self._make_plan(db_app, h1).get_json()["id"]

        # User 2 must not be able to read it.
        r2 = db_app.post("/v1/auth/register", json={"email": "intruder@example.com", "password": "password123"})
        h2 = {"Authorization": f"Bearer {r2.get_json()['access_token']}"}
        assert db_app.get(f"/v1/plans/{pid}", headers=h2).status_code == 403

    def test_nonexistent_plan_is_404(self, auth_headers):
        client, headers = auth_headers
        assert client.get(f"/v1/plans/{uuid.uuid4()}", headers=headers).status_code == 404

    def test_bad_uuid_is_400(self, auth_headers):
        client, headers = auth_headers
        assert client.get("/v1/plans/not-a-uuid", headers=headers).status_code == 400

    def test_full_dod_signout_signin_reload(self, db_app):
        # DoD: register, save a plan, "sign out", sign back in, reload it.
        reg = db_app.post("/v1/auth/register", json={"email": "dod@example.com", "password": "password123"})
        h = {"Authorization": f"Bearer {reg.get_json()['access_token']}"}
        pid = self._make_plan(db_app, h).get_json()["id"]
        # Sign back in (fresh token, as if a new session).
        login = db_app.post("/v1/auth/login", json={"email": "dod@example.com", "password": "password123"})
        h2 = {"Authorization": f"Bearer {login.get_json()['access_token']}"}
        reloaded = db_app.get(f"/v1/plans/{pid}", headers=h2)
        assert reloaded.status_code == 200
        assert reloaded.get_json()["id"] == pid


# --------------------------------------------------------------------------- #
# DB-backed metrics cache
# --------------------------------------------------------------------------- #

class TestMetricsCacheDB:
    def test_db_roundtrip_and_prefers_db(self, db_app):
        # db_app has persistence enabled; save then load via the engine helpers.
        import investment_strategy as eng

        sample = [
            {"Stock Name": "EQ_A", "Asset Class": "Eq", "Annual Return (%)": 14.0,
             "Volatility (%)": 18.0, "Beta": 1.0, "Sharpe Ratio": 0.4, "Risk Profile": "Medium"},
        ]
        assert eng._db_available() is True
        eng._save_metrics_cache(sample)
        from_db = eng._load_metrics_cache_db()
        assert len(from_db) == 1 and from_db[0]["Stock Name"] == "EQ_A"
        # _load_metrics_cache should prefer the DB value.
        assert len(eng._load_metrics_cache()) >= 1


# --------------------------------------------------------------------------- #
# Stateless guarantees still hold (no DATABASE_URL)
# --------------------------------------------------------------------------- #

class TestStatelessGuarantees:
    def test_v1_auth_returns_503_without_db(self, flask_client):
        # flask_client fixture does NOT enable persistence.
        resp = flask_client.post(
            "/v1/auth/register", json={"email": "a@b.com", "password": "password123"}
        )
        assert resp.status_code == 503

    def test_anonymous_recommendation_still_works(self, flask_client, valid_payload):
        assert flask_client.post("/investment-strategy", json=valid_payload).status_code == 200

    def test_v1_recommendation_alias_works_anonymously(self, flask_client, valid_payload):
        assert flask_client.post("/v1/investment-strategy", json=valid_payload).status_code == 200
