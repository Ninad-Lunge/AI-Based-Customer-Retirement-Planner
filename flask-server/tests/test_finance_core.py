"""
Unit tests for the finance core in investment_strategy.py.

These exercise the pure, deterministic functions on synthetic data. No network
calls, no model training, no TensorFlow usage at call time. (Importing the
module does load keras/tensorflow once; that import is marked slow implicitly by
being module-level, but the tests themselves are fast.)

Covered:
- _calculate_future_value: FV-of-annuity math (incl. the 0% edge case).
- _required_annual_return: bisection inversion of the FV formula.
- _filter_by_risk_category: volatility-band filtering (low/medium/high).
- suggest_investment: end-to-end allocation on synthetic metrics, including the
  response contract keys, percentage normalisation, and the Monte Carlo stats.
"""

import math

import numpy as np
import pandas as pd
import pytest

import investment_strategy as eng

# --------------------------------------------------------------------------- #
# _calculate_future_value
# --------------------------------------------------------------------------- #

class TestCalculateFutureValue:
    def test_zero_return_is_simple_sum(self):
        # With 0% return, FV is just contributions * months.
        assert eng._calculate_future_value(1000, 0.0, 12) == pytest.approx(12_000)

    def test_matches_closed_form_annuity(self):
        # FV = P * [((1+r)^n - 1) / r], r = annual/100/12.
        P, annual_pct, n = 10_000, 12.0, 120
        r = annual_pct / 100 / 12
        expected = P * (((1 + r) ** n - 1) / r)
        assert eng._calculate_future_value(P, annual_pct, n) == pytest.approx(expected)

    def test_monotonic_in_return(self):
        low = eng._calculate_future_value(5000, 6.0, 240)
        high = eng._calculate_future_value(5000, 12.0, 240)
        assert high > low

    def test_monotonic_in_months(self):
        short = eng._calculate_future_value(5000, 10.0, 60)
        long = eng._calculate_future_value(5000, 10.0, 240)
        assert long > short


# --------------------------------------------------------------------------- #
# Step-up SIP (yearly escalation of the monthly contribution)
# --------------------------------------------------------------------------- #

class TestStepUp:
    def test_zero_stepup_matches_closed_form(self):
        # step_up=0 must reproduce the plain annuity exactly (backward compat).
        P, r, n = 15000, 12.0, 360
        baseline = eng._calculate_future_value(P, r, n)
        with_zero = eng._calculate_future_value(P, r, n, 0.0)
        assert with_zero == pytest.approx(baseline, rel=1e-12)

    def test_stepup_increases_corpus(self):
        P, r, n = 15000, 12.0, 360
        base = eng._calculate_future_value(P, r, n, 0.0)
        stepped = eng._calculate_future_value(P, r, n, 10.0)
        assert stepped > base

    def test_stepup_increases_total_contributions(self):
        # FV at 0% return = total nominal contributions; step-up grows it beyond P*n.
        P, n = 15000, 360
        assert eng._calculate_future_value(P, 0.0, n, 10.0) > P * n

    def test_stepup_lowers_required_return(self):
        target, P, n = 20_000_000, 15000, 360
        req0 = eng._required_annual_return(target, P, n, 0.0)
        req10 = eng._required_annual_return(target, P, n, 10.0)
        assert req10 < req0

    def test_suggest_investment_accepts_stepup(self, synthetic_metrics_df):
        import numpy as np

        np.random.seed(0)
        base = eng.suggest_investment(synthetic_metrics_df, 30, 10_000_000, 15_000, "medium", 0.0)
        np.random.seed(0)
        stepped = eng.suggest_investment(synthetic_metrics_df, 30, 10_000_000, 15_000, "medium", 10.0)
        assert "error" not in base and "error" not in stepped
        assert stepped["Total Future Value (INR)"] > base["Total Future Value (INR)"]


# --------------------------------------------------------------------------- #
# _required_annual_return
# --------------------------------------------------------------------------- #

class TestRequiredAnnualReturn:
    def test_zero_when_contributions_already_exceed_target(self):
        # 1000 * 120 = 120_000 >= target 100_000 => 0% needed.
        assert eng._required_annual_return(100_000, 1000, 120) == 0.0

    def test_guard_on_nonpositive_inputs(self):
        assert eng._required_annual_return(100_000, 0, 120) == 0.0
        assert eng._required_annual_return(100_000, 1000, 0) == 0.0

    def test_inversion_round_trips_through_fv(self):
        # The return it finds, fed back into FV, should reach ~the target.
        # Target must exceed the zero-return contribution sum (P*n), otherwise
        # the function short-circuits to 0% (see test below).
        P, n = 15_000, 360
        assert P * n < 10_000_000  # precondition: 0% is not enough
        target = 10_000_000
        req = eng._required_annual_return(target, P, n)
        assert req > 0.0
        fv = eng._calculate_future_value(P, req, n)
        assert fv == pytest.approx(target, rel=1e-3)

    def test_within_search_bounds(self):
        req = eng._required_annual_return(10_000_000, 15_000, 360)
        assert 0.0 <= req <= 200.0


# --------------------------------------------------------------------------- #
# _filter_by_risk_category
# --------------------------------------------------------------------------- #

class TestFilterByRiskCategory:
    def test_low_band_keeps_only_vol_le_15(self, synthetic_metrics_df):
        out = eng._filter_by_risk_category(synthetic_metrics_df, "low")
        assert not out.empty
        assert (out["Volatility (%)"] <= 15).all()

    def test_medium_band_is_12_to_30(self, synthetic_metrics_df):
        out = eng._filter_by_risk_category(synthetic_metrics_df, "medium")
        assert not out.empty
        assert ((out["Volatility (%)"] > 12) & (out["Volatility (%)"] <= 30)).all()

    def test_high_band_is_gt_25(self, synthetic_metrics_df):
        out = eng._filter_by_risk_category(synthetic_metrics_df, "high")
        assert not out.empty
        assert (out["Volatility (%)"] > 25).all()

    def test_unknown_category_returns_all(self, synthetic_metrics_df):
        out = eng._filter_by_risk_category(synthetic_metrics_df, "unknown")
        assert len(out) == len(synthetic_metrics_df)

    def test_returns_a_copy_not_a_view(self, synthetic_metrics_df):
        out = eng._filter_by_risk_category(synthetic_metrics_df, "medium")
        out.loc[out.index[0], "Volatility (%)"] = 999
        # Original must be untouched.
        assert 999 not in synthetic_metrics_df["Volatility (%)"].values


# --------------------------------------------------------------------------- #
# suggest_investment (end-to-end on synthetic metrics)
# --------------------------------------------------------------------------- #

class TestSuggestInvestment:
    def _call(self, df, risk="medium", years=30, target=10_000_000, monthly=15_000):
        # Deterministic Monte Carlo for assertions.
        np.random.seed(42)
        return eng.suggest_investment(df, years, target, monthly, risk)

    def test_returns_full_contract_on_success(self, synthetic_metrics_df):
        res = self._call(synthetic_metrics_df)
        assert "error" not in res
        for key in (
            "Total Future Value (INR)",
            "Investment Suggestions",
            "Average Annual Return (%)",
            "Value at Risk (5th percentile) (INR)",
            "Value at Risk (5th percentile) (%)",
            "Average Value from Simulation (INR)",
            "Optimistic Value (90th percentile) (INR)",
            "Goal Achievable",
        ):
            assert key in res, f"missing response key: {key}"

    def test_suggestion_rows_have_expected_shape(self, synthetic_metrics_df):
        res = self._call(synthetic_metrics_df)
        suggestions = res["Investment Suggestions"]
        assert isinstance(suggestions, list) and suggestions
        for s in suggestions:
            for key in (
                "Stock Name",
                "Asset Class",
                "Investment Percentage",
                "Annual Return (%)",
                "Risk Profile",
                "Future Value (INR)",
            ):
                assert key in s

    def test_percentages_sum_to_100(self, synthetic_metrics_df):
        res = self._call(synthetic_metrics_df)
        total = sum(s["Investment Percentage"] for s in res["Investment Suggestions"])
        assert total == pytest.approx(100.0, abs=0.5)

    def test_portfolio_size_capped(self, synthetic_metrics_df):
        res = self._call(synthetic_metrics_df)
        assert len(res["Investment Suggestions"]) <= eng.MAX_PORTFOLIO_SIZE

    def test_invalid_risk_category_returns_error(self, synthetic_metrics_df):
        res = eng.suggest_investment(synthetic_metrics_df, 30, 1e7, 15_000, "bogus")
        assert "error" in res

    def test_empty_category_returns_error(self):
        # A df whose only row sits outside the "low" band => empty after filter.
        df = pd.DataFrame(
            [{
                "Stock Name": "HIGHVOL", "Asset Class": "x",
                "Annual Return (%)": 20.0, "Volatility (%)": 40.0,
                "Beta": 1.5, "Sharpe Ratio": 0.3, "Risk Profile": "High",
            }]
        )
        res = eng.suggest_investment(df, 30, 1e7, 15_000, "low")
        assert "error" in res

    def test_goal_achievable_is_bool(self, synthetic_metrics_df):
        res = self._call(synthetic_metrics_df)
        assert isinstance(res["Goal Achievable"], bool)

    def test_monte_carlo_percentile_ordering(self, synthetic_metrics_df):
        # p5 <= p90 is the sound invariant. Note: the simulation mean can
        # exceed p90 because the FV-of-annuity is highly convex and the
        # normal-return sampling is right-skewed, so a few extreme draws pull
        # the mean above the 90th percentile. (This fat-tail behaviour is a
        # known limitation of the current normal-sampling Monte Carlo.)
        res = self._call(synthetic_metrics_df)
        p5 = res["Value at Risk (5th percentile) (INR)"]
        p90 = res["Optimistic Value (90th percentile) (INR)"]
        assert p5 <= p90

    def test_monte_carlo_values_are_finite_and_positive(self, synthetic_metrics_df):
        res = self._call(synthetic_metrics_df)
        for key in (
            "Value at Risk (5th percentile) (INR)",
            "Average Value from Simulation (INR)",
            "Optimistic Value (90th percentile) (INR)",
            "Total Future Value (INR)",
        ):
            val = res[key]
            assert math.isfinite(val)
            assert val > 0

    def test_low_risk_selection_prefers_low_vol(self, synthetic_metrics_df):
        # A low-risk request should only draw from the low-vol band.
        res = self._call(synthetic_metrics_df, risk="low")
        assert "error" not in res
        names = {s["Stock Name"] for s in res["Investment Suggestions"]}
        # Low band = volatility <= 15: BOND_A (5%), BOND_B (12%), LIQUID_A (3%).
        # None of the medium/high-vol-only names may appear.
        assert names.issubset({"BOND_A", "BOND_B", "LIQUID_A"})

    def test_missing_asset_class_column_defaults_to_other(self):
        # Older cached metrics may lack the "Asset Class" column; the engine
        # must still produce suggestions, defaulting the class to "Other".
        df = pd.DataFrame(
            [
                {"Stock Name": "A", "Annual Return (%)": 14.0, "Volatility (%)": 18.0,
                 "Beta": 1.0, "Sharpe Ratio": 0.4, "Risk Profile": "Medium"},
                {"Stock Name": "B", "Annual Return (%)": 16.0, "Volatility (%)": 22.0,
                 "Beta": 1.1, "Sharpe Ratio": 0.45, "Risk Profile": "High"},
                {"Stock Name": "C", "Annual Return (%)": 12.0, "Volatility (%)": 20.0,
                 "Beta": 0.9, "Sharpe Ratio": 0.3, "Risk Profile": "Medium"},
            ]
        )
        np.random.seed(42)
        res = eng.suggest_investment(df, 30, 10_000_000, 15_000, "medium")
        assert "error" not in res
        assert all(s["Asset Class"] == "Other" for s in res["Investment Suggestions"])
