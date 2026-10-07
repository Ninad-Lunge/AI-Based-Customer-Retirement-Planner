"""
Investment strategy module for the AI-Based Customer Retirement Planner.

Uses LSTM-based stock price prediction to recommend a diversified investment
portfolio tailored to the user's risk appetite, investment horizon, and
target retirement fund.

Logic improvements:
- Risk category filters use contiguous/overlapping ranges so no stock falls
  through the cracks between categories.
- Required annual return is computed via proper FV-of-annuity inversion
  instead of an arbitrary 1% constant.
- Scoring uses the Sharpe Ratio directly (no double volatility penalty).
- Stock selection picks top Sharpe-ranked stocks for diversification even
  when they fall short of the required return.
- LSTM is trained for 10 epochs (vs 1) for more meaningful predictions.
- Sharpe Ratio incorporates a configurable risk-free rate.
- Monte Carlo simulation correctly samples annual returns and converts to
  monthly compounding in a consistent way.
"""

import json
import logging
import os
import warnings
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
import yfinance as yf
from keras.layers import LSTM, Dense
from keras.models import Sequential
from sklearn.preprocessing import MinMaxScaler

__all__ = [
    "investment_tickers",
    "fetch_stock_data",
    "suggest_investment",
    "reload_universe",
    "TICKER_ASSET_CLASS",
]

logger = logging.getLogger(__name__)

# Suppress noisy, non-actionable sklearn warnings that fire on every prediction:
# - InconsistentVersionWarning: saved models were pickled with a slightly
#   different sklearn patch version (safe to ignore for MinMaxScaler).
# - UserWarning about feature names: scaler was fitted on a numpy array but
#   transforms a DataFrame; the result is identical.
warnings.filterwarnings("ignore", message=".*InconsistentVersionWarning.*")
warnings.filterwarnings("ignore", message=".*X has feature names.*")
try:
    from sklearn.exceptions import InconsistentVersionWarning
    warnings.filterwarnings("ignore", category=InconsistentVersionWarning)
except Exception:
    pass

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Annualised risk-free rate (%) — approximate Indian 10-year govt bond yield.
# Used in Sharpe Ratio = (return - risk_free) / volatility.
RISK_FREE_RATE: float = float(os.environ.get("RISK_FREE_RATE", "7.0"))

# LSTM training epochs.  1 epoch barely learns; 10 gives a reasonable fit
# without taking too long on CPU.
LSTM_EPOCHS: int = int(os.environ.get("LSTM_EPOCHS", "10"))

# Number of Monte Carlo simulations.
MC_SIMULATIONS: int = int(os.environ.get("MC_SIMULATIONS", "10000"))

# Maximum stocks in a suggested portfolio.
MAX_PORTFOLIO_SIZE: int = 10

# Minimum number of stocks for diversification.  If fewer stocks meet the
# required-return threshold, we pad with the best remaining Sharpe stocks.
MIN_PORTFOLIO_SIZE: int = 3

# --------------------------------------------------------------------------- #
# Ticker universe
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Ticker universe
#
# The universe is loaded from an external ``tickers.json`` file (see
# TICKERS_CONFIG_PATH below) so you can add/remove tickers WITHOUT editing this
# Python module.  The JSON groups tickers by asset-class label.
#
# If the config file is missing or malformed, we fall back to the built-in
# DEFAULT_TICKER_GROUPS so the app keeps working.
#
# Every ticker in the default set was verified to resolve on yfinance 1.7.0.
# Indian mutual-fund exposure is represented via liquid, index-tracking ETFs
# (the *BEES family), which are the practical investable proxies.
# --------------------------------------------------------------------------- #

# Config file lives alongside this module (not dependent on the CWD).
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
TICKERS_CONFIG_PATH: str = os.environ.get(
    "TICKERS_CONFIG_PATH", os.path.join(_MODULE_DIR, "tickers.json")
)

# Built-in fallback, used only if tickers.json is missing/invalid.
DEFAULT_TICKER_GROUPS: Dict[str, List[str]] = {
    "Indian Equity": [
        "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
        "KOTAKBANK.NS", "LT.NS", "SBIN.NS", "BHARTIARTL.NS", "ITC.NS",
        "HINDUNILVR.NS", "ASIANPAINT.NS", "AXISBANK.NS", "BAJFINANCE.NS",
        "MARUTI.NS", "M&M.NS", "SUNPHARMA.NS", "HCLTECH.NS", "ONGC.NS",
        "TITAN.NS", "ULTRACEMCO.NS", "WIPRO.NS", "ADANIGREEN.NS", "DMART.NS",
    ],
    "Indian Equity (Growth)": ["TATASTEEL.NS", "ADANIENT.NS", "IRCTC.NS", "DIXON.NS"],
    "Indian ETF / Mutual Fund": [
        "NIFTYBEES.NS", "BANKBEES.NS", "ITBEES.NS", "JUNIORBEES.NS", "LIQUIDBEES.NS",
    ],
    "Indian REIT": ["EMBASSY.NS", "MINDSPACE.NS", "BIRET.NS"],
    "Gold / Precious Metals": ["GLD", "GOLDBEES.NS", "SLV"],
    "Bonds / Fixed Income": [
        "TLT", "IEF", "BND", "AGG", "LQD", "HYG", "TIP", "SHY", "MUB", "BIV",
    ],
    "International ETF": ["QQQ", "VTI", "ARKK", "VWO"],
    "Global Index": [
        "^GSPC", "^DJI", "^IXIC", "^RUT", "^FTSE", "^N225", "^HSI", "^GDAXI",
        "^FCHI", "^STOXX50E",
    ],
}


def _load_ticker_groups() -> Dict[str, List[str]]:
    """
    Load the ticker universe from tickers.json.

    Returns a mapping of {asset_class_label: [ticker, ...]}.  Falls back to
    DEFAULT_TICKER_GROUPS if the file is missing, unreadable, or malformed.
    """
    try:
        with open(TICKERS_CONFIG_PATH, "r") as f:
            raw = json.load(f)
        groups = raw.get("groups", {})
        # Validate: must be a dict of label -> list[str].
        cleaned: Dict[str, List[str]] = {}
        for label, tickers in groups.items():
            if isinstance(label, str) and isinstance(tickers, list):
                syms = [str(t).strip() for t in tickers if str(t).strip()]
                if syms:
                    cleaned[label] = syms
        if not cleaned:
            raise ValueError("tickers.json contains no valid ticker groups")
        logger.info(
            "Loaded %d ticker groups (%d symbols) from %s.",
            len(cleaned),
            sum(len(v) for v in cleaned.values()),
            TICKERS_CONFIG_PATH,
        )
        return cleaned
    except FileNotFoundError:
        logger.warning(
            "tickers.json not found at %s; using built-in default universe.",
            TICKERS_CONFIG_PATH,
        )
    except Exception:
        logger.warning(
            "Failed to parse tickers.json; using built-in default universe.",
            exc_info=True,
        )
    return DEFAULT_TICKER_GROUPS


def _build_universe(
    groups: Dict[str, List[str]]
) -> Tuple[List[str], Dict[str, str]]:
    """
    Flatten groups into an ordered, de-duplicated ticker list and an
    accompanying {ticker: asset_class} lookup.
    """
    tickers: List[str] = []
    asset_class: Dict[str, str] = {}
    seen = set()
    for label, syms in groups.items():
        for sym in syms:
            if sym not in seen:
                seen.add(sym)
                tickers.append(sym)
                asset_class[sym] = label
    return tickers, asset_class


# Load the universe at import time.
_TICKER_GROUPS = _load_ticker_groups()
investment_tickers, TICKER_ASSET_CLASS = _build_universe(_TICKER_GROUPS)


def reload_universe() -> int:
    """
    Re-read tickers.json and refresh the in-memory universe.  Lets you edit
    the config and pick up changes without restarting the process (e.g. via a
    small admin endpoint).  Returns the number of tickers now loaded.
    """
    global investment_tickers, TICKER_ASSET_CLASS, _TICKER_GROUPS
    _TICKER_GROUPS = _load_ticker_groups()
    investment_tickers, TICKER_ASSET_CLASS = _build_universe(_TICKER_GROUPS)
    return len(investment_tickers)


# --------------------------------------------------------------------------- #
# Model persistence directory
# --------------------------------------------------------------------------- #
model_dir: str = "saved_models"

if not os.path.exists(model_dir):
    os.makedirs(model_dir)

# Cached metrics file — written after every successful fetch_stock_data() run
# so that the app can still serve requests when Yahoo Finance is unreachable.
METRICS_CACHE_PATH: str = os.path.join(model_dir, "_cached_metrics.json")


def _db_available() -> bool:
    """True if the persistence layer is wired and enabled (lazy, decoupled)."""
    try:
        import db  # local import to avoid a hard dependency / circular import

        return db.is_enabled()
    except Exception:
        return False


def _save_metrics_cache_db(stock_data: List[Dict]) -> bool:
    """
    Upsert the metrics into the model_metrics_cache table (single 'current' row).
    Returns True on success. Best-effort: never raises into the caller.
    """
    try:
        import db
        from db.models import ModelMetricsCache

        session = db.get_session()
        row = session.get(ModelMetricsCache, "current")
        if row is None:
            row = ModelMetricsCache(id="current", metrics=stock_data)
            session.add(row)
        else:
            row.metrics = stock_data
        session.commit()
        logger.info("Saved metrics cache (%d tickers) to DB.", len(stock_data))
        return True
    except Exception:
        logger.warning("Failed to write metrics cache to DB.", exc_info=True)
        try:
            import db

            db.get_session().rollback()
        except Exception:
            pass
        return False


def _load_metrics_cache_db() -> List[Dict]:
    """Load metrics from the DB cache row, or [] if absent/unavailable."""
    try:
        import db
        from db.models import ModelMetricsCache

        session = db.get_session()
        row = session.get(ModelMetricsCache, "current")
        if row and row.metrics:
            logger.info("Loaded metrics cache (%d tickers) from DB.", len(row.metrics))
            return list(row.metrics)
        return []
    except Exception:
        logger.warning("Failed to read metrics cache from DB.", exc_info=True)
        return []


def _save_metrics_cache(stock_data: List[Dict]) -> None:
    """
    Persist computed stock metrics. Writes to the DB cache when persistence is
    enabled, and ALWAYS writes the JSON file too (preserved for local dev and
    as an offline fallback).
    """
    if _db_available():
        _save_metrics_cache_db(stock_data)
    try:
        with open(METRICS_CACHE_PATH, "w") as f:
            json.dump({"metrics": stock_data}, f, indent=2)
        logger.info("Saved metrics cache (%d tickers) to %s.", len(stock_data), METRICS_CACHE_PATH)
    except Exception:
        logger.warning("Failed to write metrics cache.", exc_info=True)


def _load_metrics_cache() -> List[Dict]:
    """
    Load previously cached stock metrics. Prefers the DB cache (if persistence
    is enabled and populated), then falls back to the JSON file.
    """
    if _db_available():
        db_metrics = _load_metrics_cache_db()
        if db_metrics:
            return db_metrics
    if not os.path.exists(METRICS_CACHE_PATH):
        return []
    try:
        with open(METRICS_CACHE_PATH, "r") as f:
            data = json.load(f)
        metrics = data.get("metrics", [])
        logger.info("Loaded metrics cache (%d tickers) from %s.", len(metrics), METRICS_CACHE_PATH)
        return metrics
    except Exception:
        logger.warning("Failed to read metrics cache.", exc_info=True)
        return []


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _calculate_future_value(
    monthly_payment: float,
    annual_return_pct: float,
    total_months: int,
    step_up_percent: float = 0.0,
) -> float:
    """
    Future value of a monthly SIP, optionally with a yearly step-up.

    With ``step_up_percent == 0`` (default) this is the standard
    future-value-of-an-annuity:

        FV = P * [((1 + r)^n - 1) / r],  r = annual_return_pct/100/12, n = months.

    With a step-up, the monthly contribution increases by ``step_up_percent``
    at the start of each new year (every 12 months). We accumulate year by
    year: each year's 12 equal monthly deposits compound within the year, and
    the running balance compounds forward into subsequent years. This models a
    "step-up SIP" where you raise your monthly amount annually (e.g. with pay
    rises), which materially improves goal attainment for the same start amount.
    """
    monthly_rate = annual_return_pct / 100.0 / 12.0
    step = step_up_percent / 100.0

    # Fast path: no step-up -> the closed-form annuity (unchanged behaviour).
    if step == 0.0:
        if monthly_rate == 0:
            return monthly_payment * total_months
        return monthly_payment * (((1 + monthly_rate) ** total_months - 1) / monthly_rate)

    balance = 0.0
    payment = monthly_payment
    months_left = total_months
    year = 0
    while months_left > 0:
        if year > 0:
            payment *= (1 + step)  # escalate at each year boundary
        m = min(12, months_left)
        # Compound the existing balance through this year's m months, and add
        # this year's m monthly deposits (each compounding to year-end).
        if monthly_rate == 0:
            balance = balance + payment * m
        else:
            balance = balance * ((1 + monthly_rate) ** m)
            balance += payment * (((1 + monthly_rate) ** m - 1) / monthly_rate)
        months_left -= m
        year += 1
    return balance


def _required_annual_return(
    target_fund: float,
    monthly_payment: float,
    total_months: int,
    step_up_percent: float = 0.0,
) -> float:
    """
    Invert the FV-of-annuity formula to find the annual return needed to
    reach *target_fund* given a monthly SIP of *monthly_payment* over
    *total_months*, optionally escalating by *step_up_percent* each year.

    Uses scipy-free bisection search over the range [0%, 200%].
    """
    if monthly_payment <= 0 or total_months <= 0:
        return 0.0

    # Trivial case: can we reach the target with 0% return? (Step-up still
    # grows total contributions, so compute the 0%-return FV with step-up.)
    if _calculate_future_value(monthly_payment, 0.0, total_months, step_up_percent) >= target_fund:
        return 0.0

    lo, hi = 0.0, 200.0  # annual return % search bounds
    for _ in range(100):  # bisection iterations (converges to ~1e-30)
        mid = (lo + hi) / 2.0
        fv = _calculate_future_value(monthly_payment, mid, total_months, step_up_percent)
        if fv < target_fund:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _filter_by_risk_category(
    df: pd.DataFrame,
    risk_category: str,
) -> pd.DataFrame:
    """
    Filter stocks by risk category using overlapping volatility bands tuned
    for real-world annualised volatility (equities ~15-50%, bonds ~1-5%).

    Categories (by annualised volatility):
    - low:    volatility <= 15%   (bonds, liquid funds, large-cap indices)
    - medium: 12% < volatility <= 30%  (equities, REITs, broad ETFs)
    - high:   volatility > 25%   (growth stocks, thematic/leveraged ETFs)

    The intentional overlap between adjacent bands ensures borderline assets
    appear in two categories, giving the optimizer more candidates.
    """
    vol = df["Volatility (%)"]
    if risk_category == "low":
        return df[vol <= 15].copy()
    elif risk_category == "medium":
        return df[(vol > 12) & (vol <= 30)].copy()
    elif risk_category == "high":
        return df[vol > 25].copy()
    return df.copy()


# --------------------------------------------------------------------------- #
# Stock data fetching & LSTM prediction
# --------------------------------------------------------------------------- #

def fetch_stock_data(tickers: List[str]) -> pd.DataFrame:
    """
    Return per-ticker metrics (Annual Return, Volatility, Beta, Sharpe, Risk
    Profile) as a DataFrame.

    Phase 2 read path:
      1. If the model/metrics STORE has a current version, serve it directly
         (fast, deterministic, NO market-data network call and NO LSTM training
         in the request path). This is the production path.
      2. Otherwise fall back to the legacy live path below (yfinance + per-ticker
         LSTM), which also refreshes the metrics cache. This keeps local/dev and
         un-provisioned environments working exactly as before.

    Returns:
        DataFrame with one row per ticker.  Columns:
        Stock Name, Asset Class, Annual Return (%), Volatility (%), Beta,
        Sharpe Ratio, Risk Profile.
    """
    # ---- Phase 2: store-backed fast path ----
    try:
        from jobs.store import current_metrics_dataframe

        store_df = current_metrics_dataframe()
        if store_df is not None and not store_df.empty:
            logger.info(
                "Serving %d tickers from the model/metrics store (no network/training).",
                len(store_df),
            )
            return store_df
    except Exception:
        logger.debug("Store read unavailable; falling back to live path.", exc_info=True)

    # ---- Legacy live path (yfinance + LSTM); also used as fallback ----
    stock_data: List[Dict] = []

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5y")
        except Exception:
            logger.warning("yfinance fetch failed for ticker '%s'; skipping.", ticker)
            continue

        if hist.empty:
            logger.info("No historical data for ticker '%s'; skipping.", ticker)
            continue

        try:
            model_path = os.path.join(model_dir, f"{ticker}_lstm.pkl")
            scaler_path = os.path.join(model_dir, f"{ticker}_scaler.pkl")

            if os.path.exists(model_path) and os.path.exists(scaler_path):
                model = joblib.load(model_path)
                scaler = joblib.load(scaler_path)
                logger.debug("Loaded cached model for '%s'.", ticker)
            else:
                data = hist[["Close"]].values
                if len(data) < 120:
                    # Need at least 60 lookback + 60 training samples.
                    logger.warning(
                        "Insufficient data for '%s' (%d rows); skipping.",
                        ticker, len(data),
                    )
                    continue

                scaler = MinMaxScaler(feature_range=(0, 1))
                data_scaled = scaler.fit_transform(data)

                X_train = []
                y_train = []
                for i in range(60, len(data_scaled)):
                    X_train.append(data_scaled[i - 60 : i, 0])
                    y_train.append(data_scaled[i, 0])
                X_train, y_train = np.array(X_train), np.array(y_train)
                X_train = np.reshape(X_train, (X_train.shape[0], X_train.shape[1], 1))

                model = Sequential()
                model.add(
                    LSTM(units=50, return_sequences=True, input_shape=(X_train.shape[1], 1))
                )
                model.add(LSTM(units=50))
                model.add(Dense(1))
                model.compile(optimizer="adam", loss="mean_squared_error")
                model.fit(
                    X_train, y_train,
                    epochs=LSTM_EPOCHS,
                    batch_size=32,
                    verbose=0,
                )

                joblib.dump(model, model_path)
                joblib.dump(scaler, scaler_path)
                logger.info(
                    "Trained and saved LSTM model for '%s' (%d epochs).",
                    ticker, LSTM_EPOCHS,
                )

            # ---- Risk metrics from REAL historical prices ----
            # Volatility and the historical return baseline must come from the
            # actual observed price series.  The LSTM predictions are heavily
            # smoothed (they track a trend line), so computing volatility from
            # them produces unrealistically low values (~3-6% vs the true
            # ~15-50% for equities) and collapses every asset into "Low" risk.
            real_returns = hist["Close"].pct_change().dropna()
            if real_returns.empty or len(real_returns) < 2:
                logger.warning("Not enough price history for '%s'; skipping.", ticker)
                continue

            historical_annual_return = float(real_returns.mean() * 252 * 100)
            volatility = float(real_returns.std() * np.sqrt(252) * 100)

            # ---- Forward-looking signal from the LSTM ----
            # Use the model to project the next price and derive an implied
            # forward return, then blend it with the historical return so the
            # "expected return" reflects both the trend model and realised data.
            lstm_annual_return = historical_annual_return  # fallback
            try:
                data = scaler.transform(hist[["Close"]])
                X_test = []
                for i in range(60, len(data)):
                    X_test.append(data[i - 60 : i, 0])
                if X_test:
                    X_test = np.array(X_test)
                    X_test = np.reshape(X_test, (X_test.shape[0], X_test.shape[1], 1))
                    predicted_scaled = model.predict(X_test, verbose=0)
                    predicted = scaler.inverse_transform(predicted_scaled).flatten()

                    # Implied daily returns from the predicted trajectory.
                    pred_series = pd.Series(predicted)
                    pred_changes = pred_series.pct_change().dropna()
                    if not pred_changes.empty:
                        lstm_annual_return = float(pred_changes.mean() * 252 * 100)
            except Exception:
                logger.debug("LSTM forward signal failed for '%s'; using historical.", ticker)

            # Blend: 60% realised history, 40% LSTM forward signal.  The LSTM
            # often over-smooths magnitude, so it informs rather than dominates.
            annual_return = 0.6 * historical_annual_return + 0.4 * lstm_annual_return

            try:
                beta = stock.info.get("beta", 1)
                if beta is None:
                    beta = 1.0
                beta = float(beta)
            except Exception:
                beta = 1.0

            # Sharpe Ratio = (return - risk_free_rate) / volatility
            excess_return = annual_return - RISK_FREE_RATE
            sharpe_ratio = excess_return / volatility if volatility > 0 else 0.0

            risk_profile = "Low" if volatility < 10 else "Medium" if volatility < 20 else "High"

            stock_data.append(
                {
                    "Stock Name": ticker,
                    "Asset Class": TICKER_ASSET_CLASS.get(ticker, "Other"),
                    "Annual Return (%)": round(annual_return, 2),
                    "Volatility (%)": round(volatility, 2),
                    "Beta": round(beta, 2),
                    "Sharpe Ratio": round(sharpe_ratio, 2),
                    "Risk Profile": risk_profile,
                }
            )
            logger.debug(
                "Processed '%s': return=%.2f%%, vol=%.2f%%, sharpe=%.2f",
                ticker,
                annual_return,
                volatility,
                sharpe_ratio,
            )
        except Exception:
            logger.exception("Unexpected error processing ticker '%s'; skipping.", ticker)
            continue

    logger.info("Fetched data for %d / %d tickers.", len(stock_data), len(tickers))

    if stock_data:
        # Live fetch succeeded (at least partially) — refresh the cache.
        _save_metrics_cache(stock_data)
        return pd.DataFrame(stock_data)

    # Live fetch returned nothing (e.g. Yahoo rate-limiting / outage).
    # Fall back to the last successfully cached metrics so the app stays usable.
    logger.warning(
        "Live fetch returned no data for any ticker; attempting metrics cache fallback."
    )
    cached = _load_metrics_cache()
    if cached:
        logger.info("Serving %d tickers from cached metrics (offline fallback).", len(cached))
        return pd.DataFrame(cached)

    logger.error("No live data and no cached metrics available.")
    return pd.DataFrame(stock_data)


# --------------------------------------------------------------------------- #
# Investment suggestion engine
# --------------------------------------------------------------------------- #

def suggest_investment(
    df: pd.DataFrame,
    years: int,
    target_fund: float,
    monthly_investment: float,
    risk_category: str,
    step_up_percent: float = 0.0,
) -> Dict:
    """
    Suggest a diversified investment allocation based on LSTM-predicted stock
    metrics.

    Improvements over the original logic:
    1. Uses contiguous risk-category volatility bands (no gaps).
    2. Inverts the FV-of-annuity formula to find the required return.
    3. Ranks by Sharpe Ratio (not Sharpe/Volatility) to avoid double-
       penalising volatility.
    4. Ensures diversification: if fewer than MIN_PORTFOLIO_SIZE stocks meet
       the required return, pad with the best remaining Sharpe-ranked stocks.
    5. Monte Carlo correctly samples annual returns and converts to monthly
       compounding rates.

    Returns:
        Dict with allocation details on success, or ``{"error": "..."}`` on
        domain-level failure.
    """
    df = df.copy()

    # ---- filter by risk category ----
    risk_category = risk_category.strip().lower()
    if risk_category not in ("low", "medium", "high"):
        logger.warning("Invalid risk category: '%s'.", risk_category)
        return {"error": "Invalid risk category selected. Choose low, medium, or high."}

    df = _filter_by_risk_category(df, risk_category)

    if df.empty:
        logger.info("No stocks match the '%s' risk category.", risk_category)
        return {"error": "No stocks available in the selected risk category."}

    # ---- rank by Sharpe Ratio (highest first) ----
    df = df.sort_values(by="Sharpe Ratio", ascending=False).reset_index(drop=True)

    n: int = years * 12  # total months
    P: float = monthly_investment

    # ---- required annual return via proper FV-of-annuity inversion ----
    req_return = _required_annual_return(target_fund, P, n, step_up_percent)
    logger.debug("Required annual return to reach target: %.2f%%.", req_return)

    # ---- select stocks that meet the return threshold ----
    meeting_threshold: List[Tuple[str, float, str]] = []
    below_threshold: List[Tuple[str, float, str]] = []

    for _, row in df.iterrows():
        entry = (row["Stock Name"], row["Annual Return (%)"], row["Risk Profile"])
        if row["Annual Return (%)"] >= req_return:
            meeting_threshold.append(entry)
        else:
            below_threshold.append(entry)

    # Build allocation: stocks that meet threshold first, then pad with
    # best Sharpe stocks for diversification (up to MAX_PORTFOLIO_SIZE).
    allocation: List[Tuple[str, float, str]] = meeting_threshold[:MAX_PORTFOLIO_SIZE]

    # Ensure minimum diversification: pad from remaining best-Sharpe stocks.
    if len(allocation) < MIN_PORTFOLIO_SIZE:
        needed = MIN_PORTFOLIO_SIZE - len(allocation)
        already = {s for s, _, _ in allocation}
        for stock, ret, risk in below_threshold:
            if stock not in already:
                allocation.append((stock, ret, risk))
                needed -= 1
            if needed <= 0:
                break

    # Cap at MAX_PORTFOLIO_SIZE.
    allocation = allocation[:MAX_PORTFOLIO_SIZE]

    # ---- fallback: completely unattainable goal ----
    if not allocation:
        best = df.iloc[0]
        best_return = best["Annual Return (%)"]
        future_value = _calculate_future_value(P, best_return, n, step_up_percent)
        logger.info(
            "Goal unreachable. Best stock '%s' yields %.2f INR vs target %.2f.",
            best["Stock Name"], future_value, target_fund,
        )
        return {
            "error": (
                f"It's not possible to achieve your retirement goal. "
                f"The maximum possible future value with the best available stock "
                f"({best['Stock Name']}) is {future_value:,.2f} INR."
            )
        }

    # ---- compute per-stock future values ----
    future_values: List[Tuple[str, float, str, float]] = []
    # Total contributions. With a step-up, each year's monthly amount escalates,
    # so sum the per-year contributions rather than P*n.
    total_invested = _calculate_future_value(P, 0.0, n, step_up_percent)
    for stock, return_rate, risk in allocation:
        fv = _calculate_future_value(P, return_rate, n, step_up_percent)
        future_values.append((stock, return_rate, risk, fv))

    # ---- allocation weights by Sharpe Ratio ----
    # Use Sharpe Ratio directly as the weight.  Stocks with negative Sharpe
    # get a small positive floor so they still receive some allocation.
    allocated_names = [s for s, _, _ in allocation]
    sharpe_values = df.loc[
        df["Stock Name"].isin(allocated_names)
    ].set_index("Stock Name")["Sharpe Ratio"]

    # Floor negative Sharpes to a small positive value.
    raw_weights = sharpe_values.clip(lower=0.01)
    total_weight = raw_weights.sum()
    if total_weight == 0:
        total_weight = 1.0

    investment_percentages: List[Tuple[str, float]] = [
        (stock, round((raw_weights.get(stock, 0.01) / total_weight) * 100, 2))
        for stock, _, _ in allocation
    ]

    # Normalise to exactly 100%.
    total_pct = sum(p for _, p in investment_percentages)
    if total_pct > 0 and abs(total_pct - 100.0) > 0.01:
        investment_percentages = [
            (stock, round(p / total_pct * 100, 2))
            for stock, p in investment_percentages
        ]

    # Weighted future value.
    total_future_value = 0.0
    for stock, pct in investment_percentages:
        for f_stock, _, _, fv in future_values:
            if stock == f_stock:
                total_future_value += fv * (pct / 100)
                break

    total_return_pct = (
        ((total_future_value - total_invested) / total_invested) * 100
        if total_invested > 0 else 0.0
    )
    avg_annual_return_pct = total_return_pct / years if years > 0 else 0.0

    # ---- Vectorised Monte Carlo simulation ----
    n_stocks = len(investment_percentages)

    means = np.array(
        [df.loc[df["Stock Name"] == s, "Annual Return (%)"].values[0]
         for s, _ in investment_percentages]
    )
    stds = np.array(
        [df.loc[df["Stock Name"] == s, "Volatility (%)"].values[0]
         for s, _ in investment_percentages]
    )
    weights = np.array([pct / 100.0 for _, pct in investment_percentages])

    # Sample annual returns (%), shape: (MC_SIMULATIONS, n_stocks).
    sim_annual_returns = np.random.normal(
        loc=means, scale=stds, size=(MC_SIMULATIONS, n_stocks),
    )

    # Convert annual % to monthly rate for FV calculation.
    # annual_rate = sim_annual_returns / 100
    # monthly_rate = (1 + annual_rate)^(1/12) - 1
    # This properly converts annual compounding to monthly compounding.
    annual_rate = sim_annual_returns / 100.0
    # Clip to avoid negative (1+rate) which can't be raised to fractional power.
    annual_rate_safe = np.clip(annual_rate, -0.99, None)
    monthly_rate = (1.0 + annual_rate_safe) ** (1.0 / 12.0) - 1.0

    # Avoid exact zero monthly rate (would cause division by zero in FV formula).
    monthly_rate = np.where(monthly_rate == 0, 1e-12, monthly_rate)

    if step_up_percent and step_up_percent != 0.0:
        # Step-up SIP: contributions escalate by step_up_percent each year.
        # Accumulate year by year across the whole simulation matrix.
        step = step_up_percent / 100.0
        fv_matrix = np.zeros_like(monthly_rate)
        payment_factor = 1.0  # P * payment_factor is the current monthly amount
        months_left = n
        year = 0
        while months_left > 0:
            if year > 0:
                payment_factor *= (1 + step)
            m = min(12, months_left)
            growth = (1 + monthly_rate) ** m
            fv_matrix = fv_matrix * growth
            fv_matrix += P * payment_factor * ((growth - 1) / monthly_rate)
            months_left -= m
            year += 1
    else:
        # FV of annuity: P * [((1+r)^n - 1) / r]
        fv_matrix = P * (((1 + monthly_rate) ** n - 1) / monthly_rate)

    # Weight each stock's FV by allocation %, sum across stocks.
    final_values = (fv_matrix * weights).sum(axis=1)  # (MC_SIMULATIONS,)

    risk_value_at_risk = float(np.percentile(final_values, 5))
    risk_var_pct = (
        ((risk_value_at_risk - total_invested) / total_invested) * 100
        if total_invested > 0 else 0.0
    )
    average_value = float(np.mean(final_values))
    p90_value = float(np.percentile(final_values, 90))

    # ---- Build response ----
    # Fast lookup for asset class (may be absent in older cached data).
    if "Asset Class" in df.columns:
        asset_class_map = dict(zip(df["Stock Name"], df["Asset Class"]))
    else:
        asset_class_map = {}

    investment_suggestions = []
    for stock, pct in investment_percentages:
        for f_stock, return_rate, risk, fv in future_values:
            if stock == f_stock:
                investment_suggestions.append(
                    {
                        "Stock Name": str(stock),
                        "Asset Class": str(asset_class_map.get(stock, "Other")),
                        "Investment Percentage": round(float(pct), 2),
                        "Annual Return (%)": round(float(return_rate), 2),
                        "Risk Profile": str(risk),
                        "Future Value (INR)": round(float(fv), 2),
                    }
                )
                break

    logger.info(
        "Strategy computed: %d stocks, total_fv=%.2f, VaR_5=%.2f, P90=%.2f.",
        len(investment_suggestions),
        total_future_value,
        risk_value_at_risk,
        p90_value,
    )

    return {
        "Total Future Value (INR)": round(float(total_future_value), 2),
        "Investment Suggestions": investment_suggestions,
        "Average Annual Return (%)": round(float(avg_annual_return_pct), 2),
        "Value at Risk (5th percentile) (INR)": round(float(risk_value_at_risk), 2),
        "Value at Risk (5th percentile) (%)": round(float(risk_var_pct), 2),
        "Average Value from Simulation (INR)": round(float(average_value), 2),
        "Optimistic Value (90th percentile) (INR)": round(float(p90_value), 2),
        "Goal Achievable": bool(total_future_value >= target_fund),
    }
