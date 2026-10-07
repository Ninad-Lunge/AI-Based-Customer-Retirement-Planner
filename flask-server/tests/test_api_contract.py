"""
Contract tests for the Flask API in server.py.

The `flask_client` fixture (see conftest.py) patches `server.fetch_stock_data`
to return synthetic metrics, so these tests exercise the full request → validate
→ engine → response path without yfinance or TensorFlow at call time.

Covered:
- GET /health
- POST /investment-strategy happy path (200 + response contract)
- Each validation error branch (400) in _validate_payload
- Domain error branch (422) when no stocks match the risk category
- Unknown route (404) and wrong method (405)
"""

import pandas as pd
import pytest

# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #

def test_health_ok(flask_client):
    resp = flask_client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_ready_probe_ok(flask_client):
    resp = flask_client.get("/ready")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["checks"]["universe_loaded"] is True


def test_request_id_header_is_echoed(flask_client):
    # Inbound X-Request-ID should be echoed back on the response.
    resp = flask_client.get("/health", headers={"X-Request-ID": "test-rid-123"})
    assert resp.headers.get("X-Request-ID") == "test-rid-123"


def test_request_id_header_is_generated_when_absent(flask_client):
    resp = flask_client.get("/health")
    assert resp.headers.get("X-Request-ID")  # non-empty generated id


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

def test_investment_strategy_happy_path(flask_client, valid_payload):
    resp = flask_client.post("/investment-strategy", json=valid_payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert "error" not in body
    # Legacy response contract keys the frontend depends on.
    for key in (
        "Total Future Value (INR)",
        "Investment Suggestions",
        "Average Annual Return (%)",
        "Value at Risk (5th percentile) (INR)",
        "Optimistic Value (90th percentile) (INR)",
        "Goal Achievable",
    ):
        assert key in body
    assert isinstance(body["Investment Suggestions"], list)
    assert body["Investment Suggestions"]


# --------------------------------------------------------------------------- #
# Optional yearly step-up (stepUpPercent)
# --------------------------------------------------------------------------- #

def test_stepup_accepted_and_increases_corpus(flask_client, valid_payload):
    import numpy as np

    np.random.seed(0)
    base = flask_client.post("/investment-strategy", json=valid_payload).get_json()
    np.random.seed(0)
    stepped = flask_client.post(
        "/investment-strategy", json={**valid_payload, "stepUpPercent": 10}
    ).get_json()
    assert stepped["Total Future Value (INR)"] > base["Total Future Value (INR)"]


def test_stepup_omitted_is_backward_compatible(flask_client, valid_payload):
    # No stepUpPercent -> 200 and valid result (unchanged behaviour).
    resp = flask_client.post("/investment-strategy", json=valid_payload)
    assert resp.status_code == 200


def test_stepup_out_of_range_returns_400(flask_client, valid_payload):
    resp = flask_client.post("/investment-strategy", json={**valid_payload, "stepUpPercent": 50})
    assert resp.status_code == 400


def test_stepup_non_numeric_returns_400(flask_client, valid_payload):
    resp = flask_client.post("/investment-strategy", json={**valid_payload, "stepUpPercent": "lots"})
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Request-body / JSON errors
# --------------------------------------------------------------------------- #

def test_non_json_body_returns_400(flask_client):
    resp = flask_client.post(
        "/investment-strategy", data="not json", content_type="text/plain"
    )
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_json_array_body_returns_400(flask_client):
    # Body must be a JSON object, not an array.
    resp = flask_client.post("/investment-strategy", json=[1, 2, 3])
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Field validation errors (each 400 branch)
# --------------------------------------------------------------------------- #

def test_missing_required_fields_returns_400(flask_client):
    resp = flask_client.post("/investment-strategy", json={})
    assert resp.status_code == 400
    body = resp.get_json()
    assert "missing_fields" in (body.get("details") or {})


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("currentAge", "abc"),
        ("retirementAge", "xyz"),
        ("desiredFund", "notanumber"),
        ("monthlyInvestment", "notanumber"),
    ],
)
def test_non_numeric_fields_return_400(flask_client, valid_payload, field, bad_value):
    payload = dict(valid_payload)
    payload[field] = bad_value
    resp = flask_client.post("/investment-strategy", json=payload)
    assert resp.status_code == 400


def test_current_age_out_of_range_returns_400(flask_client, valid_payload):
    payload = dict(valid_payload, currentAge=10)  # below MIN_AGE 18
    resp = flask_client.post("/investment-strategy", json=payload)
    assert resp.status_code == 400


def test_retirement_age_out_of_range_returns_400(flask_client, valid_payload):
    payload = dict(valid_payload, retirementAge=150)  # above MAX_AGE 100
    resp = flask_client.post("/investment-strategy", json=payload)
    assert resp.status_code == 400


def test_retirement_not_greater_than_current_returns_400(flask_client, valid_payload):
    payload = dict(valid_payload, currentAge=50, retirementAge=40)
    resp = flask_client.post("/investment-strategy", json=payload)
    assert resp.status_code == 400


def test_nonpositive_desired_fund_returns_400(flask_client, valid_payload):
    payload = dict(valid_payload, desiredFund=0)
    resp = flask_client.post("/investment-strategy", json=payload)
    assert resp.status_code == 400


def test_nonpositive_monthly_investment_returns_400(flask_client, valid_payload):
    payload = dict(valid_payload, monthlyInvestment=-100)
    resp = flask_client.post("/investment-strategy", json=payload)
    assert resp.status_code == 400


def test_invalid_risk_category_returns_400(flask_client, valid_payload):
    payload = dict(valid_payload, riskCategory="extreme")
    resp = flask_client.post("/investment-strategy", json=payload)
    assert resp.status_code == 400


def test_non_string_risk_category_returns_400(flask_client, valid_payload):
    payload = dict(valid_payload, riskCategory=123)
    resp = flask_client.post("/investment-strategy", json=payload)
    assert resp.status_code == 400


def test_risk_category_is_case_insensitive(flask_client, valid_payload):
    payload = dict(valid_payload, riskCategory="MEDIUM")
    resp = flask_client.post("/investment-strategy", json=payload)
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Domain error (422) and data-availability branches
# --------------------------------------------------------------------------- #

def test_domain_error_returns_422(flask_client, valid_payload, monkeypatch):
    # Force an engine domain error (empty category) by returning metrics with
    # only a high-vol row, then requesting the "low" category.
    import server

    only_high = pd.DataFrame(
        [{
            "Stock Name": "HIGHVOL", "Asset Class": "x",
            "Annual Return (%)": 20.0, "Volatility (%)": 45.0,
            "Beta": 1.6, "Sharpe Ratio": 0.3, "Risk Profile": "High",
        }]
    )
    monkeypatch.setattr(server, "fetch_stock_data", lambda tickers: only_high)
    payload = dict(valid_payload, riskCategory="low")
    resp = flask_client.post("/investment-strategy", json=payload)
    assert resp.status_code == 422
    assert "error" in resp.get_json()


def test_empty_data_returns_503(flask_client, monkeypatch, valid_payload):
    import server

    monkeypatch.setattr(server, "fetch_stock_data", lambda tickers: pd.DataFrame())
    resp = flask_client.post("/investment-strategy", json=valid_payload)
    assert resp.status_code == 503


def test_fetch_exception_returns_502(flask_client, monkeypatch, valid_payload):
    import server

    def boom(tickers):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(server, "fetch_stock_data", boom)
    resp = flask_client.post("/investment-strategy", json=valid_payload)
    assert resp.status_code == 502


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #

def test_unknown_route_returns_404(flask_client):
    resp = flask_client.get("/does-not-exist")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_wrong_method_returns_405(flask_client):
    resp = flask_client.get("/investment-strategy")
    assert resp.status_code == 405
    assert "error" in resp.get_json()
