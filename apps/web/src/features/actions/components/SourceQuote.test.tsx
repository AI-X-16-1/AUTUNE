import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SourceQuote } from "./SourceQuote";

// A promise made in the middle of a long turn is quoted as the part it was
// made from, with the whole turn one press away (the user, 2026-10-08).

const PART = "설문은 제가 금요일까지 다시 쓰겠습니다.";
const TURN = `지난주 배포는 큰 문제 없이 끝났습니다. ${PART} 그리고 출시는 다음 달로 미루기로 했습니다.`;

afterEach(cleanup);

describe("SourceQuote", () => {
  it("quotes the part and not the turn around it", () => {
    const { container } = render(<SourceQuote source={{ id: "utt_1", text: TURN, excerpt: PART }} />);

    const quote = container.querySelector("blockquote");
    expect(quote?.textContent).toContain(PART);
    expect(quote?.textContent).not.toContain("지난주 배포는");
    expect(quote?.textContent).not.toContain("미루기로 했습니다");
  });

  it("opens the whole turn on a press, and goes back to the part on the next", () => {
    const { container } = render(<SourceQuote source={{ id: "utt_1", text: TURN, excerpt: PART }} />);
    const quote = container.querySelector("blockquote");

    const open = screen.getByRole("button", { name: "전체 발화 보기" });
    expect(open.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(open);

    expect(quote?.textContent).toContain(TURN);
    const close = screen.getByRole("button", { name: "해당 부분만 보기" });
    expect(close.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(close);

    expect(quote?.textContent).not.toContain("지난주 배포는");
  });

  it.each([undefined, null])("quotes the whole utterance, with nothing to press, when no part came (%s)", (excerpt) => {
    const { container } = render(<SourceQuote source={{ id: "utt_1", text: TURN, excerpt }} />);

    expect(container.querySelector("blockquote")?.textContent).toBe(TURN);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
