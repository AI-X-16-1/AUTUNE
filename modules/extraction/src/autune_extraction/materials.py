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
  ``acknowledge_alarm``. It finds the teams to tell with
  ``teams_with_untold_alarms`` and records that it did with
  ``mark_alarms_told``: this module does not know who an approver is.
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
- **A team nobody is on keeps nothing here** (#817; the owner, 2026-10-13).
  Only a member can delete a material or change the team's setting, so
  once a team has no member nobody could ever end what it holds -- and a
  team that chose to keep its uploads would keep them without end.
  ``forget_memberless_team_materials`` -- hourly,
  ``tasks.forget_memberless_team_materials`` -- deletes every row this
  module keeps for such a team's 자료 screen: uploads with their text and
  vectors, links, alarm rows and the keeping setting, whatever that setting
  says. The team itself, and its meetings, are module A's.

**For both: a row names no person.** Any member of the team registers,
uploads and deletes, as with the team's projects; there is no admin role
(#592). Nothing goes out: no Slack, Notion, Jira or outside model sees a title,
a file id or a piece of text. An upload's masked pieces are embedded by the
material embedder (``pipeline.registry.get_material_embedder``) -- in this
process or, when it is set to ``hosted``, on our own inference server, which
is posted the masked pieces and nothing else -- and searched by
``material_search``, whose excerpts are screened on the way out; the
assistant's tool over it is off by default (``tools.find_materials``).

The title, the file id, a file's name and its text are never logged: a title
is typed by a person and can hold a name, a file id opens a file shared by
link, and a file's name and text are what the file says. Log lines carry the
material's id and the team's.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import delete, func, select, text, union
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_core import Team, TeamMember, get_logger
from autune_core.errors import AutuneError, ConflictError, NotFoundError, ValidationError
from autune_integrations.document_masking import mask_document
from autune_integrations.privacy import assert_masked

from .config import get_settings
from .material_formats import NotRead
from .material_marking import Marking, file_marking
from .material_pieces import Piece, pieces_of
from .material_reading import read_file
from .models import (
    DRIVE_KINDS,
    MATERIAL_EMBEDDING_DIM,
    ExtMaterial,
    ExtMaterialAlarm,
    ExtMaterialChunk,
    ExtMaterialSetting,
)
from .pipeline.base import Embedder
from .pipeline.registry import get_material_embedder
from .schemas import MaterialRead, MaterialSettingsRead
from .typed_text import refuse_personal_data

log = get_logger(__name__)

MAX_MATERIALS = 200
"""How many a team keeps, links and uploads together. A shelf, not an
archive: past this, delete one first."""

MAX_TITLE_CHARS = 120
MAX_LINK_CHARS = 2000

CHUNK_CHARS = 200
"""The longest piece of an upload's masked text in one ``ext_material_chunks``
row, unless ``ExtractionSettings.material_chunk_chars`` says otherwise (ADR
0011; it was 500). A piece is cut at the coarsest separator that lets it fit
-- a blank line, a line break, a sentence end, a space -- and at the size only
as the last resort (``material_pieces``)."""

ALARM_KEPT = timedelta(days=30)
"""How long an unacknowledged alarm row is kept (#817, #1198). In a small team
the time of an attempt can point at one person, so the row is short-lived."""

FORGET_BATCH = 100
"""How many expired uploads one ``forget_expired_uploads`` call deletes, in
one transaction. The task calls it again until a call finds fewer."""

MEMBERLESS_BATCH = 20
"""How many teams one ``forget_memberless_team_materials`` call empties, in
one transaction; a team holds at most ``MAX_MATERIALS`` rows. The task calls
it again until a call finds fewer."""

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
    in the answer to the upload, and stored nowhere. ``already_kept`` says the
    row is an earlier upload of the same text and this one wrote nothing
    (``_kept_already``): its title is that upload's, not the one just typed."""

    row: ExtMaterial
    not_read: tuple[NotRead, ...]
    already_kept: bool = False


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
    back to the window leaves for an hourly run to delete, which the settings
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
    -- an hourly run deletes those already past it."""
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
    _refuse_a_full_shelf(session, team_id, field=field)


def _refuse_a_full_shelf(session: Session, team_id: str, *, field: str) -> None:
    """The cap alone. The caller holds the team's shelf (``lock_shelf``)."""
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
    they are ``masked`` again. Each is cut at the coarsest separator that lets
    it fit (``material_pieces.split_recursive``). A heading above a piece is
    not part of what this returns: ``cut_with_paths`` gives it with the piece."""
    return [piece.text for piece in pieces_of(masked, size, with_path=False)]


def cut_with_paths(masked: str) -> list[Piece]:
    """The pieces of ``masked`` as ``store_upload`` stores them, each with the
    heading path and the tail of the piece before it that are embedded before
    it, at the size and with the switches of the settings
    (``material_chunk_chars``, ``material_heading_path``,
    ``material_embed_overlap_chars``)."""
    settings = get_settings()
    return pieces_of(
        masked,
        settings.material_chunk_chars,
        with_path=settings.material_heading_path,
        overlap=settings.material_embed_overlap_chars,
    )


def embedded(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    """One vector per masked text, each as wide as the stored column. An
    embedder that answers with another count or width is refused: a vector
    of the wrong width cannot be stored, and one model's vectors are not
    compared with another's. The message carries numbers, never the text."""
    if not texts:
        return []
    vectors = embedder.embed(texts)
    if len(vectors) != len(texts) or any(len(v) != MATERIAL_EMBEDDING_DIM for v in vectors):
        raise RuntimeError(
            f"the material embedder must answer {len(texts)} vector(s) of width "
            f"{MATERIAL_EMBEDDING_DIM}; check AUTUNE_EXTRACTION_MATERIAL_EMBEDDER_IMPL"
        )
    return [[float(x) for x in vector] for vector in vectors]


def _stop(session: Session, team_id: str, marking: Marking) -> None:
    session.add(ExtMaterialAlarm(team_id=team_id, marking=marking))
    session.flush()
    log.info("extraction_material_upload_stopped", team_id=team_id, marking=marking)


def _kept_already(
    session: Session, team_id: str, pieces: list[str], *, model: str, now: datetime
) -> ExtMaterial | None:
    """This team's upload that already keeps exactly ``pieces`` -- the same
    masked text, cut the same way, in the same order -- or None.

    A second upload of a text the team keeps used to be a second material. A
    member whose first try went unanswered and who uploaded again had it
    twice, and the two took two of a search's five places with one text.

    The comparison is of what is stored already: masked pieces against masked
    pieces, character for character. Nothing is kept to make it -- no digest
    of a text, masked or not -- and nothing of the file is compared: the same
    text from a .docx and from a .txt is one material, and a text that differs
    by a space is another. Two texts that differ only in what the mask covers,
    one phone number for another of the same shape, are the same pieces and
    so one material.

    It reads ``team_id``'s rows and no other team's: what another team keeps
    cannot change the answer, so an upload cannot be used to ask.

    Only what a search would read counts as kept (``material_search``): an
    upload, embedded by ``model``, every piece with its vector, and not past
    its day unless the team keeps uploads until deleted. A copy the hourly
    task is about to delete is no answer to "it is kept". Of several, the one
    kept longest.

    The caller holds the team's shelf: of two uploads of one text at once,
    the second waits, then finds the first's row here."""
    if not pieces:
        return None
    candidates = (
        select(ExtMaterial)
        .join(ExtMaterialChunk, ExtMaterialChunk.material_id == ExtMaterial.id)
        .where(
            ExtMaterial.team_id == team_id,
            ExtMaterial.source == "upload",
            ExtMaterial.embedding_model == model,
            ExtMaterialChunk.position == 0,
            ExtMaterialChunk.text == pieces[0],
        )
        .order_by(ExtMaterial.expires_at.desc(), ExtMaterial.id)
    )
    if not keeps_until_deleted(session, team_id):
        candidates = candidates.where(ExtMaterial.expires_at > now)
    for row in session.scalars(candidates).all():
        kept = session.execute(
            select(ExtMaterialChunk.text, ExtMaterialChunk.embedding.is_(None))
            .where(ExtMaterialChunk.material_id == row.id)
            .order_by(ExtMaterialChunk.position)
        ).all()
        if [piece for piece, _ in kept] == pieces and not any(bare for _, bare in kept):
            return row
    return None


def store_upload(
    session: Session, team_id: str, *, title: str, file_name: str, data: bytes | bytearray
) -> StoredUpload:
    """Keep the masked text of a file a member uploaded, under the title they
    typed. The caller has checked the member and owns ``data``.

    In order: the title; the file read, refused whole when it cannot be; the
    marking check over the file's name, its head, its foot and its own label
    (``FileText.edges``), which writes an alarm row and raises
    ``ConfidentialFileError`` -- the caller commits that row before it
    answers; the team's shelf locked; the whole text masked, cut, each piece
    checked; a look for an upload of this team that already keeps exactly
    these pieces (``_kept_already``); the team's cap; and the row and its
    pieces written. ``file_name`` is read for its ending and the marking check
    and is not stored. A piece the outbound guard still refuses raises
    ``PrivacyViolationError`` and nothing is written. What the file held that
    was not read -- pictures, charts, embedded files -- comes back beside the
    row.

    When the team already keeps the text, that earlier row is the answer
    (``already_kept``) and nothing is written or changed: no second copy, no
    place taken on the shelf -- so a full shelf still answers it -- and the
    title typed now is dropped; the earlier row keeps its own title and its
    own day."""
    clean = _checked_title(title, team_id)
    found = read_file(file_name, data)
    marking = file_marking(file_name, found.edges())
    if marking is not None:
        _stop(session, team_id, marking)
        raise ConfidentialFileError
    lock_shelf(session, team_id)
    masked = mask_document(found.text).text
    not_read = found.not_read
    del found
    cut_pieces = cut_with_paths(masked)
    pieces = [piece.text for piece in cut_pieces]
    for piece in cut_pieces:
        assert_masked(piece.text, destination="ext_material_chunks")
        if piece.embedded != piece.text:
            # What the embedder is given is the path, the tail of the piece
            # before it and the piece. Both are slices of the masked text, and
            # a pattern across the cut between two pieces is only seen in the
            # one string they make: so it is looked at again as that string,
            # whichever of the two the piece has.
            assert_masked(piece.embedded, destination="material_embedder")
    embedder = get_material_embedder()
    now = datetime.now(UTC)
    same = _kept_already(session, team_id, pieces, model=embedder.model_version, now=now)
    if same is not None:
        log.info("extraction_material_upload_already_kept", material_id=same.id, team_id=team_id)
        return StoredUpload(same, not_read, already_kept=True)
    _refuse_a_full_shelf(session, team_id, field="file")
    # Vectors from the masked pieces and nothing else, in this request: no
    # text or path goes to a task (#817).
    vectors = embedded(embedder, [piece.embedded for piece in cut_pieces])
    row = ExtMaterial(
        team_id=team_id,
        title=clean,
        source="upload",
        created_at=now,
        expires_at=upload_deadline(session, team_id, now=now),
        embedding_model=embedder.model_version,
    )
    session.add(row)
    session.flush()
    session.add_all(
        ExtMaterialChunk(material_id=row.id, position=position, text=piece, embedding=vector)
        for position, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
    )
    session.flush()
    log.info(
        "extraction_material_uploaded", material_id=row.id, team_id=team_id, pieces=len(pieces)
    )
    return StoredUpload(row, not_read)


def material_text(session: Session, material_id: str) -> list[str]:
    """An upload's stored pieces of masked text, in order -- for tests. No
    route returns it; a search returns excerpts (``material_search``)."""
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


def teams_with_untold_alarms(session: Session) -> list[str]:
    """The teams holding an alarm whose approvers have not been told of it.
    For the agent layer's periodic task (#1201): this module does not know
    who a team's approvers are and sends them nothing itself. A row the
    30-day task already deleted is in no answer, so it is never told."""
    return list(
        session.scalars(
            select(ExtMaterialAlarm.team_id)
            .where(ExtMaterialAlarm.told_at.is_(None))
            .distinct()
            .order_by(ExtMaterialAlarm.team_id)
        )
    )


def mark_alarms_told(
    session: Session, team_id: str, alarm_ids: Collection[str], *, now: datetime | None = None
) -> int:
    """The team's approvers were told of these alarms; set when. Returns how
    many it set; the caller commits.

    By id, not "every alarm of the team": an upload stopped between the
    caller's count and this call was not in the message, and stays untold
    for the next one. Another team's alarm, one already told and an id that
    is not there are passed over. Who was told is not a parameter, so it
    cannot be kept. The count goes in the log; the ids and the team do not.

    Being told does not keep a row: acknowledged or not, it still goes 30
    days after it was written (``forget_old_alarms``).
    """
    if not alarm_ids:
        return 0
    moment = now or datetime.now(UTC)
    rows = session.scalars(
        select(ExtMaterialAlarm).where(
            ExtMaterialAlarm.team_id == team_id,
            ExtMaterialAlarm.id.in_(alarm_ids),
            ExtMaterialAlarm.told_at.is_(None),
        )
    ).all()
    for row in rows:
        row.told_at = moment
    session.flush()
    log.info("extraction_material_alarms_told", count=len(rows))
    return len(rows)


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


def _deleted(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


def forget_memberless_team_materials(session: Session, *, limit: int | None = None) -> int:
    """Delete what this module keeps for the 자료 screen of at most ``limit``
    (``MEMBERLESS_BATCH``) teams that have no member: every material -- an
    upload with its masked text and vectors, and a link -- every alarm row, and
    the keeping setting. Returns how many teams were emptied; the caller
    commits and calls again while a call returns the whole batch.

    **Why whatever the setting says.** "Kept until a member deletes it" is a
    promise to the members, and there are none: every route over these rows
    asks for one, so nobody can read, search, delete or switch back. Such a
    team does not get a member again either -- only a member makes an
    invitation, and an invitation goes with the member who made it.

    **Why here and not when an account is deleted.** This reads the state, so
    it holds whichever way a team came to have nobody on it, and it acts only
    once the last membership row is really gone. A hook on account deletion
    runs before that delete commits, in a transaction of its own: if the
    deletion then failed, the person would still be on a team whose materials
    were gone.

    A team is made with its first member in one transaction (module A's
    ``create_team``), and an upload needs a member, so a team seen here with
    no member is not one that is about to get its first. The ``teams`` row and
    the team's meetings are not touched: they are module A's (invariant 4)."""
    if limit is None:
        limit = MEMBERLESS_BATCH
    held = union(
        select(ExtMaterial.team_id),
        select(ExtMaterialAlarm.team_id),
        select(ExtMaterialSetting.team_id),
    ).subquery()
    on_it = select(TeamMember.user_id).where(TeamMember.team_id == held.c.team_id).exists()
    teams = list(
        session.scalars(select(held.c.team_id).where(~on_it).order_by(held.c.team_id).limit(limit))
    )
    for team_id in teams:
        theirs = select(ExtMaterial.id).where(ExtMaterial.team_id == team_id)
        # The text first, as in ``delete_material``.
        session.execute(delete(ExtMaterialChunk).where(ExtMaterialChunk.material_id.in_(theirs)))
        materials = _deleted(
            session.execute(delete(ExtMaterial).where(ExtMaterial.team_id == team_id))
        )
        alarms = _deleted(
            session.execute(delete(ExtMaterialAlarm).where(ExtMaterialAlarm.team_id == team_id))
        )
        session.execute(delete(ExtMaterialSetting).where(ExtMaterialSetting.team_id == team_id))
        log.info(
            "extraction_memberless_team_materials_forgotten",
            team_id=team_id,
            materials=materials,
            alarms=alarms,
        )
    return len(teams)


def forget_old_alarms(session: Session, *, now: datetime) -> int:
    """Delete every alarm row older than ``ALARM_KEPT``. Returns how many."""
    gone = session.execute(
        delete(ExtMaterialAlarm).where(ExtMaterialAlarm.created_at < now - ALARM_KEPT)
    )
    return int(getattr(gone, "rowcount", 0) or 0)
