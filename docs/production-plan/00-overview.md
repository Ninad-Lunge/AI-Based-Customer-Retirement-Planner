# AI-Based Customer Retirement Planner — Production Evolution Plan

**Status:** Planning (design-only). No application code is changed by this set of documents.
**Audience:** Engineering, product, and the stakeholder/sponsor who must resolve the open questions.
**Scope of this document set:** How to take the existing prototype to a production-grade,
sellable product, with a credible quantitative core (MPT + backtesting), an expanded but
backward-compatible user profile, and the operational foundations (tests, CI, containers,
persistence, observability, compliance) that are currently absent.

---

## How to read these documents

| # | Document | What it answers |
|---|----------|-----------------|
| 00 | `00-overview.md` (this file) | What exists today, the gap, and how the docs fit together. |
| 01 | `01-roadmap.md` | The phased plan (Phase 0 → 4), each independently shippable, with definition of done. |
| 02 | `02-architecture.md` | Target components, ASCII diagram, request/data flow, build-vs-buy. |
| 03 | `03-data-model.md` | Schemas for users, profiles, preferences, saved plans, instrument metadata, model cache; PII handling. |
| 04 | `04-api-contract.md` | The evolved endpoint(s): request/response schemas, versioning, errors, migration from the current shape. |
| 05 | `05-engine-design.md` | MPT + backtesting methodology, LSTM-as-a-view, Monte Carlo, output contract, limitations. |
| 06 | `06-risk-compliance-trust.md` | Positioning, disclaimers, data licensing, auditability, what needs legal/registered-adviser review. |
| 07 | `07-decision-log.md` | ADR-style record of key decisions and alternatives. |
| 08 | `08-open-questions.md` | Ambiguities for the stakeholder, each with a recommended default. |

---

## Current state (verified against the source, 2026-10)

Read directly from the repository; this is ground truth, not assumption.

### Backend — `flask-server/`
- **`server.py`** — one business endpoint `POST /investment-strategy` plus `GET /health`.
  Config is environment-driven (`FLASK_DEBUG`, `FLASK_PORT`, `FLASK_HOST`, `CORS_ORIGINS`,
  `LOG_LEVEL`). Request validation is manual and strict: `currentAge`, `retirementAge`,
  `desiredFund`, `monthlyInvestment`, `riskCategory` are all **required**; errors return a
  consistent `{"error", "status", "details?}` shape with codes 400/422/500/502/503.
- **`investment_strategy.py`** — the engine:
  - `fetch_stock_data()` pulls **5y** daily history per ticker via `yfinance==1.7.0`.
  - Per-ticker **LSTM** (Keras/TensorFlow, 2× LSTM(50) + Dense(1), 60-day lookback,
    `LSTM_EPOCHS=10`) is trained on first use and persisted to `saved_models/<ticker>_lstm.pkl`
    (+ `_scaler.pkl`). **Models are trained in the request path on cold start.**
  - Return and volatility are computed from **real** historical prices (`pct_change`,
    annualised ×252 / ×√252). The LSTM contributes a **forward return signal** blended
    60% historical / 40% LSTM into "expected return". Volatility is historical only.
  - Sharpe = `(annual_return − RISK_FREE_RATE) / volatility`, `RISK_FREE_RATE=7.0%`.
  - Risk filter: volatility bands (low ≤15%, medium 12–30%, high >25%, intentionally overlapping).
  - Allocation: rank by Sharpe, select names meeting the FV-of-annuity **required return**,
    pad to `MIN_PORTFOLIO_SIZE=3`, cap at `MAX_PORTFOLIO_SIZE=10`, weight by clipped Sharpe.
  - **Vectorised Monte Carlo** (`MC_SIMULATIONS=10000`) on the chosen allocation produces
    P5/P90/mean corpus (fed by each asset's return/vol, normal sampling, annual→monthly rate).
  - **Metrics cache fallback**: after any successful fetch, metrics are written to
    `saved_models/_cached_metrics.json`; if Yahoo is unreachable, the app serves from cache.
- **`tickers.json`** — the investment universe grouped by asset class (Indian Equity, Growth,
  ETF/MF, REIT, Gold, Bonds/Fixed Income, International ETF, Global Index). `reload_universe()`
  hook exists to refresh without a restart.
- **`requirements.txt`** — pinned; TensorFlow 2.17 constrains runtime to **CPython 3.9–3.12**.

### Frontend — `customer_retirement_planner/` (React CRA)
- Routes `/` (hero) and `/Plan` (form + results). No image assets (CSS + inline SVG).
- `PlanInput.jsx` posts the raw `formData` object (the 5 fields, camelCase) via `axios` to
  `${REACT_APP_API_URL}/investment-strategy`. Client-side validation mirrors the server.
- `InvestmentResult.jsx` reads a response with these exact keys: `Investment Suggestions[]`
  (each `Stock Name`, `Asset Class`, `Investment Percentage`, `Annual Return (%)`,
  `Risk Profile`, `Future Value (INR)`), plus `Total Future Value (INR)`,
  `Average Annual Return (%)`, `Value at Risk (5th percentile) (INR)` / `(%)`,
  `Average Value from Simulation (INR)`, `Optimistic Value (90th percentile) (INR)`,
  `Goal Achievable`, and an `error` branch. **These names are the backward-compat contract.**

### Tooling — `run.sh`
- Orchestrates a Python venv (auto-selects a 3.9–3.12 interpreter for TensorFlow),
  installs deps, writes `.env.local`, starts Flask + CRA, health-checks `/health`.

### Known gaps (the reason this plan exists)
1. **No auth, no database, no persistence** — fully stateless; profiles/plans are never saved.
2. **No tests, no CI, no containerization.**
3. **LSTM-per-ticker trained in the request path** — ~40s cold start, and not a defensible
   methodology to sell as "the engine" to retail users.
4. **Market data via `yfinance`** — Yahoo's terms are personal-use; not licensed for a product.
5. **No formal compliance posture** — allocation guidance to retail users with only an inline
   one-line disclaimer.

---

## Target end-state (one paragraph)

A versioned HTTP API (`/v1/...`) behind auth, backed by a database for user profiles and saved
plans, and an **offline training/analytics pipeline** that refreshes instrument metrics,
covariance inputs, and the optional LSTM return-views on a schedule into a **model/metrics
store**. The request path becomes fast and deterministic: it reads precomputed metrics, runs a
**Modern Portfolio Theory optimizer** (max-Sharpe / min-variance / risk-parity) subject to
constraints derived from the user's optional preferences, **backtests** the resulting weights
over historical windows, runs the existing **Monte Carlo** projection, and returns an
allocation + evidence (backtest metrics) + projection + a plain-language explanation. The LSTM
survives only as an **optional Black–Litterman-style view** that tilts expected returns and can
be switched off. The product is positioned as **educational/illustrative**, with disclaimers,
licensed data, and an **audit trail** of every recommendation (inputs, model versions, outputs).

---

## Guiding principles

- **Backward compatibility first.** The 5-field flow and the current response key names keep
  working. New inputs are **optional with graceful defaults**; new output fields are additive.
- **Defensible over clever.** MPT + backtesting is the credible core; ML is a supplementary,
  optional signal, never the decision-maker.
- **No training in the request path.** Anything slow or stateful is precomputed offline.
- **Evidence with every recommendation.** Backtest metrics and assumptions ship in the response.
- **Standard, maintainable tech.** Justify any non-obvious choice; note cost and ops burden.
- **Compliance is a feature, not an afterthought.** Positioning, disclaimers, and auditability
  are designed in Phase 0/4, not retrofitted.
