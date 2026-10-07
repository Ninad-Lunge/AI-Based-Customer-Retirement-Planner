"""
Alembic migration environment.

Reads the database URL from DATABASE_URL (via the app settings) so migrations
run against SQLite, docker Postgres, or production unchanged. Targets the
project's SQLAlchemy metadata for autogenerate support.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the app package importable (alembic runs from flask-server/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Base  # noqa: E402
import db.models  # noqa: E402,F401  (import registers all models on Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the DB URL from env/settings; required for migrations."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        try:
            from settings import get_settings

            url = get_settings().database_url.strip()
        except Exception:
            url = ""
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Alembic migrations require a database. "
            "Example: DATABASE_URL=postgresql+psycopg://crp:crp@localhost:5432/crp"
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI connection)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with a live DBAPI connection)."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        is_sqlite = connection.dialect.name == "sqlite"
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite can't ALTER many things; batch mode makes downgrades work.
            render_as_batch=is_sqlite,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
