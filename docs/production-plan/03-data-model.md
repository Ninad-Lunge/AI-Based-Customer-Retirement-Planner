# 03 — Data Model

PostgreSQL schema for the production system. All new user-supplied profile/preference fields are
**optional with defaults**, so the engine degrades gracefully when they are absent (see
`04-api-contract.md` and `05-engine-design.md` for how each is used). PII is explicitly flagged
and protected (see the PII section at the end).

Conventions: `id` columns are UUID (v4). Timestamps are `timestamptz` (UTC). Monetary amounts are
`numeric(18,2)` with an explicit `currency` where relevant. JSONB is used for open-ended,
low-query preference blobs; relational columns are used where we filter/join.

---

## Entity overview

```
users ──1:1── financial_profiles
  │
  ├──1:1── preferences
  │
  └──1:N── saved_plans ──N:1── recommendation_snapshots ──(audited by)── audit_log
instruments (universe, reference data)         model_metrics_versions ──1:N── instrument_metrics
                                                                       └──1:1── covariance_artifacts
```

---

## `users`

| Column | Type | Null | Default | PII | Notes |
|--------|------|------|---------|-----|-------|
| id | uuid (PK) | no | gen_random_uuid() | — | |
| email | citext (unique) | yes | null | **PII** | Null for anonymous. Unique when present. |
| auth_provider | text | yes | null | — | e.g. `google`, `password`, `null` for anonymous. |
| auth_subject | text | yes | null | **PII** | IdP subject id; unique per provider. |
| display_name | text | yes | null | **PII** | |
| created_at | timestamptz | no | now() | — | |
| updated_at | timestamptz | no | now() | — | |
| deleted_at | timestamptz | yes | null | — | Soft delete for right-to-be-forgotten workflow. |

Passwords are **not** stored (OAuth via managed IdP — ADR-006). If a password option is later
required, store only an Argon2id hash in a separate `user_credentials` table, never here.

---

## `financial_profiles`  (optional; 1:1 with users)

Everything here is optional. Absence = "unknown", and the engine uses neutral defaults.

| Column | Type | Null | Default | PII | Engine use |
|--------|------|------|---------|-----|------------|
| user_id | uuid (PK, FK users.id) | no | — | — | |
| annual_income | numeric(18,2) | yes | null | **PII (financial)** | Risk-capacity signal; explanation modifier. |
| income_currency | char(3) | yes | 'INR' | — | ISO-4217. |
| dependents | smallint | yes | null | **PII (financial)** | Lowers risk capacity; explanation modifier. |
| existing_savings | numeric(18,2) | yes | null | **PII (financial)** | Context for goal feasibility. |
| monthly_debt_emi | numeric(18,2) | yes | null | **PII (financial)** | Reduces investable surplus; explanation. |
| emergency_fund_months | smallint | yes | null | **PII (financial)** | <3 → warn, nudge to safer tilt. |
| experience_level | text (enum) | yes | 'beginner' | — | `beginner|intermediate|advanced|expert`; drives explanation depth. |
| liquidity_need_horizon_months | smallint | yes | null | — | Short horizon → liquidity-floor constraint. |
| country_of_residence | char(2) | yes | 'IN' | **PII** | ISO-3166; universe/currency context. |
| base_currency | char(3) | yes | 'INR' | — | Display + projection currency. |
| updated_at | timestamptz | no | now() | — | |

**Graceful-default rule:** a missing field is treated as `null`/the stated default and must never
cause an error. Risk-capacity adjustments only apply when the relevant fields are present.

---

## `preferences`  (optional; 1:1 with users)

Relational columns for things we constrain/filter on; JSONB for open-ended lists.

| Column | Type | Null | Default | Engine use |
|--------|------|------|---------|------------|
| user_id | uuid (PK, FK users.id) | no | — | |
| preferred_sectors | text[] | yes | '{}' | **Tilt**: overweight these sectors (soft). |
| asset_class_prefs | jsonb | yes | '{}' | **Constraint**: per-class min/max caps, e.g. `{"equity":{"max":0.7},"gold":{"min":0.05}}`. |
| esg_only | boolean | yes | false | **Filter**: restrict universe to ESG-flagged instruments. |
| shariah_only | boolean | yes | false | **Filter**: restrict to Shariah-compliant instruments. |
| ethical_flags | text[] | yes | '{}' | **Filter**: named ethical screens (e.g. `no_gambling`). |
| excluded_sectors | text[] | yes | '{}' | **Hard constraint**: drop instruments in these sectors. |
| excluded_tickers | text[] | yes | '{}' | **Hard constraint**: drop these symbols. |
| existing_holdings | jsonb | yes | '[]' | **Anti-concentration**: `[{"ticker":"TCS.NS","value":50000}]`; cap added weight where already concentrated. |
| allow_crypto | boolean | yes | false | **Filter**: include/exclude crypto class. |
| allow_international | boolean | yes | true | **Filter**: include/exclude non-domestic. |
| updated_at | timestamptz | no | now() | |

**Why JSONB for some, columns for others:** `asset_class_prefs`, `existing_holdings` are
structured-but-variable and only read during optimization (no cross-row querying needed) → JSONB.
Flags and arrays we may filter/report on stay as typed columns/arrays.

---

## `saved_plans`  (1:N from users)

A user-named, retrievable plan = the inputs + a pointer to the immutable recommendation snapshot.

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| id | uuid (PK) | no | gen_random_uuid() | |
| user_id | uuid (FK users.id) | no | — | |
| name | text | yes | null | User label. |
| input_payload | jsonb | no | — | Snapshot of the request inputs (base + optional). |
| recommendation_snapshot_id | uuid (FK) | no | — | The immutable result (below). |
| created_at | timestamptz | no | now() | |

---

## `recommendation_snapshots`  (immutable result record)

The exact output served, stored once, referenced by both saved plans and the audit log. Immutable
→ a saved plan always shows what the user actually saw.

| Column | Type | Null | Notes |
|--------|------|------|-------|
| id | uuid (PK) | no | |
| api_version | text | no | e.g. `v1`. |
| objective | text | no | `max_sharpe|min_variance|risk_parity`. |
| weights | jsonb | no | `[{"ticker":...,"weight":...}]`. |
| allocation | jsonb | no | Full `Investment Suggestions`-shaped array (legacy-compatible). |
| backtest_metrics | jsonb | no | CAGR, max_drawdown, sharpe, sortino, vol, rolling series. |
| projection | jsonb | no | Monte Carlo P5/P50/P90/mean + `Total Future Value`, `Goal Achievable`. |
| explanation | jsonb | no | Rendered rationale + experience level. |
| metrics_version_id | uuid (FK model_metrics_versions.id) | no | Which precomputed inputs were used. |
| lstm_view_applied | boolean | no | Whether BL view was on. |
| disclaimer_version | text | no | Which disclaimer text was shown. |
| created_at | timestamptz | no | |

---

## `audit_log`  (append-only, Phase 4)

Reproducibility + compliance. One row per recommendation served (anonymous or authed).

| Column | Type | Notes |
|--------|------|-------|
| id | uuid (PK) | |
| request_id | text | Correlates to logs. |
| user_id | uuid (FK, nullable) | Null for anonymous. |
| input_snapshot | jsonb | Inputs as validated (PII-minimised — see below). |
| recommendation_snapshot_id | uuid (FK) | The output. |
| metrics_version_id | uuid (FK) | Data/version provenance. |
| data_source | text | e.g. licensed provider name + dataset version. |
| optimizer_config | jsonb | Objective + constraints hash. |
| created_at | timestamptz | Append-only; no updates/deletes. |

---

## `instruments`  (universe / reference data — replaces/augments `tickers.json`)

The `tickers.json` universe graduates into a table so it can carry the metadata the new filters
need. `tickers.json` can remain the seed/bootstrap source.

| Column | Type | Null | Default | Notes |
|--------|------|------|---------|-------|
| ticker | text (PK) | no | — | yfinance/provider symbol, e.g. `RELIANCE.NS`. |
| name | text | yes | null | Display name. |
| asset_class | text | no | — | Equity, ETF/MF, REIT, Gold, Bonds, International, Index, Crypto. |
| sector | text | yes | null | For sector tilt/exclusion. |
| country | char(2) | yes | null | Domestic vs international. |
| currency | char(3) | yes | null | Instrument trading currency. |
| esg_flag | boolean | no | false | ESG screen. |
| shariah_flag | boolean | no | false | Shariah screen. |
| ethical_tags | text[] | yes | '{}' | e.g. `{tobacco}`, `{fossil_fuel}` for exclusion matching. |
| liquidity_tier | smallint | yes | null | 1=very liquid … n; feeds liquidity floors. |
| active | boolean | no | true | Soft-remove from the universe without deleting history. |
| updated_at | timestamptz | no | now() | |

---

## Model/metrics store schema

### `model_metrics_versions`

| Column | Type | Notes |
|--------|------|-------|
| id | uuid (PK) | |
| computed_at | timestamptz | When the analytics job ran. |
| data_window_start | date | History window used. |
| data_window_end | date | |
| data_source | text | Provider + dataset version. |
| is_current | boolean | Exactly one row true at a time (atomic flip). |
| notes | text | e.g. "shrinkage=ledoit_wolf". |

### `instrument_metrics`  (N per version)

| Column | Type | Notes |
|--------|------|-------|
| version_id | uuid (FK) | Composite PK with ticker. |
| ticker | text (FK instruments.ticker) | |
| expected_annual_return_pct | numeric | Precomputed (historical, optionally BL-ready). |
| volatility_pct | numeric | Annualised. |
| beta | numeric | |
| sharpe | numeric | |
| risk_profile | text | Low/Medium/High (kept for legacy response field). |

### `covariance_artifacts`  (1 per version)

| Column | Type | Notes |
|--------|------|-------|
| version_id | uuid (PK, FK) | |
| storage_uri | text | S3/GCS path to the serialized covariance matrix (Ledoit–Wolf). |
| ticker_order | text[] | Row/column ordering for the matrix. |
| checksum | text | Integrity check. |

### `lstm_views`  (optional, 1 per version per eligible ticker)

| Column | Type | Notes |
|--------|------|-------|
| version_id | uuid (FK) | |
| ticker | text | |
| view_return_pct | numeric | Model's forward-return view (the BL "view"). |
| confidence | numeric | 0–1; maps to BL view uncertainty. |

---

## PII classification & protection

**PII inventory** (fields marked **PII** above):
- Identity: `users.email`, `users.auth_subject`, `users.display_name`, `country_of_residence`.
- Financial-sensitive: `annual_income`, `dependents`, `existing_savings`, `monthly_debt_emi`,
  `emergency_fund_months`, `existing_holdings`.

**Controls**
| Control | Mechanism |
|---------|-----------|
| Encryption in transit | TLS everywhere (gateway → API → DB). |
| Encryption at rest | DB-level encryption (managed Postgres KMS); object storage SSE for blobs. |
| Column-level protection for financial PII | Application-layer encryption for `financial_profiles` sensitive columns and `preferences.existing_holdings` using a KMS-managed key; store ciphertext. (Trade-off: can't query/aggregate on these — acceptable, we don't need to.) |
| Access control | Row-level: users read only their own rows (enforced in app; optionally Postgres RLS). Service accounts least-privilege. |
| Audit-log minimisation | `input_snapshot` stores the **inputs used for the recommendation**, not raw contact PII; store a `user_id` reference, not email, in the audit log. |
| Secrets | DB creds, provider keys, KMS/JWT keys in a secrets manager; never in repo/images/logs. |
| Retention & deletion | Soft-delete (`users.deleted_at`) triggers a scheduled hard-delete/anonymisation of the user's profile, preferences, and saved plans. `recommendation_snapshots`/`audit_log` are retained for compliance but de-identified (keep `user_id`→null or a pseudonymous token). Retention period is an open question (`08`). |
| Logging hygiene | Never log financial PII or email; log `user_id` + `request_id` only. |
| Data minimisation | All profile/preference fields optional; collect only what the user volunteers; each field's purpose is documented in the API contract. |

**Flag for legal/privacy review (see `06`):** cross-border data (country field + non-IN users)
may trigger GDPR/DPDP-Act obligations; the retention period and the lawful basis for storing
financial PII need privacy-counsel sign-off. This plan flags; it does not provide legal advice.
