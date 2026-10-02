"""The speech-deletion hook registry (#587): who is told, with what, and when it stops."""

from __future__ import annotations

import pytest

from autune_core import deletion


@pytest.fixture(autouse=True)
def empty_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deletion, "_speech_hooks", {})


def test_a_registered_module_gets_the_person_and_their_utterance_ids() -> None:
    seen: list[tuple[str, list[str]]] = []

    @deletion.on_speech_deleted("extraction")
    def hook(user_id: str, utterance_ids: list[str]) -> None:
        seen.append((user_id, utterance_ids))

    deletion.run_speech_hooks("user_1", ["utt_1", "utt_2"])

    assert seen == [("user_1", ["utt_1", "utt_2"])]
    assert deletion.registered_speech_modules() == {"extraction"}


def test_no_utterances_tells_nobody() -> None:
    seen: list[str] = []
    deletion.on_speech_deleted("extraction")(lambda user_id, ids: seen.append(user_id))

    deletion.run_speech_hooks("user_1", [])

    assert seen == []


def test_a_failing_hook_stops_the_run() -> None:
    later: list[str] = []

    def broken(user_id: str, utterance_ids: list[str]) -> None:
        raise RuntimeError("could not clean up")

    deletion.on_speech_deleted("first")(broken)
    deletion.on_speech_deleted("second")(lambda user_id, ids: later.append(user_id))

    with pytest.raises(RuntimeError):
        deletion.run_speech_hooks("user_1", ["utt_1"])
    assert later == []


def test_a_hook_gets_its_own_copy_of_the_ids() -> None:
    """One module trimming the list must not change what the next one sees."""
    seen: list[list[str]] = []
    deletion.on_speech_deleted("greedy")(lambda user_id, ids: ids.clear())
    deletion.on_speech_deleted("after")(lambda user_id, ids: seen.append(ids))

    deletion.run_speech_hooks("user_1", ["utt_1"])

    assert seen == [["utt_1"]]
