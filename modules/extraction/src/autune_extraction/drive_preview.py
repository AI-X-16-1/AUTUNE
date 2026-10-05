"""A Drive file the person picked, read with their own grant and shown to them.

The second way of showing material in place (the user, 2026-10-05; #817):
instead of Google's preview in a frame, the file is fetched as the person who
asked and drawn in a viewer of Autune's, so it can be turned to a page.

**Provisional, and here only for now.** #817 has not decided which module
holds material. The user put the read in module B until it does; when it
does, this file moves and nothing else in B knows it existed. It touches no
table of B's and none of anybody else's.

**The person who asks is the only person it can be shown to.** The file is
read with the asker's own ``drive.file`` grant -- so only a file THEY picked
in Google's file picker can be opened at all -- and the bytes go back in the
answer to that same request. There is no parameter naming another person, no
team to share with, and nothing a teammate could ask for.

**Nothing is kept.** The bytes are held in memory for the one answer. They
are not written to a table, a file or a cache; no name, no id-to-name pairing
and no byte of the file is logged; the answer is marked ``no-store`` so a
browser does not keep it either. What is logged is that a preview was served,
with the kind of file and its size.

**Unmasked, on purpose.** Transcript text is masked before it is stored
because Autune stores it. This is the person's own document, shown to them and
stored nowhere -- masking it would only show them their file with holes in it.
Anything derived from a file and KEPT (an excerpt, an agenda draft) is #817's
question and gets a mask of its own there.

**One kind of answer: a PDF.** A PDF is passed on as it is; a Google document
or presentation is exported to PDF by Drive. Anything else is refused by type
before a byte is read. Every read has a ceiling.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.orm import Session

from autune_core.errors import ConflictError, ValidationError
from autune_core.logging import get_logger
from autune_core.settings import get_settings as get_core_settings
from autune_core.user_integrations import load_user_integration
from autune_integrations.calendar import ReconnectRequiredError, refresh_access_token
from autune_integrations.drive import PDF, DriveClient, DriveFiles, FileTooLargeError

log = get_logger(__name__)

DRIVE = "drive"
"""The ``user_integrations`` service of the person's own Drive grant."""

GOOGLE_DOCUMENT = "application/vnd.google-apps.document"
GOOGLE_PRESENTATION = "application/vnd.google-apps.presentation"
EXPORTED = frozenset({GOOGLE_DOCUMENT, GOOGLE_PRESENTATION})
"""Google's own document types Drive renders to PDF for us. A sheet is not
here: a wide one comes out cut across pages, which is worse than not shown."""

MAX_BYTES = 20 * 1024 * 1024
"""The most that is read of one file. A slide deck with pictures is a few
megabytes; twenty is past any meeting hand-out and well short of a request
that holds a worker for minutes."""


class DriveNotConnectedError(ConflictError):
    """The person has not connected Drive, or has to connect it again."""

    code = "drive_not_connected"


class UnsupportedDriveFileError(ValidationError):
    """Not a PDF, a Google document or a Google presentation."""

    code = "drive_file_unsupported"


class DriveFileTooLargeError(ValidationError):
    """Longer than ``MAX_BYTES``."""

    code = "drive_file_too_large"


@dataclass(frozen=True)
class Preview:
    """A file as a PDF, for the one answer it was read for. ``repr`` shows its
    length and never its bytes."""

    pdf: bytes
    exported: bool
    """Whether Drive rendered it from a Google document."""

    def __repr__(self) -> str:
        return f"Preview(<{len(self.pdf)} bytes>, exported={self.exported})"


def access_token(session: Session, user_id: str) -> str:
    """A fresh access token for this person's own Drive grant.

    Raises ``DriveNotConnectedError`` when they have none, when this
    deployment has no Google client to refresh it with, or when the grant is
    known to be gone -- issued to another client, revoked with another grant
    of the same account, or refused by Google. The same rules the calendar's
    grant follows (``tasks._calendars``), and for the same reason: an answer
    that is already known is not asked of Google again.
    """
    client_id, client_secret = get_core_settings().google_integration_credentials
    grant = load_user_integration(session, user_id, DRIVE)
    if grant is None or not grant.secret or not client_id or not client_secret:
        raise DriveNotConnectedError("connect Google Drive first")
    issued_to = grant.config.get("client_id")
    if (issued_to and issued_to != client_id) or grant.config.get("grant_revoked"):
        raise DriveNotConnectedError("the Drive connection has ended; connect it again")
    try:
        return refresh_access_token(
            client_id=client_id, client_secret=client_secret, refresh_token=grant.secret
        )
    except ReconnectRequiredError as exc:
        raise DriveNotConnectedError("the Drive connection has ended; connect it again") from exc


@contextmanager
def drive_for(session: Session, user_id: str) -> Iterator[DriveFiles]:
    """A Drive client acting as this person, closed on the way out. Raises
    ``DriveNotConnectedError`` before anything is opened (``access_token``)."""
    client = DriveClient(access_token(session, user_id))
    try:
        yield client
    finally:
        client.close()


def read(drive: DriveFiles, file_id: str) -> Preview:
    """The picked file as a PDF, in memory.

    The type is asked first and decides everything: a PDF is downloaded, a
    Google document or presentation is exported, and anything else is refused
    before a byte of it is read. A file the grant cannot reach -- with
    ``drive.file``, any file the person did not pick -- is Drive's refusal,
    passed on as it came.
    """
    info = drive.info(file_id)
    if info.mime_type != PDF and info.mime_type not in EXPORTED:
        raise UnsupportedDriveFileError(
            "only a PDF, a Google document or a Google presentation can be shown"
        )
    if info.size is not None and info.size > MAX_BYTES:
        raise DriveFileTooLargeError("the file is too large to be shown here")
    exported = info.mime_type in EXPORTED
    try:
        pdf = (
            drive.export_pdf(file_id, max_bytes=MAX_BYTES)
            if exported
            else drive.download(file_id, max_bytes=MAX_BYTES)
        )
    except FileTooLargeError as exc:
        raise DriveFileTooLargeError("the file is too large to be shown here") from exc
    # The kind and the size: enough to see the feature working, and nothing
    # that says which file or what is in it.
    log.info("extraction_drive_preview_served", exported=exported, bytes=len(pdf))
    return Preview(pdf=pdf, exported=exported)
