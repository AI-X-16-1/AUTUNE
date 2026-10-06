"use client";

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { Button } from "@/shared/ui";

import { getExtractionState, requestExtraction, type ExtractionState } from "../api";

/** How often a requested run is looked for, and for how long. */
export const POLL_MS = 5_000;
export const POLL_LIMIT = 60;

type Outcome = "done" | "failed" | "slow" | "too_soon" | "no_transcript" | "not_asked" | null;

/**
 * What became of this meeting's extraction, and the button that runs it again
 * (the user, 2026-10-06).
 *
 * An extraction that failed used to leave an empty board that read as "this
 * meeting produced nothing". Now the screen says it failed: while the server
 * is still trying by itself, and when it has stopped after three tries.
 *
 * "다시 추출" is offered on any meeting, failed or not. The request is
 * accepted at once and run by the worker within a minute, so the screen asks
 * again every few seconds until the run has left a trace -- a new extraction
 * time, or one more failure -- and then tells its parent to read the board
 * again. The run is the automatic one: a meeting whose items a person has
 * edited keeps its item list, which is said under the button so that nobody
 * presses it expecting their corrections to be replaced.
 */
export function ReExtract({
  meetingId,
  onExtracted,
}: {
  meetingId: string;
  onExtracted: () => void;
}) {
  const [state, setState] = useState<ExtractionState | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    getExtractionState(meetingId)
      .then((read) => alive.current && setState(read))
      .catch(() => undefined); // nothing to say: the board below has its own error line
    return () => {
      alive.current = false;
    };
  }, [meetingId]);

  const watch = (before: ExtractionState, tries: number) => {
    window.setTimeout(() => {
      if (!alive.current) return;
      getExtractionState(meetingId)
        .then((read) => {
          if (!alive.current) return;
          setState(read);
          const ran =
            !read.requested &&
            (read.extracted_at !== before.extracted_at || read.failures !== before.failures);
          if (ran) {
            setWaiting(false);
            setOutcome(read.failures > before.failures ? "failed" : "done");
            if (read.failures <= before.failures) onExtracted();
          } else if (tries + 1 >= POLL_LIMIT) {
            setWaiting(false);
            setOutcome("slow");
          } else watch(before, tries + 1);
        })
        .catch(() => {
          if (!alive.current) return;
          if (tries + 1 >= POLL_LIMIT) {
            setWaiting(false);
            setOutcome("slow");
          } else watch(before, tries + 1);
        });
    }, POLL_MS);
  };

  const ask = async () => {
    setWaiting(true);
    setOutcome(null);
    try {
      const accepted = await requestExtraction(meetingId);
      if (!alive.current) return;
      const before = state ?? { ...accepted, requested: false };
      setState(accepted);
      watch(before, 0);
    } catch (caught) {
      if (!alive.current) return;
      setWaiting(false);
      setOutcome(
        caught instanceof ApiError && caught.status === 429
          ? "too_soon"
          : caught instanceof ApiError && caught.status === 409
            ? "no_transcript"
            : "not_asked",
      );
    }
  };

  if (state === null) return null;

  const muted = { fontSize: "var(--text-meta)", color: "var(--color-ink-muted)" } as const;
  const critical = { ...muted, color: "var(--color-signal-critical)" } as const;
  const failed = state.failures > 0 && outcome !== "done";

  return (
    <div className="flex flex-col gap-1.5">
      {failed && !waiting ? (
        state.will_retry ? (
          <p role="status" style={muted}>
            이 회의의 액션 아이템과 결정을 추출하지 못해 자동으로 다시 시도하고 있습니다
            ({state.failures}번 실패). 잠시 뒤 새로 고쳐 주세요.
          </p>
        ) : (
          <p role="alert" style={critical}>
            이 회의의 액션 아이템과 결정을 추출하지 못했습니다({state.failures}번 시도). 자동으로는
            더 시도하지 않습니다. 아래 버튼으로 다시 시도할 수 있습니다.
          </p>
        )
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          tone="text"
          size="compact"
          type="button"
          disabled={waiting}
          onClick={() => void ask()}
        >
          액션·결정 다시 추출
        </Button>
        <span style={muted}>
          {waiting
            ? "다시 추출을 요청했습니다. 보통 1~2분 안에 반영됩니다."
            : "항목을 고친 적이 있는 회의는 액션 목록을 그대로 두고, 분류와 결정만 새로 만듭니다."}
        </span>
      </div>
      {outcome === "done" ? (
        <p role="status" style={muted}>
          다시 추출했습니다.
        </p>
      ) : outcome === "failed" ? (
        <p role="alert" style={critical}>
          다시 추출하지 못했습니다. 잠시 뒤 다시 시도해 주세요.
        </p>
      ) : outcome === "slow" ? (
        <p role="status" style={muted}>
          아직 끝나지 않았습니다. 잠시 뒤 이 화면을 새로 고쳐 주세요.
        </p>
      ) : outcome === "too_soon" ? (
        <p role="status" style={muted}>
          방금 요청한 추출이 진행 중입니다. 잠시 뒤 다시 눌러 주세요.
        </p>
      ) : outcome === "no_transcript" ? (
        <p role="status" style={muted}>
          아직 전사된 내용이 없어 추출할 수 없습니다.
        </p>
      ) : outcome === "not_asked" ? (
        <p role="alert" style={critical}>
          다시 추출을 요청하지 못했습니다. 잠시 뒤 다시 시도해 주세요.
        </p>
      ) : null}
    </div>
  );
}
