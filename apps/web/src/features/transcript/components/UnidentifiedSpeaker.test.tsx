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
