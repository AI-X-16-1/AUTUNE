"""A file uploaded to a team's 자료 keeps only its masked text (#817).

The rules under test (``docs/architecture/privacy.md``, "Uploaded documents are
masked before storage, and the original is not kept"; the owner's 2026-10-10
decision on retention):

- the file is read in the request's memory -- no temporary file, the cap held
  as the body arrives, the buffers wiped on every path out, nothing of the body
  read before the caller is known to be a member;
- a file whose text cannot be read is refused whole, with a reason code and
  nothing of the file in the error;
- a file marked confidential in its name or its head is refused, nothing of it
  is stored, and one alarm row says the team, the time and the kind of marking;
- only masked text is stored, in pieces that each pass the outbound guard;
- no row and no log line names a person or holds the title, the file's name or
  a word of the file;
- a member's delete takes the text with the row; there is no window.

Every value here is invented.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import starlette.formparsers
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from autune_core import Base, Team, TeamMember, User, get_session
from autune_core.errors import AutuneError, PrivacyViolationError
from autune_extraction import material_upload, materials
from autune_extraction.config import get_settings
from autune_extraction.material_marking import file_marking, head_of
from autune_extraction.material_reading import (
    MAX_BYTES,
    MAX_TEXT_CHARS,
    UnreadableFileError,
    read_text,
    suffix_of,
)
from autune_extraction.models import ExtMaterial, ExtMaterialAlarm, ExtMaterialChunk
from autune_extraction.router import router
from autune_integrations.privacy import find_unmasked

from .conftest import sign_in

TEAM = "team_1"
PREFIX = "/api/extraction"
UPLOAD = f"{PREFIX}/materials/upload?team_id={TEAM}"

PHONE = "010-2345-6789"
EMAIL = "minsu.kim@example.com"
RRN = "900101-1234567"
BODY = (
    f"3분기 계획\n\n담당 연락처 {PHONE}, 메일 {EMAIL}.\n"
    f"주민번호 {RRN} 는 지웁니다.\n예산 1200만원\n"
)


# --- reading ----------------------------------------------------------------------


def test_utf8_with_or_without_a_bom_and_cp949_are_read() -> None:
    assert read_text("a.txt", "회의 자료".encode()) == "회의 자료"
    assert read_text("a.md", "﻿# 회의 자료".encode()) == "# 회의 자료"
    assert read_text("a.csv", "항목,금액\n회의,3".encode("cp949")) == "항목,금액\n회의,3"


@pytest.mark.parametrize(
    ("name", "expected"), [("a.TXT", ".txt"), ("a.b.md", ".md"), (".txt", ""), ("noext", "")]
)
def test_the_type_is_the_names_ending(name: str, expected: str) -> None:
    assert suffix_of(name) == expected


@pytest.mark.parametrize(
    ("name", "data", "reason"),
    [
        ("plan.pdf", b"%PDF-1.7", "unsupported_type"),
        ("plan.docx", b"PK\x03\x04", "unsupported_type"),
        (".txt", b"text", "unsupported_type"),
        ("plan", b"text", "unsupported_type"),
        ("plan.txt", b"", "empty"),
        ("plan.txt", b" \n\t\n", "empty"),
        ("plan.txt", b"\x80\x80\xff\xfe", "encoding"),
        ("plan.txt", b"abc\x00def", "damaged"),
        ("plan.txt", b"a" * (MAX_BYTES + 1), "too_large"),
        ("plan.txt", b"a" * (MAX_TEXT_CHARS + 1), "too_long"),
    ],
)
def test_a_file_that_cannot_be_read_is_refused_whole_with_a_reason(
    name: str, data: bytes, reason: str
) -> None:
    with pytest.raises(UnreadableFileError) as refused:
        read_text(name, data)

    error = refused.value
    assert (error.status_code, error.details) == (422, {"field": "file", "reason": reason})
    assert str(error) == f"the file cannot be read: {reason}"


def test_a_refusal_carries_nothing_of_the_file() -> None:
    with pytest.raises(UnreadableFileError) as refused:
        read_text("김민수_평가.txt", f"{PHONE}\x00".encode())
    said = repr(refused.value.to_dict()) + str(refused.value)
    assert PHONE not in said and "김민수" not in said


# --- the marking ------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "대외비",
        "대 외 비",
        "사외비 문서",
        "극비",
        "[기밀]",
        "보안 문서",
        "비밀문서",
        "2급 비밀",
        "Ⅲ 급 비밀",
        "사내 한정",
        "사내한정",
        "내부용",
    ],
)
def test_a_korean_marking_in_the_head_stops_the_file(line: str) -> None:
    assert file_marking("plan.txt", f"\n{line}\n본문") == "korean_marking"


@pytest.mark.parametrize(
    "line",
    [
        "CONFIDENTIAL",
        "Strictly Confidential",
        "top  secret",
        "Do Not Distribute",
        "Company Secret",
        "Internal Use Only",
        "internal only",
        "For internal use",
    ],
)
def test_an_english_marking_in_any_case_stops_the_file(line: str) -> None:
    assert file_marking("plan.md", f"# Plan\n{line}") == "english_marking"


def test_the_files_name_is_looked_at_with_underscores_as_spaces() -> None:
    assert file_marking("plan_confidential.txt", "본문") == "english_marking"
    assert file_marking("대외비_계획.md", "본문") == "korean_marking"
    assert file_marking("Internal API design.md", "본문") is None


@pytest.mark.parametrize("line", ["비밀", "secret", "내부", "internal", "Internal API design"])
def test_words_that_are_not_markings_on_purpose_pass(line: str) -> None:
    assert file_marking("plan.txt", line) is None


def test_only_the_first_five_lines_that_say_anything_are_the_head() -> None:
    lines = ["하나", "", "둘", "   ", "셋", "넷", "다섯", "대외비"]
    text = "\n".join(lines)

    assert head_of(text) == ["하나", "둘", "셋", "넷", "다섯"]
    # A marking in the body, line six on, is not seen -- the documented gap.
    assert file_marking("plan.txt", text) is None
    assert file_marking("plan.txt", "\n".join(["하나", "", "", "대외비"])) == "korean_marking"


# --- cutting ----------------------------------------------------------------------


def test_the_pieces_join_back_into_the_text_and_none_is_too_long() -> None:
    text = ("한 문단의 문장입니다. " * 30 + "\n") * 20 + "x" * 1300

    pieces = materials.cut(text)

    assert "".join(pieces) == text
    assert all(0 < len(piece) <= materials.CHUNK_CHARS for piece in pieces)
    # A piece ends at a line break where one falls in its second half.
    assert pieces[0].endswith("\n")


def test_a_short_text_is_one_piece() -> None:
    assert materials.cut("짧은 글") == ["짧은 글"]


# --- storing ----------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    shared = {m.__tablename__ for m in (User, Team, TeamMember)}
    owned = {"ext_materials", "ext_material_chunks", "ext_material_alarms"}
    tables = [t for name, t in Base.metadata.tables.items() if name in shared | owned]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as s:
        yield s


def upload(
    session: Session, *, title: str = "3분기 계획", name: str = "plan.txt", body: str = BODY
):
    return materials.store_upload(session, TEAM, title=title, file_name=name, data=body.encode())


def count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_only_masked_text_is_stored_and_each_piece_passes_the_guard(session: Session) -> None:
    row = upload(session, body=BODY * 40)

    assert (row.source, row.drive_file_id, row.drive_kind, row.title) == (
        "upload",
        None,
        None,
        "3분기 계획",
    )
    pieces = materials.material_text(session, row.id)
    assert len(pieces) > 1
    stored = "".join(pieces)
    for value in (PHONE, EMAIL, RRN, "2345", "6789", "minsu", "1234567"):
        assert value not in stored
    assert all(find_unmasked(piece) == [] for piece in pieces)
    # What is not personal data is kept as it was.
    assert "3분기 계획" in stored and "예산 1200만원" in stored


def test_nothing_of_the_file_but_masked_text_is_in_any_row(session: Session) -> None:
    row = upload(session, name="김민수_인사평가.txt")

    held = repr(
        [
            {c.name: getattr(r, c.name) for c in type(r).__table__.columns}
            for r in [row, *session.scalars(select(ExtMaterialChunk))]
        ]
    )
    assert "김민수" not in held and "인사평가" not in held and PHONE not in held


def test_a_marked_file_is_refused_stores_nothing_and_leaves_one_alarm_row(
    session: Session,
) -> None:
    with pytest.raises(materials.ConfidentialFileError) as refused:
        upload(session, body="대외비\n" + BODY)

    assert (refused.value.status_code, refused.value.code) == (422, "confidential_file")
    assert count(session, ExtMaterial) == 0 and count(session, ExtMaterialChunk) == 0
    [alarm] = session.scalars(select(ExtMaterialAlarm))
    assert (alarm.team_id, alarm.marking) == (TEAM, "korean_marking")


def test_an_alarm_row_names_no_person_and_no_file() -> None:
    assert {c.name for c in ExtMaterialAlarm.__table__.columns} == {
        "id",
        "team_id",
        "marking",
        "created_at",
    }
    assert {c.name for c in ExtMaterialChunk.__table__.columns} == {
        "material_id",
        "position",
        "text",
        "embedding",
    }


def test_a_title_that_reads_as_personal_data_is_refused_before_the_file_is_read(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_read(*_: object) -> str:
        raise AssertionError("the file was read")

    monkeypatch.setattr(materials, "read_text", must_not_read)
    with pytest.raises(AutuneError) as refused:
        upload(session, title=f"연락처 {PHONE}")
    assert refused.value.status_code == 422
    assert count(session, ExtMaterial) == 0


def test_uploads_and_links_share_the_cap(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(materials, "MAX_MATERIALS", 2)
    materials.register(session, TEAM, title="링크", link="a" * 20)
    upload(session)

    with pytest.raises(AutuneError) as refused:
        upload(session, title="셋째")

    assert refused.value.details == {"field": "file", "reason": "shelf_full"}
    assert count(session, ExtMaterial) == 2


def test_a_piece_the_guard_refuses_writes_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_: str, *, destination: str) -> None:
        raise PrivacyViolationError("refused", categories=["phone"])

    monkeypatch.setattr(materials, "assert_masked", refuse)
    with pytest.raises(PrivacyViolationError):
        upload(session)
    assert count(session, ExtMaterial) == 0 and count(session, ExtMaterialChunk) == 0


def test_deleting_an_upload_takes_its_text_at_once(session: Session) -> None:
    row = upload(session, body=BODY * 40)
    kept = upload(session, title="다른 자료")

    materials.delete_material(session, TEAM, row.id)

    assert session.get(ExtMaterial, row.id) is None
    assert materials.material_text(session, row.id) == []
    assert materials.material_text(session, kept.id) != []


def test_the_log_carries_ids_and_nothing_a_member_sent(session: Session) -> None:
    with capture_logs() as lines:
        row = upload(session, title="김민수 평가", name="김민수_평가.txt")
        with pytest.raises(materials.ConfidentialFileError):
            upload(session, title="박지민 문서", name="박지민.txt", body="Confidential\n" + BODY)
        materials.delete_material(session, TEAM, row.id)

    assert [line["event"] for line in lines] == [
        "extraction_material_uploaded",
        "extraction_material_upload_stopped",
        "extraction_material_deleted",
    ]
    said = repr(lines)
    for word in ("김민수", "박지민", "평가", PHONE, EMAIL, "3분기", "Confidential"):
        assert word not in said
    assert lines[1] == {
        "event": "extraction_material_upload_stopped",
        "log_level": "info",
        "team_id": TEAM,
        "marking": "english_marking",
    }


# --- alarms ----------------------------------------------------------------------


def test_alarms_are_read_per_team_oldest_first_and_acknowledged_by_deleting(
    session: Session,
) -> None:
    now = datetime.now(UTC)
    old = ExtMaterialAlarm(team_id=TEAM, marking="english_marking", created_at=now - timedelta(1))
    new = ExtMaterialAlarm(team_id=TEAM, marking="korean_marking", created_at=now)
    theirs = ExtMaterialAlarm(team_id="team_2", marking="korean_marking", created_at=now)
    session.add_all([new, old, theirs])
    session.flush()

    assert [a.id for a in materials.pending_alarms(session, TEAM)] == [old.id, new.id]

    with pytest.raises(AutuneError) as refused:
        materials.acknowledge_alarm(session, TEAM, theirs.id)
    assert refused.value.status_code == 404

    materials.acknowledge_alarm(session, TEAM, old.id)
    assert [a.id for a in materials.pending_alarms(session, TEAM)] == [new.id]
    assert [a.id for a in materials.pending_alarms(session, "team_2")] == [theirs.id]


def test_an_alarm_nobody_acknowledged_goes_after_thirty_days(session: Session) -> None:
    now = datetime(2026, 10, 10, 12, tzinfo=UTC)
    session.add_all(
        [
            ExtMaterialAlarm(id="mal_old", team_id=TEAM, marking="korean_marking",
                             created_at=now - timedelta(days=30, seconds=1)),
            ExtMaterialAlarm(id="mal_new", team_id=TEAM, marking="korean_marking",
                             created_at=now - timedelta(days=29)),
        ]
    )  # fmt: skip
    session.flush()

    assert materials.forget_old_alarms(session, now=now) == 1
    assert [a.id for a in materials.pending_alarms(session, TEAM)] == ["mal_new"]


# --- the form, in memory ------------------------------------------------------------


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


def _form(title: bytes = "자료".encode(), name: bytes = b"a.txt", data: bytes = b"text") -> bytes:
    return (
        b"--B\r\n"
        b'Content-Disposition: form-data; name="title"\r\n\r\n' + title + b"\r\n"
        b"--B\r\n"
        b'Content-Disposition: form-data; name="file"; filename="' + name + b'"\r\n'
        b"Content-Type: text/plain\r\n\r\n" + data + b"\r\n"
        b"--B--\r\n"
    )


def read(body: bytes, *, declared: str | None = None, pieces: int = 7) -> material_upload.Upload:
    step = max(1, len(body) // pieces)
    chunks = [body[i : i + step] for i in range(0, len(body), step)]
    return asyncio.run(
        material_upload.read_upload("multipart/form-data; boundary=B", declared, _chunks(*chunks))
    )


def test_the_form_is_read_in_pieces_into_memory() -> None:
    got = read(_form(data=BODY.encode()))

    assert (got.title, got.file_name, bytes(got.data)) == ("자료", "a.txt", BODY.encode())
    assert "자료" not in repr(got) and "a.txt" not in repr(got)
    got.wipe()
    assert got.data == bytearray()


def test_the_cap_holds_as_the_body_arrives_whatever_was_declared() -> None:
    big = _form(data=b"a" * (MAX_BYTES + 1))
    with pytest.raises(material_upload.UploadTooLargeError) as refused:
        read(big, declared="10", pieces=50)
    assert refused.value.status_code == 413

    with pytest.raises(material_upload.UploadTooLargeError):
        read(_form(), declared=str(material_upload.MAX_BODY_BYTES + 1))


def test_what_is_read_before_a_refusal_is_wiped(monkeypatch: pytest.MonkeyPatch) -> None:
    buffers: list[bytearray] = []
    wipe = material_upload._wipe

    def recording(buffer: bytearray) -> None:
        buffers.append(buffer)
        wipe(buffer)

    monkeypatch.setattr(material_upload, "_wipe", recording)
    with pytest.raises(material_upload.UploadTooLargeError):
        read(_form(data=b"a" * (MAX_BYTES + 1)), pieces=50)

    assert buffers and all(buffer == bytearray() for buffer in buffers)


@pytest.mark.parametrize(
    "body",
    [
        b"not a form",
        _form().replace(
            b"--B--", b'--B\r\nContent-Disposition: form-data; name="x"\r\n\r\nx\r\n--B--'
        ),
        b'--B\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n'
        b"\r\nx\r\n--B--\r\n",
        b'--B\r\nContent-Disposition: form-data; name="other"\r\n\r\nx\r\n--B--\r\n',
        _form().replace(b'name="title"', b'name="file"; filename="b.txt"'),
    ],
)
def test_anything_but_two_parts_title_and_file_is_refused(body: bytes) -> None:
    with pytest.raises(AutuneError) as refused:
        read(body)
    assert refused.value.status_code == 422


def test_a_form_of_another_type_is_refused() -> None:
    with pytest.raises(AutuneError):
        asyncio.run(material_upload.read_upload("application/json", None, _chunks(b"{}")))


def test_a_file_part_without_a_name_has_no_type() -> None:
    with pytest.raises(UnreadableFileError):
        read(_form(name=b""))


# --- the routes ---------------------------------------------------------------------


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "material_upload", True)


@pytest.fixture
def client(session: Session) -> TestClient:
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    sign_in(app, session, team_id=TEAM)
    return TestClient(app)


def post(
    client: TestClient, *, title: str = "3분기 계획", name: str = "plan.txt", body: str = BODY
):
    return client.post(
        UPLOAD, data={"title": title}, files={"file": (name, body.encode(), "text/plain")}
    )


def test_off_by_default_the_route_is_not_there(client: TestClient, session: Session) -> None:
    answer = post(client)

    assert answer.status_code == 404
    assert "error" not in answer.json()  # the framework's own, not "team not found"
    assert count(session, ExtMaterial) == 0
    rules = client.get(f"{PREFIX}/materials/upload-rules?team_id={TEAM}").json()
    assert rules["enabled"] is False


@pytest.mark.usefixtures("enabled")
def test_the_rules_are_the_servers_own_numbers(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/materials/upload-rules?team_id={TEAM}").json() == {
        "enabled": True,
        "max_bytes": MAX_BYTES,
        "suffixes": [".txt", ".md", ".csv"],
        "max_title_chars": materials.MAX_TITLE_CHARS,
        "max_materials": materials.MAX_MATERIALS,
        "search": True,
        "max_question_chars": 300,
    }
    assert client.get(f"{PREFIX}/materials/upload-rules?team_id=team_2").status_code == 404


@pytest.mark.usefixtures("enabled")
def test_a_member_uploads_lists_and_deletes(client: TestClient, session: Session) -> None:
    answer = post(client)

    assert answer.status_code == 201
    body = answer.json()
    assert body["id"].startswith("mat_")
    assert (body["source"], body["drive_file_id"], body["drive_kind"]) == ("upload", None, None)
    assert (body["title"], body["expires_at"], body["not_read"]) == ("3분기 계획", None, [])
    # The text never comes back with the row.
    assert PHONE not in answer.text and "예산" not in answer.text

    listed = client.get(f"{PREFIX}/materials?team_id={TEAM}").json()
    assert [(m["id"], m["source"]) for m in listed] == [(body["id"], "upload")]

    assert client.delete(f"{PREFIX}/materials/{body['id']}?team_id={TEAM}").status_code == 204
    assert count(session, ExtMaterial) == 0 and count(session, ExtMaterialChunk) == 0


@pytest.mark.usefixtures("enabled")
def test_a_marked_file_answers_422_and_its_alarm_row_is_kept(
    client: TestClient, session: Session
) -> None:
    answer = post(client, name="plan_대외비.txt")

    assert answer.status_code == 422
    assert answer.json()["error"]["code"] == "confidential_file"
    assert "대외비" not in answer.text and "plan" not in answer.text
    assert count(session, ExtMaterial) == 0
    assert [a.marking for a in session.scalars(select(ExtMaterialAlarm))] == ["korean_marking"]


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(
    ("name", "body", "reason"),
    [("plan.pdf", BODY, "unsupported_type"), ("plan.txt", "  ", "empty")],
)
def test_an_unreadable_file_answers_its_reason(
    client: TestClient, name: str, body: str, reason: str
) -> None:
    answer = post(client, name=name, body=body)

    assert answer.status_code == 422
    assert answer.json()["error"]["details"] == {"field": "file", "reason": reason}


@pytest.mark.usefixtures("enabled")
def test_a_declared_length_past_the_cap_is_refused_unread(client: TestClient) -> None:
    answer = client.post(
        UPLOAD,
        content=b"x",
        headers={
            "content-type": "multipart/form-data; boundary=B",
            "content-length": str(material_upload.MAX_BODY_BYTES + 1),
        },
    )
    assert answer.status_code == 413


@pytest.mark.usefixtures("enabled")
def test_a_form_that_is_not_two_parts_is_refused(client: TestClient, session: Session) -> None:
    assert client.post(UPLOAD, data={"title": "자료"}).status_code == 422
    assert client.post(UPLOAD, json={"title": "자료"}).status_code == 422
    assert count(session, ExtMaterial) == 0


@pytest.mark.usefixtures("enabled")
def test_nothing_of_the_body_is_read_before_the_caller_is_a_member(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def must_not_read(*_: object) -> material_upload.Upload:
        raise AssertionError("the body was read")

    monkeypatch.setattr(material_upload, "read_upload", must_not_read)
    answer = client.post(
        f"{PREFIX}/materials/upload?team_id=team_2",
        data={"title": "자료"},
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "not_found"


@pytest.mark.usefixtures("enabled")
def test_no_temporary_file_is_made_at_any_size(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_disk(*_: object, **__: object) -> None:
        raise AssertionError("a temporary file was made")

    for name in ("SpooledTemporaryFile", "NamedTemporaryFile", "TemporaryFile", "mkstemp"):
        monkeypatch.setattr(tempfile, name, no_disk)
    # Starlette's form reader -- what ``UploadFile`` is built on -- holds its own
    # reference, taken at import.
    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", no_disk)

    # Past the megabyte at which a spooled form moves to disk, and under the
    # text cap: four bytes a character.
    answer = post(client, body="\U0001f4c4" * 280_000)
    assert answer.status_code == 201, answer.text


@pytest.mark.usefixtures("enabled")
@pytest.mark.parametrize(("body", "status"), [(BODY, 201), ("대외비\n" + BODY, 422), ("\x00", 422)])
def test_the_buffers_are_wiped_on_every_path_out(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, body: str, status: int
) -> None:
    wiped: list[int] = []
    wipe = material_upload.Upload.wipe

    def recording(self: material_upload.Upload) -> None:
        wipe(self)
        wiped.append(len(self.data))

    monkeypatch.setattr(material_upload.Upload, "wipe", recording)
    assert post(client, body=body).status_code == status
    assert wiped == [0]


@pytest.mark.usefixtures("enabled")
def test_a_failure_after_the_read_still_wipes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiped: list[int] = []
    wipe = material_upload.Upload.wipe

    def recording(self: material_upload.Upload) -> None:
        wipe(self)
        wiped.append(len(self.data))

    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(material_upload.Upload, "wipe", recording)
    monkeypatch.setattr(materials, "store_upload", broken)
    with pytest.raises(RuntimeError):
        post(client)
    assert wiped == [0]


@pytest.mark.usefixtures("enabled")
def test_no_log_line_of_an_upload_holds_what_a_member_sent(client: TestClient) -> None:
    with capture_logs() as lines:
        assert post(client, title="김민수 평가", name="김민수.txt").status_code == 201
        assert post(client, title="박지민", name="박지민_기밀.txt").status_code == 422

    said = repr(lines)
    for word in ("김민수", "박지민", "평가", PHONE, EMAIL, "3분기", "기밀"):
        assert word not in said
