import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionDetailDrawer } from "./ActionDetailDrawer";
import { AddActionItem } from "./AddActionItem";
import type { Assignable } from "../api";
import type { ActionItemRead } from "../types";

// Choosing an assignee (the user, 2026-10-02): a member of the team, by
// account, so the item reaches that person's calendar and their Jira account
// -- or a typed name, which reaches neither. Until this the screen could only
// type a name.

const members = vi.fn((): Assignable[] | null => MEMBERS);
vi.mock("../hooks/useAssignable", () => ({ useAssignable: () => members() }));
vi.mock("../hooks/useSourceUtterances", () => ({
  useSourceUtterances: () => ({ status: "loading" }),
}));

const MEMBERS: Assignable[] = [
  { user_id: "user_kim", name: "김민경" },
  { user_id: "user_park", name: "박재경" },
];

afterEach(() => {
  cleanup();
  members.mockImplementation(() => MEMBERS);
});

describe("adding an item", () => {
  function openForm(onAdd = vi.fn(() => Promise.resolve())) {
    render(<AddActionItem meetingId="mtg_1" onAdd={onAdd} />);
    fireEvent.click(screen.getByRole("button", { name: "+ 액션 아이템 추가" }));
    fireEvent.change(screen.getByLabelText("할 일"), { target: { value: "배포 일정 공유" } });
    return onAdd;
  }
  const submit = () => fireEvent.submit(screen.getByRole("form", { name: "액션 아이템 추가" }));

  it("sends the account of the member who was picked, and no typed name", async () => {
    const onAdd = openForm();

    fireEvent.change(screen.getByLabelText("담당자"), { target: { value: "user_park" } });
    submit();

    await waitFor(() => expect(onAdd).toHaveBeenCalledOnce());
    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({ assignee_id: "user_park", assignee_label: null }),
    );
  });

  it("still takes a typed name for somebody who is not on the team, and says what that costs", async () => {
    const onAdd = openForm();

    fireEvent.change(screen.getByLabelText("담당자"), { target: { value: "__typed" } });
    fireEvent.change(screen.getByLabelText("담당자 이름"), { target: { value: " 외부 고객 " } });
    expect(screen.getByText(/캘린더와 Jira에 연결되지 않습니다/)).toBeTruthy();
    submit();

    await waitFor(() => expect(onAdd).toHaveBeenCalledOnce());
    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({ assignee_id: null, assignee_label: "외부 고객" }),
    );
  });

  it("sends nobody when nobody was chosen", async () => {
    const onAdd = openForm();

    submit();

    await waitFor(() => expect(onAdd).toHaveBeenCalledOnce());
    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({ assignee_id: null, assignee_label: null }),
    );
  });

  it("is the text box it always was when the team's list could not be read", async () => {
    members.mockImplementation(() => null);
    const onAdd = openForm();

    expect(screen.queryByRole("combobox")).toBeNull();
    fireEvent.change(screen.getByLabelText("담당자"), { target: { value: "민구" } });
    submit();

    await waitFor(() => expect(onAdd).toHaveBeenCalledOnce());
    expect(onAdd).toHaveBeenCalledWith(
      expect.objectContaining({ assignee_id: null, assignee_label: "민구" }),
    );
  });
});

describe("the detail window", () => {
  const ITEM = {
    id: "a",
    meeting_id: "mtg_1",
    description: "배포 일정 공유",
    status: "todo",
    confidence: 1,
    is_candidate: false,
    assignee_id: null,
    assignee_label: "민구",
  } as ActionItemRead;

  const assignee = () => screen.getByLabelText("담당") as HTMLSelectElement;

  function open(item: ActionItemRead, onAssigneeChange = vi.fn(() => Promise.resolve())) {
    render(<ActionDetailDrawer item={item} onClose={vi.fn()} onAssigneeChange={onAssigneeChange} />);
    return onAssigneeChange;
  }

  it("shows a typed name as typed, not as a member", () => {
    open(ITEM);

    expect(assignee().value).toBe("__typed");
    expect((screen.getByLabelText("담당자 이름") as HTMLInputElement).value).toBe("민구");
  });

  it("saves a member the moment one is picked, and drops the typed name", async () => {
    const onAssigneeChange = open(ITEM);

    fireEvent.change(assignee(), { target: { value: "user_kim" } });

    await waitFor(() =>
      expect(onAssigneeChange).toHaveBeenCalledExactlyOnceWith({
        assignee_id: "user_kim",
        assignee_label: null,
      }),
    );
  });

  it("saves a typed name only when the person says so, not on every key", async () => {
    const onAssigneeChange = open({ ...ITEM, assignee_id: "user_kim", assignee_label: null });

    fireEvent.change(assignee(), { target: { value: "__typed" } });
    fireEvent.change(screen.getByLabelText("담당자 이름"), { target: { value: "외부 고객" } });
    expect(onAssigneeChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "이름 저장" }));

    await waitFor(() =>
      expect(onAssigneeChange).toHaveBeenCalledExactlyOnceWith({
        assignee_id: null,
        assignee_label: "외부 고객",
      }),
    );
  });

  it("clears the assignee with 미지정", async () => {
    const onAssigneeChange = open({ ...ITEM, assignee_id: "user_kim", assignee_label: null });

    fireEvent.change(assignee(), { target: { value: "" } });

    await waitFor(() =>
      expect(onAssigneeChange).toHaveBeenCalledExactlyOnceWith({
        assignee_id: null,
        assignee_label: null,
      }),
    );
  });

  it("says so when the change was refused", async () => {
    open(ITEM, vi.fn(() => Promise.reject(new Error("refused"))));

    fireEvent.change(assignee(), { target: { value: "user_kim" } });

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("담당자를 바꾸지 못했습니다"),
    );
  });

  it("keeps an account assignee as it is while the team's list is not there", () => {
    // The text box would show the account as an empty name under the
    // "name only" warning, and saving from it would swap the account for a
    // typed name -- taking the item off that person's calendar (review of #737).
    members.mockImplementation(() => null);
    const onAssigneeChange = open({
      ...ITEM,
      assignee_id: "user_kim",
      assignee_label: null,
      assignee_name: "김민경",
    });

    expect(screen.getByText("김민경")).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("combobox", { name: "담당" })).toBeNull();
    expect(screen.queryByText(/캘린더와 Jira에 연결되지 않습니다/)).toBeNull();
    expect(screen.queryByRole("button", { name: "이름 저장" })).toBeNull();
    expect(onAssigneeChange).not.toHaveBeenCalled();
  });

  it("ties the note about a typed name to the box it is about", () => {
    open(ITEM);

    const box = screen.getByLabelText("담당자 이름");
    const note = document.getElementById(box.getAttribute("aria-describedby") ?? "");
    expect(note?.textContent).toContain("캘린더와 Jira에 연결되지 않습니다");
  });

  it("only shows the assignee where nothing can change it", () => {
    render(<ActionDetailDrawer item={ITEM} onClose={vi.fn()} />);

    expect(screen.queryByLabelText("담당")).toBeNull();
    expect(screen.getByText("민구")).toBeTruthy();
  });
});
