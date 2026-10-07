# 05 — Engine Design (MPT + Backtesting core)

This is the methodology that replaces the current Sharpe-weighted selection with a defensible
quant stack: **Modern Portfolio Theory optimization**, **backtesting** for evidence, the **LSTM
demoted to an optional Black–Litterman view**, and the existing **Monte Carlo** projection driven
by the optimized portfolio. It is specified in enough detail to implement, with assumptions and
limitations stated honestly.

What carries over from today (verified in `investment_strategy.py`): return/vol from real daily
`pct_change` annualised (×252, ×√252), Sharpe with a configurable `RISK_FREE_RATE` (7%), the
FV-of-annuity math (`_calculate_future_value`, `_required_annual_return`), and the vectorised
Monte Carlo. What changes: **selection + weighting becomes convex optimization**, inputs come from
the **offline store** (not request-time training/fetch), the LSTM is a view not the driver, and
**backtest metrics ship with every recommendation**.

---

## 1. Inputs (all precomputed offline — see `02`/`03`)

| Input | Symbol | Source | Notes |
|-------|--------|--------|-------|
| Eligible universe | `U` | `instruments` filtered by preferences | After ESG/Shariah/exclusion/asset-class filters. |
| Expected annual returns | `μ` (vector) | `instrument_metrics.expected_annual_return_pct` | Historical mean; optionally BL-adjusted. |
| Covariance matrix | `Σ` (matrix) | `covariance_artifacts` (Ledoit–Wolf) | Ordered by `ticker_order`. |
| Risk-free rate | `r_f` | config (`RISK_FREE_RATE`, 7%) | Used for Sharpe + BL. |
| LSTM views (optional) | `Q`, `Ω` | `lstm_views` | View returns + confidence → BL. |
| User constraints | — | `preferences` / request | Caps, exclusions, tilts, liquidity, holdings. |
| SIP parameters | `P`, `n`, target | request | Monthly amount, months, `desiredFund`. |

### Estimators (stated explicitly)
- **Expected returns `μ`**: historical annualised mean of daily returns over the data window
  (same as today). Document the known weakness — mean-historical returns are noisy estimators;
  this is why min-variance and risk-parity objectives (which don't rely on `μ`) are offered, and
  why BL views are optional.
- **Covariance `Σ`**: **Ledoit–Wolf shrinkage** toward a structured target, not the raw sample
  covariance. Raw sample covariance is ill-conditioned when `N` approaches the number of
  observations and produces unstable, extreme optimizer weights. Shrinkage is the standard fix and
  is available directly in `PyPortfolioOpt` / scikit-learn. Computed offline across the whole
  universe, stored as a blob with its `ticker_order`.

---

## 2. Objectives (choose one per request)

| Objective | Problem | Uses `μ`? | When |
|-----------|---------|-----------|------|
| **Max-Sharpe** (tangency) | maximize `(wᵀμ − r_f) / √(wᵀΣw)` | yes | Default for `medium`/`high` risk; growth seekers. |
| **Min-variance** | minimize `wᵀΣw` | no | Default for `low` risk; `μ`-robust (ignores noisy returns). |
| **Risk-parity** | choose `w` so each asset contributes equal risk: `wᵢ(Σw)ᵢ` equal ∀ i | no | Diversification-first; offered as an option. |

**Objective selection:** from `objective` request field if present; else derived from
`riskCategory` (`low → min_variance`, `medium → max_sharpe`, `high → max_sharpe` with a higher
equity cap). This preserves the existing "risk category drives the result" behaviour while making
the mechanism principled.

All objectives are solved as convex programs via `PyPortfolioOpt` (which wraps `cvxpy`), dropping
to raw `cvxpy` only for constraints `PyPortfolioOpt` can't express.

---

## 3. Constraint mapping (user preferences → optimizer)

Every optional preference maps to one of four mechanisms. **Absent preference → no constraint.**

| Preference | Mechanism | Formalisation |
|------------|-----------|---------------|
| `excludedTickers`, `excludedSectors` | **Hard filter** (pre-optimization) | Remove from `U` before building `μ`, `Σ`. |
| `esgOnly`, `shariahOnly`, `ethicalFlags`, `allowCrypto`, `allowInternational` | **Hard filter** | Restrict `U` by `instruments` flags. |
| `assetClassPrefs` caps | **Linear inequality** | For class `c`: `L_c ≤ Σ_{i∈c} wᵢ ≤ H_c`. |
| `preferredSectors` | **Soft tilt** | Add a small bonus to `μ` for preferred-sector assets (e.g. +`τ`%, τ small and disclosed), OR a lower-bound nudge; never a hard requirement. |
| `existingHoldings` | **Anti-concentration** | For held ticker `i` with existing weight `hᵢ` (value / (portfolio+SIP context)): cap new weight `wᵢ ≤ max(0, cap − hᵢ)`. |
| `liquidityNeedHorizonMonths` short | **Liquidity floor** | Require `Σ_{i: liquidity_tier=1} wᵢ ≥ floor` (e.g. ≥ a configurable % in very-liquid instruments). |
| `emergencyFundMonths` < 3 | **Risk-capacity tilt + warning** | Shift objective toward min-variance / raise liquid floor; emit a `warnings[]` entry. Does not block. |
| `dependents`, `monthlyDebtEmi`, `annualIncome` | **Risk-capacity + explanation** | Inform a bounded reduction in max equity cap; always explained; never silently overrides the user's `riskCategory`. |

**Baseline constraints always applied:** long-only (`wᵢ ≥ 0`), fully invested (`Σwᵢ = 1`), and a
per-asset max weight (e.g. `wᵢ ≤ 0.35`) to prevent degenerate single-asset portfolios — the
production analogue of today's `MIN_PORTFOLIO_SIZE`/`MAX_PORTFOLIO_SIZE` diversification intent.

**Infeasibility handling:** if constraints make the problem infeasible or empty the universe,
return **422** with a specific message ("relax X") rather than a silent fallback. (Contrast with
today's silent padding; here we are explicit so the user understands the trade-off.)

---

## 4. LSTM as a Black–Litterman view (optional, off by default)

The LSTM stops being the engine and becomes a **view** on expected returns, combined with the
market/historical prior via Black–Litterman. This is the standard way to blend a subjective or
model-derived signal into MPT without letting it dominate.

- **Prior**: `μ` (historical) with covariance `Σ`.
- **Views**: from `lstm_views` — each eligible ticker gets an absolute view `Q_i = view_return_pct`
  with uncertainty derived from `confidence` (higher confidence → smaller view variance in `Ω`).
- **Posterior**: BL formula produces an adjusted expected-return vector `μ_BL`; the optimizer then
  runs on `μ_BL` instead of `μ`.
- **Toggle**: controlled by `options.applyLstmView` (request) and a global config kill-switch. When
  off, `μ_BL = μ` exactly and `lstmViewApplied=false` in the response.
- **Why BL, not the 60/40 blend used today**: the current code blends LSTM and historical returns
  with fixed weights (0.6/0.4) with no notion of confidence or covariance structure. BL is the
  principled generalisation: it weights the view by its stated confidence and by the covariance,
  and it degrades gracefully to the prior when confidence → 0.

**Honest limitation:** the LSTM's forward signal is weakly predictive at best (price prediction is
notoriously hard); BL is used precisely so a weak/overconfident view cannot wreck the portfolio.
It is a tilt and a differentiator, not a source of alpha we vouch for. This is stated to users via
the explanation + disclaimer when the view is on.

---

## 5. Backtesting protocol (evidence for the recommendation)

Goal: show how the recommended weights would have behaved historically, with standard metrics.

- **Method**: weight-replay backtest. Take the optimized weights `w`, apply them to historical
  (out-of-estimation where possible) daily returns, compute the portfolio return series.
- **Windows**:
  - **Full-window** performance over the available history.
  - **Walk-forward** (preferred, to reduce look-ahead bias): re-estimate `μ`/`Σ` on a rolling
    training window, optimize, hold for a step, roll forward; stitch the out-of-sample segments.
    Document the training/holding window lengths (e.g. 3y train / 3-month hold).
  - **Rolling-window** cumulative-return series for the response `backtest.rolling[]`.
- **Metrics reported** (match the API contract):
  - **CAGR**, **max drawdown**, **annualised volatility**, **Sharpe** (`r_f` consistent with the
    engine), **Sortino** (downside-deviation denominator).
- **Rebalancing & costs**: v1 assumes periodic rebalancing to target weights with **no transaction
  costs** (stated assumption). Transaction-cost modelling is a later enhancement (`08`).
- **Caveat shipped with results**: "past performance is not indicative of future results" — this is
  both a disclaimer requirement (`06`) and literally true of a backtest.

---

## 6. Monte Carlo projection (kept, improved)

Retain the goal/corpus projection, but drive it from the **optimized portfolio**, using the
covariance structure rather than independent per-asset normals.

- **Portfolio moments**: `μ_p = wᵀμ` (or `wᵀμ_BL`), `σ_p = √(wᵀΣw)` — note this uses `Σ`, so
  cross-asset correlation is respected (an improvement over today's per-asset independent sampling).
- **Simulation**: sample annual portfolio returns `~ Normal(μ_p, σ_p)` (or a fatter-tailed /
  bootstrap option as an enhancement), convert annual → monthly compounding exactly as today
  (`(1+a)^(1/12) − 1`, with the same `−0.99` clip for safety), and run the FV-of-annuity over `n`
  months for `monteCarloSims` paths (default 10000, bounded 1k–50k).
- **Outputs**: `p5`, `p50`, `p90`, `mean`, and `goalProbability` = fraction of paths with final
  value ≥ `desiredFund`. These populate both the new `projection{}` object and the legacy
  `Value at Risk (5th percentile)`, `Optimistic Value (90th percentile)`, `Average Value from
  Simulation`, `Total Future Value`, and `Goal Achievable` fields.

**Why portfolio-level, not per-asset like today:** sampling each asset independently and summing
ignores correlation and overstates diversification benefit. Sampling the portfolio's aggregate
`(μ_p, σ_p)` from the covariance matrix is both faster and correct.

---

## 7. Output contract (what the engine returns)

The engine returns a structure that the API layer maps to `04-api-contract.md`:
- `weights` (raw), and the legacy-shaped `Investment Suggestions` (per-asset %, expected return,
  risk profile, per-asset FV).
- `backtest` metrics (CAGR, maxDrawdown, Sharpe, Sortino, volatility, window, rolling series).
- `projection` (p5/p50/p90/mean/goalProbability) + legacy VaR/optimistic/average/total/achievable.
- `objective`, `lstmViewApplied`, `metricsVersion`, `appliedConstraints`.
- `explanation` (see below).

---

## 8. Explanation layer (experience-level aware)

Template-based, deterministic (no LLM required for v1). Inputs: objective, applied constraints,
backtest metrics, warnings. Depth scales with `experienceLevel`:
- **beginner**: plain-language summary + 2–3 bullets ("diversified mix", "capped risky assets").
- **intermediate**: adds objective name + key backtest numbers.
- **advanced/expert**: adds optimizer objective, constraints, shrinkage method, BL-view status.

Always include `warnings[]` for low emergency fund, over-restrictive filters that were relaxed,
or an unreachable goal.

---

## 9. Assumptions & limitations (state them in the product)

1. **Historical estimators are noisy**; expected returns especially. Mitigations: shrinkage
   covariance, min-variance/risk-parity options, BL for views, per-asset weight caps.
2. **Normal-return Monte Carlo** understates tail risk (real returns are fat-tailed/skewed).
   Enhancement: Student-t or historical bootstrap (`08`).
3. **No transaction costs / taxes / slippage** in v1 backtest and projection.
4. **No FX modelling** for multi-currency portfolios beyond labelling; mixing currencies without
   FX risk modelling is a simplification (`08`).
5. **Backtest ≠ future.** Walk-forward reduces but does not eliminate overfitting/look-ahead.
6. **Universe is finite and curated** (`instruments`); results are only as good as its coverage.
7. **LSTM views are weak signals**, used only as bounded BL tilts, off by default.

All of these are surfaced to users via the explanation/disclaimer so recommendations are honest
about their own uncertainty.
