"""Module C's HTTP surface: ``/reports``, ``/topics`` and ``/templates``.

SQLite in memory and the router on a bare app, the way apps/api mounts it —
the same harness module B's ``test_read_endpoints`` uses, and for the same
reason. What is under test is which rows a read keeps, the order they come
back in, and what a response is allowed to carry; none of that is visible in a
pure function, and none of it needs PostgreSQL.

This is not the integration suite: no Postgres, no migrations.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autune_core import (
    AutuneError,
    Base,
    Meeting,
    Participant,
    Team,
    TeamMember,
    User,
    Utterance,
    get_session,
    issue_token,
)
from autune_gap import service
from autune_gap.models import (
    GapGap,
    GapMeetingTemplate,
    GapParticipation,
    GapRelatedTopic,
    GapTopic,
    GapTopicEdge,
    GapTopicUtterance,
)
from autune_gap.router import router

MEETING = "mtg_1"
OTHER_MEETING = "mtg_2"
PREFIX = "/api/gap"

TEAM = "team_1"
OTHER_TEAM = "team_2"
MEMBER = "usr_member"
OUTSIDER = "usr_outsider"
"""A user of another team. Not "a user with no team": the interesting caller is
one who holds a perfectly good token, because that is who a 403 would tell
which meeting ids are real."""

FOREIGN_MEETING = "mtg_elsewhere"
"""A real meeting of a team the caller does not belong to. Its answer has to be
the same as an id nobody ever issued."""

TABLES = [
    User.__table__,
    Team.__table__,
    TeamMember.__table__,
    Meeting.__table__,
    Participant.__table__,
    Utterance.__table__,
    GapTopic.__table__,
    GapTopicUtterance.__table__,
    GapTopicEdge.__table__,
    GapParticipation.__table__,
    GapGap.__table__,
    GapRelatedTopic.__table__,
    GapMeetingTemplate.__table__,
]


@pytest.fixture
def session() -> Iterator[Session]:
    # One connection shared with the TestClient's thread: an in-memory database
    # exists per connection, and SQLite refuses cross-thread use by default.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=TABLES)
    with Session(engine) as session:
        for user_id in (MEMBER, OUTSIDER):
            session.add(User(id=user_id, email=f"{user_id}@example.com", display_name=user_id))
        session.add(Team(id=TEAM, name="autune"))
        session.add(Team(id=OTHER_TEAM, name="somebody else"))
        session.add(TeamMember(team_id=TEAM, user_id=MEMBER))
        session.add(TeamMember(team_id=OTHER_TEAM, user_id=OUTSIDER))
        for meeting_id in (MEETING, OTHER_MEETING):
            session.add(Meeting(id=meeting_id, team_id=TEAM, title="주간 회의"))
        session.add(Meeting(id=FOREIGN_MEETING, team_id=OTHER_TEAM, title="남의 회의"))
        session.flush()
        yield session


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    """Module C's router on a bare app, with the error mapping apps/api installs."""
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app, headers=_bearer(MEMBER))


@pytest.fixture
def anonymous(session: Session) -> Iterator[TestClient]:
    """The same app with no Authorization header. What the world had until
    #276."""
    app = FastAPI()

    @app.exception_handler(AutuneError)
    async def _render(_: Request, exc: AutuneError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    app.include_router(router, prefix=PREFIX)
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)


def _bearer(user_id: str) -> dict[str, str]:
    """A real token, signed the way `current_user` verifies it. Overriding the
    dependency instead would test the routes and not the wiring that makes them
    require a caller at all."""
    return {"Authorization": f"Bearer {issue_token(user_id)}"}


def participant(
    session: Session,
    participant_id: str,
    *,
    meeting_id: str = MEETING,
    user_id: str | None = None,
    consented: bool = True,
) -> str:
    session.add(
        Participant(
            id=participant_id,
            meeting_id=meeting_id,
            user_id=user_id,
            speaker_label=f"SPEAKER_{participant_id[-2:]}",
            consented=consented,
        )
    )
    session.flush()
    return participant_id


def utterance(session: Session, utterance_id: str, *, meeting_id: str = MEETING) -> str:
    session.add(
        Utterance(
            id=utterance_id,
            meeting_id=meeting_id,
            speaker_label="SPEAKER_00",
            start_sec=0.0,
            end_sec=2.0,
            text="검색 랭킹 이야기",
        )
    )
    session.flush()
    return utterance_id


def topic(
    session: Session,
    topic_id: str,
    *,
    meeting_id: str = MEETING,
    label: str = "검색 랭킹",
    centrality: float = 1.0,
    betweenness: float = 0.0,
    said_at: tuple[tuple[str, int], ...] = (),
) -> str:
    session.add(
        GapTopic(
            id=topic_id,
            meeting_id=meeting_id,
            label=label,
            extractor_version="fake-1",
            centrality=centrality,
            betweenness=betweenness,
        )
    )
    session.flush()
    for utterance_id, position in said_at:
        session.add(
            GapTopicUtterance(
                topic_id=topic_id,
                utterance_id=utterance(session, utterance_id, meeting_id=meeting_id),
                position=position,
            )
        )
    session.flush()
    return topic_id


def edge(
    session: Session,
    source: str,
    target: str,
    *,
    meeting_id: str = MEETING,
    weight: float = 1.0,
    relation: str = "co_occurs",
) -> None:
    session.add(
        GapTopicEdge(
            meeting_id=meeting_id,
            source_topic_id=source,
            target_topic_id=target,
            relation=relation,
            weight=weight,
        )
    )
    session.flush()


def gap(
    session: Session,
    gap_id: str,
    *,
    meeting_id: str = MEETING,
    risk_score: float = 0.9,
    dismissed: bool = False,
    related: tuple[str, ...] = (),
) -> str:
    session.add(
        GapGap(
            id=gap_id,
            meeting_id=meeting_id,
            category="technical_spec",
            title="성능 요구사항이 정해지지 않았습니다",
            severity="high",
            risk_score=risk_score,
            dismissed_at=datetime.now(tz=UTC) if dismissed else None,
        )
    )
    session.flush()
    for topic_id in related:
        session.add(GapRelatedTopic(gap_id=gap_id, topic_id=topic_id))
    session.flush()
    return gap_id


def spoke_on(session: Session, topic_id: str, participant_id: str, *, spoke: bool) -> None:
    session.add(GapParticipation(topic_id=topic_id, participant_id=participant_id, spoke=spoke))
    session.flush()


# --- what is found, and what is merely empty --------------------------------


@pytest.mark.parametrize("path", ["reports", "topics"])
def test_an_unknown_meeting_is_not_found(client: TestClient, path: str) -> None:
    response = client.get(f"{PREFIX}/{path}/mtg_nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("path", ["reports", "topics"])
def test_a_meeting_no_one_has_analysed_is_empty_rather_than_missing(
    client: TestClient, path: str
) -> None:
    """A screen polling while the pipeline runs has to tell the two apart."""
    body = client.get(f"{PREFIX}/{path}/{MEETING}").json()

    assert body["meeting_id"] == MEETING
    assert all(value == [] for value in body.values() if isinstance(value, list))


def test_an_error_body_names_no_meeting_content(client: TestClient) -> None:
    """Error strings reach external tracking — they carry the id and nothing else."""
    body = client.get(f"{PREFIX}/reports/mtg_nope").json()

    assert body["error"]["details"] == {"resource": "meeting"}


# --- GET /reports/{meeting_id} ----------------------------------------------


def test_topics_come_most_central_first_and_ties_in_the_order_they_were_reached(
    client: TestClient, session: Session
) -> None:
    """Topic ids are random, so a tie with no rule is a report that reshuffles."""
    topic(session, "topic_b", label="B", centrality=0.5, said_at=(("utt_b", 3),))
    topic(session, "topic_a", label="A", centrality=1.0, said_at=(("utt_a", 0),))
    topic(session, "topic_c", label="C", centrality=0.5, said_at=(("utt_c", 1),))

    body = client.get(f"{PREFIX}/reports/{MEETING}").json()

    assert [entry["id"] for entry in body["topics"]] == ["topic_a", "topic_c", "topic_b"]


def test_a_topics_evidence_comes_back_in_meeting_order(
    client: TestClient, session: Session
) -> None:
    topic(session, "topic_a", said_at=(("utt_late", 9), ("utt_early", 2)))

    body = client.get(f"{PREFIX}/reports/{MEETING}").json()

    assert body["topics"][0]["utterance_ids"] == ["utt_early", "utt_late"]


def test_participation_is_two_lists_of_ids_and_no_numbers(
    client: TestClient, session: Session
) -> None:
    """The column is a boolean and the payload stays one too.

    A duration, a count or a share here would be the speaking ratio that
    privacy.md section 3 keeps private to its subject, arriving inside a report
    the whole team reads — and arriving without anybody deciding to build one.
    """
    topic(session, "topic_a")
    spoke_on(session, "topic_a", participant(session, "prt_01"), spoke=True)
    spoke_on(session, "topic_a", participant(session, "prt_02"), spoke=False)

    entry = client.get(f"{PREFIX}/reports/{MEETING}").json()["participation"][0]

    assert set(entry) == {"topic_id", "spoke", "silent"}
    assert entry["spoke"] == ["prt_01"]
    assert entry["silent"] == ["prt_02"]


def test_a_participant_who_did_not_consent_is_in_neither_list(
    client: TestClient, session: Session
) -> None:
    """A stored row outlives a withdrawal until the next run; the read closes it."""
    topic(session, "topic_a")
    spoke_on(session, "topic_a", participant(session, "prt_01"), spoke=True)
    spoke_on(session, "topic_a", participant(session, "prt_02", consented=False), spoke=False)

    entry = client.get(f"{PREFIX}/reports/{MEETING}").json()["participation"][0]

    assert entry["spoke"] == ["prt_01"]
    assert entry["silent"] == []


def test_a_dismissed_gap_is_not_reported(client: TestClient, session: Session) -> None:
    """The row stays for threshold tuning; the team has said it is wrong."""
    topic(session, "topic_a")
    gap(session, "gap_kept", risk_score=0.8, related=("topic_a",))
    gap(session, "gap_dismissed", risk_score=0.9, dismissed=True)

    body = client.get(f"{PREFIX}/reports/{MEETING}").json()

    assert [entry["id"] for entry in body["gaps"]] == ["gap_kept"]
    assert body["gaps"][0]["related_topic_ids"] == ["topic_a"]


def test_the_report_holds_only_this_meeting(client: TestClient, session: Session) -> None:
    topic(session, "topic_here")
    topic(session, "topic_elsewhere", meeting_id=OTHER_MEETING)
    gap(session, "gap_elsewhere", meeting_id=OTHER_MEETING)

    body = client.get(f"{PREFIX}/reports/{MEETING}").json()

    assert [entry["id"] for entry in body["topics"]] == ["topic_here"]
    assert body["gaps"] == []


# --- GET /topics/{meeting_id} -----------------------------------------------


def test_the_graph_carries_betweenness_the_contract_does_not(
    client: TestClient, session: Session
) -> None:
    topic(session, "topic_a", centrality=1.0, betweenness=0.25)

    node = client.get(f"{PREFIX}/topics/{MEETING}").json()["nodes"][0]

    assert node == {"id": "topic_a", "label": "검색 랭킹", "centrality": 1.0, "betweenness": 0.25}


def test_the_graph_draws_its_nodes_in_the_reports_order(
    client: TestClient, session: Session
) -> None:
    """One screen shows both. Two orderings would be a reconciliation nobody asked for."""
    topic(session, "topic_b", label="B", centrality=0.5, said_at=(("utt_b", 3),))
    topic(session, "topic_a", label="A", centrality=1.0, said_at=(("utt_a", 0),))
    topic(session, "topic_c", label="C", centrality=0.5, said_at=(("utt_c", 1),))

    graph = client.get(f"{PREFIX}/topics/{MEETING}").json()
    report = client.get(f"{PREFIX}/reports/{MEETING}").json()

    assert [node["id"] for node in graph["nodes"]] == [entry["id"] for entry in report["topics"]]


def test_both_directions_of_a_symmetric_edge_come_back_strongest_first(
    client: TestClient, session: Session
) -> None:
    """``co_occurs`` is stored as two rows and is not collapsed on the way out.

    Relation extraction (#32) replaces it with directed triples, where
    collapsing would lose which topic acted on which. Order is by weight and
    then by the node order, never by ``id``: that is an autoincrement a re-run
    reassigns, and the same graph would redraw in a different order.
    """
    topic(session, "topic_a", label="A", centrality=1.0)
    topic(session, "topic_b", label="B", centrality=0.6)
    topic(session, "topic_c", label="C", centrality=0.3)
    edge(session, "topic_b", "topic_c", weight=0.4)
    edge(session, "topic_c", "topic_b", weight=0.4)
    edge(session, "topic_a", "topic_b", weight=1.0)
    edge(session, "topic_b", "topic_a", weight=1.0)

    edges = client.get(f"{PREFIX}/topics/{MEETING}").json()["edges"]

    assert [(entry["source_topic_id"], entry["target_topic_id"]) for entry in edges] == [
        ("topic_a", "topic_b"),
        ("topic_b", "topic_a"),
        ("topic_b", "topic_c"),
        ("topic_c", "topic_b"),
    ]
    assert {entry["relation"] for entry in edges} == {"co_occurs"}


def test_two_relations_on_one_pair_come_back_in_a_fixed_order(
    client: TestClient, session: Session
) -> None:
    """A pair may carry more than one relation, and equal weights must not fall
    through to the scan order.

    ``uq_gap_topic_edges`` is on (source, target, relation), so #32's triples
    can sit beside the ``co_occurs`` edge of the same pair. Without the
    relation in the sort key the two swap places between reads of one stored
    graph. Raised in review of #220.
    """
    topic(session, "topic_a", label="A", centrality=1.0)
    topic(session, "topic_b", label="B", centrality=0.6)
    edge(session, "topic_a", "topic_b", weight=0.5, relation="depends_on")
    edge(session, "topic_a", "topic_b", weight=0.5, relation="co_occurs")

    edges = client.get(f"{PREFIX}/topics/{MEETING}").json()["edges"]

    assert [entry["relation"] for entry in edges] == ["co_occurs", "depends_on"]


def test_the_graph_holds_only_this_meeting(client: TestClient, session: Session) -> None:
    topic(session, "topic_here")
    topic(session, "topic_elsewhere_1", meeting_id=OTHER_MEETING)
    topic(session, "topic_elsewhere_2", meeting_id=OTHER_MEETING)
    edge(session, "topic_elsewhere_1", "topic_elsewhere_2", meeting_id=OTHER_MEETING)

    body = client.get(f"{PREFIX}/topics/{MEETING}").json()

    assert [node["id"] for node in body["nodes"]] == ["topic_here"]
    assert body["edges"] == []


def test_an_edge_whose_endpoints_this_read_does_not_have_is_left_out(
    client: TestClient, session: Session
) -> None:
    """Not a 500, and not a half-drawn graph.

    The topics and the edges are two statements, and on PostgreSQL a re-run of
    ``build_topic_graph`` committing between them is visible: the nodes are the
    deleted set and the edges reference the new one. The endpoint's contract is
    that an empty graph is a state rather than an error, so an edge this read
    cannot place is dropped instead of raising. Raised in review of #220.
    """
    topic(session, "topic_here")
    topic(session, "topic_rebuilt_1", meeting_id=OTHER_MEETING)
    topic(session, "topic_rebuilt_2", meeting_id=OTHER_MEETING)
    # An edge of *this* meeting pointing at topics this read does not hold —
    # the shape a mid-read rebuild leaves behind.
    edge(session, "topic_rebuilt_1", "topic_rebuilt_2")

    response = client.get(f"{PREFIX}/topics/{MEETING}")

    assert response.status_code == 200
    assert [node["id"] for node in response.json()["nodes"]] == ["topic_here"]
    assert response.json()["edges"] == []


def test_a_topic_with_no_evidence_sorts_after_one_that_has_some(
    client: TestClient, session: Session
) -> None:
    """``NULLS LAST``, spelled out because SQLite and PostgreSQL disagree.

    Ascending order puts NULL first on SQLite and last on PostgreSQL, so the
    implicit version was a rule these tests could not have caught — they run on
    SQLite and production runs on PostgreSQL. A topic nobody can be quoted on
    is the one a reader can check least. Raised in review of #220.
    """
    topic(session, "topic_unquoted", label="A", centrality=0.5)
    topic(session, "topic_quoted", label="Z", centrality=0.5, said_at=(("utt_1", 7),))

    body = client.get(f"{PREFIX}/reports/{MEETING}").json()

    assert [entry["id"] for entry in body["topics"]] == ["topic_quoted", "topic_unquoted"]


def test_the_graph_carries_no_participation(client: TestClient, session: Session) -> None:
    """The one surface where a per-person number could arrive attached to a picture."""
    topic(session, "topic_a")
    spoke_on(session, "topic_a", participant(session, "prt_01"), spoke=True)

    body = client.get(f"{PREFIX}/topics/{MEETING}").json()

    assert set(body) == {"meeting_id", "nodes", "edges"}
    assert set(body["nodes"][0]) == {"id", "label", "centrality", "betweenness"}


# --- which template a meeting is compared against ---------------------------


@pytest.fixture
def detection(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records the meetings ``PUT /templates`` re-compared, without running it.

    ``detect_gaps`` opens its own ``session_scope`` against the real database;
    this harness is SQLite in memory. What the route owes its caller is that it
    asks for the re-comparison, and that is what is asserted.
    """
    called: list[str] = []
    monkeypatch.setattr(service, "detect_gaps", lambda meeting_id: called.append(meeting_id))
    return called


def test_the_templates_a_meeting_can_be_held_to_are_listed(client: TestClient) -> None:
    body = client.get(f"{PREFIX}/templates").json()

    assert {entry["key"] for entry in body} == {"general", "feature_planning"}
    assert all(entry["items"] > 0 for entry in body)


def test_the_listing_carries_no_items(client: TestClient) -> None:
    """Choosing a template is choosing a name. Ten checklists in a payload that
    shows one of them is every template shipped to a screen nobody opens."""
    body = client.get(f"{PREFIX}/templates").json()

    assert set(body[0]) == {"key", "name", "version", "items"}


def test_a_meeting_nobody_chose_for_answers_with_the_default(client: TestClient) -> None:
    """Not an empty body: there is always a template in force, and a rail
    showing nothing selected would misreport that."""
    body = client.get(f"{PREFIX}/templates/{MEETING}").json()

    assert body == {"template_key": "general"}


def test_an_unknown_meeting_is_a_404(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/templates/mtg_missing").status_code == 404


def test_choosing_a_template_stores_it_and_re_compares(
    client: TestClient, session: Session, detection: list[str]
) -> None:
    response = client.put(
        f"{PREFIX}/templates/{MEETING}", json={"template_key": "feature_planning"}
    )

    assert response.json() == {"template_key": "feature_planning"}
    assert session.get(GapMeetingTemplate, MEETING).template_key == "feature_planning"
    assert detection == [MEETING]


def test_choosing_again_replaces_the_choice(
    client: TestClient, session: Session, detection: list[str]
) -> None:
    """One row per meeting — the table stores the exception, not a history."""
    client.put(f"{PREFIX}/templates/{MEETING}", json={"template_key": "feature_planning"})
    client.put(f"{PREFIX}/templates/{MEETING}", json={"template_key": "general"})

    assert session.get(GapMeetingTemplate, MEETING).template_key == "general"


def test_a_template_no_file_defines_is_a_422(
    client: TestClient, session: Session, detection: list[str]
) -> None:
    """What is wrong is the value, not the address. Nothing is stored and
    nothing is re-compared."""
    response = client.put(f"{PREFIX}/templates/{MEETING}", json={"template_key": "retrospective"})

    assert response.status_code == 422
    assert session.get(GapMeetingTemplate, MEETING) is None
    assert detection == []


# --- who may read a meeting at all (#276) -----------------------------------


ROUTES = [
    f"{PREFIX}/reports/{{meeting_id}}",
    f"{PREFIX}/topics/{{meeting_id}}",
    f"{PREFIX}/templates/{{meeting_id}}",
]
"""Every GET that names a meeting. Parameterised rather than written out three
times, so a route added later without the check fails here — the list is the
thing a reviewer compares against the router."""


@pytest.mark.parametrize("route", ROUTES)
def test_a_member_of_the_team_may_read(client: TestClient, route: str) -> None:
    assert client.get(route.format(meeting_id=MEETING)).status_code == 200


@pytest.mark.parametrize("route", ROUTES)
def test_no_token_is_refused(anonymous: TestClient, route: str) -> None:
    """What the world had until #276: anyone who knew a meeting id read that
    meeting's gap report, participation matrix included."""
    assert anonymous.get(route.format(meeting_id=MEETING)).status_code == 403


@pytest.mark.parametrize("route", ROUTES)
def test_another_teams_meeting_is_indistinguishable_from_one_that_does_not_exist(
    client: TestClient, route: str
) -> None:
    """The point of the rule, and the reason it is not a 403.

    A 403 confirms the id exists, and the ids are the only thing a caller needs
    to walk the table. Status *and* body have to match — an error message that
    named the team would give the same thing away.
    """
    foreign = client.get(route.format(meeting_id=FOREIGN_MEETING))
    unknown = client.get(route.format(meeting_id="mtg_no_such_thing"))

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert "team" not in foreign.text


@pytest.mark.parametrize("route", ROUTES)
def test_an_analysed_meeting_and_an_unanalysed_one_are_both_readable(
    client: TestClient, route: str
) -> None:
    """Nothing analysed yet is a state, not a missing resource. A screen polling
    while the pipeline runs needs that difference, and the new check must not
    have collapsed it into the 404 above."""
    assert client.get(route.format(meeting_id=OTHER_MEETING)).status_code == 200


def test_the_one_write_is_refused_to_an_outsider(
    client: TestClient, session: Session, detection: list[str]
) -> None:
    """``PUT /templates`` changes what a team sees rather than just reading it,
    so an unauthenticated caller could have rewritten another team's report."""
    outsider = TestClient(client.app, headers=_bearer(OUTSIDER))

    response = outsider.put(f"{PREFIX}/templates/{MEETING}", json={"template_key": "general"})

    assert response.status_code == 404
    assert session.get(GapMeetingTemplate, MEETING) is None
    assert detection == []


def test_the_write_is_refused_with_no_token(
    anonymous: TestClient, session: Session, detection: list[str]
) -> None:
    response = anonymous.put(f"{PREFIX}/templates/{MEETING}", json={"template_key": "general"})

    assert response.status_code == 403
    assert session.get(GapMeetingTemplate, MEETING) is None
    assert detection == []


def test_a_token_for_a_user_who_no_longer_exists_is_refused(
    client: TestClient, session: Session
) -> None:
    """A signed token outlives the row it names. `current_user` answers 404 for
    the user; what matters here is that it does not fall through to the report.
    """
    gone = TestClient(client.app, headers=_bearer("usr_deleted"))

    assert gone.get(f"{PREFIX}/reports/{MEETING}").status_code == 404


def test_the_template_listing_needs_a_caller_and_the_health_check_does_not(
    client: TestClient, anonymous: TestClient
) -> None:
    """``/health`` is what apps/api calls and carries nothing. The template
    listing carries no meeting content either, but an unauthenticated route in
    this router is the one somebody copies for the next endpoint.
    """
    assert anonymous.get(f"{PREFIX}/health").status_code == 200
    assert client.get(f"{PREFIX}/health").status_code == 200

    assert anonymous.get(f"{PREFIX}/templates").status_code == 403
    assert client.get(f"{PREFIX}/templates").status_code == 200
