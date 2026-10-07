"""
Typed application settings for the Flask backend (Phase 0).

Formalises the previously ad-hoc ``os.environ.get(...)`` configuration into a
single, validated, documented settings object using pydantic-settings.

Design goals
------------
- **No behaviour change.** Defaults and parsing semantics match the original
  inline configuration in server.py and investment_strategy.py exactly:
    * FLASK_DEBUG   : truthy if one of {"1","true","yes"} (case-insensitive)
    * FLASK_PORT    : int, default 5000
    * FLASK_HOST    : str, default "0.0.0.0"
    * CORS_ORIGINS  : "*" means allow-all; otherwise a comma-separated list
    * LOG_LEVEL     : str, default "INFO" (upper-cased)
    * RISK_FREE_RATE / LSTM_EPOCHS / MC_SIMULATIONS / TICKERS_CONFIG_PATH:
      documented here for completeness; the engine still reads its own copies
      so importing this module is not required by investment_strategy.py.
- Environment variables remain the single source of truth, so existing
  deployments and run.sh keep working unchanged.

Usage
-----
    from settings import get_settings
    settings = get_settings()
    app.run(host=settings.host, port=settings.port, debug=settings.debug)
"""

from functools import lru_cache
from typing import List, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application configuration, sourced from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Flask server ---
    debug: bool = Field(default=False, alias="FLASK_DEBUG")
    port: int = Field(default=5000, alias="FLASK_PORT")
    host: str = Field(default="0.0.0.0", alias="FLASK_HOST")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Raw CORS origins string; parsed into `cors_origins` below.
    cors_origins_raw: str = Field(default="*", alias="CORS_ORIGINS")

    # --- Phase 1: database & auth ---
    # When unset/empty, the app runs in stateless mode (Phase 0 behaviour):
    # anonymous recommendations work, but persistence endpoints return 503.
    database_url: str = Field(default="", alias="DATABASE_URL")

    # JWT signing. In production this MUST come from a secrets manager; the
    # insecure default exists only so local/dev and tests can run. The app logs
    # a warning when the default is in use.
    jwt_secret: str = Field(default="dev-insecure-change-me", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_ttl_minutes: int = Field(default=15, alias="JWT_ACCESS_TTL_MINUTES")
    jwt_refresh_ttl_days: int = Field(default=30, alias="JWT_REFRESH_TTL_DAYS")

    # --- Phase 4: rate limiting / abuse protection ---
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_per_minute: int = Field(default=30, alias="RATE_LIMIT_PER_MINUTE")
    auth_rate_limit_per_minute: int = Field(default=10, alias="AUTH_RATE_LIMIT_PER_MINUTE")

    # --- Engine knobs (documented; engine reads its own env copies) ---
    risk_free_rate: float = Field(default=7.0, alias="RISK_FREE_RATE")
    lstm_epochs: int = Field(default=10, alias="LSTM_EPOCHS")
    mc_simulations: int = Field(default=10000, alias="MC_SIMULATIONS")
    tickers_config_path: str = Field(default="", alias="TICKERS_CONFIG_PATH")

    @field_validator("debug", mode="before")
    @classmethod
    def _parse_debug(cls, v):
        # Match the original semantics exactly: truthy for 1/true/yes.
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        return str(v).strip().lower() in ("1", "true", "yes")

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, v):
        return str(v).upper() if v is not None else "INFO"

    @field_validator("rate_limit_enabled", mode="before")
    @classmethod
    def _parse_rate_limit_enabled(cls, v):
        if isinstance(v, bool):
            return v
        if v is None:
            return True
        return str(v).strip().lower() in ("1", "true", "yes")

    @property
    def cors_origins(self) -> Union[str, List[str]]:
        """
        Parse CORS origins: ``"*"`` means allow-all; otherwise a cleaned
        comma-separated list. Mirrors the original server.py logic.
        """
        raw = (self.cors_origins_raw or "*").strip()
        if raw == "*":
            return "*"
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def persistence_enabled(self) -> bool:
        """True when a DATABASE_URL is configured (persistence features on)."""
        return bool(self.database_url.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance (parsed once per process)."""
    return Settings()
