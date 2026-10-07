# Prompt — Production-Grade Evolution Plan (Design Only)

> Paste the section below to an agent working in this repository. It asks for a
> **phased roadmap and architecture design**, not an implementation. The agent
> should produce planning artifacts (documents, diagrams-as-text, API/schema
> specs, decision records) and must NOT write application code or modify
> existing source files unless explicitly asked in a later turn.

---

## ROLE

You are a senior product architect and quantitative-finance engineer. You are
planning the evolution of an existing prototype — the **AI-Based Customer
Retirement Planner** — into a production-grade product. In THIS task you
**design and plan only**: deliver a phased roadmap, target architecture, data
model, API contracts, and a recommendation-engine redesign. Do **not** modify
or write application code. Output is planning documentation.

## CURRENT STATE (ground truth — read these before planning)

- **Backend**: Flask (`flask-server/`)
  - `server.py` — single `POST /investment-strategy` endpoint + `/health`, env-driven config, structured errors, logging.
  - `investment_strategy.py` — fetches 5y history via `yfinance==1.7.0`, trains/loads a **per-ticker LSTM** (keras/tensorflow), derives return/volatility/Sharpe from REAL historical prices (LSTM contributes a blended forward signal), filters by risk band, allocates by Sharpe weight, runs a vectorized Monte Carlo, returns a JSON strategy. Has a metrics cache fallback (`saved_models/_cached_metrics.json`).
  - `tickers.json` — externalized investment universe grouped by asset class (layer-1 config; `reload_universe()` hook exists).
  - `requirements.txt` — pinned deps (TensorFlow needs CPython 3.9–3.12).
- **Frontend**: React CRA (`customer_retirement_planner/`)
  - Routes: `/` (hero), `/Plan` (form + results). Modern self-contained UI (no image assets; CSS + inline SVG).
  - `PlanInput.jsx` collects: currentAge, retirementAge, desiredFund, monthlyInvestment, riskCategory. `InvestmentResult.jsx` renders goal banner, stat cards, allocation bar, breakdown table.
  - API URL via `REACT_APP_API_URL`.
- **Tooling**: `run.sh` orchestrates venv + both servers.
- **Known gaps**: no auth, no database (stateless), no persistence of user profiles, no tests/CI, no containerization, LSTM-per-ticker is slow (~40s cold) and not a defensible methodology for a sellable product.

## PRODUCT DIRECTION (decisions already made)

1. **Expanded, optional user inputs.** Add a richer user profile. ALL new fields
   must be **optional** with sensible defaults, and the engine must degrade
   gracefully when they are absent. Design for at least:
   - Areas of interest / preferred **sectors** (tech, pharma, green energy, banking, real estate, FMCG, etc.)
   - **Asset-class preferences** (equities, mutual funds/ETFs, gold, bonds, REITs, international, crypto)
   - **ESG / ethical / Shariah-compliant** filters
   - **Exclusions** (avoid specific sectors/tickers — e.g. tobacco, fossil fuels)
   - **Existing holdings** (recommend complements, avoid over-concentration)
   - Financial profile: **income, dependents, existing savings, debt/EMIs, emergency-fund status**
   - **Investment experience level** (beginner → expert) — drives explanation depth
   - **Liquidity needs / near-term goals**
   - **Currency / country of residence**
   Design how each input influences the engine (as a filter, a constraint, a tilt,
   or an explanation modifier). Keep the base flow (the 5 original fields) working
   for users who provide nothing else.

2. **Recommendation engine redesign → MPT + backtesting as the credible core.**
   Replace LSTM-as-the-primary-engine with a standard, defensible quant stack:
   - **Modern Portfolio Theory optimization** (mean-variance / efficient frontier;
     consider max-Sharpe and min-variance portfolios, and risk-parity as an option).
     Specify how expected returns and the covariance matrix are estimated
     (e.g. historical, shrinkage estimators like Ledoit-Wolf), and how user
     constraints (sector tilts, exclusions, asset-class caps, existing holdings,
     liquidity) map to optimizer constraints.
   - **Backtesting** of the recommended allocation over historical windows, with
     reported metrics (CAGR, max drawdown, Sharpe/Sortino, volatility,
     rolling-window performance) so recommendations are evidence-backed.
   - **LSTM becomes a supplementary signal** (an optional return-view input / tilt
     into the MPT expected-return vector), not the decision-maker. Explain how to
     combine it (e.g. Black-Litterman-style views) and how to turn it off.
   - Keep the **Monte Carlo projection** for the goal/corpus forecast, fed by the
     optimized portfolio's return/vol.
   - Address the **offline training/model-store** concern: ML/stat models should be
     trained on a schedule and served from a store, not trained in the request path.

## WHAT TO PRODUCE (planning artifacts only)

Deliver a cohesive set of design documents (markdown is fine). At minimum:

1. **Phased roadmap** — 3–5 phases from current prototype to production, each with:
   goals, scope, deliverables, dependencies, rough effort/sequence, and explicit
   "definition of done." Sequence so each phase is independently shippable.
   Phase 0 should cover foundations (tests, CI, containerization, config/secrets,
   observability) before feature expansion.

2. **Target architecture** — components and how they connect: API layer, auth,
   persistence (user profiles, saved plans, model/metrics store), the data-ingestion
   + offline training pipeline, the optimization/backtesting service, caching,
   and the frontend. Include a text/ASCII component diagram and the request/data flow.
   Note build-vs-buy tradeoffs (e.g. market-data provider vs. yfinance for production,
   given Yahoo's personal-use terms).

3. **Data model** — schemas for user, financial profile, preferences (the optional
   inputs above), saved plans/recommendations, instrument/universe metadata, and
   cached model outputs. Call out PII and how it's protected.

4. **API contract** — the evolved endpoint(s): request schema with all optional
   fields and defaults, response schema (allocation + backtest metrics + projection
   + explanation), versioning strategy, and error model. Keep backward compatibility
   with the current `/investment-strategy` shape or define a clean migration.

5. **Engine design** — the MPT + backtesting methodology in enough detail to
   implement: inputs, estimators, objective(s), constraint mapping from user
   preferences, how the LSTM view is incorporated, backtest protocol, and the
   output contract. Include assumptions and their limitations.

6. **Risk, compliance & trust** — because this gives allocation guidance to retail
   users, explicitly address positioning (educational/illustrative vs. regulated
   advice), required disclaimers, data-source licensing, and auditability of
   recommendations. Flag anything that would need a registered adviser or legal
   review (do NOT give legal advice — flag for human/legal review).

7. **Decision log** — key architectural decisions with the alternatives considered
   and why (ADR-style, concise).

8. **Open questions & assumptions** — anything ambiguous, listed for the stakeholder
   to resolve, with your recommended default for each.

## CONSTRAINTS

- **Design only. Do not write or modify application code** in this task. Planning
  documents and specs only. If you believe a code change is warranted, propose it
  in the roadmap for a later phase rather than making it now.
- Ground every recommendation in the actual current state above; read the real
  files to verify before asserting how something works.
- Prefer standard, well-understood, maintainable technologies. Justify any
  non-obvious choice. Note tradeoffs honestly, including cost and operational burden.
- Keep all newly proposed user inputs **optional with graceful defaults**.
- Be explicit about what is NOT in scope for each phase.

## STYLE

Be concrete and specific — a reader should be able to start building Phase 0 from
your document without further clarification. Use tables for schemas/contracts,
short prose for rationale, and ASCII for diagrams. Avoid hand-waving ("add AI",
"make it scalable") — state the actual mechanism.
