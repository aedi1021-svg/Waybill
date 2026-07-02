"""Database engine and session management.

One place that builds the SQLAlchemy engine from config. The DATABASE_URL comes
from the environment, so the same code runs against local Docker Postgres and,
in Phase 2, against AWS RDS — the connection string is the only thing that
changes. Sessions are handed out via a context manager so callers can't leak
connections.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _database_url() -> str:
    # Default matches the docker-compose Postgres service.
    return os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://waybill:waybill@localhost:5432/waybill",
    )


class Base(DeclarativeBase):
    pass


_engine = create_engine(_database_url(), pool_pre_ping=True, future=True)
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def get_engine():
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error, always close."""
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
