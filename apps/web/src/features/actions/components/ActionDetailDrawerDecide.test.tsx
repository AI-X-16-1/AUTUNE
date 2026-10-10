import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionDetailDrawer } from "./ActionDetailDrawer";
import { CONFIRMED_NOTICE } from "../board";
import type { Assignable } from "../api";
import type { ActionItemRead } from "../types";

// #1183: what a person does in the item window is on its face -- 확정 and
// 거부 for an item waiting on them, a sentence instead of a score, a due date
// that reads as a field, and a speaker label that asks for a member.

const members = vi.fn((): Assignable[] | null => null);
vi.mock("../hooks/useAssignable", () => ({ useAssignable: () => members() }));
vi.mock("../hooks/useSourceUtterances", () => ({
  useSourceUtterances: () => ({ status: "loading" }),
}));

beforeEach(() => {
  // Tuesday 13 October 2026, 10:00 in Seoul.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-13T01:00:00Z"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  members.mockImplementation(() => null);
});

const WAITING = {
  id: "a",
  meeting_id: "mtg_1",
  description: "배포 일정 공유",
  status: "needs_confirmation",
  origin: "model",
  confidence: 0.82,
  is_candidate: false,
  due_date: null,
} as unknown as ActionItemRead;

describe("ActionDetailDrawer, 확정 and 거부", () => {
  function open(item: ActionItemRead = WAITING) {
    const onStatusChange = vi.fn(() => Promise.resolve());
    const onDelete = vi.fn(() => Promise.resolve());
    render(
      <ActionDetailDrawer
        item={item}
        onClose={vi.fn()}
        onStatusChange={onStatusChange}
        onDelete={onDelete}
      />,
    );
    return { onStatusChange, onDelete, header: screen.getByRole("banner") };
  }

  it("confirms with the status change the select makes, and says so", async () => {
    const { onStatusChange, header } = open();

    fireEvent.click(within(header).getByRole("button", { name: "확정" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe(CONFIRMED_NOTICE));
    expect(onStatusChange).toHaveBeenCalledExactlyOnceWith("todo");
  });

  it("rejects through the delete's confirmation, and deletes nothing before it", async () => {
    const { onDelete, header } = open();

    fireEvent.click(within(header).getByRole("button", { name: "거부" }));
    expect(onDelete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));

    await waitFor(() => expect(onDelete).toHaveBeenCalledOnce());
  });

  it("offers neither on an item already confirmed", () => {
    const { header } = open({ ...WAITING, status: "todo" } as ActionItemRead);

    expect(within(header).queryByRole("button", { name: "확정" })).toBeNull();
    expect(within(header).queryByRole("button", { name: "거부" })).toBeNull();
  });
});

describe("ActionDetailDrawer, how sure the model was", () => {
  it("says it was extracted, and keeps the score off the face", () => {
    render(<ActionDetailDrawer item={WAITING} onClose={vi.fn()} />);

    expect(screen.getByText(/자동 추출/)).toBeTruthy();
    expect(screen.queryByText(/0\.82|82%/)).toBeNull();
    expect(screen.queryByText(/확실하지 않음/)).toBeNull();
  });

  it("says 확실하지 않음 only for an item the server made a candidate", () => {
    render(
      <ActionDetailDrawer item={{ ...WAITING, is_candidate: true } as ActionItemRead} onClose={vi.fn()} />,
    );

    expect(screen.getByText(/확실하지 않음/)).toBeTruthy();
  });

  it("says nothing of it for an item a person added", () => {
    render(
      <ActionDetailDrawer item={{ ...WAITING, origin: "user" } as ActionItemRead} onClose={vi.fn()} />,
    );

    expect(screen.queryByText(/자동 추출/)).toBeNull();
  });
});

describe("ActionDetailDrawer, the due date", () => {
  function open(item: ActionItemRead = WAITING) {
    const onDueChange = vi.fn<(due: string | null) => Promise<void>>(() => Promise.resolve());
    render(<ActionDetailDrawer item={item} onClose={vi.fn()} onDueChange={onDueChange} />);
    return onDueChange;
  }

  it("saves a quick pick at once, counted in Korea", async () => {
    const onDueChange = open();

    expect(
      ["내일", "이번 주 금", "다음 주 월"].map(
        (name) => screen.getByRole("button", { name }).textContent,
      ),
    ).toEqual(["내일", "이번 주 금", "다음 주 월"]);
    fireEvent.click(screen.getByRole("button", { name: "이번 주 금" }));

    await waitFor(() => expect(onDueChange).toHaveBeenCalledExactlyOnceWith("2026-10-16"));
  });

  it("saves a typed date with its own button, and a cleared one as none", async () => {
    const onDueChange = open({ ...WAITING, due_date: "2026-10-20" } as ActionItemRead);
    const field = screen.getByLabelText("기한");
    expect((field as HTMLInputElement).value).toBe("2026-10-20");
    expect(screen.queryByRole("button", { name: "기한 저장" })).toBeNull();

    fireEvent.change(field, { target: { value: "2026-10-22" } });
    expect(onDueChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "기한 저장" }));
    await waitFor(() => expect(onDueChange).toHaveBeenCalledWith("2026-10-22"));

    fireEvent.change(field, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "기한 저장" }));
    await waitFor(() => expect(onDueChange).toHaveBeenLastCalledWith(null));
  });

  it("stays text where the date cannot be changed", () => {
    render(<ActionDetailDrawer item={WAITING} onClose={vi.fn()} />);

    expect(screen.queryByLabelText("기한")).toBeNull();
    expect(screen.queryByRole("button", { name: "내일" })).toBeNull();
  });
});

describe("ActionDetailDrawer, an assignee the meeting knew only as a speaker", () => {
  const SPOKEN = { ...WAITING, assignee_label: "SPEAKER_01" } as ActionItemRead;

  it("opens the picker on 미지정 and names the speaker above it", () => {
    members.mockImplementation(() => [{ user_id: "user_kim", name: "김민경" }]);
    render(<ActionDetailDrawer item={SPOKEN} onClose={vi.fn()} onAssigneeChange={vi.fn()} />);

    expect(screen.getByText(/화자 2\(이름 미지정\)/)).toBeTruthy();
    expect((screen.getByLabelText("담당") as HTMLSelectElement).value).toBe("");
    expect(screen.queryByLabelText("담당자 이름")).toBeNull();
  });

  it("keeps the label in the name box when there is no list to pick from", () => {
    render(<ActionDetailDrawer item={SPOKEN} onClose={vi.fn()} onAssigneeChange={vi.fn()} />);

    expect((screen.getByLabelText("담당") as HTMLInputElement).value).toBe("SPEAKER_01");
  });
});
