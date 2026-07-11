"""SQLAlchemy engine, session factory and declarative base.

Provides the central declarative ``Base``, an ``Engine`` and a session
factory. The database schema is managed by Alembic (run ``alembic upgrade
head``); this module intentionally does not create tables itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

engine = create_engine(_settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Shared declarative base class for all ORM models."""


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional session scope with commit/rollback handling."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
