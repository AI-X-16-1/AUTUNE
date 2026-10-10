import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { MeetingState } from "../hooks/useMeeting";
import type {
  LiveResearchDocument,
  MeetingDetail,
  MeetingStatus,
} from "../types";
import { StoredMeetingScreen } from "./StoredMeetingScreen";

let state: MeetingState = { status: "loading" };
vi.mock("../hooks/useMeeting", () => ({ useMeeting: () => state }));
// Each of these fetches on its own; what is under test is where the list goes.
vi.mock("./ProcessingStages", () => ({ ProcessingStages: () => null }));
vi.mock("./ResearchCard", () => ({ ResearchCard: () => null }));
vi.mock("./StoredTranscript", () => ({ StoredTranscript: () => null }));

afterEach(cleanup);

const DONE: LiveResearchDocument = {
  id: "alr_1",
  origin: "auto",
  status: "done",
  question: "지난달 가격 정책 결정",
  body: "가격 정책\n- 9월 10일 회의에서 월 구독으로 정했습니다",
  web_sources: [],
  meeting_sources: [],
  created_at: "2026-10-09T01:00:00Z",
};

function meeting(status: MeetingStatus): MeetingDetail {
  return {
    meeting_id: "mtg_1",
    title: "주간 회의",
    status,
    team_id: "team_1",
  } as unknown as MeetingDetail;
}

describe("StoredMeetingScreen", () => {
  it.each([
    "scheduled",
    "recording",
    "analyzing",
    "failed",
    "complete",
  ] as const)("lists the live documents of a %s meeting", async (status) => {
    vi.spyOn(api, "listLiveResearch").mockResolvedValueOnce([DONE]);
    state = { status: "ready", meeting: meeting(status) };

    render(<StoredMeetingScreen meetingId="mtg_1" />);

    expect(await screen.findByText("가격 정책")).toBeTruthy();
  });

  it("shows the title just saved, which the route does not send back (#1161)", async () => {
    vi.spyOn(api, "listLiveResearch").mockResolvedValue([]);
    const rename = vi
      .spyOn(api, "renameMeeting")
      .mockResolvedValue({ meeting_id: "mtg_1", status: "complete" });
    state = { status: "ready", meeting: meeting("complete") };
    render(<StoredMeetingScreen meetingId="mtg_1" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("주간 회의");

    fireEvent.click(screen.getByRole("button", { name: "이름 변경" }));
    fireEvent.change(screen.getByLabelText("회의 이름"), {
      target: { value: "3분기 계획" },
    });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    expect(await screen.findByRole("heading", { level: 1, name: "3분기 계획" })).toBeTruthy();
    expect(rename).toHaveBeenCalledWith("mtg_1", "3분기 계획");
  });

  it("does not show one meeting's saved title on another meeting", async () => {
    vi.spyOn(api, "listLiveResearch").mockResolvedValue([]);
    vi.spyOn(api, "renameMeeting").mockResolvedValue({ meeting_id: "mtg_1", status: "complete" });
    state = { status: "ready", meeting: meeting("complete") };
    const view = render(<StoredMeetingScreen meetingId="mtg_1" />);
    fireEvent.click(screen.getByRole("button", { name: "이름 변경" }));
    fireEvent.change(screen.getByLabelText("회의 이름"), {
      target: { value: "3분기 계획" },
    });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    await screen.findByRole("heading", { level: 1, name: "3분기 계획" });

    state = {
      status: "ready",
      meeting: { ...meeting("complete"), meeting_id: "mtg_2", title: "다른 회의" },
    };
    view.rerender(<StoredMeetingScreen meetingId="mtg_2" />);

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("다른 회의");
  });

  it("offers no rename before the meeting has been read", () => {
    state = { status: "loading" };

    render(<StoredMeetingScreen meetingId="mtg_1" />);

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("회의 전사");
    expect(screen.queryByRole("button", { name: "이름 변경" })).toBeNull();
  });

  it("offers to delete a meeting that has been read, and sends its id (#1161)", async () => {
    vi.spyOn(api, "listLiveResearch").mockResolvedValue([]);
    const remove = vi.spyOn(api, "deleteMeeting").mockReturnValue(new Promise(() => {}));
    state = { status: "ready", meeting: meeting("complete") };
    render(<StoredMeetingScreen meetingId="mtg_1" />);

    fireEvent.click(screen.getByRole("button", { name: "회의 삭제" }));
    fireEvent.change(screen.getByLabelText("삭제하려면 이 회의의 이름을 입력해 주세요."), {
      target: { value: "주간 회의" },
    });
    fireEvent.click(screen.getByRole("button", { name: "이 회의 삭제" }));

    expect(remove.mock.calls).toEqual([["mtg_1", "주간 회의"]]);
  });

  it("does not leave a title typed to delete one meeting in front of another", () => {
    vi.spyOn(api, "listLiveResearch").mockResolvedValue([]);
    state = { status: "ready", meeting: meeting("complete") };
    const view = render(<StoredMeetingScreen meetingId="mtg_1" />);
    fireEvent.click(screen.getByRole("button", { name: "회의 삭제" }));
    fireEvent.change(screen.getByLabelText("삭제하려면 이 회의의 이름을 입력해 주세요."), {
      target: { value: "주간 회의" },
    });

    state = {
      status: "ready",
      meeting: { ...meeting("complete"), meeting_id: "mtg_2", title: "주간 회의" },
    };
    view.rerender(<StoredMeetingScreen meetingId="mtg_2" />);

    expect(screen.queryByRole("form", { name: "회의 삭제" })).toBeNull();
    expect(screen.getByRole("button", { name: "회의 삭제" })).toBeTruthy();
  });

  it("offers no deletion before the meeting has been read", () => {
    state = { status: "loading" };

    render(<StoredMeetingScreen meetingId="mtg_1" />);

    expect(screen.queryByRole("button", { name: "회의 삭제" })).toBeNull();
  });
});
