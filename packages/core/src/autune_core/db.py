"""Database engine, session, and the declarative base.

Shared entities are defined in ``autune_core.entities``. Module tables live in
each module's ``models.py`` and must carry the module's table prefix — see
docs/architecture/data-model.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
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
    """FastAPI dependency. Commits on success, rolls back on failure.

    Its commit runs after the response has been sent. A route takes
    ``SessionDep`` below instead, which commits before it.
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def committed_session(session: Annotated[Session, Depends(get_session)]) -> Iterator[Session]:
    """The request's session, committed as the route returns.

    ``get_session`` commits after its ``yield``, and FastAPI (0.118 and later)
    runs that part *after the response has been sent*. A client that acts on a
    2xx at once can then read before the write exists, and a commit that fails
    there fails after the client was told it worked (#1041).

    It takes the session from ``get_session`` itself, so ``CurrentUser`` and
    the route share one session and one transaction; ``get_session``'s own
    commit then finds nothing left to do. When the route raises, the exception
    arrives at this ``yield``, the commit is skipped, and ``get_session`` rolls
    back as before.
    """
    yield session
    session.commit()


# ``scope="function"`` is what moves the commit: the teardown runs as soon as
# the route function returns, before the response is built.
SessionDep = Annotated[Session, Depends(committed_session, scope="function")]


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
