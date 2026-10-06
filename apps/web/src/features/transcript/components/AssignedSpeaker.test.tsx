import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssignedSpeaker } from "./AssignedSpeaker";

// Undoing a wrong assignment. Nothing is sent until the second press, and the
// question says the voice learned from this meeting goes too.

afterEach(cleanup);

describe("AssignedSpeaker", () => {
  it("shows who the speaker is put to", () => {
    render(<AssignedSpeaker speaker="화자 1" name="김민경" onUnassign={vi.fn()} />);

    expect(screen.getByText("화자 1 · 김민경")).toBeTruthy();
  });

  it("asks before unassigning, then unassigns", () => {
    const onUnassign = vi.fn();
    render(<AssignedSpeaker speaker="화자 1" name="김민경" onUnassign={onUnassign} />);

    fireEvent.click(screen.getByRole("button", { name: "지정 해제" }));
    expect(onUnassign).not.toHaveBeenCalled();
    expect(screen.getByRole("status").textContent).toContain("이 회의에서 익힌 김민경의 목소리도 지워집니다");

    fireEvent.click(screen.getByRole("button", { name: "해제하기" }));
    expect(onUnassign).toHaveBeenCalledTimes(1);
  });

  it("sends nothing when the question is backed out of", () => {
    const onUnassign = vi.fn();
    render(<AssignedSpeaker speaker="화자 1" name="김민경" onUnassign={onUnassign} />);

    fireEvent.click(screen.getByRole("button", { name: "지정 해제" }));
    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(onUnassign).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "지정 해제" })).toBeTruthy();
  });

  it("disables the controls while a write is in flight", () => {
    render(<AssignedSpeaker speaker="화자 1" name="김민경" pending onUnassign={vi.fn()} />);

    expect((screen.getByRole("button", { name: "지정 해제" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("names a person no longer on the team as such", () => {
    render(<AssignedSpeaker speaker="화자 1" name={null} onUnassign={vi.fn()} />);

    expect(screen.getByText("화자 1 · 팀에 없는 사람")).toBeTruthy();
  });
});
