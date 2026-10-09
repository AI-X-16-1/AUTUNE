import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as auth from "@/shared/api/auth";

import * as api from "../api";
import { WeeklyReportScheduleCard, WeeklyReportScheduleView } from "./WeeklyReportScheduleCard";
import type { WeeklyReportSchedule } from "../types";

// When the team's weekly report goes out (#227): any member sets the weekday,
// the hour in Korean time, and whether a week with nothing to say is sent.

const DEFAULTS: WeeklyReportSchedule = {
  weekday: 0,
  hour: 9,
  send_empty: false,
  updated_by_name: null,
  updated_at: null,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WeeklyReportScheduleView", () => {
  it("says when the report goes out and that an empty week is not sent", () => {
    render(<WeeklyReportScheduleView schedule={DEFAULTS} error={null} onSave={vi.fn()} />);

    expect(screen.getByText("매주 월요일 09:00(한국 시간)에 팀 Slack 채널로 보냅니다.")).toBeTruthy();
    expect(screen.getByText(/없는 주에는 보내지 않습니다/)).toBeTruthy();
  });

  it("names who changed it", () => {
    render(
      <WeeklyReportScheduleView
        schedule={{ ...DEFAULTS, updated_by_name: "이승환", updated_at: "2026-10-05T06:00:00Z" }}
        error={null}
        onSave={vi.fn()}
      />,
    );

    expect(screen.getByText(/이승환님이 .*에 바꿈/)).toBeTruthy();
  });

  it("saves the day, the hour and the empty-week choice", async () => {
    const onSave = vi.fn().mockResolvedValue(null);
    render(<WeeklyReportScheduleView schedule={DEFAULTS} error={null} onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "바꾸기" }));
    fireEvent.change(screen.getByLabelText("요일"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("시각"), { target: { value: "18" } });
    fireEvent.click(screen.getByRole("checkbox"));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "저장" }));
    });

    expect(onSave).toHaveBeenCalledWith({ weekday: 2, hour: 18, send_empty: true });
  });

  it("keeps the form open with the reason when saving fails", async () => {
    const onSave = vi.fn().mockResolvedValue("이 팀의 설정을 바꿀 권한이 없습니다.");
    render(<WeeklyReportScheduleView schedule={DEFAULTS} error={null} onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "바꾸기" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "저장" }));
    });

    expect(screen.getByText("이 팀의 설정을 바꿀 권한이 없습니다.")).toBeTruthy();
    expect(screen.getByLabelText("요일")).toBeTruthy();
  });
});

describe("WeeklyReportScheduleView across a person's teams", () => {
  const TEAMS = [
    { id: "team_a", name: "A팀" },
    { id: "team_b", name: "B팀" },
    { id: "team_c", name: "C팀" },
  ];

  function view(onSave = vi.fn().mockResolvedValue(null), onSaveTeam = vi.fn().mockResolvedValue(null)) {
    render(
      <WeeklyReportScheduleView
        schedule={DEFAULTS}
        error={null}
        onSave={onSave}
        teamId="team_a"
        teams={TEAMS}
        onSaveTeam={onSaveTeam}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "바꾸기" }));
    fireEvent.change(screen.getByLabelText("시각"), { target: { value: "18" } });
    return { onSave, onSaveTeam };
  }

  it("offers no reach to a person in one team", () => {
    render(
      <WeeklyReportScheduleView
        schedule={DEFAULTS}
        error={null}
        onSave={vi.fn()}
        teamId="team_a"
        teams={TEAMS.slice(0, 1)}
        onSaveTeam={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "바꾸기" }));

    expect(screen.queryByText("적용할 팀")).toBeNull();
  });

  it("saves this team only by default, without asking", async () => {
    const { onSave, onSaveTeam } = view();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "저장" }));
    });

    expect(onSave).toHaveBeenCalledWith({ weekday: 0, hour: 18, send_empty: false });
    expect(onSaveTeam).not.toHaveBeenCalled();
  });

  it("asks before giving every team the schedule, then saves each", async () => {
    const { onSave, onSaveTeam } = view();

    fireEvent.click(screen.getByLabelText("내 모든 팀"));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(onSave).not.toHaveBeenCalled(); // nothing until confirmed
    expect(screen.getByRole("alertdialog").textContent).toContain("B팀, C팀");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "3개 팀에 적용" }));
    });

    const choice = { weekday: 0, hour: 18, send_empty: false };
    expect(onSave).toHaveBeenCalledWith(choice);
    expect(onSaveTeam.mock.calls).toEqual([
      ["team_b", choice],
      ["team_c", choice],
    ]);
    expect(screen.getByText("3개 팀에 적용했습니다.")).toBeTruthy();
  });

  it("saves only the teams picked", async () => {
    const { onSaveTeam } = view();

    fireEvent.click(screen.getByLabelText("팀 골라서"));
    expect(screen.getByRole("button", { name: "저장" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByLabelText("C팀에도 적용"));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "2개 팀에 적용" }));
    });

    expect(onSaveTeam.mock.calls.map(([team]) => team)).toEqual(["team_c"]);
  });

  it("says which teams were not changed and why, and keeps the form", async () => {
    const onSaveTeam = vi
      .fn()
      .mockImplementation(async (team: string) => (team === "team_b" ? "바꿀 권한이 없습니다" : null));
    view(undefined, onSaveTeam);

    fireEvent.click(screen.getByLabelText("내 모든 팀"));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "3개 팀에 적용" }));
    });

    expect(screen.getByText("3개 팀 중 2개 팀을 바꿨습니다. B팀: 바꿀 권한이 없습니다")).toBeTruthy();
    expect(screen.getByLabelText("요일")).toBeTruthy();
  });

  it("goes back from the question without saving", () => {
    const { onSave, onSaveTeam } = view();

    fireEvent.click(screen.getByLabelText("내 모든 팀"));
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    fireEvent.click(screen.getByRole("button", { name: "돌아가기" }));

    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(onSave).not.toHaveBeenCalled();
    expect(onSaveTeam).not.toHaveBeenCalled();
  });
});

describe("WeeklyReportScheduleCard", () => {
  it("offers the person's other teams, read from their session", async () => {
    vi.spyOn(api, "getWeeklyReportSchedule").mockResolvedValue(DEFAULTS);
    vi.spyOn(auth, "getSession").mockResolvedValue({
      id: "usr_1",
      email: "a@example.com",
      display_name: "승환",
      teams: [
        { id: "team_a", name: "A팀" },
        { id: "team_b", name: "B팀" },
      ],
    });

    render(<WeeklyReportScheduleCard teamId="team_a" />);
    fireEvent.click(await screen.findByRole("button", { name: "바꾸기" }));
    fireEvent.click(screen.getByLabelText("팀 골라서"));

    expect(screen.getByLabelText("B팀에도 적용")).toBeTruthy();
    expect(screen.queryByLabelText("A팀에도 적용")).toBeNull(); // this team is saved anyway
  });
});
