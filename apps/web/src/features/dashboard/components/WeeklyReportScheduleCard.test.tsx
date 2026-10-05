import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WeeklyReportScheduleView } from "./WeeklyReportScheduleCard";
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

afterEach(cleanup);

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
