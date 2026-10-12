"use client";

import { useEffect, useState } from "react";

import { StatusDot } from "@/shared/ui";

/**
 * Something a team already has that an agenda can be drafted from: its name,
 * and its lines for one team. Each is another module's and is supplied by the
 * route; this feature names the shape and imports none of them. Only whether
 * a source has a line is read here — the draft itself is drawn elsewhere.
 */
export type AgendaSource = {
  label: string;
  lines: (teamId: string) => Promise<readonly unknown[]>;
};

/** How many lines one source answered for one team; `null` is "could not be read". */
type Answer = number | null;

/**
 * The row's name, as module A's owner set it on #1147 (B4). S06 calls it
 * "자료 연결 후 어젠다 자동 생성"; the sources are named in the line under it,
 * not in the name.
 */
export const AGENDA_ROW_TITLE = "어젠다 초안 자동 생성";

/**
 * S06's agenda row, as the small cut of S08 (#1147).
 *
 * **On when there is something to draft from** (the user, 2026-10-09: "자료
 * 연결이나 연동에서 가져오거나 이전회의 어젠다 있을 때 활성화"): at least one
 * of the sources the route supplied has a line for the chosen team. With none
 * it is off and says what is missing, and while they are being read it says
 * that.
 *
 * **The row reports; it does not choose.** The draft is drawn in the
 * pre-meeting brief (module D's `BriefPanel`, #1147 B3), which exists for a
 * meeting saved for later, from ten minutes before its start. A meeting has no
 * field that could carry a tick there and none is invented, so the row is a
 * status line and holds no control (#1147: "체크박스가 아니라 상태를 알리는
 * 줄"): a dot, filled when a draft will have something in it, and the same
 * said in words for a reader who is not shown the dot. A disabled checkbox
 * was read out as one — "checkbox, checked, disabled" (review of #1193).
 *
 * A source that cannot be read counts as having nothing. The row is an offer,
 * not a step of opening a meeting, so its failure is never the form's error.
 * A third kind of source joins by being added to the list the route passes.
 */
export function AgendaDraftRow({
  teamId,
  sources,
}: {
  teamId: string;
  sources: readonly AgendaSource[];
}) {
  // Keyed by the team asked: an answer that arrives for a team the person has
  // since left is not this team's.
  const [read, setRead] = useState<{ teamId: string; answers: Record<number, Answer> }>({
    teamId,
    answers: {},
  });

  useEffect(() => {
    if (teamId === "") return;
    let current = true;
    setRead({ teamId, answers: {} });
    sources.forEach((source, index) => {
      const settle = (answer: Answer) => {
        if (!current) return;
        setRead((previous) =>
          previous.teamId === teamId
            ? { teamId, answers: { ...previous.answers, [index]: answer } }
            : previous,
        );
      };
      source
        .lines(teamId)
        .then((lines) => settle(lines.length))
        .catch(() => settle(null));
    });
    return () => {
      current = false;
    };
  }, [teamId, sources]);

  const answers = read.teamId === teamId ? read.answers : {};
  const found = sources.filter((_, index) => (answers[index] ?? 0) > 0);
  const waiting = teamId !== "" && Object.keys(answers).length < sources.length;
  const available = found.length > 0;

  let detail: string;
  if (available) {
    detail = `${found.map((source) => source.label).join(" · ")}에서 모읍니다 · 모델을 쓰지 않습니다 · “저장만”으로 연 회의는 시작 10분 전 브리프에 초안이 나옵니다`;
  } else if (teamId === "") {
    detail = "팀을 고르면 만들 수 있는지 확인합니다";
  } else if (waiting) {
    detail = "초안에 넣을 것이 있는지 확인하는 중…";
  } else {
    detail = `${sources.map((source) => source.label).join(", ")} 가운데 아직 아무것도 없습니다`;
  }

  return (
    <div
      className="flex items-center gap-3 border-b border-[var(--color-hairline)]"
      style={{ paddingBlock: "var(--space-12)" }}
    >
      {/* As wide as the checkbox of the rows beside it, so the names line up. */}
      <span className="inline-flex shrink-0 justify-center" style={{ width: 13 }}>
        <StatusDot variant={available ? "confirmed" : "idle"} hollow={!available} />
      </span>
      <span>
        <span
          className={`block ${available ? "text-[var(--color-ink-strong)]" : "text-[var(--color-ink-muted)]"}`}
          style={{
            fontSize: "var(--text-rowLabel)",
            fontWeight: "var(--text-rowLabel-weight)",
          }}
        >
          {AGENDA_ROW_TITLE}
        </span>
        {/* The dot is hidden from a screen reader; this is what it says. */}
        <span className="sr-only">{available ? "켜짐" : "꺼짐"}</span>
        <span
          className="block text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-meta)" }}
        >
          {detail}
        </span>
      </span>
    </div>
  );
}
