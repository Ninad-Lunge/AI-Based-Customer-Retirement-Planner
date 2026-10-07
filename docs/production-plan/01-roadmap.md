# 01 — Phased Roadmap

Five phases, **Phase 0 → Phase 4**. Each phase is **independently shippable**: at the end of
each, the product still runs and delivers value. Phase 0 establishes foundations (tests, CI,
containers, config/secrets, observability) **before** any feature expansion. Effort is given in
rough engineer-weeks (EW) for a small team (1–2 backend, 1 frontend, part-time quant); treat as
sequencing guidance, not a commitment.

**Legend:** DoD = Definition of Done. "Not in scope" is stated explicitly per phase.

---

## Phase 0 — Foundations (harden what exists)

**Goal:** Make the current prototype safe to change and deploy repeatedly, without altering
behaviour or methodology yet.

**Scope**
- **Tests**: unit tests for the pure finance functions (`_calculate_future_value`,
  `_required_annual_return`, `_filter_by_risk_category`, Sharpe, Monte Carlo stats) using
  synthetic DataFrames (no network). Contract test for `POST /investment-strategy` happy path +
  each validation error, mocking `fetch_stock_data`. Frontend: smoke test of `PlanInput` submit
  and `InvestmentResult` render. Target: finance core ≥80% line coverage.
- **CI**: GitHub Actions (or equivalent) — lint (ruff/flake8 + eslint), run backend pytest and
  frontend tests, build the React bundle, build Docker images. Block merge on failure.
- **Containerization**: `Dockerfile` for backend (pin CPython 3.12 base for TensorFlow),
  `Dockerfile` for frontend (multi-stage: build CRA → serve static via nginx), `docker-compose.yml`
  wiring both + a Postgres service (unused until Phase 1, present for parity).
- **Config/secrets**: formalise the existing env-var config into a typed settings object
  (pydantic-settings). Document every variable. No secrets in the repo; `.env.example` only.
- **Observability**: structured JSON logs (already partly there), a `/health` (exists) plus a
  `/ready` readiness probe, request-id middleware, and basic latency/error counters exposed for
  scraping (Prometheus text format) or at minimum timing logs. Capture engine timing
  (fetch vs optimize vs simulate).
- **Repo hygiene**: remove stale `__pycache__` for Python 3.10/3.14 and the `investment_strategy_2`
  artefact from version control; add `.dockerignore`; pin frontend deps.

**Deliverables:** test suite + CI pipeline green; images build and run via compose; settings module;
readiness probe; ops README.

**Dependencies:** none (operates on current code).

**Effort:** ~2–3 EW.

**DoD**
- `docker compose up` starts both services; `/health` and `/ready` return 200.
- CI runs on PR and blocks on lint/test/build failure.
- Finance-core unit tests pass with ≥80% coverage; one contract test per API error path.
- No secrets committed; all config documented.

**Not in scope:** auth, database persistence, new inputs, engine redesign, data-provider change.

---

## Phase 1 — Persistence & identity (stateful product)

**Goal:** Introduce a database and optional accounts so users can save profiles and plans.
Keep anonymous use working.

**Scope**
- **Database**: PostgreSQL. Schema per `03-data-model.md` (users, financial_profile,
  preferences, saved_plans, instruments, model_metrics_cache). Migrations via Alembic.
- **Auth**: optional accounts. Email+password or OAuth (recommend OAuth via a managed provider
  to avoid storing passwords — see ADR-006). Anonymous users still get recommendations;
  signing in enables **save plan / load plan / profile reuse**. JWT (short-lived) + refresh.
- **Persistence endpoints** (new, versioned under `/v1`): create/read/update profile,
  save/list/get plans. The existing recommendation endpoint remains callable anonymously.
- **Move metrics cache into the DB** (`model_metrics_cache` table) instead of a loose JSON file,
  while keeping the JSON fallback for local dev.

**Deliverables:** Postgres schema + migrations; auth flow; profile and saved-plan CRUD;
DB-backed metrics cache.

**Dependencies:** Phase 0 (containers, config, CI).

**Effort:** ~3–4 EW.

**DoD**
- A user can register/sign in, save a plan, sign out, sign back in, and reload it.
- Anonymous `POST /v1/investment-strategy` still returns a recommendation with no account.
- All tables created via migration from empty DB; rollback tested.
- PII columns identified and encrypted-at-rest per `03-data-model.md`.

**Not in scope:** engine redesign, new preference inputs beyond storing them, data-provider change.

---

## Phase 2 — Offline training/analytics pipeline & model store

**Goal:** Remove all slow/stateful work from the request path. Metrics, covariance inputs, and
optional LSTM views are computed on a schedule and served from a store.

**Scope**
- **Ingestion job**: scheduled (daily after market close; cron/Airflow/managed scheduler) pull
  of OHLCV history for the whole universe from the chosen data provider (see Phase 3 /
  build-vs-buy). Writes raw/clean price series to storage.
- **Analytics job**: compute per-instrument expected return, volatility, beta, Sharpe, and the
  **covariance matrix** (with Ledoit–Wolf shrinkage) across the universe; version and persist to
  the model/metrics store with a `computed_at` + `data_window` stamp.
- **LSTM retraining job** (optional path): retrain per-eligible-instrument LSTM views on a slower
  cadence (weekly), persist to the model store with versions. **Never** trained on request.
- **Model store**: versioned artefacts (metrics table rows + covariance blob + optional LSTM
  view vector), with a "current" pointer the API reads. Keep N previous versions for audit.
- **Request path change**: `fetch_stock_data()` is replaced by a fast **read** of the current
  metrics/covariance from the store. yfinance stays only as a dev/fallback source.

**Deliverables:** two (optionally three) scheduled jobs; versioned model/metrics store;
request path reads precomputed data; cold-start < 1s.

**Dependencies:** Phase 1 (DB), Phase 0 (containers/CI/observability).

**Effort:** ~4–5 EW.

**DoD**
- A scheduled run populates metrics + covariance for the full universe and flips the "current"
  pointer atomically.
- API recommendation uses store data only; p95 latency for a recommendation < 1s (excluding
  Monte Carlo tuning) with no network calls to a market-data provider in the hot path.
- Last-good version is retained and auditable; a failed job does not corrupt "current".

**Not in scope:** the MPT optimizer itself (Phase 3), new user inputs (Phase 3), compliance docs.

---

## Phase 3 — Engine redesign (MPT + backtesting core) & expanded inputs

**Goal:** Replace Sharpe-weighted selection with a defensible **MPT optimizer**, add
**backtesting** so recommendations are evidence-backed, demote the LSTM to an **optional view**,
and expand the user profile with the new **optional** inputs — all behind the versioned API.

**Scope**
- **MPT optimizer** (`05-engine-design.md`): mean-variance optimization over the universe using
  precomputed expected returns + Ledoit–Wolf covariance. Support **max-Sharpe**, **min-variance**,
  and **risk-parity** objectives; pick objective from risk category (or explicit request field).
- **Constraint mapping**: translate the new optional preferences into optimizer constraints/tilts
  — sector tilts, asset-class caps, exclusions (hard constraints), existing-holdings
  anti-concentration, liquidity floors, ESG/Shariah universe filters.
- **Backtesting**: walk-forward / rolling-window backtest of the optimized weights; report CAGR,
  max drawdown, Sharpe, Sortino, volatility, and rolling performance. Ship these in the response.
- **LSTM as a Black–Litterman view**: optional tilt into the expected-return vector; off by
  default and controllable via a request flag + global config.
- **Monte Carlo**: keep, but drive it from the **optimized portfolio's** aggregate return/vol
  (using the covariance matrix, not independent per-asset normals).
- **Expanded inputs** (all optional, graceful defaults): sectors of interest, asset-class
  preferences, ESG/ethical/Shariah filters, exclusions, existing holdings, income/dependents/
  savings/debt/emergency-fund, experience level, liquidity needs, currency/country. See
  `03-data-model.md` and `04-api-contract.md` for how each maps (filter / constraint / tilt /
  explanation modifier).
- **Explanation layer**: template-based, experience-level-aware rationale ("why these assets,
  why these weights, what the backtest shows").

**Deliverables:** optimizer service, constraint mapper, backtest module, BL-view integration,
expanded request/response (v1, additive), explanation generator.

**Dependencies:** Phase 2 (precomputed metrics + covariance in the store), Phase 1 (store prefs).

**Effort:** ~6–8 EW (largest phase; quant-heavy).

**DoD**
- Given precomputed inputs, the optimizer returns weights for max-Sharpe, min-variance, and
  risk-parity, honouring exclusions and caps (verified by tests with synthetic covariances).
- Response includes backtest metrics (CAGR, max drawdown, Sharpe, Sortino, vol, rolling series)
  and a human-readable explanation.
- Every new input is optional; omitting all of them reproduces a sensible default portfolio and
  the legacy-compatible response fields are still present.
- LSTM view toggles on/off and measurably changes the expected-return vector only when on.

**Not in scope:** real-money execution/brokerage, tax optimization, multi-currency FX modelling
beyond labelling (flag in open questions).

---

## Phase 4 — Trust, compliance & go-to-market hardening

**Goal:** Make the product defensible to ship to retail users: positioning, disclaimers,
licensed data, auditability, and the operational polish for scale.

**Scope**
- **Positioning & disclaimers** (`06-risk-compliance-trust.md`): explicit "educational/
  illustrative, not regulated financial advice" framing in UI and API responses; mandatory
  disclaimer block in every recommendation payload.
- **Data licensing**: migrate the hot path to a **licensed market-data provider** (build-vs-buy
  in `02-architecture.md`); retire yfinance from production. Record data-source + license in the
  audit trail.
- **Auditability**: persist every recommendation (input snapshot, model/metrics versions,
  optimizer config, output, disclaimer version) to an append-only audit log for reproducibility.
- **Rate limiting, abuse protection, and PII controls** finalised; data-retention and deletion
  (right-to-be-forgotten) workflow.
- **Legal review gate**: package the positioning + disclaimers for human/legal and
  registered-adviser review (this plan flags; it does not provide legal advice).
- **Scale/ops**: horizontal scaling of the stateless API, cache warm paths, dashboards + alerts
  on data-staleness, job failures, and error rates.

**Deliverables:** disclaimer system, licensed-data integration, audit log, retention/deletion
workflow, legal-review package, production dashboards/alerts.

**Dependencies:** Phase 3 (recommendations to audit), Phase 2 (data pipeline to relicense),
Phase 1 (user data to govern).

**Effort:** ~4–6 EW + external legal review (not an engineering estimate).

**DoD**
- Every recommendation is reproducible from the audit log (same inputs + versions → same output).
- No production request path depends on yfinance; data source is licensed and recorded.
- Disclaimers present in UI and payload; retention/deletion workflow demonstrated.
- Legal-review package delivered to stakeholders; open compliance items tracked.

**Not in scope:** actual legal sign-off (external), brokerage integration, becoming a registered
adviser (business decision — flagged).

---

## Sequencing summary

```
Phase 0  Foundations ............ tests, CI, Docker, config, observability   (~2-3 EW)
   │
Phase 1  Persistence & identity .. Postgres, auth, profiles, saved plans      (~3-4 EW)
   │
Phase 2  Offline pipeline ........ scheduled metrics/covariance/LSTM store    (~4-5 EW)
   │
Phase 3  Engine redesign ......... MPT + backtest + BL-view + new inputs      (~6-8 EW)
   │
Phase 4  Trust & GTM ............. disclaimers, licensed data, audit, scale   (~4-6 EW + legal)
```

Each arrow is a hard dependency. Phases 0–2 are "the product keeps working but gets safer and
faster"; Phase 3 is where the credible methodology and richer inputs land; Phase 4 is what makes
it sellable to real users.
