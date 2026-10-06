"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

import { ApiError } from "@/shared/api/client";

import { pinTeam, unpinTeam } from "../api";
import { announceTeams } from "../selectedTeam";
import type { TeamSummary } from "../types";

/**
 * Every team a person is on, in a small window over the page: where one is
 * chosen when it is not among the few a list shows, and where a team is
 * pinned (the user, 2026-10-06: "작은 화면 띄워서 보여줘", "맨위 고정도 거기로
 * 이동").
 *
 * The sidebar's menu and the home screen's row each show three teams and a
 * "더보기" that opens this. It is one window for both, so there is one place
 * that lists them all and one place that pins.
 *
 * **Choosing** is the caller's (`onChoose`): the window says which team and
 * closes; what choosing means -- keeping the choice, leaving a meeting's
 * screen -- is decided where it is opened.
 *
 * **Pinning** is done here and stays open while it is: a pin reorders the
 * list, and the person may want to pin another. The new list goes to every
 * list of teams on the page (`announceTeams`), since the pin is on the
 * account and they all show its order. At most three; the server refuses a
 * fourth and the window says to unpin one first. A pin changes nobody's
 * choice of team.
 *
 * It closes on a choice, on Escape and on a press outside it; a press on the
 * control that opened it is that control's to handle, so it is not counted
 * as outside.
 *
 * `position: fixed`, at a place the opener measured: the sidebar scrolls and
 * clips, and a child positioned inside it would be cut off at its edge.
 */
export type Place = { left: number; top: number };

/** The window scrolls past this height rather than grow down the page. */
export const WINDOW_MAX_HEIGHT = 320;

/** About how tall the window is with `rows` teams: enough to keep it on the page. */
const tall = (rows: number) => Math.min(WINDOW_MAX_HEIGHT, rows * 36 + 18);

const onPage = (top: number, rows: number) =>
  Math.max(8, Math.min(top, window.innerHeight - tall(rows) - 8));

/**
 * Beside a control in the sidebar: level with it and just past the sidebar's
 * own edge (the nearest `aside`) -- the control ends short of that edge by
 * the sidebar's padding, and a window placed from the control alone lay half
 * on the sidebar.
 */
export function besideSidebar(control: HTMLElement, rows: number): Place {
  const box = control.getBoundingClientRect();
  const edge = control.closest("aside")?.getBoundingClientRect().right ?? 0;
  return { left: Math.max(box.right + 12, edge + 8), top: onPage(box.top, rows) };
}

/** Under a control on the page, starting at its left edge. */
export function below(control: HTMLElement, rows: number): Place {
  const box = control.getBoundingClientRect();
  return { left: box.left, top: onPage(box.bottom + 6, rows) };
}

export function TeamsWindow({
  teams,
  teamId,
  at,
  opener,
  onChoose,
  onClose,
}: {
  teams: TeamSummary[];
  /** The team being looked at, marked in the list; `null` when none is. */
  teamId: string | null;
  at: Place;
  /** The control that opened this: a press on it is not a press outside. */
  opener: RefObject<HTMLElement | null>;
  onChoose: (teamId: string) => void;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [pinning, setPinning] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    const onPress = (event: MouseEvent) => {
      const target = event.target as Node;
      if (box.current?.contains(target) || opener.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPress);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPress);
    };
  }, [onClose, opener]);

  const togglePin = async (team: TeamSummary) => {
    setPinning(true);
    setProblem(null);
    try {
      announceTeams(await (team.pinned ? unpinTeam(team.team_id) : pinTeam(team.team_id)));
    } catch (caught) {
      setProblem(
        caught instanceof ApiError && caught.code === "too_many_pinned_teams"
          ? "팀은 3개까지 고정할 수 있습니다. 다른 팀의 고정을 풀고 다시 시도해 주세요."
          : "고정을 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.",
      );
    } finally {
      setPinning(false);
    }
  };

  const text = { fontSize: "var(--control-text-default)", fontWeight: 500 } as const;

  return (
    <div
      ref={box}
      id="teams-window"
      role="dialog"
      aria-label="팀"
      className="flex flex-col rounded-[var(--radius)] border border-[var(--color-hairline)] bg-[var(--color-paper)]"
      style={{
        position: "fixed",
        left: at.left,
        top: at.top,
        zIndex: 50,
        minWidth: 240,
        maxWidth: 320,
        maxHeight: WINDOW_MAX_HEIGHT,
        overflowY: "auto",
        padding: "8px 14px",
        boxShadow: "var(--shadow-overlay)",
      }}
    >
      {teams.map((team) => {
        const looking = team.team_id === teamId;
        return (
          <div key={team.team_id} className="flex items-center gap-3">
            <button
              type="button"
              aria-pressed={looking}
              onClick={() => onChoose(team.team_id)}
              className={
                looking
                  ? "min-w-0 flex-1 truncate text-left text-[var(--color-accent-hover)]"
                  : "min-w-0 flex-1 truncate text-left text-[var(--color-ink-body)] hover:text-[var(--color-ink-strong)]"
              }
              style={{ ...text, fontWeight: looking ? 600 : 500, padding: "6px 0" }}
            >
              {team.name}
            </button>
            <button
              type="button"
              disabled={pinning}
              aria-label={`${team.name} ${team.pinned ? "고정 해제" : "맨 위에 고정"}`}
              onClick={() => void togglePin(team)}
              className={
                team.pinned
                  ? "shrink-0 text-[var(--color-accent-default)]"
                  : "shrink-0 text-[var(--color-ink-muted)] hover:text-[var(--color-ink-strong)]"
              }
              style={{ fontSize: "var(--text-metaSmall)", fontWeight: 500 }}
            >
              {team.pinned ? "고정 해제" : "고정"}
            </button>
          </div>
        );
      })}
      {problem !== null ? (
        <p
          role="alert"
          className="mt-1"
          style={{ fontSize: "var(--text-metaSmall)", color: "var(--color-signal-critical)" }}
        >
          {problem}
        </p>
      ) : null}
    </div>
  );
}
