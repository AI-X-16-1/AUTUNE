"""A team's materials: Google Drive files it keeps on the 자료 screen (#817).

A member pastes a Drive link and types a title; the team's members see the
list and open a file in Google's own preview (#844). That is all of it, by
the decision on #817 (2026-10-06, "링크와 미리보기까지만"):

- **Autune reads nothing of the file.** No text, no chunks, no vectors, no
  Drive permission. Whether a viewer may see the file is Google's answer under
  the viewer's own sign-in.
- **The pasted text is not stored.** ``parse_drive_link`` takes the file's id
  and which Google editor it belongs to, and only those are kept; the address
  is built again from the id where it is shown. It follows the rules of
  ``apps/web/src/shared/drive/driveLink.ts`` -- the two are kept together, and
  what one refuses the other refuses.
- **A row names no person.** Any member of the team registers and deletes, as
  with the team's projects; there is no admin role yet (#592).
- **Nothing goes out.** No Slack, Notion, Jira or model sees a title or a file
  id, and no agent tool reads this table.

The title and the file id are never logged: a title is typed by a person and
can hold a name, and a file id opens a file shared by link. Log lines carry
the material's id and the team's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autune_core import get_logger
from autune_core.errors import ConflictError, NotFoundError, ValidationError

from .models import DRIVE_KINDS, ExtMaterial
from .schemas import MaterialRead

log = get_logger(__name__)

MAX_MATERIALS = 200
"""How many a team keeps. A shelf, not an archive: past this, delete one first."""

MAX_TITLE_CHARS = 120
MAX_LINK_CHARS = 2000

_ID = re.compile(r"[A-Za-z0-9_-]{10,200}")
"""Drive ids are URL-safe base64-ish -- ``ID`` in ``driveLink.ts``."""

_EDITORS = tuple(kind for kind in DRIVE_KINDS if kind != "file")


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


def register(session: Session, team_id: str, *, title: str, link: str) -> ExtMaterial:
    """Put a Drive file on the team's shelf under the title a member typed."""
    clean = " ".join(title.split())
    if not clean:
        raise ValidationError("a material needs a title", field="title")
    if len(clean) > MAX_TITLE_CHARS:
        raise ValidationError(f"a title has at most {MAX_TITLE_CHARS} characters", field="title")
    file = parse_drive_link(link)
    if file is None:
        # The message names the rule and not the value: what was pasted may be
        # anything, and an error string ends up in error tracking.
        raise ValidationError("not a Google Drive file link", field="link")
    lock_shelf(session, team_id)
    held = session.scalar(
        select(func.count()).select_from(ExtMaterial).where(ExtMaterial.team_id == team_id)
    )
    if (held or 0) >= MAX_MATERIALS:
        raise ValidationError(f"a team keeps at most {MAX_MATERIALS} materials", field="link")
    row = ExtMaterial(team_id=team_id, title=clean, drive_file_id=file.id, drive_kind=file.kind)
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


def delete_material(session: Session, team_id: str, material_id: str) -> None:
    """Take one of this team's materials off its shelf. The Drive file is not
    Autune's and is not touched."""
    row = session.get(ExtMaterial, material_id)
    if row is None or row.team_id != team_id:
        raise NotFoundError("material", material_id)
    session.delete(row)
    session.flush()
    log.info("extraction_material_deleted", material_id=material_id, team_id=team_id)
