import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { MeetingDeletion } from "./MeetingDeletion";

// #1161: any member deletes a meeting from its own screen. What is pinned:
// nothing is sent before the person has read what goes and what stays and
// typed the meeting's title; one request, with the title as typed; a refusal
// as a sentence they can act on; and the meeting list afterwards.

const remove = vi.fn<(meetingId: string, title: string) => Promise<void>>();
vi.mock("../api", () => ({
  deleteMeeting: (meetingId: string, title: string) => remove(meetingId, title),
}));

const assign = vi.fn<(href: string) => void>();
const location = window.location;

beforeEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...location, assign },
  });
});

afterEach(() => {
  cleanup();
  remove.mockReset();
  assign.mockReset();
  Object.defineProperty(window, "location", { configurable: true, value: location });
});

const TITLE = "고객사 A 주간 회의";

const open = () => {
  render(<MeetingDeletion meetingId="mtg_1" />);
  fireEvent.click(screen.getByRole("button", { name: "회의 삭제" }));
};
const form = () => screen.queryByRole("form", { name: "회의 삭제" });
const field = () =>
  screen.getByLabelText("삭제하려면 이 회의의 이름을 입력해 주세요.") as HTMLInputElement;
const type = (value: string) => fireEvent.change(field(), { target: { value } });
const confirm = () => screen.getByRole("button", { name: "이 회의 삭제" }) as HTMLButtonElement;

describe("MeetingDeletion", () => {
  it("shows one button and asks nothing until it is pressed", () => {
    render(<MeetingDeletion meetingId="mtg_1" />);

    expect(screen.getByRole("button", { name: "회의 삭제" })).toBeTruthy();
    expect(form()).toBeNull();
    expect(remove).not.toHaveBeenCalled();
  });

  it("says what goes, what is asked back and what stays before the title is typed", () => {
    open();

    const said = form()!.textContent ?? "";
    // What goes, whose words go with it, and that it cannot be undone.
    expect(said).toContain("Autune이 이 회의에 대해 보관하는 것이 모두 삭제됩니다");
    expect(said).toContain("다른 사람이 이 회의에서 한 말도 함께 삭제되며");
    expect(said).toContain("알림은 가지 않습니다");
    expect(said).toContain("되돌릴 수 없습니다");
    // What Autune asks the tools to take back, and that it may not happen.
    expect(said).toContain("Google Calendar에 넣은 일정");
    expect(said).toContain("프로젝트별 회의록은 삭제를 요청합니다");
    expect(said).toContain("응답하지 않으면 남을 수 있습니다");
    // What stays where it was sent.
    expect(said).toContain("Notion 페이지와 Jira 이슈");
    expect(said).toContain("이전 회의 연결 알림과 결정 변경 알림, 회의 전 브리핑");
    expect(said).toContain("회의 리포트와 주간 팀 리포트");
    expect(said).toContain("후속 회의로 잡은 Google Calendar 일정은 잡은 사람의 일정으로 남습니다");
    expect(said).toContain("Slack 개인 메시지로 받은 알림도 남습니다");
    expect(remove).not.toHaveBeenCalled();
  });

  it("waits for something to be typed, and the field starts empty", () => {
    open();

    expect(field().value).toBe("");
    expect(field().maxLength).toBe(400);
    expect(field().autocomplete).toBe("off");
    expect(confirm().disabled).toBe(true);
    type("   ");
    expect(confirm().disabled).toBe(true);
    fireEvent.submit(form()!);
    expect(remove).not.toHaveBeenCalled();

    type(TITLE);
    expect(confirm().disabled).toBe(false);
  });

  it("sends the title as typed, once, and goes to the meeting list", async () => {
    remove.mockResolvedValue();
    open();
    type(` ${TITLE} `);

    fireEvent.click(confirm());

    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    // The server compares; the screen does not tidy what was typed.
    expect(remove.mock.calls).toEqual([["mtg_1", ` ${TITLE} `]]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not send a second request while the first is out", async () => {
    let finish: () => void = () => {};
    remove.mockReturnValue(new Promise<void>((resolve) => (finish = resolve)));
    open();
    type(TITLE);

    fireEvent.submit(form()!);
    fireEvent.submit(form()!);

    expect(remove).toHaveBeenCalledTimes(1);
    expect(assign).not.toHaveBeenCalled();
    finish();
    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
  });

  it.each([
    [422, "meeting_title_mismatch", "입력한 이름이 회의 이름과 다릅니다."],
    [409, "meeting_in_progress", "전사 중이거나 실시간으로 진행 중인 회의는 삭제할 수 없습니다."],
    [403, "permission_denied", "이 회의를 삭제할 수 없습니다."],
    [404, "not_found", "이미 삭제되었을 수 있습니다."],
    [500, "unknown", "회의를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요."],
  ])("says a %i %s as a sentence, and stays", async (status, code, sentence) => {
    remove.mockRejectedValue(new ApiError(status, code, "the server's English"));
    open();
    type(TITLE);

    fireEvent.click(confirm());

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(sentence);
    expect(alert.textContent).not.toContain("English");
    expect(assign).not.toHaveBeenCalled();
    // What was typed is still there, and the request can be made again.
    expect(field().value).toBe(TITLE);
    expect(confirm().disabled).toBe(false);
  });

  it("says a request that did not come back the same way", async () => {
    remove.mockRejectedValue(new TypeError("Failed to fetch"));
    open();
    type(TITLE);

    fireEvent.click(confirm());

    expect((await screen.findByRole("alert")).textContent).toBe(
      "회의를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );
  });

  it("takes a refusal away when the title is typed again", async () => {
    remove.mockRejectedValue(new ApiError(422, "meeting_title_mismatch", "no"));
    open();
    type("아닌 이름");
    fireEvent.click(confirm());
    await screen.findByRole("alert");

    type(TITLE);

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("closes on 취소 and keeps neither what was typed nor the refusal", async () => {
    remove.mockRejectedValue(new ApiError(422, "meeting_title_mismatch", "no"));
    open();
    type("아닌 이름");
    fireEvent.click(confirm());
    await screen.findByRole("alert");

    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(form()).toBeNull();
    expect(remove).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "회의 삭제" }));
    expect(field().value).toBe("");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
