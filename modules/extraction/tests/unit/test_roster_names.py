"""Roster names never reach the LLM provider (#411).

Module A masks numbers, not names, so a name said aloud is in the text. Before
``LlmClassifier`` sends a window it replaces the meeting team's names with
``[사람N]``; nothing else changes. No network: the fake provider records bodies.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from autune_core import Base, Meeting, Participant, TeamMember, User
from autune_extraction import service
from autune_extraction.pipeline import llm as llm_module
from autune_extraction.pipeline.base import Prediction, give_roster
from autune_extraction.pipeline.checked import CheckedClassifier
from autune_extraction.pipeline.llm import LlmClassifier, substitute_names

from .test_llm_classifier import Provider, classifier

ROSTER = ["김민경", "박재경", "이승환"]


def test_the_full_name_and_the_given_name_become_one_person() -> None:
    assert substitute_names(["김민경 님이 하시고", "민경 님 확인 부탁해요"], ROSTER) == [
        "[사람1] 님이 하시고",
        "[사람1] 님 확인 부탁해요",
    ]


def test_particles_and_honorifics_stay() -> None:
    assert substitute_names(["재경이가 맡고 재경씨는 검토", "재경님, 재경 님"], ROSTER) == [
        "[사람1]이가 맡고 [사람1]씨는 검토",
        "[사람1]님, [사람1] 님",
    ]


def test_the_longest_form_goes_first() -> None:
    """Otherwise "김민경" would leave "김[사람1]"."""
    assert substitute_names(["김민경"], ["김민경"]) == ["[사람1]"]


def test_two_people_in_one_window_are_numbered_by_first_appearance() -> None:
    """Numbers follow the text, not the roster, so they say nothing of its order."""
    assert substitute_names(["승환 님이 재경 님께 넘기고 승환 님은 빠져요"], ROSTER) == [
        "[사람1] 님이 [사람2] 님께 넘기고 [사람1] 님은 빠져요"
    ]


def test_a_given_name_two_members_share_is_its_own_person() -> None:
    texts = ["김민경, 이민경, 그리고 민경 님"]

    (out,) = substitute_names(texts, ["김민경", "이민경"])

    assert "민경" not in out
    assert out == "[사람1], [사람2], 그리고 [사람3] 님"


def test_a_display_name_with_a_space_is_replaced_as_speech_to_text_writes_it() -> None:
    """The account's `name` claim is often "박 재경"; the transcript says "박재경"
    and "재경님" (PARK's review of #500)."""
    texts = [
        "재경님이 정리해 주세요",
        "박재경 님 의견은요?",
        "박 재경 님 확인해 주세요",
        "재경씨가 할게요",
    ]

    assert substitute_names(texts, ["박 재경"]) == [
        "[사람1]님이 정리해 주세요",
        "[사람1] 님 의견은요?",
        "[사람1] 님 확인해 주세요",
        "[사람1]씨가 할게요",
    ]


def test_a_display_name_written_given_name_first_is_replaced_too() -> None:
    texts = ["민구씨가 할게요", "강민구 님도요", "민구강 님", "재경님이 정리해 주세요"]

    assert substitute_names(texts, ["재경 박", "민구 강"]) == [
        "[사람1]씨가 할게요",
        "[사람1] 님도요",
        "[사람1] 님",
        "[사람2]님이 정리해 주세요",
    ]


def test_extra_whitespace_in_a_roster_name_does_not_matter() -> None:
    assert substitute_names(["재경님"], ["  박   재경 "]) == ["[사람1]님"]
    assert substitute_names(["박재경 님"], ["박 재경", "박재경"]) == ["[사람1] 님"]


def test_a_spaced_given_name_two_members_share_is_its_own_person() -> None:
    (out,) = substitute_names(["박 재경, 김재경, 재경님"], ["박 재경", "김재경"])

    assert "재경" not in out
    assert out == "[사람1], [사람2], [사람3]님"


def test_syllables_written_apart_are_joined() -> None:
    assert substitute_names(["민경 님, 김민경"], ["김 민 경"]) == ["[사람1] 님, [사람1]"]


def test_a_two_word_name_with_no_single_syllable_word_has_no_given_name() -> None:
    """ "선우 재경": either word could be the surname, so only the joined forms."""
    assert substitute_names(["선우재경 님과 재경 님"], ["선우 재경"]) == ["[사람1] 님과 재경 님"]


def test_an_english_two_word_name_is_replaced_whole_only() -> None:
    assert substitute_names(["Alex Kim will send it", "Alex sent"], ["Alex Kim"]) == [
        "[사람1] will send it",
        "Alex sent",
    ]


def test_no_roster_changes_nothing() -> None:
    texts = ["김민경 님이 하시고"]
    assert substitute_names(texts, []) == texts
    assert substitute_names(texts, ["", "  "]) == texts


def test_a_one_letter_name_is_not_replaced() -> None:
    assert substitute_names(["A안과 B안"], ["A"]) == ["A안과 B안"]


def test_an_english_name_is_replaced_whole() -> None:
    assert substitute_names(["Alex will send it"], ["Alex"]) == ["[사람1] will send it"]


# --- what leaves ------------------------------------------------------------------


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []
    monkeypatch.setattr(llm_module.time, "sleep", waits.append)
    return waits


def test_no_roster_name_appears_in_any_request(slept) -> None:
    """Across every window of a long meeting, not only the first."""
    provider = Provider()
    llm = classifier(provider)
    llm.use_roster(ROSTER)
    texts = [f"{i}번째로 김민경 님과 재경 님이 이승환 님 대신 정리할게요" for i in range(200)]

    predictions = llm.classify(texts)

    assert len(provider.bodies) > 1, "the meeting went out in several requests"
    sent = json.dumps(provider.bodies, ensure_ascii=False)
    for name in ["김민경", "민경", "박재경", "재경", "이승환", "승환"]:
        assert name not in sent
    assert "[사람1]" in sent
    assert len(predictions) == len(texts)


def test_the_prompt_says_what_a_placeholder_is() -> None:
    assert "[사람1]" in llm_module.INSTRUCTIONS
    # By marks that can be on a line, never by the general form: a model wrote
    # "[사람N]" back as the one who spoke, a person it is never told.
    assert "사람N" not in llm_module.INSTRUCTIONS
    assert "새로 만들지 마세요" in llm_module.INSTRUCTIONS


def test_the_labels_still_map_back_to_the_original_utterances(slept) -> None:
    provider = Provider()
    llm = classifier(provider)
    llm.use_roster(ROSTER)

    predictions = llm.classify(["김민경 님 이건 제가 할게요", "재경 님 생각은요?"])

    assert predictions[0].kind is not None
    assert predictions[1].kind is None


# --- the handoff --------------------------------------------------------------------


class _Local:
    model_version = "local"

    def classify(self, texts: list[str]) -> list[Prediction]:
        self.seen = texts
        return [llm_module._prediction(None) for _ in texts]


def test_a_classifier_that_runs_here_is_left_alone() -> None:
    local = _Local()
    give_roster(local, ROSTER)  # no use_roster: nothing to do, nothing raised
    assert not hasattr(local, "_roster")


def test_the_checked_classifier_hands_the_roster_to_its_proposer(slept) -> None:
    provider = Provider()
    checker = _Local()
    checked = CheckedClassifier(proposer=classifier(provider), checker=checker)

    give_roster(checked, ROSTER)
    checked.classify(["김민경 님이 할게요"])

    assert "김민경" not in json.dumps(provider.bodies, ensure_ascii=False)
    assert checker.seen == ["김민경 님이 할게요"], "the local checker reads the text as it is"


# --- the roster -------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[Meeting.__table__, User.__table__, TeamMember.__table__, Participant.__table__],
    )
    with Session(engine) as s:
        s.add(Meeting(id="mtg_1", team_id="team_1", title="주간 회의"))
        for uid, name, team in (
            ("user_a", "김민경", "team_1"),
            ("user_b", "박재경", "team_1"),
            ("user_x", "다른팀", "team_2"),
        ):
            s.add(User(id=uid, email=f"{uid}@example.com", display_name=name))
            s.add(TeamMember(team_id=team, user_id=uid))
        s.flush()
        yield s


def test_the_roster_is_the_meeting_teams_members(session: Session) -> None:
    assert service.team_roster(session, "mtg_1") == ["김민경", "박재경"]
    assert service.team_roster(session, "mtg_missing") == []


def test_somebody_who_spoke_in_the_meeting_and_left_the_team_is_still_on_its_roster(
    session: Session,
) -> None:
    """Their participant row still names them; their membership is gone."""
    session.add(User(id="user_gone", email="gone@example.com", display_name="한서윤"))
    session.add(
        Participant(id="par_gone", meeting_id="mtg_1", speaker_label="C", user_id="user_gone")
    )
    session.flush()

    assert service.team_roster(session, "mtg_1") == ["김민경", "박재경", "한서윤"]


def test_a_member_who_also_spoke_is_listed_once(session: Session) -> None:
    session.add(Participant(id="par_a", meeting_id="mtg_1", speaker_label="A", user_id="user_a"))
    session.add(Participant(id="par_a2", meeting_id="mtg_1", speaker_label="B", user_id="user_a"))
    session.flush()

    assert service.team_roster(session, "mtg_1") == ["김민경", "박재경"]


def test_a_speaker_nobody_identified_and_another_meetings_speaker_add_nothing(
    session: Session,
) -> None:
    session.add(Meeting(id="mtg_2", team_id="team_1", title="다른 회의"))
    session.add(User(id="user_gone", email="gone@example.com", display_name="한서윤"))
    session.add(
        Participant(id="par_gone", meeting_id="mtg_2", speaker_label="C", user_id="user_gone")
    )
    session.add(Participant(id="par_unknown", meeting_id="mtg_1", speaker_label="D", user_id=None))
    session.flush()

    assert service.team_roster(session, "mtg_1") == ["김민경", "박재경"]
    assert service.team_roster(session, "mtg_2") == ["김민경", "박재경", "한서윤"]


def test_llm_classifier_starts_with_no_roster() -> None:
    llm = LlmClassifier(api_key="k", model="m", base_url="http://llm.invalid")
    assert llm._roster == ()
