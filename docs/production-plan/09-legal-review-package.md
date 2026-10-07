# 09 — Legal-Review Package (Phase 4)

**Purpose:** consolidate everything a lawyer and (where required) a registered
investment adviser need to review **before this product is offered to the
public**. This is an engineering deliverable that *packages and flags*; it is
**not legal advice** and does not resolve any legal question. The engineering
controls referenced here are implemented and verified (see Phase 4); the
legal/regulatory sign-offs are **external gates** that remain open.

> **Launch gate:** public launch to retail users should be blocked until the
> items in §7 marked **OPEN (external)** are cleared by qualified counsel.

---

## 1. Product positioning (as built)

The product is built and worded as an **educational and illustrative tool** that
demonstrates how portfolio construction (Modern Portfolio Theory) and goal
projection (Monte Carlo) work on historical data. As implemented:

- It produces **illustrative allocations + probabilistic projections + a
  historical backtest**, framed as model estimates — not directives.
- It performs **no trade execution, no brokerage, no custody, and moves no
  money**. It only displays information.
- Every recommendation response carries a **machine-readable disclaimer block**
  (version + text) and the UI shows a disclaimer.
- Outputs are **transparent**: the response echoes the applied constraints and a
  plain-language explanation of *why* an allocation was produced.

**Flag for review:** whether tailoring named instruments + weights to a user's
stated profile crosses from "educational illustration" into "personalised
investment advice" in each target jurisdiction. This is the central legal
question and must be answered by counsel per jurisdiction.

---

## 2. Disclaimer text + versioning

Disclaimers are **versioned** and the version shown is recorded in the audit log
(so we can prove which text a user saw).

- **Current version:** `2026-10-01`
- **Current text** (as emitted in every recommendation payload and shown in UI):

  > "This tool is for educational and illustrative purposes only and does not
  > constitute investment, financial, legal, or tax advice, nor a recommendation
  > or solicitation to buy or sell any security. Projections are model estimates
  > based on historical data. Past performance is not indicative of future
  > results. Consult a qualified, registered financial adviser before making
  > decisions."

- **When the LSTM view is enabled** (off by default), an additional note that an
  experimental predictive signal was used and carries extra model uncertainty
  should be appended. **Flag:** confirm wording with counsel.

**Review asks:**
1. Approve/replace the disclaimer wording per jurisdiction.
2. Confirm placement/prominence requirements (e.g. pre-use acknowledgement vs
   footer) are met.
3. Confirm the versioning + audit-trail approach satisfies record-keeping norms.

---

## 3. Data-source licensing

- **Current state:** development/analytics uses `yfinance` (Yahoo Finance),
  whose terms are **personal-use** — **not** licensed for a commercial product.
  In the architecture it is confined to the dev/offline ingestion source and the
  request path reads a precomputed store (no live provider call at serve time).
  The audit log records `data_source` for every recommendation.
- **Required before launch:** procure a **commercial market-data license** that
  permits serving **derived analytics** (returns / volatility / covariance /
  backtests) to end users for the target markets (NSE/BSE + any global
  instruments), and swap the ingestion source to that provider. The source
  abstraction (`jobs/ingest.py` `PriceSource`) is already pluggable for this.

**Review asks (scope questions for counsel/procurement):**
1. Does the chosen provider's license permit redistribution of **derived
   analytics** (not raw quotes) to retail end users?
2. Caching/retention limits on provider data?
3. Attribution requirements to display?
4. Territorial scope (India + any international instruments offered)?

---

## 4. Jurisdiction & registration flags

**Flag for legal/RIA determination (do not self-certify):**

- **India (SEBI):** whether operating this tool for Indian retail users requires
  **SEBI Investment Adviser (RIA)** registration, or qualifies for an exemption
  (e.g. because it is positioned as educational and does not provide personalised
  advice for consideration). This determination gates an India launch.
- **United States:** whether the Investment Advisers Act applies (holding out as
  an adviser / providing advice for compensation).
- **Other jurisdictions:** repeat the determination wherever users are offered
  the product.
- **Marketing claims:** "AI-based", "retirement planner", projected-corpus
  figures — must not imply guaranteed outcomes; review all marketing copy.

---

## 5. Privacy & data protection

- **PII inventory and controls** are specified in `03-data-model.md` §PII:
  identity fields (email, auth subject, display name, country) and
  financial-sensitive fields (income, dependents, savings, debt, emergency fund,
  existing holdings). Controls: TLS in transit, encryption at rest, application-
  layer encryption for financial PII, no PII in logs, secrets in a manager.
- **Audit minimisation (implemented):** the audit log stores a PII-minimised
  input snapshot (financial inputs used for the recommendation — ages, amounts,
  risk, preferences) and a `user_id` reference, **never** contact PII.
- **Right-to-be-forgotten (implemented):** account deletion purges the user's
  profile, preferences, saved plans (and their snapshots) and credentials, and
  **de-identifies** audit rows (`user_id → NULL`) while retaining them for
  compliance. Verified end-to-end in Phase 4.

**Review asks:**
1. Confirm applicable regimes (India **DPDP Act**; **GDPR** for any EU users) and
   design to the stricter.
2. **Set the retention periods** (recommended defaults below) — needs
   privacy-counsel sign-off:
   - User profiles/preferences/plans: **delete/anonymise within 30 days** of
     account deletion.
   - Audit log: **retain ~7 years** (financial-record norm), **de-identified**.
3. Confirm the lawful basis for storing financial PII and the consent flow.

---

## 6. Auditability & reproducibility (implemented)

Every recommendation writes an **append-only `audit_log` row** capturing:
`request_id`, `user_id` (nullable), PII-minimised `input_snapshot`,
`metrics_version_id`, `data_source`, `optimizer_config` (+ `configHash`),
`lstm_view_applied`, `disclaimer_version`, `rng_seed`, `created_at`.

**Reproducibility (verified):** given an audit row and the referenced metrics
version, re-running the engine with the recorded RNG seed reproduces the **exact
weights and projection** — satisfying the Phase 4 reproducibility DoD.

Kill-switches: the LSTM view can be globally disabled (`LSTM_VIEW_ENABLED=false`)
and a known-good metrics version can be pinned if an analytics run looks
anomalous.

---

## 7. Compliance checklist (launch gate)

| # | Item | Owner | Status |
|---|------|-------|--------|
| 1 | Positioning: educational vs personalised advice, per jurisdiction | Legal / RIA | **OPEN (external)** |
| 2 | Disclaimer wording + prominence finalised, per jurisdiction | Legal | **OPEN (external)** |
| 3 | SEBI RIA (India) registration/exemption determination | Legal / RIA | **OPEN (external)** |
| 4 | US Advisers Act applicability determination | Legal | **OPEN (external)** |
| 5 | Commercial market-data license (derived-analytics redistribution) | Legal / Procurement | **OPEN (external)** |
| 6 | Privacy regimes + retention periods + lawful basis | Privacy counsel | **OPEN (external)** |
| 7 | Marketing-claims review (no guaranteed-return language) | Legal / Marketing | **OPEN (external)** |
| 8 | Disclaimer present in UI + every API payload | Engineering | **DONE** |
| 9 | Versioned disclaimer recorded in audit trail | Engineering | **DONE** |
| 10 | Append-only audit log of every recommendation | Engineering | **DONE** |
| 11 | Reproducibility from audit row (fixed RNG seed) | Engineering | **DONE** |
| 12 | `data_source` recorded per recommendation | Engineering | **DONE** |
| 13 | yfinance confined to dev; serve path reads store (no live provider call) | Engineering | **DONE** |
| 14 | Rate limiting / abuse protection on recommendation + auth endpoints | Engineering | **DONE** |
| 15 | Right-to-be-forgotten: purge PII + de-identify audit | Engineering | **DONE** |
| 16 | PII minimisation in audit log + logs | Engineering | **DONE** |

**Engineering items (8–16) are implemented and verified in Phases 1–4.** Items
**1–7 are external gates** this package surfaces for human/legal and
registered-adviser review; the product must not launch to retail users until
they are cleared.

---

## 8. Explicitly out of scope (business/legal decisions)

- Actual legal sign-off (external to engineering).
- Becoming a registered investment adviser (a business decision).
- Brokerage / execution / custody integration.
- Tax advice or tax-optimised allocation.
