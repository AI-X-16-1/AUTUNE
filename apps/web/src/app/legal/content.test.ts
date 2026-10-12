import { describe, expect, it } from "vitest";

import { REQUIRED_CONSENTS } from "./consents";
import { COPY_NOTICE, LEGAL_DOCUMENTS } from "./content";

// The documents are published only with every value the operator gives filled
// (2026-10-11). A `blank(...)` left anywhere -- a paragraph, a list, a table
// cell -- would show on the page as "확정 전".
function blanks(value: unknown, found: string[] = []): string[] {
  if (Array.isArray(value)) value.forEach((v) => blanks(v, found));
  else if (value && typeof value === "object") {
    if ("blank" in value) found.push(String((value as { blank: unknown }).blank));
    else Object.values(value).forEach((v) => blanks(v, found));
  }
  return found;
}

describe("legal documents", () => {
  it("leave no value to be filled", () => {
    expect(blanks(LEGAL_DOCUMENTS)).toEqual([]);
    expect(blanks(COPY_NOTICE)).toEqual([]);
  });

  // The sentences about a team's uploaded document (#817) came into the
  // two documents a person agrees to after `2026-10-12` had already been
  // on main with the consent page. Whoever agreed to that version agreed
  // to a text without them, so the text that has them is not `2026-10-12`
  // (the owner, 2026-10-12).
  it("are not asked for under the version that lacked the materials sentences", () => {
    for (const { document, version } of REQUIRED_CONSENTS) {
      const doc = LEGAL_DOCUMENTS.find((d) => d.id === document);
      expect(JSON.stringify(doc), document).toContain("팀이 올린 문서 자료");
      expect(version, document).not.toBe("2026-10-12");
    }
  });

  // Searching a team's uploaded documents (#817) is a purpose of its own: a
  // person who agreed to keeping them had not agreed to searching them. Both
  // documents name it, and the privacy policy says what happens to the
  // question a person types (the owner, 2026-10-12).
  it("name the search of a team's uploaded documents as a purpose", () => {
    const text = (id: string) => JSON.stringify(LEGAL_DOCUMENTS.find((d) => d.id === id));
    expect(text("terms")).toContain("팀이 올린 문서 자료의 보관 및 검색");
    expect(text("privacy")).toContain("팀 자료의 보관 및 검색");
    expect(text("privacy")).toContain(
      "입력한 질문은 같은 방법으로 가림 처리한 뒤 자료를 찾는 데에만 이용하며, 저장하거나 로그에 기록하지 않습니다.",
    );
    for (const { document, version } of REQUIRED_CONSENTS) {
      expect(version, document).not.toBe("2026-10-12.2");
    }
  });

  // The server refuses any other shape (`autune_core.consents._VERSION`),
  // and a version it refuses leaves every person at the consent page.
  it("are asked for under a version the server takes", () => {
    for (const { version } of REQUIRED_CONSENTS) {
      expect(version).toMatch(/^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$/);
    }
  });
});
