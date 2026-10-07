"""
Authentication module (Phase 1).

Provides:
- Argon2id password hashing/verification (via argon2-cffi).
- JWT access + refresh token issue/verify (via PyJWT), signed with the
  configured secret and algorithm.
- Flask decorators: ``auth_required`` (401 if no/invalid token) and
  ``auth_optional`` (attaches the user id if a valid token is present, else
  leaves it None so anonymous flows keep working).

AUTH APPROACH (see Phase 1 notes / ADR-006)
-------------------------------------------
The production path is OAuth via a managed IdP. Because that needs external
provider setup, Phase 1 ships an email+password fallback — explicitly permitted
by the data model (a dedicated ``user_credentials`` table holding only an
Argon2id hash). JWTs are short-lived (access) with a longer-lived refresh token.
The design keeps the token/claims shape IdP-agnostic so OAuth can slot in later.
"""

from __future__ import annotations

import datetime as _dt
import logging
from functools import wraps
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from flask import g, jsonify, request

logger = logging.getLogger(__name__)

# Argon2id is the default variant for argon2-cffi's PasswordHasher.
_ph = PasswordHasher()

# Token type claim values.
_ACCESS = "access"
_REFRESH = "refresh"


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #

def hash_password(password: str) -> str:
    """Return an Argon2id hash for the given plaintext password."""
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a plaintext password against a stored Argon2id hash."""
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, Exception):  # noqa: BLE001
        return False


def needs_rehash(password_hash: str) -> bool:
    """True if the stored hash should be upgraded to current parameters."""
    try:
        return _ph.check_needs_rehash(password_hash)
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# JWT issue / verify
# --------------------------------------------------------------------------- #

def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _encode(subject: str, token_type: str, ttl: _dt.timedelta, secret: str, algorithm: str) -> str:
    now = _now()
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def issue_access_token(user_id: str, secret: str, algorithm: str, ttl_minutes: int) -> str:
    return _encode(
        user_id, _ACCESS, _dt.timedelta(minutes=ttl_minutes), secret, algorithm
    )


def issue_refresh_token(user_id: str, secret: str, algorithm: str, ttl_days: int) -> str:
    return _encode(
        user_id, _REFRESH, _dt.timedelta(days=ttl_days), secret, algorithm
    )


def decode_token(token: str, secret: str, algorithm: str, expected_type: Optional[str] = None) -> dict:
    """
    Decode and validate a JWT. Raises jwt exceptions on invalid/expired tokens.
    If ``expected_type`` is given, enforces the ``type`` claim.
    """
    payload = jwt.decode(token, secret, algorithms=[algorithm])
    if expected_type is not None and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(
            f"expected {expected_type} token, got {payload.get('type')}"
        )
    return payload


def _bearer_token_from_request() -> Optional[str]:
    """Extract a Bearer token from the Authorization header, if present."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :].strip()
    return None


def _settings():
    from settings import get_settings

    return get_settings()


# --------------------------------------------------------------------------- #
# Decorators
# --------------------------------------------------------------------------- #

def auth_required(fn):
    """
    Require a valid access token. On success sets ``g.user_id`` and calls the
    view; otherwise returns 401. Returns 503 if persistence is disabled.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        import db

        if not db.is_enabled():
            return jsonify({"error": "Persistence is not enabled on this server.", "status": 503}), 503

        token = _bearer_token_from_request()
        if not token:
            return jsonify({"error": "Authentication required.", "status": 401}), 401

        s = _settings()
        try:
            payload = decode_token(token, s.jwt_secret, s.jwt_algorithm, expected_type=_ACCESS)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token has expired.", "status": 401}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid authentication token.", "status": 401}), 401

        g.user_id = payload["sub"]
        return fn(*args, **kwargs)

    return wrapper


def auth_optional(fn):
    """
    Attach ``g.user_id`` if a valid access token is present; otherwise leave it
    None and proceed (anonymous). Never blocks the request on auth.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        g.user_id = None
        token = _bearer_token_from_request()
        if token:
            s = _settings()
            try:
                payload = decode_token(token, s.jwt_secret, s.jwt_algorithm, expected_type=_ACCESS)
                g.user_id = payload["sub"]
            except jwt.InvalidTokenError:
                g.user_id = None  # ignore bad token, stay anonymous
        return fn(*args, **kwargs)

    return wrapper


def current_user_id() -> Optional[str]:
    """Return the authenticated user id for the current request, or None."""
    return getattr(g, "user_id", None)
