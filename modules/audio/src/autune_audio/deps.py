"""The database session module A's routes take: committed before the response.

``autune_core.get_session`` commits after its ``yield``, and FastAPI (0.118 and
later) runs that part *after the response has been sent*. A browser that acts
on a 201 at once can then read before the write exists: "새 회의" got its
meeting id, opened ``/live``, and ``GET /meetings/{id}`` answered 404 "meeting
... not found" on the dev server while the row appeared a moment later. A
commit that failed there would also fail after the client had been told it
worked.

``scope="function"`` runs this dependency's teardown as soon as the route
function returns, before the response is built. It takes the session from
``get_session`` itself, so ``CurrentUser`` and the route share one session and
one transaction, as before; ``get_session``'s own commit then finds nothing
left to do, and its rollback still runs when the route raises (this commit is
skipped, because the exception arrives at the ``yield``).

Module A's alone. Every module's routes and ``packages/core``'s own have the
same gap; changing ``get_session`` is a shared change and is raised there.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from autune_core import get_session


def committed_session(session: Annotated[Session, Depends(get_session)]) -> Iterator[Session]:
    yield session
    session.commit()


SessionDep = Annotated[Session, Depends(committed_session, scope="function")]
