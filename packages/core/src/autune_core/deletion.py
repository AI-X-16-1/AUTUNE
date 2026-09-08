"""Deletion registry.

Rows reachable by ON DELETE CASCADE from ``meetings`` are handled by the
database. Everything else — Neo4j nodes, Chroma embeddings, cached artifacts,
files — is the owning module's responsibility, registered here.

A table that cannot be cleaned up is a compliance defect, not a backlog item.
See docs/architecture/privacy.md section 4.
"""

from __future__ import annotations

from collections.abc import Callable

from .logging import get_logger

log = get_logger(__name__)

MeetingHook = Callable[[str], None]
UserHook = Callable[[str], None]

_meeting_hooks: dict[str, MeetingHook] = {}
_user_hooks: dict[str, UserHook] = {}


def on_meeting_deleted(module: str) -> Callable[[MeetingHook], MeetingHook]:
    """Register cleanup that runs when a meeting is deleted."""

    def decorator(fn: MeetingHook) -> MeetingHook:
        _meeting_hooks[module] = fn
        return fn

    return decorator


def on_user_deleted(module: str) -> Callable[[UserHook], UserHook]:
    """Register cleanup that runs when a user leaves or deletes their account."""

    def decorator(fn: UserHook) -> UserHook:
        _user_hooks[module] = fn
        return fn

    return decorator


def run_meeting_hooks(meeting_id: str) -> None:
    for module, hook in _meeting_hooks.items():
        hook(meeting_id)
        log.info("deletion_hook_ran", scope="meeting", module=module, meeting_id=meeting_id)


def run_user_hooks(user_id: str) -> None:
    for module, hook in _user_hooks.items():
        hook(user_id)
        log.info("deletion_hook_ran", scope="user", module=module, user_id=user_id)


def registered_modules() -> tuple[set[str], set[str]]:
    return set(_meeting_hooks), set(_user_hooks)
