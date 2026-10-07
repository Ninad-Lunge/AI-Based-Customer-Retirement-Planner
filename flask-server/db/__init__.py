"""
Database layer: SQLAlchemy engine, session factory, and declarative Base.

Design
------
- The app runs in **two modes**:
    * stateless (no DATABASE_URL): Phase 0 behaviour. ``engine`` is None and
      ``is_enabled()`` returns False. Persistence endpoints return 503.
    * persistent (DATABASE_URL set): a real engine + scoped session are created.
- The engine is created lazily on first use so importing this module never
  requires a database (keeps unit tests and the stateless path fast).
- A FLASK `g`-scoped session is provided via ``get_session`` + ``shutdown_session``
  so each request gets its own session that is cleaned up afterwards.

The DATABASE_URL uses the psycopg3 driver, e.g.:
    postgresql+psycopg://user:pass@host:5432/dbname
SQLite is also supported (used by the test suite):
    sqlite:///./dev.db   or   sqlite+pysqlite:///:memory:
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, scoped_session, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Module-level singletons, initialised by init_engine().
_engine: Optional[Engine] = None
_SessionFactory: Optional[scoped_session] = None


def init_engine(database_url: str, echo: bool = False) -> Optional[Engine]:
    """
    Initialise the engine + session factory from a database URL.

    Returns the Engine, or None if ``database_url`` is empty (stateless mode).
    Safe to call more than once; subsequent calls with the same URL are no-ops.
    """
    global _engine, _SessionFactory

    if not database_url or not database_url.strip():
        logger.info("No DATABASE_URL configured; running in stateless mode.")
        return None

    if _engine is not None:
        return _engine

    # SQLite needs a special connect arg when used across threads (tests).
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    _engine = create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,  # recover from dropped DB connections gracefully
        future=True,
        connect_args=connect_args,
    )
    _SessionFactory = scoped_session(
        sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    )
    logger.info("Database engine initialised (%s).", _redact(database_url))
    return _engine


def is_enabled() -> bool:
    """True when a database engine has been initialised."""
    return _engine is not None and _SessionFactory is not None


def get_engine() -> Optional[Engine]:
    return _engine


def get_session() -> Session:
    """
    Return the current scoped session. Raises if persistence is disabled; call
    sites must check is_enabled() (or rely on the require-DB guard) first.
    """
    if _SessionFactory is None:
        raise RuntimeError("Database is not configured (no DATABASE_URL).")
    return _SessionFactory()


def shutdown_session(exc=None) -> None:
    """Remove the current scoped session (call at request teardown)."""
    if _SessionFactory is not None:
        _SessionFactory.remove()


def reset_engine_for_tests() -> None:
    """Dispose the engine and clear singletons. Test-only helper."""
    global _engine, _SessionFactory
    if _SessionFactory is not None:
        _SessionFactory.remove()
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


def _redact(url: str) -> str:
    """Hide credentials when logging a database URL."""
    if "@" in url and "//" in url:
        scheme, rest = url.split("//", 1)
        if "@" in rest:
            _, host = rest.split("@", 1)
            return f"{scheme}//***@{host}"
    return url
