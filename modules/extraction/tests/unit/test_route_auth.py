"""Every /api/extraction route but /health depends on ``current_user`` (#189).

A route added later inherits nothing: this is what fails until it takes
``CurrentUser`` -- and, by the rule in ``router.py``'s docstring, resolves what it
names through a ``service`` membership check before anything else. The ``/dev``
page is exempt: it is mounted only for a local run that opts in, and a
developer's ``.env`` doing so must not fail this test.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from autune_core.auth import current_user
from autune_extraction.router import router

OPEN = {"/health"}
LOCAL_ONLY = "/dev"


def _depends_on_current_user(route: APIRoute) -> bool:
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        if dependant.call is current_user:
            return True
        pending.extend(dependant.dependencies)
    return False


def test_every_route_but_health_takes_the_current_user() -> None:
    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert routes, "no routes found -- the router moved"
    unguarded = sorted(
        r.path
        for r in routes
        if r.path not in OPEN
        and not r.path.startswith(LOCAL_ONLY)
        and not _depends_on_current_user(r)
    )
    assert unguarded == []
