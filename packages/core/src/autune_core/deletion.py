"""Deletion registry.

Rows reachable by ON DELETE CASCADE from ``meetings`` are handled by the
database, which is nearly everything: topic graphs and embeddings are ordinary
PostgreSQL rows. Anything a module keeps outside PostgreSQL — a cached artifact,
a file on disk — is its own responsibility and is registered here.

The registry stays even though little needs it today. Phase 2 adds uploaded
material and desktop capture, and a module that stores something outside the
database must have somewhere to say so.

A table that cannot be cleaned up is a compliance defect, not a backlog item.
See docs/architecture/privacy.md section 4.

**Every hook must be safe to run twice.** The callers run a hook, then delete
the row it was about, in a transaction the hook is not part of: if that delete
or its commit fails, the hook runs again on the next attempt, and two
overlapping retention sweeps can run it concurrently. A hook is a set of
``DELETE``/``UPDATE ... WHERE <id> = ...`` statements, or anything else that
finds nothing to do the second time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .logging import get_logger

log = get_logger(__name__)

MeetingHook = Callable[[str], None]
UserHook = Callable[[str], None]
SpeechHook = Callable[[str, Sequence[str]], None]

_meeting_hooks: dict[str, MeetingHook] = {}
_user_hooks: dict[str, UserHook] = {}
_speech_hooks: dict[str, SpeechHook] = {}


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


def on_speech_deleted(module: str) -> Callable[[SpeechHook], SpeechHook]:
    """Register cleanup that runs when a person deletes their own speech (#587).

    The hook gets the person and the ids of the utterances about to go, before
    they are deleted -- so it can still find what it derived from them. Ids
    only, as for the other hooks. It must be safe to repeat.
    """

    def decorator(fn: SpeechHook) -> SpeechHook:
        _speech_hooks[module] = fn
        return fn

    return decorator


def run_meeting_hooks(meeting_id: str) -> None:
    for module, hook in _meeting_hooks.items():
        hook(meeting_id)
        log.info("deletion_hook_ran", scope="meeting", module=module, meeting_id=meeting_id)


def run_user_hooks(user_id: str) -> None:
    """Every module's user hook, then the person's Google grants revoked at
    Google (#763). Called by module A's account deletion right before the
    ``users`` row goes.

    The revoke is last on purpose: a module may still need the grant to clean
    up what it put outside Autune -- B's due dates on that person's calendar --
    and a hook that raises stops the run before it, leaving the account and
    its grants as they were for the next attempt. The revoke itself never
    raises (``user_integrations.revoke_google_grants``)."""
    for module, hook in _user_hooks.items():
        hook(user_id)
        log.info("deletion_hook_ran", scope="user", module=module, user_id=user_id)
    from .user_integrations import revoke_google_grants

    revoke_google_grants(user_id)


def run_speech_hooks(user_id: str, utterance_ids: Sequence[str]) -> None:
    """Run every speech hook before the utterances are deleted. Called by module
    A's speech and account deletion (#582, #587); a hook that raises stops it.

    Nothing to tell when there are no utterances. Each hook gets its own copy
    of the ids, so one module trimming the list cannot change what the next
    one sees (#628)."""
    if not utterance_ids:
        return
    for module, hook in _speech_hooks.items():
        hook(user_id, list(utterance_ids))
        log.info(
            "deletion_hook_ran",
            scope="speech",
            module=module,
            user_id=user_id,
            utterances=len(utterance_ids),
        )


def registered_speech_modules() -> set[str]:
    return set(_speech_hooks)


def registered_modules() -> tuple[set[str], set[str]]:
    return set(_meeting_hooks), set(_user_hooks)
