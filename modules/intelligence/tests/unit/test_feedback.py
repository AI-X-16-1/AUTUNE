"""The speaking-ratio DM: what it says. Pure, like confirmations.py.

Screen S23: subject only, the percentage, an even-share baseline, and a notice
that the number is not stored server-side. No buttons in this version.
"""

from __future__ import annotations

from autune_intelligence.feedback import build_speaking_ratio_dm


def _text(blocks: list[dict]) -> str:
    """All rendered text in the blocks, flattened."""
    out: list[str] = []
    for block in blocks:
        section = block.get("text")
        if isinstance(section, dict):
            out.append(section.get("text", ""))
        for element in block.get("elements", []):
            out.append(element.get("text", ""))
    return "\n".join(out)


def test_dm_states_the_participants_own_percentage() -> None:
    _fallback, blocks = build_speaking_ratio_dm(ratio=0.42, participant_count=3)

    assert "42%" in _text(blocks)


def test_dm_shows_the_even_share_baseline() -> None:
    _fallback, blocks = build_speaking_ratio_dm(ratio=0.42, participant_count=4)

    assert "25%" in _text(blocks)


def test_dm_says_the_number_is_not_stored() -> None:
    _fallback, blocks = build_speaking_ratio_dm(ratio=0.1, participant_count=5)

    assert "저장" in _text(blocks)


def test_fallback_does_not_carry_the_number() -> None:
    fallback, _blocks = build_speaking_ratio_dm(ratio=0.42, participant_count=3)

    assert "42" not in fallback


def test_dm_has_no_action_buttons() -> None:
    _fallback, blocks = build_speaking_ratio_dm(ratio=0.42, participant_count=3)

    assert all(block.get("type") != "actions" for block in blocks)
