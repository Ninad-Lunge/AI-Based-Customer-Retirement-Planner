# 07 — Decision Log (ADRs)

Concise, ADR-style records of the key architectural decisions in this plan: the context, the
decision, the alternatives considered, and the consequences. Status is **Proposed** for all
(this is a planning document; nothing is built yet).

---

## ADR-001 — Replace yfinance with a licensed market-data provider for production

- **Status:** Proposed
- **Context:** The prototype uses `yfinance==1.7.0`, which scrapes Yahoo Finance under
  personal-use terms. Shipping a paid product on it is a licensing risk and operationally fragile
  (rate limits, undocumented breakage).
- **Decision:** Procure a commercial market-data license for the production serving path; keep
  yfinance only for local dev and emergency fallback, flagged non-production.
- **Alternatives:** (a) Stay on yfinance — rejected: licensing + reliability. (b) Scrape multiple
  free sources — rejected: same licensing issue, more fragility.
- **Consequences:** Recurring cost; vendor selection needed (`08`); but removes the single biggest
  go-to-market blocker and improves reliability. Data source recorded in the audit log.

---

## ADR-002 — Keep Flask for now; FastAPI is an optional later migration

- **Status:** Proposed
- **Context:** The backend is Flask with hand-rolled validation and a clean error shape.
- **Decision:** Keep Flask through Phases 0–2 to avoid churn; introduce typed request/response
  models via pydantic. Consider migrating to FastAPI during/after Phase 3 if async I/O, built-in
  schema validation, and OpenAPI generation justify it.
- **Alternatives:** (a) Rewrite to FastAPI in Phase 0 — rejected: premature, risks regressions
  before tests exist. (b) Never migrate — acceptable; Flask is fine. 
- **Consequences:** Lower short-term risk; a possible later migration cost. Validation gets typed
  regardless of framework.

---

## ADR-003 — MPT (mean-variance) as the core; LSTM demoted to an optional view

- **Status:** Proposed
- **Context:** The prototype's LSTM-per-ticker is slow (~40s cold), trained in the request path,
  and not a defensible "engine" for a sellable product.
- **Decision:** Make convex MPT optimization (max-Sharpe / min-variance / risk-parity) the core,
  with Ledoit–Wolf shrinkage covariance. The LSTM becomes an optional Black–Litterman view, off by
  default.
- **Alternatives:** (a) Keep LSTM as the driver — rejected: indefensible, slow, weak signal.
  (b) Pure heuristic Sharpe weighting (today's approach) — rejected: not a recognised methodology.
  (c) Factor model (e.g. Fama–French) — deferred: more data/complexity than needed for v1.
- **Consequences:** Credible, explainable, fast core; LSTM retained as a differentiator without
  betting the product on it. Requires a quant-heavy Phase 3.

---

## ADR-004 — Black–Litterman to combine the LSTM view, replacing the fixed 60/40 blend

- **Status:** Proposed
- **Context:** Current code blends historical and LSTM returns 60/40 with no confidence or
  covariance awareness.
- **Decision:** Use Black–Litterman: historical `μ`/`Σ` as the prior, LSTM outputs as views with
  confidence-derived uncertainty, producing a posterior `μ_BL` the optimizer consumes.
- **Alternatives:** (a) Keep 60/40 — rejected: arbitrary, ignores confidence/correlation.
  (b) Just add LSTM return to `μ` — rejected: unbounded, can wreck weights.
- **Consequences:** Principled, bounded influence that degrades to the prior as confidence → 0;
  more implementation complexity, mitigated by `PyPortfolioOpt`'s BL support.

---

## ADR-005 — Offline training/analytics pipeline + versioned model/metrics store

- **Status:** Proposed
- **Context:** Metrics (and LSTM) are computed/trained in the request path today; cold start ~40s.
- **Decision:** Compute metrics + Ledoit–Wolf covariance (+ optional LSTM views) on a schedule,
  publish versioned artefacts, and have the API read the "current" version. No training/fetch in
  the hot path.
- **Alternatives:** (a) Cache-on-first-request — rejected: still slow for the unlucky first user,
  and non-deterministic. (b) Train on deploy only — rejected: data goes stale between deploys.
- **Consequences:** Fast, deterministic, auditable request path; adds scheduler + store ops.
  Atomic "current" pointer + last-good retention protect against bad runs.

---

## ADR-006 — Managed-IdP OAuth; do not store passwords

- **Status:** Proposed
- **Context:** No auth today; adding accounts introduces credential-handling risk.
- **Decision:** Use a managed identity provider (OAuth) with short-lived JWTs; allow anonymous use
  of the recommendation endpoint. If a password option is ever required, store only Argon2id
  hashes in a dedicated table.
- **Alternatives:** (a) Roll our own email+password — rejected: breach/PII surface, must build
  reset/verify/MFA. (b) No accounts ever — rejected: can't offer saved plans/profiles.
- **Consequences:** Lower security burden, faster delivery of save/load features; a dependency on
  an IdP vendor (choice in `08`).

---

## ADR-007 — Keep CRA now; migrate the frontend to Vite later

- **Status:** Proposed
- **Context:** Frontend is Create React App (`react-scripts`), which is effectively unmaintained.
- **Decision:** Keep CRA through the early phases to avoid churn; migrate to Vite when touching the
  frontend substantially for the new rich-input UI (Phase 3).
- **Alternatives:** (a) Migrate in Phase 0 — rejected: no user value yet, risk before tests.
  (b) Stay on CRA indefinitely — rejected: build tooling will rot.
- **Consequences:** Defers churn to when the UI is being rewritten anyway; interim reliance on CRA.

---

## ADR-008 — Universe graduates from `tickers.json` to an `instruments` table

- **Status:** Proposed
- **Context:** The universe lives in `tickers.json` (asset-class groups) with a `reload_universe()`
  hook. The new filters need per-instrument metadata (sector, ESG, Shariah, liquidity, country).
- **Decision:** Move the universe into an `instruments` DB table carrying that metadata;
  `tickers.json` remains the seed/bootstrap source.
- **Alternatives:** (a) Extend `tickers.json` with nested metadata — rejected: no querying, hard to
  keep in sync with metrics. (b) Hardcode metadata in Python — rejected: the prototype already
  avoided that.
- **Consequences:** Queryable, joinable universe that drives filters and metrics; a migration/seed
  step. Keeps the "edit config without code" ergonomics via the seed file + an admin path.

---

## ADR-009 — Backward compatibility via URI versioning + legacy alias

- **Status:** Proposed
- **Context:** The current React client posts to `/investment-strategy` and reads specific
  response key names.
- **Decision:** Serve `/v1/investment-strategy` and keep `/investment-strategy` as a permanent
  alias; preserve all legacy response keys; make new inputs optional and new outputs additive.
- **Alternatives:** (a) Break and update the client in lockstep — rejected: fragile, no graceful
  rollout. (b) Header/query versioning — rejected: URI versioning is simpler and cache-friendly.
- **Consequences:** Zero-change path for the existing client; clean room for `/v2` when a breaking
  change is truly needed.

---

## ADR-010 — Portfolio-level Monte Carlo using the covariance matrix

- **Status:** Proposed
- **Context:** Today's Monte Carlo samples each asset independently and sums, ignoring correlation.
- **Decision:** Simulate the optimized portfolio's aggregate return/vol (`μ_p = wᵀμ`,
  `σ_p = √(wᵀΣw)`), so correlation is respected; keep the existing annual→monthly conversion.
- **Alternatives:** (a) Keep independent per-asset sampling — rejected: overstates diversification.
  (b) Full multivariate simulation of all assets — unnecessary for a corpus projection and slower.
- **Consequences:** Correct and faster projection; depends on the covariance artefact from the
  offline job. Normal-return limitation noted (`05`), with fat-tail options flagged (`08`).
