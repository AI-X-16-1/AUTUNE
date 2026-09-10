"""The speaking-ratio DM: what it says and how it is shaped.

Pure functions over values — no Slack client, no database. The service layer
sends what this builds. Keeping it separate is what lets the wording be tested
without credentials.

Message shape follows docs/design/ui-spec.md section 2: status is "●" plus text
rather than an emoji, hierarchy comes from weight. Screen S23: the subject's own
percentage, an even-share baseline, and a line stating the number is not stored.
No buttons — a working opt-out needs a handler and a stored preference, which is
a separate change.

The percentage is personal data. It goes in the blocks, which Slack shows only
after the DM is opened, and not in the fallback, which appears in a notification
preview on a phone the person may not be holding.
"""

from __future__ import annotations

from typing import Any

_NOT_STORED = "이 수치는 서버에 저장되지 않으며, 본인에게만 전송됩니다."


def build_speaking_ratio_dm(*, ratio: float, participant_count: int) -> tuple[str, list[dict]]:
    """The DM telling one participant their share of one meeting.

    ``ratio`` is 0..1. ``participant_count`` is how many people the ratio is
    shared among, used for the even-share baseline and shown so the number is
    legible in a small meeting.
    """
    percent = round(ratio * 100)
    fallback = "이번 회의에서의 발언 비중을 알려드립니다."
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "● *이번 회의 발언 비중*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{percent}%*"}},
    ]
    if participant_count > 0:
        even = round(100 / participant_count)
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"참여자 {participant_count}명 · 균등 분배 기준 {even}%",
                    }
                ],
            }
        )
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _NOT_STORED}]})
    return fallback, blocks
