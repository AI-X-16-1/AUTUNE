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
  With ``material_search_excerpts`` above 1 (off by default), a hit also
  carries the excerpts of its next-nearest pieces among the ``CANDIDATES``
  read, each cut and screened the same way; one the screen refuses is left
  out and the hit stays.
- **No model but the embedder.** This function hands nothing to a language
  model. The assistant's tool over it (``tools.find_materials``) is off by
  default for that reason.

Ranking is cosine similarity with a floor
(``ExtractionSettings.material_search_min_similarity``): a material whose
nearest piece is under it is not a hit, so a question about nothing the team
uploaded answers no materials, with a notice, instead of the nearest
unrelated ones. Over the floor there is a second cut, relative to the best
material (``ExtractionSettings.material_search_max_below_best``): a material
whose nearest piece is further below the best one's than that is not a hit
either, so one clear answer does not arrive with four that merely passed.
Both defaults were measured for KURE-v1 on made-up text, not on a team's
materials; each setting's docstring says what it does and does not do.

**A word match finds what the floor loses** (#817, 2026-10-13). A question of
one or two words, or one that repeats a file's title, often scores under the
floor against the very piece that answers it. So, the floor and the cut left
as they are, a material is also a hit when every word of the masked question
(``_words``: two letters or digits and more, nothing the masker hid) is

- in its **title** -- whatever its pieces score; or
- in one of its **pieces** that scores under the floor and at or over
  ``ExtractionSettings.material_search_word_match_min_similarity``, and no
  further below the best hit than the relative cut allows (the best material
  over the floor, or, with none, the best piece holding the words).

An answer is ordered: materials found by their title, nearest first; then
the materials over the floor, exactly those the floor and the cut alone
leave, nearest first; then the materials found by a piece's words. Five at
most all the same, so a title match can take the place of a material the
vectors found -- ``more`` says so.

**It is a substring match, not a word match in the dictionary's sense.** The
question's "지사" is in a piece that says "부산지사", and its "선물" in
"선물할 수 있나요". That is what finds 롤백 in 롤백은, and what finds a
common word everywhere: on the measured shelf "회의" was in 50 of 200 files
and in 17 titles, and a one-word question on it answers the five nearest of
those. A question with no word of two letters (a version number, "앱") gets
no word match.

**The words stay in this process.** They are compared with rows read by the
same rule the vectors are read by (``_reach``: the asking team, uploads not
past their day, this embedder), and they are in no statement: a bound
parameter is part of a failed statement's message, and the engine does not
hide parameters (#356), so a ``LIKE`` on the words would put a person's
question in an error. A title or an excerpt found this way passes
``screen_output`` like any other.

Measured for KURE-v1 on made-up text, not on a team's materials; the
setting's docstring has the numbers.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, select
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
"""The longest excerpt, of one stored piece: with pieces of 200 characters
(``materials.CHUNK_CHARS``) the piece itself, and the first 200 of an older,
longer one. With ``material_search_excerpts`` at 1, five hits are at most a
thousand characters of a team's documents; at 3 they are at most three
thousand. A material of 200 characters or less is shown whole at 1, and one of
600 or less at 3: "never a whole document" holds only for a longer one. What
#817 10(b) allows once a piece is the excerpt is open decision 8 of ADR 0011.
#817 set no number; this is the conservative one until the team does."""

CANDIDATES = 50
"""Nearest pieces read before they are grouped by material. Five materials
are found among them unless one material holds nearly all of them."""

WORD_CANDIDATES = 1000
"""Pieces under the floor whose text is read for a word match, nearest
first. A team's shelf can be far larger -- ``materials.MAX_MATERIALS`` files
of ``material_formats.MAX_TEXT_CHARS`` characters are 120,000 pieces -- and
a search reads at most this many of them, half a million characters, to look
for words. The shelf the word match was measured on had 229 pieces."""

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
    """Cosine similarity of the excerpt's piece to the question, -1 to 1.
    Under the floor for a material found by its words."""
    more_excerpts: tuple[str, ...] = ()
    """Excerpts of the material's next-nearest pieces, nearest first, as many
    as ``material_search_excerpts`` allows less the first -- none by default.
    Masked, screened and cut as ``excerpt`` is."""


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


_Piece = tuple[str, str, int, str, float]
"""(material id, title, position, text, similarity to the question)."""

_Reach = Select[tuple[str, str, int, str]]


def _reach(session: Session, team_id: str, model: str, *, now: datetime) -> _Reach:
    """What a search of ``team_id`` may read, however a hit is found: the
    pieces of the team's uploads embedded by ``model``, as (material id,
    title, position, text). An upload whose ``expires_at`` is ``now`` or
    earlier is not read, though the hourly task may not have deleted it yet
    -- unless the team keeps its uploads until deleted, as that task passes
    such a team over. Every reader below starts from this, so a material the
    vectors would not show does not come back through a word."""
    reach = (
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
        reach = reach.where(ExtMaterial.expires_at > now)
    return reach


def _ranked_here(session: Session, reach: _Reach, vector: list[float]) -> list[_Piece]:
    """Every piece of ``reach`` with its similarity to ``vector``, nearest
    first, ranked in this process: for a database without pgvector (the unit
    suite's SQLite)."""
    scored = [
        (m, t, p, x, _cosine([float(v) for v in e], vector))
        for m, t, p, x, e in session.execute(reach.add_columns(ExtMaterialChunk.embedding))
    ]
    scored.sort(key=lambda row: (-row[4], row[0], row[2]))
    return scored


def _on_pgvector(session: Session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def _nearest(session: Session, reach: _Reach, vector: list[float]) -> list[_Piece]:
    """The ``CANDIDATES`` pieces of ``reach`` nearest ``vector``, nearest
    first. PostgreSQL ranks by pgvector's cosine operator under its index;
    another database ranks the pieces here."""
    if not _on_pgvector(session):
        return _ranked_here(session, reach, vector)[:CANDIDATES]
    distance = ExtMaterialChunk.embedding.cosine_distance(vector)
    rows = session.execute(
        reach.add_columns(distance.label("distance"))
        .order_by(distance, ExtMaterial.id, ExtMaterialChunk.position)
        .limit(CANDIDATES)
    )
    return [(m, t, p, x, 1.0 - float(d)) for m, t, p, x, d in rows]


def _nearest_of_each(session: Session, reach: _Reach, vector: list[float]) -> list[_Piece]:
    """Every material of ``reach`` once, with the piece of it nearest
    ``vector``: what a title is matched against, and the excerpt a material
    found by its title is shown with. As many rows as the team has uploads,
    ``materials.MAX_MATERIALS`` at most. No word of the question is in the
    statement; the titles are compared in this process."""
    if not _on_pgvector(session):
        first: dict[str, _Piece] = {}
        for row in _ranked_here(session, reach, vector):
            first.setdefault(row[0], row)
        return list(first.values())
    score = (1 - ExtMaterialChunk.embedding.cosine_distance(vector)).label("score")
    rows = session.execute(
        reach.add_columns(score)
        .distinct(ExtMaterial.id)
        .order_by(ExtMaterial.id, score.desc(), ExtMaterialChunk.position)
    )
    return [(m, t, p, x, float(s)) for m, t, p, x, s in rows]


def _under_the_floor(
    session: Session, reach: _Reach, vector: list[float], *, low: float, high: float
) -> list[_Piece]:
    """The pieces of ``reach`` scoring ``low`` or more and under ``high``,
    nearest first, ``WORD_CANDIDATES`` at most: what a word match reads the
    text of. The two bounds are settings and a score, never the question's
    words, which are compared in this process.

    PostgreSQL sorts these itself rather than through the vector index: the
    index answers "the nearest few", approximately, and this asks for every
    piece of a band. Ordering by the similarity, not by the operator the
    index is built on, is what keeps the planner off it."""
    if not _on_pgvector(session):
        ranked = _ranked_here(session, reach, vector)
        return [row for row in ranked if low <= row[4] < high][:WORD_CANDIDATES]
    score = (1 - ExtMaterialChunk.embedding.cosine_distance(vector)).label("score")
    rows = session.execute(
        reach.add_columns(score)
        .where(score >= low, score < high)
        .order_by(score.desc(), ExtMaterial.id, ExtMaterialChunk.position)
        .limit(WORD_CANDIDATES)
    )
    return [(m, t, p, x, float(s)) for m, t, p, x, s in rows]


def _words(masked_question: str) -> list[str]:
    """The masked question's words worth looking for in a piece: two letters
    or more, and nothing the masker hid."""
    return [w.casefold() for w in _WORD.findall(masked_question)]


def _holds(text: str, words: list[str]) -> bool:
    """Whether every one of ``words`` is somewhere in ``text``, letter case
    aside. A substring: "지사" is in "부산지사"."""
    folded = text.casefold()
    return all(word in folded for word in words)


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


def _more_of(
    candidates: list[_Piece],
    material_id: str,
    position: int,
    masked_question: str,
    floor: float,
    count: int,
) -> tuple[str, ...]:
    """Excerpts of up to ``count`` other pieces of ``material_id`` among the
    nearest ``candidates`` (nearest first) that score ``floor`` or more. A
    piece whose excerpt the screen refuses is left out; the hit stays."""
    if count <= 0:
        return ()
    out: list[str] = []
    for m, _title, other, text, score in candidates:
        if len(out) >= count:
            break
        if m != material_id or other == position or score < floor:
            continue
        try:
            shown = screen_output(excerpt_of(text, masked_question))
        except PrivacyViolationError:
            continue
        if shown and shown not in out:
            out.append(shown)
    return tuple(out)


def search_materials(
    session: Session,
    team_id: str,
    query: str,
    *,
    limit: int = MAX_HITS,
    embedder: Embedder | None = None,
    now: datetime | None = None,
) -> MaterialAnswer:
    """The team's uploaded materials that answer ``query``: one hit per
    material, one piece of it as an excerpt. **The caller has checked that
    the asker is a member of ``team_id``.**

    First the materials whose title holds every word of the masked question,
    nearest first, each with its nearest piece. Then the materials nearest
    the question, best first: one whose nearest piece is under the
    similarity floor is not among them, nor one further below the best than
    the relative cut allows. Last the materials one of whose pieces, under
    the floor, holds every word -- at or over the word match's own lower
    bound and within the cut of the best hit. With none of the three,
    ``hits`` is empty and ``notice`` says nothing close enough was found;
    ``more`` counts the materials of all three.

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
    settings = get_settings()
    floor = settings.material_search_min_similarity
    margin = settings.material_search_max_below_best
    word_floor = settings.material_search_word_match_min_similarity
    reach = _reach(session, team_id, model.model_version, now=now or datetime.now(UTC))

    best: float | None = None
    near: dict[str, _Piece] = {}
    candidates = _nearest(session, reach, vector)
    for row in candidates:
        if row[4] < floor:
            # Nearest first: everything after this is further still.
            break
        if best is None:
            best = row[4]
        elif row[4] < best - margin:
            break
        near.setdefault(row[0], row)

    # Only the masked question's words: a value the masker hid is stars, and
    # stars are no word, so a search cannot ask whether a hidden value is in
    # a stored piece or a title.
    words = _words(masked.text)
    named: list[_Piece] = []
    worded: dict[str, _Piece] = {}
    if words and word_floor <= 1:
        named = [row for row in _nearest_of_each(session, reach, vector) if _holds(row[1], words)]
        named.sort(key=lambda row: (-row[4], row[0]))
        low = word_floor if best is None else max(word_floor, best - margin)
        if low < floor:
            top = best
            for row in _under_the_floor(session, reach, vector, low=low, high=floor):
                if not _holds(row[3], words):
                    continue
                if top is None:
                    top = row[4]
                elif row[4] < top - margin:
                    break
                worded.setdefault(row[0], row)

    extra = max(1, settings.material_search_excerpts) - 1
    found: dict[str, tuple[_Piece, str]] = {}
    for how, rows in (("title", named), ("vector", near.values()), ("words", worded.values())):
        for row in rows:
            found.setdefault(row[0], (row, how))

    hits: list[MaterialHit] = []
    shown = {"title": 0, "vector": 0, "words": 0}
    dropped = 0
    for (material_id, title, position, text, score), how in found.values():
        if len(hits) >= wanted:
            break
        try:
            hit = MaterialHit(
                material_id=material_id,
                title=screen_output(title),
                position=position,
                excerpt=screen_output(excerpt_of(text, masked.text)),
                score=round(score, 4),
                more_excerpts=_more_of(
                    candidates, material_id, position, masked.text, floor, extra
                ),
            )
        except PrivacyViolationError:
            dropped += 1
            continue
        hits.append(hit)
        shown[how] += 1
    more = len(found) - dropped > len(hits)
    log.info(
        "extraction_material_searched",
        team_id=team_id,
        hits=len(hits),
        dropped=dropped,
        by_title=shown["title"],
        by_words=shown["words"],
    )
    return MaterialAnswer(
        hits=hits, notice=_notice(bool(masked.counts), asks_personal, hits), more=more
    )
