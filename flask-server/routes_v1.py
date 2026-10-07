"""
Versioned API routes (/v1) for Phase 1: auth, profile, preferences, plans.

All persistence endpoints require a database; if none is configured they return
503 via the auth decorators / explicit guards. The anonymous recommendation
path lives in server.py and is unaffected.

Endpoints
---------
POST /v1/auth/register   {email, password, display_name?}  -> tokens
POST /v1/auth/login      {email, password}                 -> tokens
POST /v1/auth/refresh    {refresh_token}                   -> new access token
GET  /v1/profile                                           (auth) -> profile
PUT  /v1/profile         {<optional fields>}               (auth) -> profile
GET  /v1/preferences                                       (auth) -> preferences
PUT  /v1/preferences     {<optional fields>}               (auth) -> preferences
POST /v1/plans           {name?, input_payload, recommendation?} (auth) -> plan
GET  /v1/plans                                             (auth) -> [plans]
GET  /v1/plans/<id>                                        (auth, owner) -> plan
"""

from __future__ import annotations

import logging
import uuid

import jwt
from flask import Blueprint, jsonify, request

import db
from auth import (
    auth_required,
    current_user_id,
    decode_token,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    verify_password,
)
from db.models import FinancialProfile, Preferences, RecommendationSnapshot, SavedPlan, User

logger = logging.getLogger(__name__)

v1 = Blueprint("v1", __name__, url_prefix="/v1")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _err(message, status, details=None):
    payload = {"error": message, "status": status}
    if details is not None:
        payload["details"] = details
    return jsonify(payload), status


def _settings():
    from settings import get_settings

    return get_settings()


def _require_db():
    """Return an error response tuple if persistence is disabled, else None."""
    if not db.is_enabled():
        return _err("Persistence is not enabled on this server.", 503)
    return None


def _issue_tokens(user_id: str) -> dict:
    s = _settings()
    return {
        "access_token": issue_access_token(user_id, s.jwt_secret, s.jwt_algorithm, s.jwt_access_ttl_minutes),
        "refresh_token": issue_refresh_token(user_id, s.jwt_secret, s.jwt_algorithm, s.jwt_refresh_ttl_days),
        "token_type": "Bearer",
        "expires_in": s.jwt_access_ttl_minutes * 60,
    }


# Allowed, writable fields per resource (whitelist — never trust the client blob).
_PROFILE_FIELDS = {
    "annual_income", "income_currency", "dependents", "existing_savings",
    "monthly_debt_emi", "emergency_fund_months", "experience_level",
    "liquidity_need_horizon_months", "country_of_residence", "base_currency",
}
_PREFERENCES_FIELDS = {
    "preferred_sectors", "asset_class_prefs", "esg_only", "shariah_only",
    "ethical_flags", "excluded_sectors", "excluded_tickers", "existing_holdings",
    "allow_crypto", "allow_international",
}


def _serialize_profile(p: FinancialProfile) -> dict:
    return {f: getattr(p, f) for f in _PROFILE_FIELDS}


def _serialize_preferences(p: Preferences) -> dict:
    return {f: getattr(p, f) for f in _PREFERENCES_FIELDS}


def _serialize_plan(plan: SavedPlan) -> dict:
    out = {
        "id": str(plan.id),
        "name": plan.name,
        "input_payload": plan.input_payload,
        "recommendation_snapshot_id": (
            str(plan.recommendation_snapshot_id) if plan.recommendation_snapshot_id else None
        ),
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }
    if plan.snapshot is not None:
        out["recommendation"] = {
            "allocation": plan.snapshot.allocation,
            "projection": plan.snapshot.projection,
            "objective": plan.snapshot.objective,
            "api_version": plan.snapshot.api_version,
        }
    return out


# --------------------------------------------------------------------------- #
# Auth endpoints
# --------------------------------------------------------------------------- #

@v1.route("/auth/register", methods=["POST"])
def register():
    guard = _require_db()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    display_name = data.get("display_name")

    if not email or "@" not in email:
        return _err("A valid 'email' is required.", 400)
    if len(password) < 8:
        return _err("'password' must be at least 8 characters.", 400)

    session = db.get_session()
    existing = session.query(User).filter(User.email == email).first()
    if existing is not None:
        return _err("An account with this email already exists.", 409)

    user = User(email=email, auth_provider="password", display_name=display_name)
    from db.models import UserCredential

    user.credential = UserCredential(password_hash=hash_password(password))
    # Create empty profile + preferences rows so PUT has something to update.
    user.profile = FinancialProfile()
    user.preferences = Preferences()
    session.add(user)
    session.commit()

    tokens = _issue_tokens(str(user.id))
    return jsonify({"user": {"id": str(user.id), "email": user.email}, **tokens}), 201


@v1.route("/auth/login", methods=["POST"])
def login():
    guard = _require_db()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    session = db.get_session()
    user = session.query(User).filter(User.email == email).first()
    if user is None or user.credential is None or not verify_password(
        user.credential.password_hash, password
    ):
        # Uniform error to avoid leaking which part failed.
        return _err("Invalid email or password.", 401)

    tokens = _issue_tokens(str(user.id))
    return jsonify({"user": {"id": str(user.id), "email": user.email}, **tokens}), 200


@v1.route("/auth/refresh", methods=["POST"])
def refresh():
    guard = _require_db()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    refresh_token = data.get("refresh_token") or ""
    if not refresh_token:
        return _err("'refresh_token' is required.", 400)

    s = _settings()
    try:
        payload = decode_token(refresh_token, s.jwt_secret, s.jwt_algorithm, expected_type="refresh")
    except jwt.ExpiredSignatureError:
        return _err("Refresh token has expired.", 401)
    except jwt.InvalidTokenError:
        return _err("Invalid refresh token.", 401)

    user_id = payload["sub"]
    access = issue_access_token(user_id, s.jwt_secret, s.jwt_algorithm, s.jwt_access_ttl_minutes)
    return jsonify({"access_token": access, "token_type": "Bearer", "expires_in": s.jwt_access_ttl_minutes * 60}), 200


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #

@v1.route("/profile", methods=["GET"])
@auth_required
def get_profile():
    session = db.get_session()
    profile = session.get(FinancialProfile, uuid.UUID(current_user_id()))
    if profile is None:
        profile = FinancialProfile(user_id=uuid.UUID(current_user_id()))
        session.add(profile)
        session.commit()
    return jsonify(_serialize_profile(profile)), 200


@v1.route("/profile", methods=["PUT"])
@auth_required
def update_profile():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err("Request body must be a JSON object.", 400)

    session = db.get_session()
    uid = uuid.UUID(current_user_id())
    profile = session.get(FinancialProfile, uid)
    if profile is None:
        profile = FinancialProfile(user_id=uid)
        session.add(profile)

    for field, value in data.items():
        if field in _PROFILE_FIELDS:
            setattr(profile, field, value)
    session.commit()
    return jsonify(_serialize_profile(profile)), 200


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #

@v1.route("/preferences", methods=["GET"])
@auth_required
def get_preferences():
    session = db.get_session()
    prefs = session.get(Preferences, uuid.UUID(current_user_id()))
    if prefs is None:
        prefs = Preferences(user_id=uuid.UUID(current_user_id()))
        session.add(prefs)
        session.commit()
    return jsonify(_serialize_preferences(prefs)), 200


@v1.route("/preferences", methods=["PUT"])
@auth_required
def update_preferences():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err("Request body must be a JSON object.", 400)

    session = db.get_session()
    uid = uuid.UUID(current_user_id())
    prefs = session.get(Preferences, uid)
    if prefs is None:
        prefs = Preferences(user_id=uid)
        session.add(prefs)

    for field, value in data.items():
        if field in _PREFERENCES_FIELDS:
            setattr(prefs, field, value)
    session.commit()
    return jsonify(_serialize_preferences(prefs)), 200


# --------------------------------------------------------------------------- #
# Saved plans (owner-scoped)
# --------------------------------------------------------------------------- #

@v1.route("/plans", methods=["POST"])
@auth_required
def create_plan():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err("Request body must be a JSON object.", 400)

    input_payload = data.get("input_payload")
    if not isinstance(input_payload, dict):
        return _err("'input_payload' (object) is required.", 400)

    session = db.get_session()
    uid = uuid.UUID(current_user_id())

    snapshot_id = None
    recommendation = data.get("recommendation")
    if isinstance(recommendation, dict):
        snapshot = RecommendationSnapshot(
            api_version=recommendation.get("api_version", "v1"),
            objective=recommendation.get("objective"),
            weights=recommendation.get("weights"),
            allocation=recommendation.get("allocation") or recommendation.get("Investment Suggestions"),
            backtest_metrics=recommendation.get("backtest"),
            projection=recommendation.get("projection"),
            explanation=recommendation.get("explanation"),
            disclaimer_version=recommendation.get("disclaimer_version"),
        )
        session.add(snapshot)
        session.flush()  # populate snapshot.id
        snapshot_id = snapshot.id

    plan = SavedPlan(
        user_id=uid,
        name=data.get("name"),
        input_payload=input_payload,
        recommendation_snapshot_id=snapshot_id,
    )
    session.add(plan)
    session.commit()
    return jsonify(_serialize_plan(plan)), 201


@v1.route("/plans", methods=["GET"])
@auth_required
def list_plans():
    session = db.get_session()
    uid = uuid.UUID(current_user_id())
    plans = (
        session.query(SavedPlan)
        .filter(SavedPlan.user_id == uid)
        .order_by(SavedPlan.created_at.desc())
        .all()
    )
    return jsonify({"plans": [_serialize_plan(p) for p in plans]}), 200


@v1.route("/plans/<plan_id>", methods=["GET"])
@auth_required
def get_plan(plan_id):
    try:
        pid = uuid.UUID(plan_id)
    except (ValueError, TypeError):
        return _err("Invalid plan id.", 400)

    session = db.get_session()
    plan = session.get(SavedPlan, pid)
    if plan is None:
        return _err("Plan not found.", 404)
    # Owner scoping: never reveal another user's plan.
    if str(plan.user_id) != current_user_id():
        return _err("You do not have access to this plan.", 403)
    return jsonify(_serialize_plan(plan)), 200



# --------------------------------------------------------------------------- #
# Account deletion (right-to-be-forgotten, Phase 4)
# --------------------------------------------------------------------------- #

@v1.route("/account", methods=["DELETE"])
@auth_required
def delete_account_endpoint():
    """
    Erase the authenticated user's personal data (profile, preferences, saved
    plans + their snapshots, credentials, and the user row) and de-identify
    their audit-log rows. Irreversible.
    """
    import retention

    uid = current_user_id()
    try:
        summary = retention.delete_account(uid)
    except Exception:  # noqa: BLE001
        logger.exception("Account deletion failed for user %s.", uid)
        return _err("Account deletion failed. Please try again later.", 500)
    return jsonify({"status": "deleted", "erased": summary}), 200
