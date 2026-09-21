"""HTTP entry point for module A.

Routes parse, delegate to ``service``, and format the result. No business logic
here — it cannot be reused by ``tasks.py`` if it lives in a route.

The prefix ``/api/audio`` is applied by apps/api; declare paths relative to it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autune_contracts.transcript import Utterance
from autune_core import CurrentUser, get_session
from autune_core.settings import get_settings as get_core_settings

from . import service
from .schemas import ConsentAttestation, ConsentState

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]

# A local-only page for putting a recording through the pipeline by hand.
# It has no auth, so it is mounted nowhere but a developer's machine.
if get_core_settings().env == "local":
    from .dev import router as dev_router

    router.include_router(dev_router, prefix="/dev")


@router.get("/health")
def health() -> dict[str, str]:
    return {"module": "audio", "status": "ok"}


@router.get("/transcripts/{meeting_id}", response_model=list[Utterance])
def get_transcript(meeting_id: str, user: CurrentUser, session: SessionDep) -> list[Utterance]:
    """This meeting's transcript, masked, for a member of its team.

    **The route takes `CurrentUser` and the service checks the team.** A meeting
    transcript is the most sensitive thing this repository stores, and a token
    alone says only that somebody is signed in. #156 and #189 are open about the
    rest of the routes; this one does not wait for them.

    `list[Utterance]`, not `TranscriptReady`. That payload is the announcement A
    publishes once when a meeting is over, and a consumer is required to check
    `privacy.original_audio_deleted` before touching it. A screen reading a
    stored transcript is not that consumer and should not be handed something
    that looks like the event.
    """
    return service.transcript_for_meeting(session, meeting_id=meeting_id, reader=user)


@router.post("/meetings/{meeting_id}/consent", response_model=ConsentState)
def attest_consent(
    meeting_id: str, body: ConsentAttestation, user: CurrentUser, session: SessionDep
) -> ConsentState:
    """A member states that everyone in this meeting's recording consented.

    Its own route rather than a checkbox on the upload, on purpose. "Everyone
    consented" is a statement with legal weight, and it is made by pressing one
    thing that means only that -- not by a box in the corner of a form whose
    main job is a file. It is also what lets this land independently of the
    upload route (#259): a meeting exists, somebody attests, and the next
    transcript written for it carries the consent.

    ``body.attested`` can only be ``true``. See ``ConsentAttestation``.

    This is the only writer of ``participants.consented = True`` in the
    repository, and it is per meeting because per person is not possible before
    identification (#6). When S10's per-attendee table exists, this route is
    derived from it or removed -- #190.
    """
    service.attest_consent(session, meeting_id=meeting_id, attested_by=user)
    session.commit()
    return ConsentState(meeting_id=meeting_id, attested=True)
