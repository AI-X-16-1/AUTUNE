"""Every /api/gap route takes the session that commits before the response.

``autune_core.get_session`` commits after the response has been sent (#1041);
``autune_core.SessionDep`` commits as the route returns. What that dependency
does is tested in ``packages/core``. This file holds the other half: no route
here, and no route added later, takes ``get_session`` directly.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from autune_core import committed_session, get_session
from autune_gap.router import router

ROUTES = [route for route in router.routes if isinstance(route, APIRoute)]


def test_no_route_takes_the_session_that_commits_after_the_response() -> None:
    assert ROUTES, "no routes found -- the router moved"
    late = sorted(
        route.path
        for route in ROUTES
        if any(dep.call is get_session for dep in route.dependant.dependencies)
    )

    assert late == []


NO_SESSION = {"/health", "/templates"}
"""The routes that read no database: the health check, and the template list,
which comes from the YAML files shipped with the module."""


def test_every_route_with_a_session_takes_the_one_committed_as_it_returns() -> None:
    """The check above passes on a router with no session at all; this one says
    the session routes are there and that the commit is tied to the route
    function."""
    taken = [
        dep
        for route in ROUTES
        for dep in route.dependant.dependencies
        if dep.call is committed_session
    ]
    without = {
        route.path
        for route in ROUTES
        if not any(dep.call is committed_session for dep in route.dependant.dependencies)
    }

    assert len(taken) == len(ROUTES) - len(NO_SESSION)
    assert without == NO_SESSION
    assert {dep.scope for dep in taken} == {"function"}
