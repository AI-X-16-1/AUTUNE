import { describe, expect, it } from "vitest";

import { AMENDED_ON, REQUIRED_CONSENTS } from "./consents";
import { AMENDMENT_DAYS, COPY_NOTICE, LEGAL_DOCUMENTS } from "./content";

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
});

// The amendment for a team's uploaded documents (#817; the owner, 2026-10-12).
// Its two days are empty until the day the page goes on the site is known, and
// "leave no value to be filled" above is what refuses a merge until then. The
// two checks on the days themselves have nothing to read before that, so they
// are skipped -- shown as skipped, not as passed.
const unset = AMENDMENT_DAYS.announced === "" || AMENDMENT_DAYS.applies === "";
const text = (value: unknown): string =>
  Array.isArray(value)
    ? value.map(text).join("")
    : typeof value === "string"
      ? value
      : value && typeof value === "object" && "strong" in value
        ? String((value as { strong: unknown }).strong)
        : "";

describe("the materials amendment", () => {
  it("is announced at the head of all three documents", () => {
    for (const doc of LEGAL_DOCUMENTS) {
      expect(text(doc.amendment), doc.id).toMatch(/^개정 안내 \(.* 공지, .* 시행\)/);
    }
  });

  it("names every article it changed, in the two documents a person agrees to", () => {
    const of = (id: string) => text(LEGAL_DOCUMENTS.find((d) => d.id === id)?.amendment);
    expect(of("privacy")).toContain(
      "제1조, 제2조 제1항 및 제5항, 제3조 제2항, 제4조 제2항, 제8조 제2항",
    );
    for (const article of ["제2조", "제8조", "제10조 제4항", "제15조 제2항 제1호"]) {
      expect(of("terms")).toContain(article);
    }
  });

  it("does not make silence count as agreement", () => {
    // Left out on the owner's word: both versions move, so each member agrees
    // in person and nobody's silence is relied on (약관 제3조 ④).
    expect(text(LEGAL_DOCUMENTS.map((d) => d.amendment))).not.toMatch(/동의한 것으로/);
  });

  it("keeps the first text's version for as long as its day is not set", () => {
    const expected = AMENDED_ON === "" ? "2026-10-12" : AMENDED_ON;
    expect(REQUIRED_CONSENTS.map((c) => c.version)).toEqual([expected, expected]);
    expect(AMENDMENT_DAYS.applies).toBe(AMENDED_ON);
  });

  it.skipIf(unset)("applies at least seven days after it is announced", () => {
    const at = (iso: string) => {
      expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      return Date.parse(`${iso}T00:00:00Z`);
    };
    const days = (at(AMENDMENT_DAYS.applies) - at(AMENDMENT_DAYS.announced)) / 86_400_000;
    expect(days).toBeGreaterThanOrEqual(7);
  });

  it.skipIf(unset)("is the version both consents ask for", () => {
    expect(REQUIRED_CONSENTS.map((c) => c.version)).toEqual([
      AMENDMENT_DAYS.applies,
      AMENDMENT_DAYS.applies,
    ]);
  });
});
