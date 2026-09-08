"""Database engine, session, and the declarative base.

Shared entities are defined in ``autune_core.entities``. Module tables live in
each module's ``models.py`` and must carry the module's table prefix — see
docs/architecture/data-model.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .settings import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by shared entities and every module's tables.

    Importing another module's models pulls its tables into this metadata and
    makes your migrations emit DDL you do not own. Don't.
    """


@lru_cache
def get_engine():
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Commits on success, rolls back on failure."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session for Celery tasks and scripts, with the same commit semantics."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
