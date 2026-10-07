"""
SQLAlchemy ORM models for Phase 1 (persistence & identity).

Implements the subset of docs/production-plan/03-data-model.md needed for
Phase 1: users, user_credentials, financial_profiles, preferences,
saved_plans, recommendation_snapshots, and model_metrics_cache.

Portability note
----------------
The production target is PostgreSQL, but the test suite uses SQLite. We use
SQLAlchemy's cross-dialect generic types:
  - ``Uuid`` -> native uuid on Postgres, CHAR(32) on SQLite
  - ``JSON``  -> JSONB on Postgres (via variant), JSON text on SQLite
This keeps a single model definition working on both. PII protection
(column-level encryption) is layered in the application/service code; the
columns here hold the stored (cipher or plain-in-dev) values.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from db import Base

# JSON that is JSONB on Postgres and plain JSON elsewhere (SQLite in tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    # Email is optional at the schema level (anonymous users), but the auth
    # flow requires it for registration. Unique when present.
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    auth_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    auth_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    credential: Mapped["UserCredential"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    profile: Mapped["FinancialProfile"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    preferences: Mapped["Preferences"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    plans: Mapped[list["SavedPlan"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserCredential(Base):
    """
    Email+password credentials, kept in a dedicated table (per 03-data-model.md)
    so password material never lives on the users row. Stores only an Argon2id
    hash, never the plaintext.
    """

    __tablename__ = "user_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="credential")


class FinancialProfile(Base):
    """Optional financial profile; all fields nullable with graceful defaults."""

    __tablename__ = "financial_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    annual_income: Mapped[float | None] = mapped_column(Float, nullable=True)
    income_currency: Mapped[str | None] = mapped_column(String(3), default="INR", nullable=True)
    dependents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    existing_savings: Mapped[float | None] = mapped_column(Float, nullable=True)
    monthly_debt_emi: Mapped[float | None] = mapped_column(Float, nullable=True)
    emergency_fund_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    experience_level: Mapped[str | None] = mapped_column(
        String(16), default="beginner", nullable=True
    )
    liquidity_need_horizon_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    country_of_residence: Mapped[str | None] = mapped_column(String(2), default="IN", nullable=True)
    base_currency: Mapped[str | None] = mapped_column(String(3), default="INR", nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="profile")


class Preferences(Base):
    """Optional investment preferences (filters/tilts/constraints for Phase 3)."""

    __tablename__ = "preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    preferred_sectors: Mapped[list | None] = mapped_column(JSONType, default=list)
    asset_class_prefs: Mapped[dict | None] = mapped_column(JSONType, default=dict)
    esg_only: Mapped[bool] = mapped_column(Boolean, default=False)
    shariah_only: Mapped[bool] = mapped_column(Boolean, default=False)
    ethical_flags: Mapped[list | None] = mapped_column(JSONType, default=list)
    excluded_sectors: Mapped[list | None] = mapped_column(JSONType, default=list)
    excluded_tickers: Mapped[list | None] = mapped_column(JSONType, default=list)
    existing_holdings: Mapped[list | None] = mapped_column(JSONType, default=list)
    allow_crypto: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_international: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="preferences")


class RecommendationSnapshot(Base):
    """Immutable record of a served recommendation (referenced by saved plans)."""

    __tablename__ = "recommendation_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    api_version: Mapped[str] = mapped_column(String(16), default="v1")
    objective: Mapped[str | None] = mapped_column(String(32), nullable=True)
    weights: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    allocation: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    backtest_metrics: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    projection: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    explanation: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    metrics_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lstm_view_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    disclaimer_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SavedPlan(Base):
    """A user-named, retrievable plan: inputs + pointer to the result snapshot."""

    __tablename__ = "saved_plans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    recommendation_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("recommendation_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped[User] = relationship(back_populates="plans")
    snapshot: Mapped[RecommendationSnapshot | None] = relationship()


class ModelMetricsCache(Base):
    """
    DB-backed replacement for saved_models/_cached_metrics.json. A single-row
    cache (id='current') holding the last computed per-ticker metrics list, so
    the app can serve when the live data source is unreachable. The JSON-file
    fallback is preserved in the engine for local dev.
    """

    __tablename__ = "model_metrics_cache"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default="current")
    metrics: Mapped[list] = mapped_column(JSONType, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


# =========================================================================== #
# Phase 2: investment universe + versioned model/metrics store
# =========================================================================== #

class Instrument(Base):
    """
    The investment universe (graduates from tickers.json). Carries the metadata
    the Phase 3 filters need (sector, ESG/Shariah, liquidity, country).
    """

    __tablename__ = "instruments"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    asset_class: Mapped[str] = mapped_column(String(64), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    esg_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    shariah_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    # text[] on Postgres; JSONType keeps it portable to SQLite in tests.
    ethical_tags: Mapped[list | None] = mapped_column(JSONType, default=list)
    liquidity_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class PriceSeries(Base):
    """
    Clean OHLCV price series produced by the ingestion job. One row per
    (ticker, date). The analytics job reads these to compute metrics +
    covariance, so the request path never fetches from a market-data provider.
    """

    __tablename__ = "price_series"

    ticker: Mapped[str] = mapped_column(
        String(32), ForeignKey("instruments.ticker", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)


class ModelMetricsVersion(Base):
    """
    A versioned snapshot of the analytics output. Exactly one row has
    is_current=True (atomic flip); older versions are retained for audit.
    """

    __tablename__ = "model_metrics_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    data_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    instrument_metrics: Mapped[list["InstrumentMetric"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )
    covariance: Mapped["CovarianceArtifact"] = relationship(
        back_populates="version", uselist=False, cascade="all, delete-orphan"
    )
    lstm_views: Mapped[list["LstmView"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class InstrumentMetric(Base):
    """Per-instrument precomputed metrics for a given version."""

    __tablename__ = "instrument_metrics"

    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_metrics_versions.id", ondelete="CASCADE"), primary_key=True
    )
    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    asset_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_annual_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_profile: Mapped[str | None] = mapped_column(String(16), nullable=True)

    version: Mapped[ModelMetricsVersion] = relationship(back_populates="instrument_metrics")


class CovarianceArtifact(Base):
    """
    Covariance matrix for a version. Production stores the serialized matrix in
    object storage (storage_uri). For local/dev we also allow an inline matrix
    (JSON) so the pipeline is fully runnable without external storage.
    """

    __tablename__ = "covariance_artifacts"

    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_metrics_versions.id", ondelete="CASCADE"), primary_key=True
    )
    storage_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ticker_order: Mapped[list] = mapped_column(JSONType, nullable=False)
    matrix_inline: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    shrinkage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)

    version: Mapped[ModelMetricsVersion] = relationship(back_populates="covariance")


class LstmView(Base):
    """Optional per-instrument LSTM return view (Black-Litterman input, Phase 3)."""

    __tablename__ = "lstm_views"

    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_metrics_versions.id", ondelete="CASCADE"), primary_key=True
    )
    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    view_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    version: Mapped[ModelMetricsVersion] = relationship(back_populates="lstm_views")


# =========================================================================== #
# Phase 4: audit log (append-only) for reproducibility & compliance
# =========================================================================== #

class AuditLog(Base):
    """
    One append-only row per recommendation served (anonymous or authenticated).

    Enables reproducibility (re-run the engine from the recorded inputs + the
    referenced metrics version + the recorded RNG seed -> same output) and a
    compliance trail (which data source, model version, optimizer config, and
    disclaimer version produced what the user saw).

    PII minimisation: ``input_snapshot`` stores the recommendation INPUTS only
    (ages, amounts, risk, preferences) — never contact PII. ``user_id`` is a
    reference, not an email, and is nulled on right-to-be-forgotten de-identify.
    No updates or deletes in normal operation (append-only); the deletion
    workflow only de-identifies (user_id -> null).
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_uuid)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    input_snapshot: Mapped[dict] = mapped_column(JSONType, nullable=False)
    recommendation_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    metrics_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    data_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    optimizer_config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    lstm_view_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    disclaimer_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rng_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
