"""A meeting's material, kept as masked text in pieces with a vector each, and
found again by meaning (the user, 2026-10-06, after the 10-05 mentoring: do
retrieval on a real vector database).

A team brings a document to a meeting. Kept here, a question about it can be
answered from what it says: the document is cut into chunks, each chunk is
embedded, and a question is answered by the chunks whose vectors lie nearest
the question's -- PostgreSQL's ``pgvector``, the extension module D already
uses for its topics (ADR 0004). Read once, when it is registered, so everyone
on the meeting's team asks the same store and nobody needs their own grant to
the file.

**What is built here is the store and the search, and nothing that fills it.**
Where a document comes from -- a file a person picked from their Drive, an
upload -- and the rules it is masked by are other owners' to agree (#817):
there is no route, no task and no tool that registers one, and ``register``
cannot be called without a masker, which no package provides yet. Asking in
words from the assistant is not here either: an agent tool takes ids, and a
question is not one (agent-layer.md, rules for a tool).

**Privacy is in the write, not in the caller** (invariant 11):

- ``register`` takes the masker as a required argument. There is no default.
- After masking, the title and every chunk are checked with the same detector
  the outbound check uses (``find_unmasked``). A value it still finds refuses
  the whole document: nothing is stored, and the error names categories, never
  text.
- The original file is never here. ``source_ref`` says where it is.
- Embedding is whatever ``get_embedder`` gives -- weights in this process, or
  the fake. No text leaves for it.
- A material is its meeting's: deleted with it, and not read once the meeting
  is past retention. Whoever may read the meeting may read it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from autune_core import Meeting, PrivacyViolationError, get_logger
from autune_integrations.privacy import find_unmasked

from .models import EMBEDDING_DIM, ExtMaterial, ExtMaterialChunk
from .service import live_meeting, within_retention

log = get_logger(__name__)

CHUNK_CHARS = 500
"""The longest chunk. A few sentences: long enough to answer from, short enough
that its vector is about one thing. Not measured on real documents."""

OVERLAP_CHARS = 120
"""A chunk begins with the last sentence of the one before it when that
sentence is no longer than this, so a point made across a chunk's edge is
whole in one of the two."""

MAX_HITS = 5
"""What a search returns at most -- the number an agent tool keeps
(agent-layer.md section 4), so a caller built on this needs no second cut."""

SOURCES = ("drive", "upload")

_SENTENCE_END = re.compile(r"(?<=[.?!。])\s+|\n+")
_SPACE = re.compile(r"\s+")


class DocumentMasker(Protocol):
    """What turns a document's text into text that may be stored. The rules are
    not this module's: a document is not speech, and which values a document's
    masker hides is the privacy owner's decision (#817)."""

    def mask(self, text: str) -> str: ...


class Embedder(Protocol):
    model_version: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class Hit:
    """One chunk a search found, nearest first. ``distance`` is cosine distance:
    0 is the same direction, 1 unrelated."""

    material_id: str
    meeting_id: str
    title: str
    position: int
    text: str
    distance: float


def chunks(text: str) -> list[str]:
    """``text`` as chunks of at most ``CHUNK_CHARS``, in order.

    Sentences are kept whole and packed until the next would not fit; a
    sentence longer than a chunk is cut at a space. Each chunk after the first
    starts with the previous one's last sentence when it is short
    (``OVERLAP_CHARS``). Blank text gives no chunk.
    """
    sentences: list[str] = []
    for part in _SENTENCE_END.split(text):
        part = _SPACE.sub(" ", part).strip()
        while len(part) > CHUNK_CHARS:
            cut = part.rfind(" ", CHUNK_CHARS // 2, CHUNK_CHARS + 1)
            cut = cut if cut > 0 else CHUNK_CHARS
            sentences.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            sentences.append(part)

    out: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in sentences:
        if current and size + 1 + len(sentence) > CHUNK_CHARS:
            out.append(" ".join(current))
            last = current[-1]
            carried = len(last) <= OVERLAP_CHARS and len(last) + 1 + len(sentence) <= CHUNK_CHARS
            current = [last] if carried else []
            size = len(last) if carried else 0
        current.append(sentence)
        size += len(sentence) + (1 if size else 0)
    if current:
        out.append(" ".join(current))
    return out


def _fitted(vector: Sequence[float]) -> list[float]:
    """``vector`` at the column's width. A shorter one -- the fake embedder's --
    is padded with zeros, which changes no cosine distance; a longer one is a
    model this column was not made for."""
    if len(vector) > EMBEDDING_DIM:
        raise ValueError(
            f"the embedder gives {len(vector)} dimensions; ext_material_chunks holds "
            f"{EMBEDDING_DIM}"
        )
    return [float(v) for v in vector] + [0.0] * (EMBEDDING_DIM - len(vector))


def _require_masked(texts: Sequence[str]) -> None:
    """Refuse the document if the detector still finds a value in any of
    ``texts``. Categories only: the error is read by people and by error
    tracking, and the value is what must not be there."""
    found = sorted({category for text in texts for category in find_unmasked(text)})
    if found:
        raise PrivacyViolationError(
            "refusing to store a document that is not masked", categories=found
        )


def register(
    session: Session,
    *,
    meeting_id: str,
    title: str,
    source: str,
    text: str,
    masker: DocumentMasker,
    embedder: Embedder,
    source_ref: str | None = None,
    registered_by: str | None = None,
) -> ExtMaterial | None:
    """Keep a document's text for its meeting: masked, chunked, embedded.

    ``text`` is the document as read, before masking, and goes no further than
    this call -- it is not logged and not stored. ``None`` when the meeting does
    not exist or is past retention. Registering the same ``source_ref`` for the
    same meeting again replaces what was kept, so a document that changed is
    read again rather than kept twice.

    Raises ``PrivacyViolationError`` when the masked title or text still holds
    a value the detector knows; nothing is stored then.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; known: {SOURCES}")
    if live_meeting(session, meeting_id) is None:
        return None

    kept_title = masker.mask(title).strip()
    pieces = chunks(masker.mask(text))
    _require_masked([kept_title, *pieces])
    vectors = embedder.embed(pieces) if pieces else []
    if len(vectors) != len(pieces):
        raise ValueError(f"asked for {len(pieces)} vectors, the embedder returned {len(vectors)}")

    if source_ref is not None:
        for stale in session.scalars(
            select(ExtMaterial).where(
                ExtMaterial.meeting_id == meeting_id,
                ExtMaterial.source == source,
                ExtMaterial.source_ref == source_ref,
            )
        ):
            session.delete(stale)
        session.flush()

    material = ExtMaterial(
        meeting_id=meeting_id,
        title=kept_title,
        source=source,
        source_ref=source_ref,
        registered_by=registered_by,
        model_version=embedder.model_version,
        chunks=[
            ExtMaterialChunk(position=position, text=piece, embedding=_fitted(vector))
            for position, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
        ],
    )
    session.add(material)
    session.flush()
    # Ids and counts only: a title and a chunk are a team's document.
    log.info(
        "extraction_material_registered",
        material_id=material.id,
        meeting_id=meeting_id,
        source=source,
        chunks=len(pieces),
        model_version=embedder.model_version,
    )
    return material


def for_meeting(session: Session, meeting_id: str) -> list[ExtMaterial]:
    """The meeting's materials, oldest first; none for a meeting past retention."""
    if live_meeting(session, meeting_id) is None:
        return []
    return list(
        session.scalars(
            select(ExtMaterial)
            .where(ExtMaterial.meeting_id == meeting_id)
            .order_by(ExtMaterial.created_at, ExtMaterial.id)
        )
    )


def remove(session: Session, material_id: str) -> bool:
    """Delete one material and its chunks. Whether there was one."""
    session.execute(delete(ExtMaterialChunk).where(ExtMaterialChunk.material_id == material_id))
    return bool(
        session.execute(delete(ExtMaterial).where(ExtMaterial.id == material_id)).rowcount  # type: ignore[attr-defined]
    )


def search(
    session: Session,
    *,
    team_id: str,
    vector: Sequence[float],
    limit: int = MAX_HITS,
    meeting_id: str | None = None,
    now: datetime | None = None,
) -> list[Hit]:
    """The chunks nearest ``vector`` among the team's materials, nearest first.

    Only meetings of ``team_id`` that are within retention are read -- the
    visibility a meeting's own rows have -- and ``meeting_id`` narrows it to
    one. Cosine distance, by ``pgvector``'s operator and its index: this is a
    PostgreSQL query and has no other form. The caller has already checked
    that whoever is asking is on the team.
    """
    distance = ExtMaterialChunk.embedding.cosine_distance(_fitted(vector))
    statement = (
        select(ExtMaterialChunk, ExtMaterial, distance.label("distance"))
        .join(ExtMaterial, ExtMaterial.id == ExtMaterialChunk.material_id)
        .join(Meeting, Meeting.id == ExtMaterial.meeting_id)
        .where(Meeting.team_id == team_id, within_retention(now))
        .order_by(distance, ExtMaterialChunk.id)
        .limit(max(1, min(limit, MAX_HITS)))
    )
    if meeting_id is not None:
        statement = statement.where(ExtMaterial.meeting_id == meeting_id)
    return [
        Hit(
            material_id=material.id,
            meeting_id=material.meeting_id,
            title=material.title,
            position=chunk.position,
            text=chunk.text,
            distance=float(found),
        )
        for chunk, material, found in session.execute(statement)
    ]


def ask(
    session: Session,
    *,
    team_id: str,
    question: str,
    embedder: Embedder,
    limit: int = MAX_HITS,
    meeting_id: str | None = None,
) -> list[Hit]:
    """``search`` for a question in words: embedded by the same model the
    chunks were, in this process. A blank question finds nothing."""
    if not question.strip():
        return []
    (vector,) = embedder.embed([question.strip()])
    return search(session, team_id=team_id, vector=vector, limit=limit, meeting_id=meeting_id)
