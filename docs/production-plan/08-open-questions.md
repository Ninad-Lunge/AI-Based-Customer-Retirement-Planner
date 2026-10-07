# 08 — Open Questions & Assumptions

Items that need a stakeholder decision, each with a **recommended default** so engineering can
proceed if no explicit answer is given. Grouped by area. "Default if unanswered" is what we will
assume unless told otherwise.

---

## Product & scope

| # | Question | Recommended default |
|---|----------|---------------------|
| P1 | Primary market(s) at launch — India only, or India + global? | **India-first** (NSE/BSE focus), global instruments available but secondary. Drives data-provider choice and compliance scope. |
| P2 | Should `desiredFund` become optional (a "just project my SIP" mode)? | **Keep required in v1**; add an optional-target mode in `/v2`. |
| P3 | Anonymous use retained long-term, or accounts required? | **Keep anonymous** for the core recommendation; accounts unlock save/load/profile. |
| P4 | Is the LSTM view a user-facing differentiator, or internal-only/experimental? | **User-facing but off by default**, clearly labelled experimental when on. |
| P5 | Target user segments (DIY retail only, or also advisers/B2B)? | **DIY retail** for v1; B2B/adviser mode is a later track. |

---

## Compliance & legal (external gates — see `06`)

| # | Question | Recommended default |
|---|----------|---------------------|
| C1 | Does the tailored output cross into regulated "personalised advice" per jurisdiction? | **Assume it might**; position strictly as educational/illustrative and route to legal/RIA review **before** public launch. Block launch on this. |
| C2 | Does India operation need SEBI RIA registration or an exemption? | **Assume review required**; do not launch to Indian retail until counsel confirms. |
| C3 | Data-retention period for user profiles and audit log? | **Profiles: delete/anonymise within 30 days of account deletion. Audit log: retain 7 years (financial-record norm), de-identified.** Confirm with privacy counsel. |
| C4 | Which privacy regimes apply (DPDP Act India, GDPR for EU users)? | **Design to the stricter (GDPR-level)**; confirm applicability by residence. |

---

## Data & market provider

| # | Question | Recommended default |
|---|----------|---------------------|
| D1 | Which commercial market-data provider? | **Pick on coverage (NSE/BSE + needed global) + redistribution terms for derived analytics + cost.** No specific vendor assumed; shortlist in procurement. Interim dev stays on yfinance. |
| D2 | History depth for estimators/backtests? | **≥10 years daily** where available (covers multiple regimes incl. 2020 drawdown); fall back to max available per instrument, recorded in `data_window`. |
| D3 | How often to refresh metrics/covariance? | **Daily post-close** for metrics/covariance; **weekly** for LSTM views. |
| D4 | Universe size cap? | **Soft cap a few hundred instruments** before needing factor-model dimensionality reduction (covariance is `O(N²)`/`O(N³)`). |

---

## Engine & methodology

| # | Question | Recommended default |
|---|----------|---------------------|
| E1 | Default objective mapping from risk category? | **low→min_variance, medium→max_sharpe, high→max_sharpe with higher equity cap.** Overridable via `objective`. |
| E2 | Baseline per-asset weight cap? | **0.35**, with a long-only, fully-invested baseline. Tunable per objective. |
| E3 | Monte Carlo return distribution — normal, or fat-tailed? | **Normal in v1** (matches current), flag Student-t/historical-bootstrap as a fast-follow for honest tail risk. |
| E4 | Transaction costs / taxes / rebalancing in backtest? | **Exclude in v1** (stated assumption); add cost modelling later. |
| E5 | Walk-forward window lengths? | **3-year train / 3-month hold**, rolling; tune empirically. |
| E6 | How are sector/ESG/Shariah/liquidity tags sourced for `instruments`? | **Open** — needs a reference-data source or manual curation. Default: seed manually for the current universe; automate via the provider's reference data when chosen. |
| E7 | Multi-currency handling for mixed-currency portfolios? | **v1: label currency, project in `baseCurrency`, no FX-risk modelling** (stated simplification); proper FX modelling is a later track. |

---

## Platform & operations

| # | Question | Recommended default |
|---|----------|---------------------|
| O1 | Cloud/hosting target (AWS, GCP, other)? | **Open** — the design is portable (managed containers + Postgres + object storage + Redis + secrets manager + scheduler exist on all majors). Default to the team's existing cloud. |
| O2 | Identity provider for OAuth? | **Open** — pick a managed IdP; default to the chosen cloud's native option to reduce vendors. |
| O3 | Scheduler for offline jobs — cron vs managed? | **Start with cron in a container; graduate to a managed scheduler** when jobs multiply or need retries/alerting. |
| O4 | Scale targets (RPS, concurrent users) at launch? | **Open** — assume modest launch load; the stateless API scales horizontally and the hot path is sub-second, so this is not an early blocker. Confirm for capacity planning. |
| O5 | Keep the metrics-cache JSON fallback in production? | **Dev/fallback only**; production reads the DB-backed store. The JSON stays for local runs. |

---

## Assumptions baked into this plan

These are taken as true unless a stakeholder corrects them:

1. The **current response key names** (e.g. `Investment Suggestions`, `Total Future Value (INR)`)
   are a hard backward-compat contract because the live React client depends on them (verified in
   `InvestmentResult.jsx`).
2. The **five base inputs stay required**; everything new is optional with safe defaults.
3. **TensorFlow stays pinned to CPython 3.9–3.12** and is isolated to the LSTM job image; the API
   and analytics jobs do not import it.
4. The product **does not execute trades, hold custody, or move money** — recommendations only.
5. **INR** is the default display/base currency (matches the current UI and universe).
6. Team size is small (1–2 backend, 1 frontend, part-time quant); roadmap effort reflects that.
7. The **existing `tickers.json` universe** is the starting universe; it will be enriched with
   metadata and can grow, subject to data-licensing coverage.
8. "Production-grade/sellable" implies the **compliance gates in `06` are cleared by humans**
   before public launch; engineering can build everything else in parallel.
