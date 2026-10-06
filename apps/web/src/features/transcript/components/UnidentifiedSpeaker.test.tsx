import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UnidentifiedSpeaker } from "./UnidentifiedSpeaker";

// Typing a name for a voice with no account on the team. The name belongs to
// this meeting only: no person is attached, and nothing is sent until 저장.

afterEach(cleanup);

function open() {
  fireEvent.click(screen.getByRole("button", { name: "직접 입력" }));
}

describe("UnidentifiedSpeaker direct input", () => {
  it("sends the trimmed name only when 저장 is clicked", () => {
    const onName = vi.fn();
    render(<UnidentifiedSpeaker speaker="화자 2" members={[]} onName={onName} />);

    open();
    fireEvent.change(screen.getByLabelText("화자 2 이름 직접 입력"), {
      target: { value: "  외부 디자이너 " },
    });
    expect(onName).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "저장" }));
    expect(onName).toHaveBeenCalledWith("외부 디자이너");
  });

  it("keeps 저장 disabled while the name is blank", () => {
    render(<UnidentifiedSpeaker speaker="화자 2" members={[]} onName={vi.fn()} />);

    open();
    fireEvent.change(screen.getByLabelText("화자 2 이름 직접 입력"), { target: { value: "   " } });

    expect((screen.getByRole("button", { name: "저장" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("caps the name at 50 characters, the server's limit", () => {
    render(<UnidentifiedSpeaker speaker="화자 2" members={[]} onName={vi.fn()} />);

    open();

    expect((screen.getByLabelText("화자 2 이름 직접 입력") as HTMLInputElement).maxLength).toBe(50);
  });

  it("says a typed name is shown in this meeting only", () => {
    render(
      <UnidentifiedSpeaker speaker="화자 2" displayName="외부 디자이너" members={[]} onName={vi.fn()} />,
    );

    expect(screen.getByText(/외부 디자이너/).textContent).toContain("이 회의에서만");
  });

  it("closes the input on 취소 without sending anything", () => {
    const onName = vi.fn();
    render(<UnidentifiedSpeaker speaker="화자 2" members={[]} onName={onName} />);

    open();
    fireEvent.click(screen.getByRole("button", { name: "취소" }));

    expect(screen.queryByLabelText("화자 2 이름 직접 입력")).toBeNull();
    expect(onName).not.toHaveBeenCalled();
  });
});

// One voice split into two labels by diarization: the person already put to
// 화자 1 stays pickable for 화자 3, last and marked, behind one confirmation.
// Without that path their 화자 3 lines are tied to nobody, and "내 발화 삭제"
// cannot find them (#912 review).
describe("UnidentifiedSpeaker picking someone already assigned", () => {
  const members = [
    { user_id: "usr_2", name: "강민구" },
    { user_id: "usr_1", name: "김민경", assignedTo: "화자 1" },
  ];

  function pick(userId: string) {
    fireEvent.change(screen.getByLabelText("화자 3 화자 지정"), { target: { value: userId } });
    fireEvent.click(screen.getByRole("button", { name: "지정" }));
  }

  it("marks an assigned member in the list", () => {
    render(<UnidentifiedSpeaker speaker="화자 3" members={members} onAssign={vi.fn()} />);

    expect(screen.getByRole("option", { name: "김민경 (화자 1로 지정됨)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "강민구" })).toBeTruthy();
  });

  it("asks once before putting an assigned member to a second speaker", () => {
    const onAssign = vi.fn();
    render(<UnidentifiedSpeaker speaker="화자 3" members={members} onAssign={onAssign} />);

    pick("usr_1");
    expect(onAssign).not.toHaveBeenCalled();
    // Announced: it appears beside the button, where a screen reader is not
    // reading (#912 review).
    expect(screen.getByRole("status").textContent).toMatch(/같은 사람의 목소리가 둘로 나뉜 경우에만/);

    fireEvent.click(screen.getByRole("button", { name: "그래도 지정" }));
    expect(onAssign).toHaveBeenCalledWith("usr_1");
  });

  it("sends nothing when the confirmation is backed out of", () => {
    const onAssign = vi.fn();
    render(<UnidentifiedSpeaker speaker="화자 3" members={members} onAssign={onAssign} />);

    pick("usr_1");
    fireEvent.click(screen.getByRole("button", { name: "다시 고르기" }));

    expect(onAssign).not.toHaveBeenCalled();
    expect(screen.queryByText(/같은 사람의 목소리가 둘로 나뉜 경우에만/)).toBeNull();
  });

  it("assigns an unassigned member without asking", () => {
    const onAssign = vi.fn();
    render(<UnidentifiedSpeaker speaker="화자 3" members={members} onAssign={onAssign} />);

    pick("usr_2");

    expect(onAssign).toHaveBeenCalledWith("usr_2");
  });
});
