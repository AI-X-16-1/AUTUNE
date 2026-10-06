import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GapList } from "./GapList";
import { DEMO_EXPLANATIONS, DEMO_REPORT } from "../fixtures/report-demo";

// "편집" on 해소용 질문 (#824): a member puts the question in their own words.
// A refusal keeps the editor open with what was typed.

const gaps = (DEMO_REPORT.gaps ?? []).filter((gap) => gap.suggested_question).slice(0, 1);

afterEach(cleanup);

function renderList(onSaveQuestion: (gapId: string, question: string) => Promise<string | null>) {
  render(
    <GapList
      gaps={gaps}
      explanations={DEMO_EXPLANATIONS}
      onDismiss={() => undefined}
      onSaveQuestion={onSaveQuestion}
    />,
  );
  const card = screen.getAllByRole("article")[0]!;
  fireEvent.click(within(card).getByRole("button", { name: "편집" }));
  return screen.getByLabelText("해소용 질문 편집") as HTMLTextAreaElement;
}

describe("GapList — 해소용 질문 편집", () => {
  it("saves the question in the member's words and closes", async () => {
    const onSaveQuestion = vi.fn().mockResolvedValue(null);
    const box = renderList(onSaveQuestion);

    expect(box.value).toBe(gaps[0]!.suggested_question);
    fireEvent.change(box, { target: { value: "  배포 일정은 누가 정합니까?  " } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() =>
      expect(onSaveQuestion).toHaveBeenCalledWith(gaps[0]!.id, "배포 일정은 누가 정합니까?"),
    );
    await waitFor(() => expect(screen.queryByLabelText("해소용 질문 편집")).toBeNull());
  });

  it("keeps the editor open and says why when the save is refused", async () => {
    const onSaveQuestion = vi.fn().mockResolvedValue("개인정보로 보이는 내용이 있어 저장하지 않았습니다.");
    const box = renderList(onSaveQuestion);

    fireEvent.change(box, { target: { value: "010-1234-5678로 연락할까요?" } });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect((screen.getByLabelText("해소용 질문 편집") as HTMLTextAreaElement).value).toBe(
      "010-1234-5678로 연락할까요?",
    );
  });

  it("cannot save an empty or unchanged question", () => {
    const box = renderList(vi.fn());
    const save = () => screen.getByRole("button", { name: "저장" }) as HTMLButtonElement;

    expect(save().disabled).toBe(true);
    fireEvent.change(box, { target: { value: "   " } });
    expect(save().disabled).toBe(true);
  });

  it("draws no 편집 without a way to save", () => {
    render(<GapList gaps={gaps} explanations={DEMO_EXPLANATIONS} onDismiss={() => undefined} />);

    expect(screen.queryByRole("button", { name: "편집" })).toBeNull();
  });
});
