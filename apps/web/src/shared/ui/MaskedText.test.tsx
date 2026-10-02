import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MaskedText } from "./MaskedText";

// The positive half of invariant 11 on screen (ADR 0009, decision 4): a masked
// span renders as a PiiToken, and nothing offers to reveal the original, which
// was never stored. Every screen that shows transcript text draws it through
// this component.

afterEach(cleanup);

const LINE = "연락처는 010-****-5678 이고 메일은 k***@example.com 입니다";

describe("MaskedText", () => {
  it("marks every masked run as a PiiToken and leaves the rest as text", () => {
    const { container } = render(<MaskedText>{LINE}</MaskedText>);

    const tokens = [...container.querySelectorAll("span[title]")];
    expect(tokens.map((t) => t.textContent)).toEqual(["010-****-5678", "k***@example.com"]);
    expect(tokens.every((t) => t.getAttribute("title")?.includes("원문 미저장"))).toBe(true);
    expect(container.textContent).toBe(LINE);
  });

  it("offers no control that could reveal an original", () => {
    const { container } = render(<MaskedText>{LINE}</MaskedText>);

    expect(container.querySelectorAll("button, a, input, select, [role='button']")).toHaveLength(
      0,
    );
  });

  it("draws an unmasked line as plain text", () => {
    const { container } = render(<MaskedText>{"마스킹할 것이 없는 문장"}</MaskedText>);

    expect(container.querySelectorAll("span[title]")).toHaveLength(0);
    expect(container.textContent).toBe("마스킹할 것이 없는 문장");
  });
});
