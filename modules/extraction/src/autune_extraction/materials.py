"""A team's materials: what it keeps on the 자료 screen (#817).

Two kinds of row, told apart by ``ext_materials.source``.

**A Google Drive link** (#1016). A member pastes a link and types a title;
the team's members see the list and open the file in Google's own preview
(#844).

- **Autune reads nothing of the file.** No text, no Drive permission. Whether
  a viewer may see the file is Google's answer under the viewer's own sign-in.
- **The pasted text is not stored.** ``parse_drive_link`` takes the file's id
  and which Google editor it belongs to, and only those are kept; the address
  is built again from the id where it is shown. It follows the rules of
  ``apps/web/src/shared/drive/driveLink.ts`` -- the two are kept together, and
  what one refuses the other refuses.

**An uploaded file** (#817; ``store_upload``). The rule is
``docs/architecture/privacy.md``, "Uploaded documents are masked before
storage, and the original is not kept":

- **The original is not kept.** The route reads the file into memory and
  wipes it when the request ends (``material_upload``); this module gets the
  bytes as a parameter, reads the text (``material_reading``) and writes no
  byte of the file anywhere -- no table, disk, queue, log or Celery payload.
- **A file marked confidential is refused** before anything is masked or
  stored (``material_marking``). One ``ext_material_alarms`` row is written:
  the team, the time and the kind of marking. The team's approvers are told
  by the agent layer (#1201), through ``pending_alarms`` and
  ``acknowledge_alarm``.
- **Only masked text is kept.** The whole text is masked with
  ``autune_integrations.document_masking.mask_document`` (#1199), then cut
  into pieces, and every piece passes ``assert_masked`` before it is written
  to ``ext_material_chunks``.
- **Kept until the team deletes it** (the owner, 2026-10-10). No retention
  window: any member deletes a material at once, and its text goes in the
  same transaction; everything goes with the team (``ON DELETE CASCADE``).

**For both: a row names no person.** Any member of the team registers,
uploads and deletes, as with the team's projects; there is no admin role
(#592). Nothing goes out: no Slack, Notion, Jira or model sees a title, a file
id or a piece of text, and no agent tool reads these tables yet.

The title, the file id, a file's name and its text are never logged: a title
is typed by a person and can hold a name, a file id opens a file shared by
link, and a file's name and text are what the file says. Log lines carry the
material's id and the team's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_core import get_logger
from autune_core.errors import AutuneError, ConflictError, NotFoundError, ValidationError
from autune_integrations.document_masking import mask_document
from autune_integrations.privacy import assert_masked

from .material_marking import Marking, file_marking
from .material_reading import read_text
from .models import DRIVE_KINDS, ExtMaterial, ExtMaterialAlarm, ExtMaterialChunk
from .schemas import MaterialRead
from .typed_text import refuse_personal_data

log = get_logger(__name__)

MAX_MATERIALS = 200
"""How many a team keeps, links and uploads together. A shelf, not an
archive: past this, delete one first."""

MAX_TITLE_CHARS = 120
MAX_LINK_CHARS = 2000

CHUNK_CHARS = 500
"""The longest piece of an upload's masked text in one ``ext_material_chunks``
row. A piece ends after a line break, else after a space, where one falls in
its second half."""

ALARM_KEPT = timedelta(days=30)
"""How long an unacknowledged alarm row is kept (#817, #1198). In a small team
the time of an attempt can point at one person, so the row is short-lived."""

_ID = re.compile(r"[A-Za-z0-9_-]{10,200}")
"""Drive ids are URL-safe base64-ish -- ``ID`` in ``driveLink.ts``."""

_EDITORS = tuple(kind for kind in DRIVE_KINDS if kind != "file")


class ConfidentialFileError(AutuneError):
    """An upload stopped for a confidentiality marking. Says that there is
    one -- not which word, where, or in which file."""

    code = "confidential_file"
    status_code = 422

    def __init__(self) -> None:
        super().__init__("the file carries a confidentiality marking and was not kept")


@dataclass(frozen=True)
class DriveFile:
    id: str
    kind: str


def _checked(file_id: str | None, kind: str) -> DriveFile | None:
    return DriveFile(file_id, kind) if file_id and _ID.fullmatch(file_id) else None


def parse_drive_link(pasted: str) -> DriveFile | None:
    """The Drive file a pasted link or bare id names, or None when it names none.

    Accepts ``https://drive.google.com/file/d/<id>/...``, ``.../open?id=<id>``,
    ``.../uc?id=<id>``,
    ``https://docs.google.com/{document,presentation,spreadsheets}/d/<id>/...``
    and a bare id. Anything else -- another host, another scheme, a Drive
    folder -- is None.
    """
    text = pasted.strip()
    if _ID.fullmatch(text):
        return DriveFile(text, "file")
    try:
        url = urlsplit(text)
        host = url.hostname
    except ValueError:
        return None
    if url.scheme != "https":
        return None
    parts = [part for part in url.path.split("/") if part]
    if host == "drive.google.com":
        if parts[:2] == ["file", "d"]:
            return _checked(parts[2] if len(parts) > 2 else None, "file")
        if parts[:1] in (["open"], ["uc"]):
            return _checked(next(iter(parse_qs(url.query).get("id", [])), None), "file")
        return None
    if host == "docs.google.com" and len(parts) > 2 and parts[0] in _EDITORS and parts[1] == "d":
        return _checked(parts[2], parts[0])
    return None


def team_materials(session: Session, team_id: str) -> list[ExtMaterial]:
    """The team's materials, the newest first."""
    return list(
        session.scalars(
            select(ExtMaterial)
            .where(ExtMaterial.team_id == team_id)
            .order_by(ExtMaterial.created_at.desc(), ExtMaterial.id.desc())
        )
    )


def read(row: ExtMaterial) -> MaterialRead:
    return MaterialRead(
        id=row.id,
        team_id=row.team_id,
        title=row.title,
        source=row.source,  # type: ignore[arg-type]  # the table's check constraint
        drive_file_id=row.drive_file_id,
        drive_kind=row.drive_kind,  # type: ignore[arg-type]  # the table's check constraint
        created_at=row.created_at,
    )


def lock_shelf(session: Session, team_id: str) -> None:
    """Hold the team's shelf for the rest of ``session``'s transaction.

    The cap is a count and then an insert. Two registrations at once both
    count 199 and both insert, and the team keeps 201 (a review note on
    #1016): neither sees the other's uncommitted row, so no later count in the
    same transaction can catch it either. A transaction-scoped advisory lock
    keyed by team makes the second wait until the first has committed, then
    count its row. The key carries B's own namespace, as ``notion_setup``'s
    does. PostgreSQL only; SQLite (unit tests) has no such lock and runs one
    writer anyway."""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"extraction.materials:{team_id}"},
    )


def _checked_title(title: str, team_id: str) -> str:
    clean = " ".join(title.split())
    if not clean:
        raise ValidationError("a material needs a title", field="title")
    if len(clean) > MAX_TITLE_CHARS:
        raise ValidationError(f"a title has at most {MAX_TITLE_CHARS} characters", field="title")
    # A title is typed by a member: screened before it is stored (#1130).
    refuse_personal_data(clean, field="title", team_id=team_id)
    return clean


def _hold_a_place(session: Session, team_id: str, *, field: str) -> None:
    """Lock the team's shelf and refuse when it is full. Links and uploads
    share the one cap."""
    lock_shelf(session, team_id)
    held = session.scalar(
        select(func.count()).select_from(ExtMaterial).where(ExtMaterial.team_id == team_id)
    )
    if (held or 0) >= MAX_MATERIALS:
        refused = ValidationError(f"a team keeps at most {MAX_MATERIALS} materials", field=field)
        refused.details["reason"] = "shelf_full"
        raise refused


def register(session: Session, team_id: str, *, title: str, link: str) -> ExtMaterial:
    """Put a Drive file on the team's shelf under the title a member typed."""
    clean = _checked_title(title, team_id)
    file = parse_drive_link(link)
    if file is None:
        # The message names the rule and not the value: what was pasted may be
        # anything, and an error string ends up in error tracking.
        raise ValidationError("not a Google Drive file link", field="link")
    _hold_a_place(session, team_id, field="link")
    row = ExtMaterial(
        team_id=team_id,
        title=clean,
        source="drive_link",
        drive_file_id=file.id,
        drive_kind=file.kind,
    )
    # The same file twice -- by one member or by two at once: the unique
    # constraint answers, as a conflict rather than a 500. Added inside the
    # savepoint, so a refused row leaves the session with it and the caller's
    # transaction goes on.
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        raise ConflictError("the team already keeps that file") from exc
    log.info("extraction_material_registered", material_id=row.id, team_id=team_id)
    return row


def cut(masked: str, size: int = CHUNK_CHARS) -> list[str]:
    """``masked`` in pieces of at most ``size`` characters, in order; joined,
    they are ``masked`` again. A piece ends after a line break, else after a
    space, when one falls in its second half -- else at ``size``."""
    pieces: list[str] = []
    start = 0
    while len(masked) - start > size:
        window = masked[start : start + size]
        end = window.rfind("\n") + 1
        if end <= size // 2:
            end = window.rfind(" ") + 1
        if end <= size // 2:
            end = size
        pieces.append(window[:end])
        start += end
    if start < len(masked):
        pieces.append(masked[start:])
    return pieces


def _stop(session: Session, team_id: str, marking: Marking) -> None:
    session.add(ExtMaterialAlarm(team_id=team_id, marking=marking))
    session.flush()
    log.info("extraction_material_upload_stopped", team_id=team_id, marking=marking)


def store_upload(
    session: Session, team_id: str, *, title: str, file_name: str, data: bytes | bytearray
) -> ExtMaterial:
    """Keep the masked text of a file a member uploaded, under the title they
    typed. The caller has checked the member and owns ``data``.

    In order: the title; the file read, refused whole when it cannot be; the
    marking check, which writes an alarm row and raises
    ``ConfidentialFileError`` -- the caller commits that row before it
    answers; the team's cap; then the whole text masked, cut, each piece
    checked, and the row and its pieces written. ``file_name`` is read for its
    ending and the marking check and is not stored. A piece the outbound
    guard still refuses raises ``PrivacyViolationError`` and nothing is
    written."""
    clean = _checked_title(title, team_id)
    original = read_text(file_name, data)
    marking = file_marking(file_name, original)
    if marking is not None:
        _stop(session, team_id, marking)
        raise ConfidentialFileError
    _hold_a_place(session, team_id, field="file")
    masked = mask_document(original).text
    del original
    pieces = cut(masked)
    for piece in pieces:
        assert_masked(piece, destination="ext_material_chunks")
    row = ExtMaterial(team_id=team_id, title=clean, source="upload")
    session.add(row)
    session.flush()
    session.add_all(
        ExtMaterialChunk(material_id=row.id, position=position, text=piece)
        for position, piece in enumerate(pieces)
    )
    session.flush()
    log.info(
        "extraction_material_uploaded", material_id=row.id, team_id=team_id, pieces=len(pieces)
    )
    return row


def material_text(session: Session, material_id: str) -> list[str]:
    """An upload's stored pieces of masked text, in order -- for the search
    that comes later and for tests. No route returns it."""
    return list(
        session.scalars(
            select(ExtMaterialChunk.text)
            .where(ExtMaterialChunk.material_id == material_id)
            .order_by(ExtMaterialChunk.position)
        )
    )


def delete_material(session: Session, team_id: str, material_id: str) -> None:
    """Take one of this team's materials off its shelf, at once and for good.
    An upload's masked text goes in the same transaction -- there is no trash
    and no original to bring it back from. A Drive file is not Autune's and is
    not touched."""
    row = session.get(ExtMaterial, material_id)
    if row is None or row.team_id != team_id:
        raise NotFoundError("material", material_id)
    # ``ON DELETE CASCADE`` does this on PostgreSQL; said here as well, so the
    # text goes on any database and before the row does.
    session.execute(delete(ExtMaterialChunk).where(ExtMaterialChunk.material_id == material_id))
    session.delete(row)
    session.flush()
    log.info("extraction_material_deleted", material_id=material_id, team_id=team_id)


def pending_alarms(session: Session, team_id: str) -> list[ExtMaterialAlarm]:
    """The team's stopped uploads nobody has acknowledged, the oldest first.
    For the agent layer's approvers' alert (#1201): **the caller has checked
    that the reader is one of the team's approvers.** B opens no route to
    this."""
    return list(
        session.scalars(
            select(ExtMaterialAlarm)
            .where(ExtMaterialAlarm.team_id == team_id)
            .order_by(ExtMaterialAlarm.created_at, ExtMaterialAlarm.id)
        )
    )


def acknowledge_alarm(session: Session, team_id: str, alarm_id: str) -> None:
    """Delete one of the team's alarm rows. Takes no user, so who acknowledged
    is not recorded. **The caller has checked that the reader is one of the
    team's approvers** (#1201). Another team's row, or none, is not found."""
    row = session.get(ExtMaterialAlarm, alarm_id)
    if row is None or row.team_id != team_id:
        raise NotFoundError("material alarm", alarm_id)
    session.delete(row)
    session.flush()
    log.info("extraction_material_alarm_acknowledged", team_id=team_id)


def forget_old_alarms(session: Session, *, now: datetime) -> int:
    """Delete every alarm row older than ``ALARM_KEPT``. Returns how many."""
    gone = session.execute(
        delete(ExtMaterialAlarm).where(ExtMaterialAlarm.created_at < now - ALARM_KEPT)
    )
    return int(getattr(gone, "rowcount", 0) or 0)
