import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionDetailDrawer } from "./ActionDetailDrawer";
import { CONFIRMED_NOTICE } from "../board";
import type { ActionItemRead } from "../types";

// What a person sees and does first in the detail window (#1183): confirm or
// reject an item, read how sure the model was as a sentence, set a due date
// like the other fields, and read a voice's label as a voice.

vi.mock("../hooks/useSourceUtterances", () => ({
  useSourceUtterances: () => ({ status: "loading" }),
}));
vi.mock("../hooks/useAssignable", () => ({ useAssignable: () => null }));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const WAITING = {
  id: "a",
  meeting_id: "mtg_1",
  description: "배포 일정 공유",
  status: "needs_confirmation",
  confidence: 0.82,
  is_candidate: false,
} as ActionItemRead;

describe("ActionDetailDrawer, 확정 and 거부", () => {
  it("puts 확정 and 거부 on an item that waits for confirmation, and only there", () => {
    render(<ActionDetailDrawer item={WAITING} onClose={vi.fn()} onStatusChange={vi.fn()} onDelete={vi.fn()} />);
    const group = screen.getByRole("group", { name: "이 항목 처리" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["확정", "거부"]);
    cleanup();

    render(
      <ActionDetailDrawer
        item={{ ...WAITING, status: "todo" } as ActionItemRead}
        onClose={vi.fn()}
        onStatusChange={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByRole("group", { name: "이 항목 처리" })).toBeNull();
  });

  it("confirms into 진행 전 and says so in the board's words", async () => {
    const onStatusChange = vi.fn(() => Promise.resolve());
    render(<ActionDetailDrawer item={WAITING} onClose={vi.fn()} onStatusChange={onStatusChange} />);

    fireEvent.click(screen.getByRole("button", { name: "확정" }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toBe(CONFIRMED_NOTICE));
    expect(onStatusChange).toHaveBeenCalledExactlyOnceWith("todo");
  });

  it("says the item stays unconfirmed when the confirmation was refused", async () => {
    render(
      <ActionDetailDrawer
        item={WAITING}
        onClose={vi.fn()}
        onStatusChange={vi.fn(() => Promise.reject(new Error("refused")))}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "확정" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("asks before 거부 removes anything: a rejection is a deletion, not a status", () => {
    const onDelete = vi.fn(() => Promise.resolve());
    const onStatusChange = vi.fn();
    render(<ActionDetailDrawer item={WAITING} onClose={vi.fn()} onStatusChange={onStatusChange} onDelete={onDelete} />);

    fireEvent.click(screen.getByRole("button", { name: "거부" }));

    expect(onDelete).not.toHaveBeenCalled();
    expect(onStatusChange).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "할 일 삭제" })).toBeTruthy();
  });

  it("deletes after the confirmation", async () => {
    const onDelete = vi.fn(() => Promise.resolve());
    const onClose = vi.fn();
    render(<ActionDetailDrawer item={WAITING} onClose={onClose} onStatusChange={vi.fn()} onDelete={onDelete} />);

    fireEvent.click(screen.getByRole("button", { name: "거부" }));
    const dialog = screen.getByRole("dialog", { name: "할 일 삭제" });
    fireEvent.click(within(dialog).getByRole("button", { name: "삭제" }));

    await waitFor(() => expect(onDelete).toHaveBeenCalledOnce());
  });
});

describe("ActionDetailDrawer, how sure the model was", () => {
  it("does not print the number", () => {
    render(<ActionDetailDrawer item={WAITING} onClose={vi.fn()} />);

    expect(screen.queryByText("0.82")).toBeNull();
    expect(screen.queryByText(/확실하지 않음/)).toBeNull();
  });

  it("says a candidate is not certain, in words", () => {
    render(
      <ActionDetailDrawer
        item={{ ...WAITING, confidence: 0.31, is_candidate: true } as ActionItemRead}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByText("0.31")).toBeNull();
    expect(within(screen.getByRole("banner")).getByText(/확실하지 않음/)).toBeTruthy();
  });
});

describe("ActionDetailDrawer, the due date", () => {
  it("is plain text while nothing can change it, as before", () => {
    render(
      <ActionDetailDrawer
        item={{ ...WAITING, due_date: "2026-10-13" } as ActionItemRead}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText("기한")).toBeNull();
    expect(screen.getByText(/10월 13일 화/)).toBeTruthy();
  });

  it("is a date field that saves the day picked", async () => {
    const onDueChange = vi.fn(() => Promise.resolve());
    render(
      <ActionDetailDrawer
        item={{ ...WAITING, due_date: "2026-10-13" } as ActionItemRead}
        onClose={vi.fn()}
        onDueChange={onDueChange}
      />,
    );
    const field = screen.getByLabelText("기한") as HTMLInputElement;
    expect(field.type).toBe("date");
    expect(field.value).toBe("2026-10-13");

    fireEvent.change(field, { target: { value: "2026-10-20" } });

    await waitFor(() => expect(onDueChange).toHaveBeenCalledExactlyOnceWith("2026-10-20"));
  });

  it("offers tomorrow, Friday and next Monday, and 기한 없애기 clears it", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 9, 13, 10, 0, 0)); // Tue
    const onDueChange = vi.fn(() => Promise.resolve());
    render(
      <ActionDetailDrawer
        item={{ ...WAITING, due_date: "2026-10-30" } as ActionItemRead}
        onClose={vi.fn()}
        onDueChange={onDueChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "이번 주 금" }));
    await waitFor(() => expect(onDueChange).toHaveBeenLastCalledWith("2026-10-16"));

    fireEvent.click(screen.getByRole("button", { name: "다음 주 월" }));
    await waitFor(() => expect(onDueChange).toHaveBeenLastCalledWith("2026-10-19"));

    fireEvent.click(screen.getByRole("button", { name: "기한 없애기" }));
    await waitFor(() => expect(onDueChange).toHaveBeenLastCalledWith(null));
  });

  it("says the due date was not changed when the save was refused", async () => {
    render(
      <ActionDetailDrawer
        item={WAITING}
        onClose={vi.fn()}
        onDueChange={vi.fn(() => Promise.reject(new Error("refused")))}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "내일" }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/기한을 바꾸지 못했습니다/));
  });
});

describe("ActionDetailDrawer, an assignee that is a voice", () => {
  it("reads 화자 2(이름 미지정), not SPEAKER_01", () => {
    render(
      <ActionDetailDrawer
        item={{ ...WAITING, assignee_label: "SPEAKER_01" } as ActionItemRead}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByText("SPEAKER_01")).toBeNull();
    expect(screen.getByText("화자 2(이름 미지정)")).toBeTruthy();
  });

  it("tells the person what to do when the assignee can be changed", () => {
    render(
      <ActionDetailDrawer
        item={{ ...WAITING, assignee_label: "SPEAKER_01" } as ActionItemRead}
        onClose={vi.fn()}
        onAssigneeChange={vi.fn()}
      />,
    );

    expect(screen.getByText(/화자 2\(이름 미지정\)입니다\. 팀원 목록에서 고르면/)).toBeTruthy();
  });

  it("shows a typed name as it was typed", () => {
    render(
      <ActionDetailDrawer
        item={{ ...WAITING, assignee_label: "박지영" } as ActionItemRead}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("박지영")).toBeTruthy();
  });
});
