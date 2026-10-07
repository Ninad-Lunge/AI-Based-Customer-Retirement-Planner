# 02 — Target Architecture

Describes the production component set, how they connect, the request/data flows, and the
build-vs-buy decisions. The design keeps the current two-tier (React + Flask) shape and adds the
missing production tiers: auth, persistence, an offline pipeline, a model/metrics store, and
caching. The hot path becomes a fast, deterministic read + compute with **no model training and
no third-party market-data call** inside the request.

---

## Components

| Component | Responsibility | Tech (recommended) | Notes |
|-----------|----------------|--------------------|-------|
| **Web client** | Form (5 base + optional fields), results, saved plans | React (keep CRA now; migrate to Vite later — ADR-007) | Reads API via `REACT_APP_API_URL`. |
| **API gateway / reverse proxy** | TLS, routing, rate limiting, CORS | nginx or managed ALB/API Gateway | Terminates TLS; forwards to API. |
| **Recommendation API** | Validate → build constraints → optimize → backtest → simulate → explain → respond | Flask (keep) or FastAPI (ADR-002) | **Stateless, horizontally scalable.** No training here. |
| **Auth service / module** | Optional accounts, tokens, sessions | OAuth via managed IdP + JWT (ADR-006) | Anonymous use still allowed. |
| **Primary database** | Users, financial profile, preferences, saved plans, instrument metadata, audit log | PostgreSQL | Alembic migrations. |
| **Model / metrics store** | Versioned per-instrument metrics + covariance matrix + optional LSTM view vectors | Postgres tables + object storage (S3/GCS) for the covariance/LSTM blobs | "current" pointer read by API. |
| **Cache** | Hot metrics/covariance, computed recommendations, session data | Redis | TTL-based; optional in Phase 2, needed by Phase 4 for scale. |
| **Ingestion job** | Scheduled pull of OHLCV for the universe | Python job + scheduler (cron → Airflow/managed) | Writes clean price series to storage. |
| **Analytics job** | Compute returns, vol, beta, Sharpe, Ledoit–Wolf covariance; version + publish | Python (numpy/pandas/`PyPortfolioOpt` or `cvxpy`) | Flips "current" pointer atomically. |
| **LSTM training job** | Retrain optional per-instrument return views | Python/Keras (TensorFlow) on a 3.9–3.12 runtime | Slower cadence (weekly); off-path. |
| **Market-data provider** | Source of OHLCV + reference data | **Licensed provider in prod** (ADR-001); yfinance only for dev/fallback | See build-vs-buy. |
| **Observability stack** | Logs, metrics, traces, alerts | Structured JSON logs + Prometheus/Grafana or managed APM | Data-staleness + job-failure alerts. |
| **Secrets manager** | DB creds, provider API keys, JWT signing keys | Cloud secrets manager | No secrets in repo/images. |

---

## Component diagram (ASCII)

```
                              ┌──────────────────────────────────────────────────────┐
                              │                     USERS (browser)                   │
                              └───────────────────────────┬──────────────────────────┘
                                                          │ HTTPS
                                            ┌─────────────▼─────────────┐
                                            │   API gateway / reverse   │  TLS, rate-limit,
                                            │     proxy (nginx/ALB)     │  CORS, routing
                                            └─────────────┬─────────────┘
                             ┌───────────────────────────┼───────────────────────────┐
                             │                            │                           │
                   ┌─────────▼─────────┐        ┌─────────▼──────────┐       ┌─────────▼─────────┐
                   │  Web client (SPA) │        │ Recommendation API │       │   Auth module     │
                   │  React static     │◄──────►│  (Flask/FastAPI)   │◄─────►│ OAuth IdP + JWT   │
                   │  (served by nginx)│  JSON  │  STATELESS         │       └─────────┬─────────┘
                   └───────────────────┘        │  validate→build    │                 │
                                                │  constraints→MPT   │                 │
                                                │  optimize→backtest │                 │
                                                │  →Monte Carlo→     │                 │
                                                │  explain→respond   │                 │
                                                └───┬───────────┬────┘                 │
                                                    │ read      │ read/write           │ read/write
                                   ┌────────────────▼───┐   ┌───▼────────────────────────▼───────────┐
                                   │  Model/metrics     │   │        PostgreSQL (primary DB)          │
                                   │  store             │   │  users · financial_profile · prefs      │
                                   │  ┌───────────────┐ │   │  saved_plans · instruments · audit_log  │
                                   │  │ metrics (rows)│ │   │  model_metrics_cache (pointer)          │
                                   │  │ covariance    │ │   └─────────────────────────────────────────┘
                                   │  │  blob (S3/GCS)│ │                      ▲
                                   │  │ LSTM views    │ │                      │ "current" pointer
                                   │  └───────────────┘ │                      │
                                   └─────────▲──────────┘                      │
                                             │ publish (versioned, atomic flip)│
            OFFLINE (scheduled, NOT in request path)                          │
      ┌──────────────────┐   ┌───────────────────────┐   ┌─────────────────────────────┐
      │ Ingestion job    │──►│ Analytics job         │──►│ LSTM training job (optional)│
      │ pull OHLCV for   │   │ returns, vol, beta,   │   │ retrain per-instrument      │
      │ full universe    │   │ Sharpe, Ledoit-Wolf   │   │ return-view vectors         │
      └────────┬─────────┘   │ covariance; version   │   └─────────────────────────────┘
               │             └───────────────────────┘
               │ pull
      ┌────────▼───────────────────────┐
      │ Market-data provider           │   licensed in prod (yfinance = dev/fallback only)
      └────────────────────────────────┘

      Cross-cutting:  Redis cache · Secrets manager · Observability (logs/metrics/traces/alerts)
```

---

## Request/data flow — recommendation (hot path, Phase 3+)

```
1. Client POSTs /v1/investment-strategy  (5 base fields required; optional profile fields)
2. Gateway: TLS, rate-limit, CORS, attach request-id; (optional) validate JWT if present
3. API: validate + coerce payload (base fields strict; optional fields defaulted if absent)
4. API: load CURRENT metrics + covariance from the model/metrics store (DB rows + blob),
        and the active universe (instruments table). NO provider call, NO training.
5. API: build the eligible universe:
        - apply ESG/Shariah/exclusion FILTERS  (removes instruments)
        - apply asset-class / sector availability
6. API: build optimizer CONSTRAINTS from preferences
        - asset-class caps, sector tilts, existing-holdings anti-concentration, liquidity floors
7. API: (optional) apply LSTM Black-Litterman VIEW to the expected-return vector if enabled
8. API: run MPT optimizer (objective from risk category / explicit field) → weights
9. API: BACKTEST the weights over historical windows → CAGR, maxDD, Sharpe, Sortino, vol, rolling
10.API: MONTE CARLO projection from the optimized portfolio's aggregate return/vol (via covariance)
11.API: generate EXPLANATION (experience-level aware, template-based)
12.API: persist AUDIT record (inputs snapshot, versions, config, output, disclaimer version)
13.API: respond with allocation + backtest + projection + explanation + disclaimer
        (legacy-compatible fields preserved; new fields additive)
14.(If authenticated and user chose) persist as a saved_plan
```

**Latency budget (target p95, excluding first-ever JIT warmup):** steps 4–11 < 1s. Monte Carlo
(10k sims, vectorised) and the optimizer dominate; both are pure NumPy/convex solves. Cache the
optimizer result keyed by (universe-version, constraints-hash) to make repeat/identical requests
near-instant.

---

## Data flow — offline pipeline

```
Scheduler (daily, post-close)
   └─► Ingestion job: pull OHLCV for every active instrument from the licensed provider
        └─► store clean price series (object storage / price table), stamped with data_window
             └─► Analytics job: compute per-instrument return, vol, beta, Sharpe;
                  compute Ledoit-Wolf shrunk covariance across the universe;
                  write a NEW versioned metrics set + covariance blob;
                  atomically flip "current" pointer; retain last N versions
                   └─► (weekly) LSTM training job: retrain return-view vectors, version + publish

Failure handling: a failed job leaves the previous "current" intact; alert on staleness.
```

---

## Build vs buy

| Decision | Build | Buy / managed | Recommendation |
|----------|-------|---------------|----------------|
| **Market data** | Keep yfinance | Licensed provider (e.g. a paid market-data API with ToS allowing commercial redistribution of derived analytics) | **Buy for production.** Yahoo/yfinance terms are personal-use; shipping a paid product on it is a licensing and reliability risk (rate limits, undocumented breakage). Keep yfinance strictly for local dev and as an emergency fallback. Exact vendor is an open question (`08`), decided on coverage of NSE/BSE + global instruments and cost. |
| **Auth / identity** | Hand-rolled email+password | Managed IdP (OAuth: Auth0/Cognito/Firebase/Google) | **Buy/managed.** Avoid storing passwords; get MFA, verification, and reset flows for free. Lower PII/breach surface. |
| **Optimizer math** | Write mean-variance from scratch with `cvxpy` | `PyPortfolioOpt` (MIT) wrapping cvxpy | **Hybrid.** Use `PyPortfolioOpt` for efficient-frontier/max-Sharpe/min-variance and Ledoit–Wolf; drop to raw `cvxpy` only for constraints it can't express. Standard, well-tested, maintainable. |
| **Backtesting** | Minimal rolling-window loop in NumPy/pandas | A full framework (e.g. `vectorbt`, `bt`) | **Build minimal first.** Our backtest is a weight-replay over historical returns with standard metrics — a few hundred lines, fully controllable and auditable. Adopt a framework only if requirements grow (transaction costs, rebalancing rules). |
| **Scheduler** | cron in a container | Managed (Airflow/MWAA, Cloud Scheduler, EventBridge) | **Start with cron**, graduate to a managed scheduler when jobs multiply or need retries/alerting. |
| **Cache** | In-process dict | Redis | **Redis** once there is more than one API replica (in-process cache doesn't share across replicas). |
| **Hosting** | VM + manual | Containers on managed orchestration (ECS/Fargate, Cloud Run, or K8s) | **Managed containers.** The stateless API scales horizontally; the offline jobs run as scheduled tasks. Keep the TensorFlow runtime isolated to the LSTM job image (pin CPython 3.12). |

**Operational-burden notes:**
- TensorFlow pins the runtime to CPython 3.9–3.12 and bloats images. **Isolate it in the LSTM
  job image only.** The API and analytics jobs should not import TensorFlow, so the hot path and
  the core analytics stay on a lean, version-flexible runtime.
- Covariance + optimization scale with universe size `O(N²)` memory / `O(N³)` solve. The current
  universe (~90 instruments) is trivial; document a soft cap (e.g. a few hundred) before needing
  factor-model dimensionality reduction.
