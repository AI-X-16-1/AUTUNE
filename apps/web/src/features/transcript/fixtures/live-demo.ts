import type { LiveRow } from "../types";

/**
 * A meeting for S13 to render before the microphone path exists.
 *
 * The screen was built against `LiveRow`, but nothing produces those rows in
 * the browser yet: web-mic capture and streaming STT are not in `audio.md`'s
 * MVP path, which transcribes an uploaded file in the worker and publishes
 * once at the end. Until that path exists the route feeds the screen this
 * meeting, one row at a time on the row's own `start`, so the page behaves the
 * way it will when rows arrive from a socket.
 *
 * Text is written the way `Utterance.text` reaches the browser — already
 * masked, `**` runs where personal data was. There is nothing to reveal.
 *
 * One voice is deliberately unnamed (`speaker_id` null) so the ochre prompt is
 * on screen, and no row carries a `kind`: B reports after the meeting, and a
 * tag during recording would show a path the architecture does not have
 * (issue #155).
 */
export const DEMO_PLANNED_SECONDS = 45 * 60;

export const DEMO_ROWS: LiveRow[] = [
  row(
    "u-01",
    "김민경",
    "p-kmk",
    2,
    9,
    "그럼 시작할게요. 오늘은 개인화 랭킹 1차 범위를 정하고, 지난주에 미뤄둔 로그 수집 건을 마무리하려고 해요.",
  ),
  row(
    "u-02",
    "강민구",
    "p-kmg",
    10,
    21,
    "로그 쪽은 제가 정리해 왔어요. 클릭 로그는 이미 들어오고 있고, 체류 시간은 프론트에서 이벤트를 하나 더 보내야 해요.",
  ),
  row(
    "u-03",
    "이승환",
    "p-lsh",
    22,
    31,
    "체류 시간 이벤트는 이번 주 안에 붙일 수 있어요. 다만 세션 기준을 어떻게 잡을지는 먼저 정해야 해요.",
  ),
  row(
    "u-04",
    "화자 2",
    null,
    32,
    44,
    "세션은 30분 무활동 기준으로 가면 될 것 같은데요. 지난번 대시보드도 그 기준이었고요. 문의는 010-****-**** 로 주시면 돼요.",
  ),
  row(
    "u-05",
    "김민경",
    "p-kmk",
    45,
    52,
    "좋아요. 그럼 세션 기준은 30분으로 하고, 승환님이 이번 주 금요일까지 이벤트 붙이는 걸로 할게요.",
  ),
  row("u-06", "이승환", "p-lsh", 53, 56, "네, 금요일까지 하겠습니다."),
  row(
    "u-07",
    "강민구",
    "p-kmg",
    57,
    70,
    "하나 걱정되는 건, 개인화 모델이 신규 사용자한테는 아무 신호가 없다는 거예요. 콜드 스타트를 어떻게 할지는 이번 범위에서 빼는 건가요?",
  ),
  row(
    "u-08",
    "화자 2",
    null,
    71,
    82,
    "1차에서는 인기순 폴백으로 가고, 콜드 스타트는 2차에서 다루는 게 어때요. 관련 자료는 ******@autune.dev 로 보내 놓을게요.",
  ),
  row(
    "u-09",
    "김민경",
    "p-kmk",
    83,
    94,
    "그건 좀 더 얘기해 봐야 할 것 같아요. 폴백으로 가더라도 어떤 지표로 판단할지는 지금 정해두는 게 좋겠어요.",
  ),
  row(
    "u-10",
    "이승환",
    "p-lsh",
    95,
    106,
    "지표는 CTR 하나로는 부족하고, 재방문율까지 봐야 한다고 생각해요. 이건 데이터 팀이랑 한 번 더 확인해 볼게요.",
  ),
];

function row(
  id: string,
  speaker: string,
  speakerId: string | null,
  start: number,
  end: number,
  text: string,
): LiveRow {
  return {
    utterance: {
      id,
      speaker,
      speaker_id: speakerId,
      start,
      end,
      text,
      confidence: 0.92,
    },
  };
}
