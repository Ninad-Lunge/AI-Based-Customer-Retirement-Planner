# 04 — API Contract

Defines the evolved HTTP contract. The guiding rule is **backward compatibility**: the existing
frontend (`PlanInput.jsx` / `InvestmentResult.jsx`) must keep working unchanged, and the five
original fields plus the current response key names remain valid. New inputs are **optional with
defaults**; new outputs are **additive**.

---

## Versioning strategy

- **URI versioning**: new endpoints live under `/v1/...`. Clean, cache-friendly, trivial to route.
- **Legacy alias**: the current un-versioned `POST /investment-strategy` is kept as a permanent
  alias of `POST /v1/investment-strategy` (same handler). The current React client calls the
  un-versioned path; it keeps working with zero changes.
- **Additive-only within a version**: within `v1` we only add optional request fields and
  additional response fields. Breaking changes (renames, removals, type changes) require `/v2`.
- **Deprecation policy**: when `/v2` ships, `/v1` is supported for a documented window (e.g. ≥6
  months) with a `Deprecation` + `Sunset` response header on the old version.

```
POST /investment-strategy            → alias → POST /v1/investment-strategy   (legacy client)
POST /v1/investment-strategy         → recommendation (anonymous or authed)
GET  /v1/instruments                 → active universe + metadata (for richer UI pickers)
# Authenticated (Phase 1+)
POST /v1/auth/*                      → managed-IdP-backed auth flows
GET/PUT /v1/profile                  → financial profile CRUD
GET/PUT /v1/preferences              → preferences CRUD
POST /v1/plans  ·  GET /v1/plans  ·  GET /v1/plans/{id}   → saved plans
GET  /health  ·  GET /ready          → liveness / readiness
```

---

## `POST /v1/investment-strategy` — request

### Base fields (required — unchanged from today)

| Field | Type | Required | Validation | Meaning |
|-------|------|----------|------------|---------|
| currentAge | int | **yes** | 18–100 | |
| retirementAge | int | **yes** | 18–100, > currentAge | |
| desiredFund | number | **yes** | > 0 | Target corpus. |
| monthlyInvestment | number | **yes** | > 0 | SIP amount. |
| riskCategory | string | **yes** | `low` / `medium` / `high` | |

### New optional fields (all default-safe; omit → neutral behaviour)

Grouped under an optional `profile` and `preferences` object so the base payload stays flat and
unchanged. Both objects, and every field within, are optional.

| Field | Type | Default | Engine effect | Maps to |
|-------|------|---------|---------------|---------|
| `objective` | enum `max_sharpe\|min_variance\|risk_parity` | derived from `riskCategory` | Selects optimizer objective | engine |
| `profile.annualIncome` | number | null | Risk-capacity + explanation | financial_profiles |
| `profile.incomeCurrency` | string(ISO-4217) | `INR` | Display | financial_profiles |
| `profile.dependents` | int | null | Lowers risk capacity | financial_profiles |
| `profile.existingSavings` | number | null | Feasibility context | financial_profiles |
| `profile.monthlyDebtEmi` | number | null | Investable-surplus context | financial_profiles |
| `profile.emergencyFundMonths` | int | null | <3 → safer tilt + warning | financial_profiles |
| `profile.experienceLevel` | enum `beginner\|intermediate\|advanced\|expert` | `beginner` | Explanation depth | financial_profiles |
| `profile.liquidityNeedHorizonMonths` | int | null | Liquidity-floor constraint | financial_profiles |
| `profile.countryOfResidence` | string(ISO-3166) | `IN` | Universe/currency context | financial_profiles |
| `profile.baseCurrency` | string(ISO-4217) | `INR` | Projection/display currency | financial_profiles |
| `preferences.preferredSectors` | string[] | `[]` | **Tilt** (soft overweight) | preferences |
| `preferences.assetClassPrefs` | object | `{}` | **Caps** per class (min/max weight) | preferences |
| `preferences.esgOnly` | bool | false | **Filter** universe | preferences |
| `preferences.shariahOnly` | bool | false | **Filter** universe | preferences |
| `preferences.ethicalFlags` | string[] | `[]` | **Filter** (named screens) | preferences |
| `preferences.excludedSectors` | string[] | `[]` | **Hard exclude** | preferences |
| `preferences.excludedTickers` | string[] | `[]` | **Hard exclude** | preferences |
| `preferences.existingHoldings` | array of `{ticker, value}` | `[]` | **Anti-concentration** cap | preferences |
| `preferences.allowCrypto` | bool | false | **Filter** class | preferences |
| `preferences.allowInternational` | bool | true | **Filter** class | preferences |
| `options.applyLstmView` | bool | false | Turn BL return-view on/off | engine |
| `options.monteCarloSims` | int | 10000 | Simulation count (bounded 1k–50k) | engine |

### Example request (backward-compatible minimum)

```json
{
  "currentAge": 30,
  "retirementAge": 60,
  "desiredFund": 10000000,
  "monthlyInvestment": 15000,
  "riskCategory": "medium"
}
```

### Example request (rich)

```json
{
  "currentAge": 30,
  "retirementAge": 60,
  "desiredFund": 10000000,
  "monthlyInvestment": 15000,
  "riskCategory": "medium",
  "objective": "max_sharpe",
  "profile": {
    "experienceLevel": "intermediate",
    "emergencyFundMonths": 2,
    "liquidityNeedHorizonMonths": 12,
    "baseCurrency": "INR"
  },
  "preferences": {
    "preferredSectors": ["technology", "green_energy"],
    "assetClassPrefs": { "equity": { "max": 0.7 }, "gold": { "min": 0.05 } },
    "esgOnly": true,
    "excludedTickers": ["ITC.NS"],
    "existingHoldings": [{ "ticker": "TCS.NS", "value": 200000 }],
    "allowInternational": true
  },
  "options": { "applyLstmView": true }
}
```

**Graceful degradation contract:** if `profile`, `preferences`, `objective`, and `options` are all
omitted, the engine produces a default portfolio equivalent in spirit to today's output and the
response includes all legacy fields. No optional field may ever cause a 4xx on its own absence.

---

## `POST /v1/investment-strategy` — response (200)

### Legacy fields (preserved — the current frontend reads these exact keys)

| Key | Type | Notes |
|-----|------|-------|
| `Investment Suggestions` | array | Each: `Stock Name`, `Asset Class`, `Investment Percentage`, `Annual Return (%)`, `Risk Profile`, `Future Value (INR)`. |
| `Total Future Value (INR)` | number | |
| `Average Annual Return (%)` | number | |
| `Value at Risk (5th percentile) (INR)` | number | |
| `Value at Risk (5th percentile) (%)` | number | |
| `Average Value from Simulation (INR)` | number | |
| `Optimistic Value (90th percentile) (INR)` | number | |
| `Goal Achievable` | bool | |

### New additive fields (ignored by the current client; used by the new UI)

| Key | Type | Notes |
|-----|------|-------|
| `apiVersion` | string | `v1`. |
| `objective` | string | Objective actually used. |
| `weights` | array `{ticker, weight}` | Raw optimizer weights (sum ≈ 1). |
| `backtest` | object | `cagr`, `maxDrawdown`, `sharpe`, `sortino`, `volatility`, `window`, `rolling[]` (date, cumulativeReturn). |
| `projection` | object | `p5`, `p50`, `p90`, `mean`, `simulations`, `goalProbability` (share of sims ≥ target). |
| `explanation` | object | `summary` (string), `bullets[]`, `experienceLevel`, `warnings[]` (e.g. low emergency fund). |
| `appliedConstraints` | object | Echo of filters/caps/exclusions actually applied (transparency). |
| `lstmViewApplied` | bool | Whether the BL view was used. |
| `metricsVersion` | string | Data/version provenance (for support + audit). |
| `disclaimer` | object | `version`, `text` — mandatory, see `06`. |

### Example response (abridged)

```json
{
  "apiVersion": "v1",
  "objective": "max_sharpe",
  "Goal Achievable": true,
  "Total Future Value (INR)": 10234000.0,
  "Average Annual Return (%)": 11.2,
  "Value at Risk (5th percentile) (INR)": 7120000.0,
  "Value at Risk (5th percentile) (%)": -8.4,
  "Average Value from Simulation (INR)": 10180000.0,
  "Optimistic Value (90th percentile) (INR)": 13990000.0,
  "Investment Suggestions": [
    { "Stock Name": "NIFTYBEES.NS", "Asset Class": "Indian ETF / Mutual Fund",
      "Investment Percentage": 32.5, "Annual Return (%)": 10.1,
      "Risk Profile": "Medium", "Future Value (INR)": 3320000.0 }
  ],
  "weights": [ { "ticker": "NIFTYBEES.NS", "weight": 0.325 } ],
  "backtest": { "cagr": 10.4, "maxDrawdown": -24.1, "sharpe": 0.72, "sortino": 1.01,
                "volatility": 14.3, "window": "2016-01..2025-12",
                "rolling": [ { "date": "2020-03", "cumulativeReturn": -0.21 } ] },
  "projection": { "p5": 7120000, "p50": 10180000, "p90": 13990000, "mean": 10180000,
                  "simulations": 10000, "goalProbability": 0.58 },
  "explanation": { "summary": "A balanced, max-Sharpe mix tilted to your ESG preference.",
                   "bullets": ["Equity capped at 70% per your preference", "ITC.NS excluded"],
                   "experienceLevel": "intermediate",
                   "warnings": ["Emergency fund below 3 months; consider building it first."] },
  "appliedConstraints": { "excludedTickers": ["ITC.NS"], "assetClassPrefs": {"equity":{"max":0.7}},
                          "esgOnly": true },
  "lstmViewApplied": true,
  "metricsVersion": "2026-10-05T18:00Z#v42",
  "disclaimer": { "version": "2026-10-01",
                  "text": "Educational and illustrative only. Not financial advice..." }
}
```

---

## Error model (unchanged shape, extended coverage)

The current structured shape is kept: `{ "error": string, "status": int, "details"?: object }`.

| Status | When |
|--------|------|
| 400 | Malformed JSON; base field missing/invalid type/out of range (unchanged behaviour). |
| 401 | Missing/invalid token on an authenticated endpoint. |
| 403 | Authenticated but not permitted (e.g. accessing another user's plan). |
| 404 | Unknown resource (e.g. plan id). |
| 405 | Method not allowed (unchanged). |
| 409 | Conflict (e.g. duplicate email on register). |
| 422 | Domain failure: no instruments in risk category after filters, or goal mathematically unreachable (unchanged behaviour — returned via the `error` branch today). |
| 429 | Rate limit exceeded (new, Phase 4). |
| 500 | Internal error (unchanged). |
| 502 | Upstream data unavailable (only relevant for dev/fallback source; prod reads the store). |
| 503 | No usable data (store empty / all versions stale). |

**New-field validation principle:** an optional field that is present but malformed (e.g.
`assetClassPrefs.equity.max = 1.5`) returns 400 with a `details` pointer to the offending path;
an **absent** optional field never errors. Over-restrictive filters that leave the universe empty
return **422** with a helpful message (e.g. "No instruments match your ESG + exclusion filters;
relax a constraint"), not 400.

---

## Migration path (current → v1)

| Step | Action | Compatibility |
|------|--------|---------------|
| 1 | Mount the existing handler at `/v1/investment-strategy` and keep `/investment-strategy` as an alias. | Current client unaffected. |
| 2 | Add optional `profile`/`preferences`/`objective`/`options` parsing with defaults; if all absent, behave exactly as today. | Current client unaffected. |
| 3 | Add additive response fields (`backtest`, `projection`, `explanation`, `disclaimer`, …). | Current client ignores unknown keys. |
| 4 | Build the new UI to send rich inputs and render the new fields; keep rendering legacy fields. | Both clients coexist. |
| 5 | When/if a breaking change is needed, introduce `/v2`, set `Deprecation`/`Sunset` on `/v1`. | Documented sunset window. |

**Note on `desiredFund`:** today it is required. To keep backward compatibility it stays required
in `v1`. A future "no explicit target, just project my SIP" mode would be a `/v2` change (making
`desiredFund` optional) — flagged in `08-open-questions.md`, not done here.
