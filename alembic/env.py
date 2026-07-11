"""Alembic environment, wired to the project's config and ORM metadata.

The database URL comes from the application settings (``.env``) so migrations
and the app never disagree, and no secret is hardcoded in ``alembic.ini``. Set
``ALEMBIC_DATABASE_URL`` to point Alembic at a different database (e.g. a
scratch DB when generating a revision).
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the ``app`` package importable when Alembic runs from the project root.
sys.path.insert(0, os.getcwd())

from app.config import get_settings  # noqa: E402
from app.db import Base  # noqa: E402
import app.models  # noqa: E402,F401  (register mappers on Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve the URL: explicit override wins, otherwise use the app settings.
database_url = os.getenv("ALEMBIC_DATABASE_URL") or get_settings().database_url
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DB connection)."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (against a live connection)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
