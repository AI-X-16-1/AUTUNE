import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api/client";

import { renameRefusal, titleRefusal } from "./titleRefusal";

// #1161: a title is screened when it is saved. The refusal says which kinds
// of value were read and never the value; the sentence says what to take out.

const refused = (categories: unknown) =>
  new ApiError(422, "validation_error", "this title looks like it holds personal data", {
    field: "title",
    reason: "personal_data",
    categories,
  });

describe("titleRefusal", () => {
  it("names the kinds of value the server read", () => {
    expect(titleRefusal(refused(["phone"]))).toBe(
      "전화번호로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.",
    );
    expect(titleRefusal(refused(["phone", "email"]))).toContain("전화번호, 이메일 주소로 보이는");
  });

  it("says 개인정보 for a kind it has no name for, and for no kind at all", () => {
    expect(titleRefusal(refused(["something_new"]))).toContain("개인정보로 보이는 값");
    expect(titleRefusal(refused([]))).toContain("개인정보로 보이는 값");
    expect(titleRefusal(refused(undefined))).toContain("개인정보로 보이는 값");
  });

  it("is null for any other failure", () => {
    expect(titleRefusal(new ApiError(422, "validation_error", "x", { field: "title" }))).toBeNull();
    expect(
      titleRefusal(new ApiError(409, "conflict", "x", { reason: "personal_data" })),
    ).toBeNull();
    expect(titleRefusal(new Error("network"))).toBeNull();
    expect(titleRefusal(null)).toBeNull();
  });
});

describe("renameRefusal", () => {
  it("is the personal-data sentence when that is why", () => {
    expect(renameRefusal(refused(["email"]))).toContain("이메일 주소로 보이는 값");
  });

  it("tells a title of no use from a meeting that cannot be renamed", () => {
    expect(renameRefusal(new ApiError(422, "validation_error", "x", { field: "title" }))).toBe(
      "이름은 1자 이상 400자 이하로 적어 주세요.",
    );
    expect(renameRefusal(new ApiError(403, "permission_denied", "x"))).toBe(
      "이 회의의 이름을 바꿀 수 없습니다.",
    );
    expect(renameRefusal(new ApiError(404, "not_found", "x"))).toBe("회의를 찾을 수 없습니다.");
  });

  it("never shows the server's own message", () => {
    for (const cause of [
      new ApiError(500, "internal_error", "boom at line 3"),
      new Error("Failed to fetch"),
      new TypeError("Cannot read properties of undefined"),
    ]) {
      expect(renameRefusal(cause)).toBe("이름을 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.");
    }
  });
});
