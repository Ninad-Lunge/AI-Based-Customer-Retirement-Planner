# 06 — Risk, Compliance & Trust

The product gives portfolio-allocation guidance to retail users. That is a regulated-adjacent
activity. This document sets the positioning, disclaimers, data-licensing, and auditability
requirements, and **flags** everything that needs human/legal or registered-adviser review.

> **This document is not legal advice.** It identifies risk areas and recommends a conservative
> default posture. A qualified lawyer and, where required, a registered investment adviser must
> review positioning and disclaimers before the product is offered to the public.

---

## 1. Positioning: educational/illustrative, not regulated advice

**Recommended default stance:** position the product as an **educational and illustrative tool**
that shows *how* portfolio construction and goal projection work on historical data — **not** as
personalised investment advice, and **not** as a solicitation to buy or sell any security.

Why this matters: in most jurisdictions (including India via SEBI's Investment Adviser
regulations, and the US via the Investment Advisers Act), giving **personalised** investment
advice for consideration, or holding out as an adviser, can require registration. The line between
"generic educational illustration" and "personalised advice" is exactly where legal review is
needed. The product's design choices that support the educational stance:

- Outputs are framed as **projections/illustrations**, with probabilities and backtests, not
  "you should buy X".
- The optimizer explains *mechanism and assumptions*, not a directive.
- No execution, no brokerage, no custody — the product never places trades.

**Flag for legal/registered-adviser review (do not self-certify):**
- Whether the specific outputs (named instruments + weights tailored to a user's profile) cross
  from "educational illustration" into "personalised advice" in each target jurisdiction.
- Whether operating in India requires SEBI RIA registration or a specific exemption.
- Whether a disclaimer alone is sufficient, or a registered entity/person must stand behind it.
- Marketing copy ("AI-based", "retirement planner") claims — must not imply guaranteed outcomes.

---

## 2. Required disclaimers

A disclaimer block is **mandatory in every recommendation** (API `disclaimer{version,text}` and
visibly in the UI). Draft content (to be finalised by legal):

- "This tool is for **educational and illustrative purposes only** and does **not** constitute
  investment, financial, legal, or tax advice, nor a recommendation or solicitation to buy or sell
  any security."
- "Projections are **model estimates based on historical data**. **Past performance is not
  indicative of future results.** Actual returns will vary and you may lose money."
- "No guarantee is made as to accuracy or completeness. Consult a **qualified, registered
  financial adviser** before making investment decisions."
- When the **LSTM view is enabled**: an added note that an experimental predictive signal was used
  and carries additional model uncertainty.
- Market-data attribution/limitations as required by the data provider's license.

**Mechanics:** disclaimers are **versioned** (`disclaimer_version`) and the version shown is
recorded in `recommendation_snapshots`/`audit_log`, so we can prove which text a user saw.

---

## 3. Data-source licensing

**Current state:** `yfinance` scrapes Yahoo Finance, whose terms are **personal-use**; it is
**not** licensed for a commercial product. This is a compliance and reliability risk and must be
resolved before go-to-market (Phase 4).

**Requirements:**
- Procure a **commercial market-data license** that permits serving derived analytics
  (returns/vol/covariance/backtests) to end users for the target markets (NSE/BSE + global
  instruments as needed). Vendor selection is an open question (`08`), driven by coverage,
  redistribution terms, and cost.
- Honour the provider's **attribution, redistribution, and caching** terms (some prohibit
  exposing raw quotes; our product exposes *derived* analytics, which is usually more permissive —
  confirm per contract).
- Record `data_source` + dataset version in the **audit log** so every recommendation is traceable
  to a licensed source and window.
- Keep `yfinance` strictly for **local development and emergency fallback**, clearly flagged as
  non-production, never in the paid serving path.

**Flag for legal review:** redistribution scope, caching duration, and whether derived-analytics
output is in-scope of the chosen provider's license.

---

## 4. Auditability & reproducibility

Every recommendation must be reconstructable. Implemented via the `audit_log` +
`recommendation_snapshots` tables (`03-data-model.md`):

| Captured | Why |
|----------|-----|
| Input snapshot (PII-minimised) | What the user asked for. |
| `metrics_version_id` + `data_source` | Exactly which precomputed metrics/covariance/window were used. |
| `optimizer_config` (objective + constraints hash) | How weights were derived. |
| `lstm_view_applied` | Whether the experimental signal influenced the result. |
| Output snapshot (weights, backtest, projection, explanation) | What the user was shown. |
| `disclaimer_version` | Which risk disclosure they saw. |
| `request_id`, `created_at` | Correlation + timeline. |

**Reproducibility test (Phase 4 DoD):** given an audit row, re-running the engine with the same
inputs + the referenced metrics version yields the same weights and projection (within Monte Carlo
seed tolerance — fix/record the RNG seed for exact reproduction).

---

## 5. Model-risk & fairness considerations

- **Model risk**: expected-return estimates are noisy and the LSTM signal is weak; the design
  mitigates via shrinkage covariance, min-variance/risk-parity options, BL confidence weighting,
  weight caps, and backtests shown to the user (`05`). Document a periodic **model-validation**
  review of the offline analytics job output (sanity bounds on metrics, covariance conditioning).
- **No profiling on protected attributes**: recommendations are driven only by the user's stated
  financial profile and preferences — never by inferred protected characteristics.
- **Transparency**: the response echoes `appliedConstraints` and an `explanation`, so users see
  *why* they got a given allocation.
- **Kill-switches**: global config can disable the LSTM view and can pin a known-good metrics
  version if an analytics run looks anomalous.

---

## 6. Operational & security risk (summary; detail in `03` PII section)

- TLS everywhere; encryption at rest; application-layer encryption for financial PII.
- Managed-IdP auth (no stored passwords); least-privilege service accounts.
- Rate limiting + abuse protection (Phase 4).
- Data retention + right-to-be-forgotten workflow; retention period to be set with privacy counsel.
- No PII in logs; secrets in a secrets manager, never in repo/images.

---

## 7. Compliance checklist (gate before public launch)

| Item | Owner | Status |
|------|-------|--------|
| Positioning (educational vs advice) reviewed per target jurisdiction | Legal / RIA | **Open — flagged** |
| Disclaimer text finalised + versioned | Legal | **Open — flagged** |
| SEBI RIA (India) registration/exemption determination | Legal / RIA | **Open — flagged** |
| Commercial market-data license signed; redistribution scope confirmed | Legal / Procurement | **Open — flagged** |
| Privacy: lawful basis for financial PII; retention period; GDPR/DPDP applicability | Privacy counsel | **Open — flagged** |
| Audit-log reproducibility verified | Engineering | Phase 4 DoD |
| Disclaimers present in UI + payload | Engineering | Phase 4 DoD |
| yfinance removed from production serving path | Engineering | Phase 4 DoD |
| Marketing claims reviewed (no guaranteed-return language) | Legal / Marketing | **Open — flagged** |

The engineering items are deliverable within the roadmap; the **legal/RIA/privacy items are
external gates** that this plan surfaces but cannot resolve. Public launch should be blocked until
those gates are cleared.
