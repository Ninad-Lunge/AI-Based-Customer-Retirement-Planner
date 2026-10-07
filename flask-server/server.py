# Flask backend for the AI-Based Customer Retirement Planner.
#
# Dependencies are pinned in requirements.txt (install with: pip install -r requirements.txt).
#
# Environment variables (all optional, with sensible defaults):
#   FLASK_DEBUG       - "true"/"false" to toggle debug mode (default: false)
#   FLASK_PORT        - port to bind the server to (default: 5000)
#   FLASK_HOST        - host interface to bind to (default: 0.0.0.0)
#   CORS_ORIGINS      - comma-separated list of allowed origins, or "*" (default: *)
#   LOG_LEVEL         - logging level, e.g. DEBUG/INFO/WARNING (default: INFO)

import logging
import os
import time
import uuid

from flask import Flask, g, jsonify, request
from flask_cors import CORS

from investment_strategy import (
    fetch_stock_data,
    investment_tickers,
    suggest_investment,
)

# --------------------------------------------------------------------------- #
# Configuration (environment-driven, via the typed settings module)
# --------------------------------------------------------------------------- #
# Settings are formalised in settings.py (pydantic-settings). We fall back to
# the original inline os.environ parsing if pydantic-settings is unavailable,
# so the server still starts in minimal environments. Behaviour is identical.
try:
    from settings import get_settings

    _settings = get_settings()
    LOG_LEVEL = _settings.log_level
    DEBUG_MODE = _settings.debug
    PORT = _settings.port
    HOST = _settings.host
    cors_origins = _settings.cors_origins
except Exception:  # noqa: BLE001 - config must never block startup
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
    DEBUG_MODE = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    PORT = int(os.environ.get("FLASK_PORT", "5000"))
    HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
    _cors_raw = os.environ.get("CORS_ORIGINS", "*")
    if _cors_raw.strip() == "*":
        cors_origins = "*"
    else:
        cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": cors_origins}})

logger.info(
    "Starting server (debug=%s, host=%s, port=%s, cors_origins=%s, log_level=%s)",
    DEBUG_MODE,
    HOST,
    PORT,
    cors_origins,
    LOG_LEVEL,
)

# --------------------------------------------------------------------------- #
# Phase 1: database + versioned API wiring (graceful no-DB fallback)
# --------------------------------------------------------------------------- #
# Initialise the DB engine from DATABASE_URL. If unset, the app runs in
# stateless mode (Phase 0 behaviour): the anonymous recommendation path works
# and /v1 persistence endpoints return 503. Must never block startup.
try:
    import db as _db
    from routes_v1 import v1 as _v1_blueprint

    _database_url = ""
    try:
        _database_url = get_settings().database_url
    except Exception:  # noqa: BLE001
        _database_url = os.environ.get("DATABASE_URL", "")

    _db.init_engine(_database_url)
    app.register_blueprint(_v1_blueprint)

    if _db.is_enabled():
        logger.info("Persistence enabled: /v1 auth + CRUD endpoints active.")
    else:
        logger.info("Persistence disabled (no DATABASE_URL): /v1 endpoints return 503.")

    # Ensure each request's DB session is cleaned up at teardown.
    @app.teardown_appcontext
    def _shutdown_db_session(exc=None):
        _db.shutdown_session(exc)

except Exception:  # noqa: BLE001 - persistence wiring must never block startup
    logger.exception("Failed to wire persistence layer; continuing stateless.")

# --------------------------------------------------------------------------- #
# Validation constants
# --------------------------------------------------------------------------- #
MIN_AGE = 18
MAX_AGE = 100
VALID_RISK_CATEGORIES = {"low", "medium", "high"}


def _error_response(message, status_code, details=None):
    """Build a structured JSON error response with a consistent shape."""
    payload = {"error": message, "status": status_code}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status_code


def _validate_payload(data):
    """
    Validate and coerce the incoming request payload.

    Returns a tuple of (parsed_dict, error_tuple). Exactly one is non-None:
      - on success:  (parsed_values, None)
      - on failure:  (None, (message, status_code, details))
    """
    if not isinstance(data, dict):
        return None, ("Request body must be a JSON object.", 400, None)

    required_fields = [
        "currentAge",
        "retirementAge",
        "desiredFund",
        "monthlyInvestment",
        "riskCategory",
    ]
    missing = [f for f in required_fields if f not in data]
    if missing:
        return None, (
            "Missing required field(s).",
            400,
            {"missing_fields": missing},
        )

    # Numeric coercion with descriptive messages.
    try:
        current_age = int(data["currentAge"])
    except (TypeError, ValueError):
        return None, ("'currentAge' must be an integer.", 400, None)

    try:
        retirement_age = int(data["retirementAge"])
    except (TypeError, ValueError):
        return None, ("'retirementAge' must be an integer.", 400, None)

    try:
        desired_fund = float(data["desiredFund"])
    except (TypeError, ValueError):
        return None, ("'desiredFund' must be a number.", 400, None)

    try:
        monthly_investment = float(data["monthlyInvestment"])
    except (TypeError, ValueError):
        return None, ("'monthlyInvestment' must be a number.", 400, None)

    risk_category = data["riskCategory"]
    if not isinstance(risk_category, str):
        return None, ("'riskCategory' must be a string.", 400, None)
    risk_category = risk_category.strip().lower()

    # Range / domain validation.
    if not (MIN_AGE <= current_age <= MAX_AGE):
        return None, (
            f"'currentAge' must be between {MIN_AGE} and {MAX_AGE}.",
            400,
            {"currentAge": current_age},
        )

    if not (MIN_AGE <= retirement_age <= MAX_AGE):
        return None, (
            f"'retirementAge' must be between {MIN_AGE} and {MAX_AGE}.",
            400,
            {"retirementAge": retirement_age},
        )

    if retirement_age <= current_age:
        return None, (
            "'retirementAge' must be greater than 'currentAge'.",
            400,
            {"currentAge": current_age, "retirementAge": retirement_age},
        )

    if desired_fund <= 0:
        return None, (
            "'desiredFund' must be a positive value.",
            400,
            {"desiredFund": desired_fund},
        )

    if monthly_investment <= 0:
        return None, (
            "'monthlyInvestment' must be a positive value.",
            400,
            {"monthlyInvestment": monthly_investment},
        )

    if risk_category not in VALID_RISK_CATEGORIES:
        return None, (
            "'riskCategory' must be one of: low, medium, high.",
            400,
            {"riskCategory": risk_category},
        )

    parsed = {
        "current_age": current_age,
        "retirement_age": retirement_age,
        "desired_fund": desired_fund,
        "monthly_investment": monthly_investment,
        "risk_category": risk_category,
        "years_to_invest": retirement_age - current_age,
    }
    return parsed, None


@app.route("/health", methods=["GET"])
def health():
    """Simple liveness/readiness health check endpoint."""
    return jsonify({"status": "ok"}), 200


@app.route("/ready", methods=["GET"])
def ready():
    """
    Readiness probe. Distinct from /health (liveness): reports whether the app
    has the pieces it needs to serve a recommendation. For Phase 0 this checks
    that the ticker universe loaded; later phases will also check DB / model
    store connectivity. Returns 200 when ready, 503 otherwise.
    """
    checks = {"universe_loaded": bool(investment_tickers)}
    ready_ok = all(checks.values())
    status_code = 200 if ready_ok else 503
    return jsonify({"status": "ready" if ready_ok else "not ready", "checks": checks}), status_code


# --------------------------------------------------------------------------- #
# Request-id + timing middleware (observability; non-behavioural)
# --------------------------------------------------------------------------- #
@app.before_request
def _assign_request_id():
    """Attach a request id (honouring an inbound X-Request-ID) and start timer."""
    g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    g.request_start = time.perf_counter()


# Paths subject to rate limiting, mapped to (bucket, per-minute setting attr).
_RATE_LIMITED_PATHS = {
    "/investment-strategy": ("recommend", "rate_limit_per_minute"),
    "/v1/investment-strategy": ("recommend", "rate_limit_per_minute"),
    "/v1/auth/register": ("auth", "auth_rate_limit_per_minute"),
    "/v1/auth/login": ("auth", "auth_rate_limit_per_minute"),
    "/v1/auth/refresh": ("auth", "auth_rate_limit_per_minute"),
}


def _rate_limit_enabled() -> bool:
    """Rate limiting is on by config, but OFF under tests unless opted in."""
    try:
        from settings import get_settings

        s = get_settings()
        if not s.rate_limit_enabled:
            return False
    except Exception:  # noqa: BLE001
        return False
    # Disable under Flask TESTING unless a test explicitly flips it on.
    if app.config.get("TESTING") and not app.config.get("RATE_LIMIT_IN_TESTS"):
        return False
    return True


@app.before_request
def _enforce_rate_limit():
    """Fixed-window per-client rate limit on recommendation + auth endpoints."""
    if request.method != "POST":
        return None
    rule = _RATE_LIMITED_PATHS.get(request.path)
    if rule is None or not _rate_limit_enabled():
        return None

    bucket, limit_attr = rule
    try:
        from settings import get_settings

        s = get_settings()
        limit = getattr(s, limit_attr)
    except Exception:  # noqa: BLE001
        return None

    # Client id: authenticated token subject if present, else remote IP.
    client_id = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        client_id = "tok:" + auth_header[7:][:32]
    if not client_id:
        client_id = "ip:" + (request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip())

    import ratelimit

    allowed, retry_after = ratelimit.check_rate_limit(bucket, client_id, limit)
    if not allowed:
        logger.info("Rate limit exceeded: bucket=%s client=%s", bucket, client_id)
        resp = jsonify({"error": "Too many requests. Please slow down.", "status": 429})
        resp.status_code = 429
        resp.headers["Retry-After"] = str(retry_after)
        return resp
    return None


@app.after_request
def _attach_request_id_header(response):
    """Echo the request id and log request latency."""
    request_id = getattr(g, "request_id", None)
    if request_id:
        response.headers["X-Request-ID"] = request_id
    start = getattr(g, "request_start", None)
    if start is not None:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "request complete method=%s path=%s status=%s request_id=%s elapsed_ms=%.1f",
            request.method,
            request.path,
            response.status_code,
            request_id,
            elapsed_ms,
        )
    return response


# --------------------------------------------------------------------------- #
# MPT recommendation helper (Phase 3)
# --------------------------------------------------------------------------- #
_PROFILE_SNAKE = {
    "annualIncome": "annual_income",
    "incomeCurrency": "income_currency",
    "dependents": "dependents",
    "existingSavings": "existing_savings",
    "monthlyDebtEmi": "monthly_debt_emi",
    "emergencyFundMonths": "emergency_fund_months",
    "experienceLevel": "experience_level",
    "liquidityNeedHorizonMonths": "liquidity_need_horizon_months",
    "countryOfResidence": "country_of_residence",
    "baseCurrency": "base_currency",
}
_PREFERENCES_SNAKE = {
    "preferredSectors": "preferred_sectors",
    "assetClassPrefs": "asset_class_prefs",
    "esgOnly": "esg_only",
    "shariahOnly": "shariah_only",
    "ethicalFlags": "ethical_flags",
    "excludedSectors": "excluded_sectors",
    "excludedTickers": "excluded_tickers",
    "existingHoldings": "existing_holdings",
    "allowCrypto": "allow_crypto",
    "allowInternational": "allow_international",
}


def _camel_to_snake(obj, mapping):
    """Map a camelCase request object to the snake_case keys the engine uses.
    Accepts snake_case passthrough too (so either style works)."""
    if not isinstance(obj, dict):
        return {}
    out = {}
    snake_values = set(mapping.values())
    for k, v in obj.items():
        if k in mapping:
            out[mapping[k]] = v
        elif k in snake_values:
            out[k] = v
    return out


def _try_mpt_recommendation(data, parsed):
    """
    Attempt the Phase 3 MPT recommendation using the model/metrics store.

    Returns:
        None            -> store not populated; caller falls back to legacy.
        (200, payload)  -> success.
        (422, {...})    -> infeasible / over-restrictive request.
    """
    try:
        from jobs import store

        inputs = store.current_optimizer_inputs()
    except Exception:  # noqa: BLE001
        logger.debug("Store optimizer inputs unavailable.", exc_info=True)
        return None

    if not inputs or not inputs.get("tickers"):
        return None  # store empty -> legacy path

    objective = data.get("objective")
    profile = _camel_to_snake(data.get("profile") or {}, _PROFILE_SNAKE)
    preferences = _camel_to_snake(data.get("preferences") or {}, _PREFERENCES_SNAKE)
    options = data.get("options") or {}

    try:
        from engine import InfeasibleError
        from engine.recommend import recommend
        from jobs.store import price_returns_frame

        price_returns = price_returns_frame(inputs["tickers"])
        _t = time.perf_counter()
        payload = recommend(
            inputs,
            years=parsed["years_to_invest"],
            target_fund=parsed["desired_fund"],
            monthly_investment=parsed["monthly_investment"],
            risk_category=parsed["risk_category"],
            objective=objective,
            profile=profile,
            preferences=preferences,
            options=options,
            price_returns=price_returns,
        )
        logger.info(
            "engine timing: MPT recommend took %.1f ms (request_id=%s)",
            (time.perf_counter() - _t) * 1000.0,
            getattr(g, "request_id", None),
        )
        _write_audit_log(inputs, parsed, objective, profile, preferences, options, payload)
        return (200, payload)
    except InfeasibleError as exc:
        logger.info("MPT recommendation infeasible: %s", exc)
        return (422, {"error": str(exc)})
    except ValueError as exc:
        return (422, {"error": str(exc)})
    except Exception:  # noqa: BLE001 - any engine failure -> legacy fallback
        logger.exception("MPT recommendation failed; falling back to legacy path.")
        return None


def _user_id_from_bearer():
    """Best-effort: extract the user id from a Bearer access token, or None.
    Used for audit attribution on the (un-decorated) recommendation route."""
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[len("Bearer ") :].strip()
        from auth import decode_token
        from settings import get_settings

        s = get_settings()
        payload = decode_token(token, s.jwt_secret, s.jwt_algorithm, expected_type="access")
        return payload.get("sub")
    except Exception:  # noqa: BLE001
        return None


def _write_audit_log(inputs, parsed, objective, profile, preferences, options, payload):
    """
    Append-only audit record for a served recommendation (Phase 4). Best-effort:
    any failure is logged and swallowed so it never breaks the response. Stores
    PII-minimised inputs (no contact PII) + the versions/config/seed needed to
    reproduce the result.
    """
    try:
        import db

        if not db.is_enabled():
            return
        from db.models import AuditLog

        try:
            from auth import current_user_id

            uid = current_user_id()
            if not uid:
                # The recommendation route isn't auth-decorated, so g.user_id may
                # be unset. Decode a Bearer token directly (best-effort) so authed
                # recommendations are attributed to the user for the audit trail.
                uid = _user_id_from_bearer()
        except Exception:  # noqa: BLE001
            uid = None

        import uuid as _uuid

        user_uuid = None
        if uid:
            try:
                user_uuid = _uuid.UUID(uid)
            except (ValueError, TypeError):
                user_uuid = None

        projection = payload.get("projection") or {}
        session = db.get_session()
        session.add(
            AuditLog(
                request_id=getattr(g, "request_id", None),
                user_id=user_uuid,
                # PII-minimised: inputs used for the recommendation, in the exact
                # shape engine.reproduce() expects. No email/contact PII.
                input_snapshot={
                    "years": parsed["years_to_invest"],
                    "desiredFund": parsed["desired_fund"],
                    "monthlyInvestment": parsed["monthly_investment"],
                    "riskCategory": parsed["risk_category"],
                    "objective": objective,
                    "profile": profile or {},
                    "preferences": preferences or {},
                    "options": options or {},
                },
                metrics_version_id=(
                    _uuid.UUID(inputs["version_id"]) if inputs.get("version_id") else None
                ),
                data_source="price_series",  # dev source; licensed provider in prod
                optimizer_config=payload.get("optimizerConfig"),
                lstm_view_applied=bool(payload.get("lstmViewApplied")),
                disclaimer_version=(payload.get("disclaimer") or {}).get("version"),
                rng_seed=projection.get("rngSeed"),
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001 - auditing must never break the response
        logger.warning("Failed to write audit log row (non-fatal).", exc_info=True)
        try:
            import db

            db.get_session().rollback()
        except Exception:  # noqa: BLE001
            pass


@app.route("/investment-strategy", methods=["POST"])
@app.route("/v1/investment-strategy", methods=["POST"])
def investment_strategy():
    """Compute an investment strategy from the validated request payload."""
    data = request.get_json(silent=True)
    if data is None:
        logger.warning("Received request with missing or invalid JSON body.")
        return _error_response("Request body must be valid JSON.", 400)

    parsed, error = _validate_payload(data)
    if error is not None:
        message, status_code, details = error
        logger.warning("Validation failed: %s (details=%s)", message, details)
        return _error_response(message, status_code, details)

    logger.info(
        "Processing investment strategy: risk=%s, years=%s, monthly=%s, target=%s",
        parsed["risk_category"],
        parsed["years_to_invest"],
        parsed["monthly_investment"],
        parsed["desired_fund"],
    )

    # ---- Phase 3: MPT path (used when the model/metrics store is populated) ----
    # The optional profile/preferences/objective/options are parsed defensively;
    # absence => default behaviour. If the store has covariance inputs we run the
    # MPT engine; otherwise we fall through to the legacy fetch+suggest path.
    mpt_result = _try_mpt_recommendation(data, parsed)
    if mpt_result is not None:
        status, payload = mpt_result
        if status == 200:
            logger.info("Served recommendation via MPT engine.")
            return jsonify(payload), 200
        # Domain infeasibility -> 422 with a helpful message.
        return _error_response(payload.get("error", "Infeasible request."), status, payload.get("details"))

    try:
        _t_fetch = time.perf_counter()
        df_stocks = fetch_stock_data(investment_tickers)
        logger.info(
            "engine timing: fetch_stock_data took %.1f ms (request_id=%s)",
            (time.perf_counter() - _t_fetch) * 1000.0,
            getattr(g, "request_id", None),
        )
    except Exception:  # noqa: BLE001 - surface a clean 502 to the client
        logger.exception("Failed to fetch stock data.")
        return _error_response(
            "Failed to fetch stock market data. Please try again later.", 502
        )

    if df_stocks is None or df_stocks.empty:
        logger.error("Stock data fetch returned no usable data.")
        return _error_response(
            "No stock market data is currently available. Please try again later.",
            503,
        )

    try:
        _t_compute = time.perf_counter()
        result = suggest_investment(
            df_stocks,
            parsed["years_to_invest"],
            parsed["desired_fund"],
            parsed["monthly_investment"],
            parsed["risk_category"],
        )
        logger.info(
            "engine timing: suggest_investment took %.1f ms (request_id=%s)",
            (time.perf_counter() - _t_compute) * 1000.0,
            getattr(g, "request_id", None),
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to compute investment strategy.")
        return _error_response(
            "An internal error occurred while computing the strategy.", 500
        )

    # suggest_investment always returns a dict; surface domain errors as 422.
    if isinstance(result, dict) and "error" in result:
        logger.info("Strategy computation returned a domain error: %s", result["error"])
        return _error_response(result["error"], 422, result.get("details"))

    logger.info("Successfully computed investment strategy.")
    return jsonify(result), 200


@app.errorhandler(404)
def not_found(_error):
    return _error_response("The requested resource was not found.", 404)


@app.errorhandler(405)
def method_not_allowed(_error):
    return _error_response("Method not allowed for this endpoint.", 405)


@app.errorhandler(500)
def internal_server_error(_error):
    logger.exception("Unhandled internal server error.")
    return _error_response("An internal server error occurred.", 500)


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=DEBUG_MODE)
