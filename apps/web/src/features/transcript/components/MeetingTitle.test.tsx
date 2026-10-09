import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { MeetingTitle } from "./MeetingTitle";

// #1161: any member renames a meeting from its own screen. What is pinned:
// one request with the title as the person meant it, a refusal as a sentence
// with their text still in the field, and what a rename does not reach said
// before it is saved.

const rename = vi.fn<(meetingId: string, title: string) => Promise<unknown>>();
vi.mock("../api", () => ({
  renameMeeting: (meetingId: string, title: string) => rename(meetingId, title),
}));

afterEach(() => {
  cleanup();
  rename.mockReset();
});

const TITLE = "고객사 A 주간 회의";
const NEW = "고객사 A 3분기 계획";

function shown(title = TITLE) {
  const onRenamed = vi.fn<(title: string) => void>();
  render(<MeetingTitle meetingId="mtg_1" title={title} onRenamed={onRenamed} />);
  return onRenamed;
}

const open = () => fireEvent.click(screen.getByRole("button", { name: "이름 변경" }));
const field = () => screen.getByLabelText("회의 이름") as HTMLInputElement;
const type = (value: string) => fireEvent.change(field(), { target: { value } });
const save = () => screen.getByRole("button", { name: "저장" }) as HTMLButtonElement;
const form = () => screen.queryByRole("form", { name: "회의 이름 변경" });

describe("MeetingTitle", () => {
  it("shows the title as the heading, with a way to change it and no field yet", () => {
    shown();

    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(TITLE);
    expect(screen.getByRole("button", { name: "이름 변경" })).toBeTruthy();
    expect(form()).toBeNull();
    expect(rename).not.toHaveBeenCalled();
  });

  it("opens a field holding the title, and keeps the title as the page's heading", () => {
    shown();

    open();

    expect(field().value).toBe(TITLE);
    expect(field().maxLength).toBe(400);
    expect(field().autocomplete).toBe("off");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(TITLE);
  });

  it("says before saving that what was already sent keeps the old name", () => {
    shown();

    open();

    expect(form()!.textContent).toContain(
      "이미 보낸 알림과 내보낸 문서에는 이전 이름이 그대로 남습니다.",
    );
  });

  it("sends the new title once, without the space around it, and hands it up", async () => {
    rename.mockResolvedValue({ meeting_id: "mtg_1", status: "complete" });
    const onRenamed = shown();
    open();
    type(`  ${NEW}  `);

    fireEvent.click(save());

    await waitFor(() => expect(onRenamed).toHaveBeenCalledWith(NEW));
    expect(rename).toHaveBeenCalledTimes(1);
    expect(rename).toHaveBeenCalledWith("mtg_1", NEW);
    expect(form()).toBeNull();
  });

  it("is saved by Enter as by the button", async () => {
    rename.mockResolvedValue({});
    const onRenamed = shown();
    open();
    type(NEW);

    fireEvent.submit(form()!);

    await waitFor(() => expect(onRenamed).toHaveBeenCalledWith(NEW));
  });

  it("sends nothing for the title the meeting already has, and closes", () => {
    const onRenamed = shown();
    open();
    type(` ${TITLE} `);

    fireEvent.click(save());

    expect(rename).not.toHaveBeenCalled();
    expect(onRenamed).not.toHaveBeenCalled();
    expect(form()).toBeNull();
  });

  it("cannot save a title of nothing but space", () => {
    shown();
    open();
    type("   ");

    expect(save().disabled).toBe(true);
    fireEvent.submit(form()!);

    expect(rename).not.toHaveBeenCalled();
    expect(form()).not.toBeNull();
  });

  it("sends one request at a time", async () => {
    let finish: (value: unknown) => void = () => undefined;
    rename.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    shown();
    open();
    type(NEW);

    fireEvent.submit(form()!);
    fireEvent.submit(form()!);

    expect(rename).toHaveBeenCalledTimes(1);
    expect(save().disabled).toBe(true);
    finish({});
    await waitFor(() => expect(form()).toBeNull());
  });

  it("leaves the title alone when the change is cancelled", () => {
    const onRenamed = shown();
    open();
    type(NEW);

    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(rename).not.toHaveBeenCalled();
    expect(onRenamed).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(TITLE);
    // What was typed and cancelled is not what the field opens with next.
    open();
    expect(field().value).toBe(TITLE);
  });

  it("shows a title refused as personal data as a sentence, with the text still in the field", async () => {
    rename.mockRejectedValue(
      new ApiError(422, "validation_error", "this title looks like it holds personal data", {
        field: "title",
        reason: "personal_data",
        categories: ["phone"],
      }),
    );
    const onRenamed = shown();
    open();
    type("김 대리 010-1234-5678 통화");

    fireEvent.click(save());

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "전화번호로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.",
    );
    expect(alert.textContent).not.toContain("010");
    expect(field().value).toBe("김 대리 010-1234-5678 통화");
    expect(onRenamed).not.toHaveBeenCalled();
    expect(save().disabled).toBe(false);
  });

  it("takes the refusal away once the title under it changes", async () => {
    rename.mockRejectedValue(new ApiError(403, "permission_denied", "no"));
    shown();
    open();
    type(NEW);
    fireEvent.click(save());
    expect((await screen.findByRole("alert")).textContent).toBe(
      "이 회의의 이름을 바꿀 수 없습니다.",
    );

    type(`${NEW}!`);

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not carry a refusal over to the next time the field is opened", async () => {
    rename.mockRejectedValue(new Error("Failed to fetch"));
    shown();
    open();
    type(NEW);
    fireEvent.click(save());
    expect((await screen.findByRole("alert")).textContent).toBe(
      "이름을 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.",
    );

    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    open();

    expect(screen.queryByRole("alert")).toBeNull();
  });
});
