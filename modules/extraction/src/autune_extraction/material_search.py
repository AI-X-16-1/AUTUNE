"""A team searches the masked text of its uploaded materials (#817, step 3).

The rule is ``docs/architecture/privacy.md``, "Uploaded documents are masked
before storage, and the original is not kept", and the answers on #817 that
bind this step (10(b), 2026-10-08):

- **What is searched is masked text, by its vectors.** Each piece of an
  upload has a vector made from its masked text (``materials.store_upload``),
  stored in ``ext_material_chunks.embedding`` and nowhere else, so a member's
  delete leaves nothing to find. Only uploads of the asking team, embedded by
  the embedder this process asks with, are read. A Drive link is never a hit:
  nothing of it was read.
- **An upload past its day is not read.** Its ``expires_at`` was written at
  the upload (``materials.upload_deadline``); from that moment on it is no
  hit, though the hourly task (``materials.forget_expired_uploads``) may not
  have deleted it yet. The two use one rule -- gone at ``expires_at <= now``,
  unless the team keeps its uploads until deleted -- so nothing the task
  would delete is ever an answer.
- **The question is a person's words, and it is used to search and for
  nothing else.** It is masked with the document masker before it is
  embedded or looked at further; the masked form is what the embedder gets
  and what an excerpt is centred on. Neither form is stored, returned or
  logged -- the log line carries the team and a count -- and an error names
  the rule, never the value.
- **What leaves is short.** One excerpt per material, at most
  ``EXCERPT_CHARS`` of one stored piece, never a whole document; at most
  ``MAX_HITS`` materials. Every excerpt and title passes ``screen_output``
  on its way out, and a hit the screen still refuses is dropped, not shown.
- **No model but the embedder.** This function hands nothing to a language
  model. The assistant's tool over it (``tools.find_materials``) is off by
  default for that reason.

Ranking is cosine similarity with a floor
(``ExtractionSettings.material_search_min_similarity``): a material whose
nearest piece is under it is not a hit, so a question about nothing the team
uploaded answers no materials, with a notice, instead of the nearest
unrelated ones. The default was measured for KURE-v1 on made-up text, not
on a team's materials; the setting's docstring says what it does and does
not do.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from autune_core import get_logger
from autune_core.errors import PrivacyViolationError, ValidationError
from autune_integrations.document_masking import mask_document, screen_output
from autune_integrations.privacy import MASK_CHAR

from .config import get_settings
from .materials import embedded, keeps_until_deleted
from .models import ExtMaterial, ExtMaterialChunk
from .pipeline.base import Embedder
from .pipeline.registry import get_material_embedder

log = get_logger(__name__)

MAX_QUESTION_CHARS = 300
"""The longest question, spaces between words counted once. The 자료 screen's
box stops here (``MAX_QUESTION_CHARS`` in ``apps/web/.../materialSearch.ts``)."""

MAX_HITS = 5
"""Materials in one answer, best first: the agent layer's cap, and the
screen's."""

EXCERPT_CHARS = 200
"""The longest excerpt: under half of a stored piece (``materials.CHUNK_CHARS``),
one piece per material. Five hits are at most a thousand characters of a
team's documents -- never a whole one. #817 set no number; this is the
conservative one until the team does."""

CANDIDATES = 50
"""Nearest pieces read before they are grouped by material. Five materials
are found among them unless one material holds nearly all of them."""

QUESTION_MASKED = "질문에서 전화번호처럼 개인정보로 보이는 값은 가리고 찾았습니다."
QUESTION_REFUSED = (
    "질문에 개인정보로 보이는 값이 남아 있어 찾지 않았습니다. 그 값을 빼고 다시 질문해 주세요."
)
"""Said when the masker could not clear a question, so nothing was searched.
Not ``QUESTION_MASKED``, which says a search was made."""
ASKS_FOR_PERSONAL = (
    "전화번호처럼 형식이 정해진 개인정보는 보관할 때 가려 두었으므로 찾아서 보여 드릴 수 없습니다."
)
STARRED = "별표(*)로 보이는 부분은 개인정보로 보여 가려진 값일 수 있습니다."
NOTHING_CLOSE = "올린 자료에서 질문과 충분히 가까운 내용을 찾지 못했습니다."

_MASKED_RUN = re.compile(r"\*+(?:[-.@_+/]\*+)*")
"""What the masker leaves where a value was: every letter and digit of it a
star, its separators kept (``document_masking._hide``) -- ``***-****-****``,
``*******@****.***``. Every shape it hides has at least three letters or
digits, so a run counts with three stars or more: a lone star (a footnote, a
bullet) or markdown's ``**bold**`` is not one."""

_RULE_LINE = re.compile(r"^[ \t]*\*{3,}[ \t]*$", re.MULTILINE)
"""A line of stars alone: markdown's horizontal rule, not a hidden value."""

_PERSONAL_KINDS = re.compile(
    r"전화\s*번호|연락처|휴대\s*폰|핸드폰|이메일|메일\s*주소|주민\s*(등록\s*)?번호|계좌|카드\s*번호|여권\s*번호",
    re.IGNORECASE,
)
"""Words that ask for a value the masker hides. The answer says such a value
cannot be shown (#817, the owner's line 7: "찾을 때 개인정보 출력 막는
가이드라인"); the search still runs for the rest of the question."""

_WORD = re.compile(r"[^\W_]{2,}")


@dataclass(frozen=True)
class MaterialHit:
    material_id: str
    title: str
    """Screened on the way out: typed by a member, it was never masked."""
    position: int
    """Which stored piece the excerpt is from, counted from 0."""
    excerpt: str
    """Masked text, screened again, at most ``EXCERPT_CHARS`` plus the "…" at
    a cut end."""
    score: float
    """Cosine similarity to the question, -1 to 1."""


@dataclass(frozen=True)
class MaterialAnswer:
    hits: list[MaterialHit]
    notice: str | None
    """A sentence of this module's own, or None. Never the question."""
    more: bool
    """More materials answered than ``hits`` holds."""


def checked_question(question: str) -> str:
    """``question`` with its spaces folded, or a refusal that names the rule
    and not the value."""
    clean = " ".join(question.split())
    if not clean or len(clean) > MAX_QUESTION_CHARS:
        raise ValidationError(
            f"a question is 1 to {MAX_QUESTION_CHARS} characters", field="question"
        )
    return clean


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _nearest(
    session: Session, team_id: str, vector: list[float], model: str, *, now: datetime
) -> list[tuple[str, str, int, str, float]]:
    """The team's pieces nearest ``vector``: (material id, title, position,
    text, similarity), nearest first. PostgreSQL ranks by pgvector's cosine
    operator under its index; another database (the unit suite's SQLite)
    ranks the team's pieces here. An upload whose ``expires_at`` is ``now``
    or earlier is not read, though the hourly task may not have deleted it
    yet -- unless the team keeps its uploads until deleted, as that task
    passes such a team over."""
    base = (
        select(ExtMaterial.id, ExtMaterial.title, ExtMaterialChunk.position, ExtMaterialChunk.text)
        .join(ExtMaterial, ExtMaterial.id == ExtMaterialChunk.material_id)
        .where(
            ExtMaterial.team_id == team_id,
            ExtMaterial.source == "upload",
            ExtMaterial.embedding_model == model,
            ExtMaterialChunk.embedding.is_not(None),
        )
    )
    if not keeps_until_deleted(session, team_id):
        base = base.where(ExtMaterial.expires_at > now)
    if session.get_bind().dialect.name == "postgresql":
        distance = ExtMaterialChunk.embedding.cosine_distance(vector)
        rows = session.execute(
            base.add_columns(distance.label("distance"))
            .order_by(distance, ExtMaterial.id, ExtMaterialChunk.position)
            .limit(CANDIDATES)
        )
        return [(m, t, p, x, 1.0 - float(d)) for m, t, p, x, d in rows]
    scored = [
        (m, t, p, x, _cosine([float(v) for v in e], vector))
        for m, t, p, x, e in session.execute(base.add_columns(ExtMaterialChunk.embedding))
    ]
    scored.sort(key=lambda row: (-row[4], row[0], row[2]))
    return scored[:CANDIDATES]


def _words(masked_question: str) -> list[str]:
    """The masked question's words worth looking for in a piece: two letters
    or more, and nothing the masker hid."""
    return [w.casefold() for w in _WORD.findall(masked_question)]


def _pairs(masked_question: str) -> set[str]:
    """Every two adjacent letters inside a word of the masked question.
    Korean glues its particles and endings to the word (롤백은, 배포할), so a
    whole word rarely recurs in a piece; its pairs (롤백) do."""
    return {w[i : i + 2] for w in _words(masked_question) for i in range(len(w) - 1)}


_SEGMENT = re.compile(r"[^\n]+?(?:[.!?。](?=\s)|$)", re.MULTILINE)
"""A line, or a sentence of a line: up to an ending mark before a space, or
to the line's end."""


def _segments(text: str) -> list[tuple[int, int]]:
    spans = []
    for match in _SEGMENT.finditer(text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        if start < end:
            spans.append((start, end))
    return spans


def _best_segment(text: str, masked_question: str) -> tuple[int, int] | None:
    """The line or sentence of ``text`` that shares most with the masked
    question, or None when none shares anything.

    Each of the question's letter pairs counts once per segment that holds
    it, weighted by how few segments do: a pair in every line (the topic of
    the whole piece) says least about where the answer is. A heading line
    counts half, for the same reason -- it names what follows rather than
    saying it. The first of equals wins."""
    pairs = _pairs(masked_question)
    spans = _segments(text)
    if not pairs or not spans:
        return None
    held = [{p for p in pairs if p in text[a:b].casefold()} for a, b in spans]
    df = {p: sum(p in h for h in held) for p in pairs}
    best: tuple[int, int] | None = None
    best_score = 0.0
    for (a, b), h in zip(spans, held, strict=True):
        score = sum(math.log(1 + len(spans) / df[p]) for p in h)
        if text[a] == "#":
            score /= 2
        if score > best_score:
            best, best_score = (a, b), score
    return best


def excerpt_of(text: str, masked_question: str, size: int = EXCERPT_CHARS) -> str:
    """At most ``size`` characters of a stored piece, centred on the line or
    sentence that shares most with the masked question (from the piece's
    start when none shares anything), with "…" where it was cut out of a
    longer piece. Never the question itself: only its masked form is read,
    and only to choose the window."""
    flat = text.strip()
    if len(flat) <= size:
        return flat
    found = _best_segment(flat, masked_question)
    if found is None:
        start = 0
    else:
        a, b = found
        if b - a >= size:
            # A segment longer than the window: start a little before the
            # first of the question's pairs it holds.
            folded = flat[a:b].casefold()
            hits = [i for i in (folded.find(p) for p in _pairs(masked_question)) if i >= 0]
            start = a + max(0, min(hits, default=0) - size // 4)
        else:
            start = a - (size - (b - a)) // 2
        start = max(0, min(start, len(flat) - size))
        if start:
            # Begin on a word (or line) boundary, without cutting into the
            # chosen segment.
            limit = start + size // 4
            if start < found[0]:
                limit = min(limit, found[0])
            cuts = [i for i in (flat.find(c, start, limit) for c in " \n") if i >= 0]
            if cuts:
                start = min(cuts) + 1
    end = min(len(flat), start + size)
    if end < len(flat):
        floor = start + size // 2
        if found is not None and found[1] <= end:
            floor = max(floor, found[1])
        cut = max(flat.rfind(" ", floor, end), flat.rfind("\n", floor, end))
        end = cut if cut > start else end
    piece = flat[start:end].strip()
    return ("…" if start > 0 else "") + piece + ("…" if end < len(flat) else "")


def shows_masked_run(excerpt: str) -> bool:
    """Whether ``excerpt`` shows a value the masker hid -- a run of stars of
    the masker's shape -- and not merely a star."""
    shown = _RULE_LINE.sub("", excerpt)
    return any(run.group().count(MASK_CHAR) >= 3 for run in _MASKED_RUN.finditer(shown))


def _notice(question_hid: bool, asks_personal: bool, hits: list[MaterialHit]) -> str | None:
    said = []
    if not hits:
        said.append(NOTHING_CLOSE)
    if question_hid:
        said.append(QUESTION_MASKED)
    if asks_personal:
        said.append(ASKS_FOR_PERSONAL)
    if any(shows_masked_run(hit.excerpt) for hit in hits):
        said.append(STARRED)
    return " ".join(said) or None


def search_materials(
    session: Session,
    team_id: str,
    query: str,
    *,
    limit: int = MAX_HITS,
    embedder: Embedder | None = None,
    now: datetime | None = None,
) -> MaterialAnswer:
    """The team's uploaded materials nearest ``query``, best first: one hit per
    material, its nearest piece as an excerpt. **The caller has checked that
    the asker is a member of ``team_id``.** A material whose nearest piece is
    under the similarity floor is not a hit; with none left, ``hits`` is
    empty and ``notice`` says nothing close enough was found.

    ``query`` is masked before anything else is done with it and is kept
    nowhere; a blank or too long one raises ``ValidationError`` naming the
    field. ``limit`` is pulled into 1..``MAX_HITS``. ``now`` is the moment an
    upload's day is compared with -- this moment, unless a caller names one."""
    clean = checked_question(query)
    try:
        masked = mask_document(clean)
    except PrivacyViolationError:
        # The masker could not clear it: nothing of it goes to the embedder.
        log.info("extraction_material_search_refused", team_id=team_id)
        return MaterialAnswer(hits=[], notice=QUESTION_REFUSED, more=False)
    del clean
    asks_personal = bool(_PERSONAL_KINDS.search(masked.text))
    model = embedder or get_material_embedder()
    (vector,) = embedded(model, [masked.text])
    wanted = max(1, min(int(limit), MAX_HITS))
    floor = get_settings().material_search_min_similarity

    hits: list[MaterialHit] = []
    seen: set[str] = set()
    dropped = 0
    for material_id, title, position, text, score in _nearest(
        session, team_id, vector, model.model_version, now=now or datetime.now(UTC)
    ):
        if score < floor:
            # Nearest first: everything after this is further still.
            break
        if material_id in seen:
            continue
        seen.add(material_id)
        if len(hits) >= wanted:
            continue
        try:
            hit = MaterialHit(
                material_id=material_id,
                title=screen_output(title),
                position=position,
                excerpt=screen_output(excerpt_of(text, masked.text)),
                score=round(score, 4),
            )
        except PrivacyViolationError:
            dropped += 1
            continue
        hits.append(hit)
    more = len(seen) - dropped > len(hits)
    log.info("extraction_material_searched", team_id=team_id, hits=len(hits), dropped=dropped)
    return MaterialAnswer(
        hits=hits, notice=_notice(bool(masked.counts), asks_personal, hits), more=more
    )
