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
- **Kept for the team's retention window, counted from the upload**
  (``teams.retention_days``, 90 days by default; the owner, 2026-10-11).
  **The day is fixed at the upload**: ``store_upload`` writes
  ``ext_materials.expires_at``, that moment plus the team's window of that
  day, and nothing moves it afterwards -- a team that later shortens or
  lengthens its window changes what its next uploads get, not what an earlier
  one was given, as with a meeting (the owner, 2026-10-12).
  ``forget_expired_uploads`` -- hourly, ``tasks.forget_expired_material_uploads``
  -- deletes an upload past it with its text. A team may switch to keeping
  its uploads until a member deletes them (``set_keep_until_deleted``,
  ``ext_material_settings``); an upload keeps its ``expires_at`` under either
  choice, so switching back leaves every existing upload the day it was
  given. Any member deletes a material at once, and its text goes in the same
  transaction; everything goes with the team (``ON DELETE CASCADE``). A Drive
  link keeps no text and has no day.

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
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_core import Team, get_logger
from autune_core.errors import AutuneError, ConflictError, NotFoundError, ValidationError
from autune_integrations.document_masking import mask_document
from autune_integrations.privacy import assert_masked

from .material_formats import NotRead
from .material_marking import Marking, file_marking
from .material_reading import read_file
from .models import (
    DRIVE_KINDS,
    ExtMaterial,
    ExtMaterialAlarm,
    ExtMaterialChunk,
    ExtMaterialSetting,
)
from .schemas import MaterialRead, MaterialSettingsRead
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

FORGET_BATCH = 100
"""How many expired uploads one ``forget_expired_uploads`` call deletes, in
one transaction. The task calls it again until a call finds fewer."""

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


class StoredUpload(NamedTuple):
    """An upload's row, and what its file held that was not read -- said once,
    in the answer to the upload, and stored nowhere."""

    row: ExtMaterial
    not_read: tuple[NotRead, ...]


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


def keeps_until_deleted(session: Session, team_id: str) -> bool:
    """Whether the team keeps its uploads until a member deletes them. No
    row is the default: it does not."""
    return bool(
        session.scalar(
            select(ExtMaterialSetting.keep_until_deleted).where(
                ExtMaterialSetting.team_id == team_id
            )
        )
    )


def upload_deadline(session: Session, team_id: str, *, now: datetime) -> datetime:
    """When an upload made at ``now`` is deleted: ``now`` plus the team's
    retention window as it is today. Written on the row and not worked out
    again --
    the team changing its window later moves no upload already made. Set
    whether or not the team keeps its uploads until deleted, so that
    switching back finds every upload with its day."""
    days = session.scalar(select(Team.retention_days).where(Team.id == team_id))
    return now + timedelta(days=days if days is not None else 90)


def _aware(moment: datetime) -> datetime:
    # SQLite hands back what it was given without the zone; every time here is UTC.
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def expires_at(row: ExtMaterial, *, kept: bool) -> datetime | None:
    """When an upload is deleted: the day it was given at its upload. None for
    a link, which keeps no text, and while the team keeps uploads until
    deleted (``kept``) -- the row holds its day all the same."""
    if row.source != "upload" or kept or row.expires_at is None:
        return None
    return _aware(row.expires_at)


def read(row: ExtMaterial, *, kept: bool = False) -> MaterialRead:
    """The row as the screen gets it. ``kept`` is ``keeps_until_deleted`` of
    the row's team; an upload of such a team answers no ``expires_at``."""
    return MaterialRead(
        id=row.id,
        team_id=row.team_id,
        title=row.title,
        source=row.source,  # type: ignore[arg-type]  # the table's check constraint
        drive_file_id=row.drive_file_id,
        drive_kind=row.drive_kind,  # type: ignore[arg-type]  # the table's check constraint
        created_at=row.created_at,
        expires_at=expires_at(row, kept=kept),
    )


def uploads_past_their_day(session: Session, team_id: str, *, now: datetime) -> int:
    """How many of the team's uploads are past the day they were given. While
    the team keeps uploads until deleted these stay; they are what switching
    back to the window deletes at the next hourly run, which the settings
    screen says before the switch is saved. A count -- no id, no title."""
    return (
        session.scalar(
            select(func.count())
            .select_from(ExtMaterial)
            .where(
                ExtMaterial.team_id == team_id,
                ExtMaterial.source == "upload",
                ExtMaterial.expires_at <= now,
            )
        )
        or 0
    )


def settings_read(
    session: Session, team_id: str, *, now: datetime | None = None
) -> MaterialSettingsRead:
    days = session.scalar(select(Team.retention_days).where(Team.id == team_id))
    return MaterialSettingsRead(
        keep_until_deleted=keeps_until_deleted(session, team_id),
        retention_days=days if days is not None else 90,
        uploads_past_their_day=uploads_past_their_day(
            session, team_id, now=now or datetime.now(UTC)
        ),
    )


def set_keep_until_deleted(session: Session, team_id: str, keep: bool) -> None:
    """Switch whether the team keeps its uploads until a member deletes them.
    Takes no user: the change records when, never who. No upload's
    ``expires_at`` is touched either way: switching back to the window
    applies at once to every existing upload, each with the day it was given
    -- the next hourly run deletes those already past it."""
    row = session.get(ExtMaterialSetting, team_id)
    if row is None:
        # Two members switching at once both find no row; the second insert
        # meets the primary key and becomes an update.
        try:
            with session.begin_nested():
                session.add(ExtMaterialSetting(team_id=team_id, keep_until_deleted=keep))
                session.flush()
        except IntegrityError:
            row = session.get(ExtMaterialSetting, team_id)
            assert row is not None  # the other insert committed it
        else:
            log.info("extraction_material_setting_changed", team_id=team_id, keep=keep)
            return
    row.keep_until_deleted = keep
    row.updated_at = datetime.now(UTC)
    session.flush()
    log.info("extraction_material_setting_changed", team_id=team_id, keep=keep)


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
) -> StoredUpload:
    """Keep the masked text of a file a member uploaded, under the title they
    typed. The caller has checked the member and owns ``data``.

    In order: the title; the file read, refused whole when it cannot be; the
    marking check over the file's name, its head, its foot and its own label
    (``FileText.edges``), which writes an alarm row and raises
    ``ConfidentialFileError`` -- the caller commits that row before it
    answers; the team's cap; then the whole text masked, cut, each piece
    checked, and the row and its pieces written. ``file_name`` is read for its
    ending and the marking check and is not stored. A piece the outbound
    guard still refuses raises ``PrivacyViolationError`` and nothing is
    written. What the file held that was not read -- pictures, charts,
    embedded files -- comes back beside the row."""
    clean = _checked_title(title, team_id)
    found = read_file(file_name, data)
    marking = file_marking(file_name, found.edges())
    if marking is not None:
        _stop(session, team_id, marking)
        raise ConfidentialFileError
    _hold_a_place(session, team_id, field="file")
    masked = mask_document(found.text).text
    not_read = found.not_read
    del found
    pieces = cut(masked)
    for piece in pieces:
        assert_masked(piece, destination="ext_material_chunks")
    now = datetime.now(UTC)
    row = ExtMaterial(
        team_id=team_id,
        title=clean,
        source="upload",
        created_at=now,
        expires_at=upload_deadline(session, team_id, now=now),
    )
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
    return StoredUpload(row, not_read)


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


def forget_expired_uploads(session: Session, *, now: datetime, limit: int | None = None) -> int:
    """Delete at most ``limit`` (``FORGET_BATCH``) uploads past the day they
    were given (``expires_at``), with their text, the earliest day first. The
    team's window of today is not read: it was counted in at the upload. A
    team that keeps uploads until deleted is passed over, and a link is never
    touched. Returns how many went; the caller commits and calls again while
    a call returns the whole batch."""
    if limit is None:
        limit = FORGET_BATCH
    kept = select(ExtMaterialSetting.team_id).where(ExtMaterialSetting.keep_until_deleted)
    gone = [
        (team_id, material_id)
        for team_id, material_id in session.execute(
            select(ExtMaterial.team_id, ExtMaterial.id)
            .where(
                ExtMaterial.source == "upload",
                ExtMaterial.expires_at <= now,
                ExtMaterial.team_id.not_in(kept),
            )
            .order_by(ExtMaterial.expires_at, ExtMaterial.id)
            .limit(limit)
        )
    ]
    if not gone:
        return 0
    ids = [material_id for _, material_id in gone]
    # The text first, as in ``delete_material``; ``ON DELETE CASCADE`` would
    # take it on PostgreSQL anyway.
    session.execute(delete(ExtMaterialChunk).where(ExtMaterialChunk.material_id.in_(ids)))
    session.execute(delete(ExtMaterial).where(ExtMaterial.id.in_(ids)))
    for team_id, material_id in gone:
        log.info("extraction_material_expired", material_id=material_id, team_id=team_id)
    return len(ids)


def forget_old_alarms(session: Session, *, now: datetime) -> int:
    """Delete every alarm row older than ``ALARM_KEPT``. Returns how many."""
    gone = session.execute(
        delete(ExtMaterialAlarm).where(ExtMaterialAlarm.created_at < now - ALARM_KEPT)
    )
    return int(getattr(gone, "rowcount", 0) or 0)
