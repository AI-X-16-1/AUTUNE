import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { LEGAL_DOCUMENTS, type LegalDocument } from "./content";
import { DocumentBody } from "./LegalDocumentView";

// An amendment is announced in the document itself (the owner, 2026-10-12).
// `DocumentBody` is what both the documents page and the consent page draw, so
// a person asked to agree again reads the notice above the text they agree to.

afterEach(cleanup);

const DOC: LegalDocument = {
  id: "terms",
  title: "문서",
  amendment: [{ strong: "개정 안내" }, " 제10조 제4항을 새로 두었습니다."],
  lead: "머리말입니다.",
  sections: [{ heading: "제1조(목적)", blocks: [{ kind: "paragraph", line: "본문입니다." }] }],
};

describe("DocumentBody", () => {
  it("draws the amendment notice above the preamble and the articles", () => {
    render(<DocumentBody doc={DOC} />);

    const notice = screen.getByText("개정 안내").closest("p") as HTMLElement;
    expect(notice.textContent).toBe("개정 안내 제10조 제4항을 새로 두었습니다.");
    const order = (later: HTMLElement) =>
      notice.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING;
    expect(order(screen.getByText("머리말입니다."))).toBeTruthy();
    expect(order(screen.getByText("본문입니다."))).toBeTruthy();
  });

  it("draws none for a document that has none", () => {
    render(<DocumentBody doc={{ ...DOC, amendment: undefined }} />);

    expect(screen.queryByText("개정 안내")).toBeNull();
    expect(screen.getByText("머리말입니다.")).toBeTruthy();
  });

  it("draws the one each published document carries", () => {
    for (const doc of LEGAL_DOCUMENTS) {
      const { container, unmount } = render(<DocumentBody doc={doc} />);
      expect(container.firstElementChild?.firstElementChild?.textContent, doc.id).toMatch(
        /^개정 안내/,
      );
      unmount();
    }
  });
});
