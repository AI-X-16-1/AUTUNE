import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api/client";

import { typedTextRefusal } from "./refusal";

const refused = (categories: unknown) =>
  new ApiError(422, "validation_error", "this text looks like it holds personal data", {
    field: "description",
    reason: "personal_data",
    categories,
  });

describe("typedTextRefusal", () => {
  it("says which kind of value to take out", () => {
    expect(typedTextRefusal(refused(["phone"]))).toBe(
      "전화번호로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.",
    );
  });

  it("names every kind the server read, once each", () => {
    expect(typedTextRefusal(refused(["phone", "email", "phone"]))).toBe(
      "전화번호, 이메일 주소로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.",
    );
  });

  it.each(["rrn", "card", "account", "digits"])("has a name for %s", (kind) => {
    const said = typedTextRefusal(refused([kind]));
    expect(said).not.toBeNull();
    expect(said).not.toContain(kind);
    expect(said).not.toContain("개인정보");
  });

  it("still says what happened for a kind it has no name for, or none at all", () => {
    const plain =
      "개인정보로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.";
    expect(typedTextRefusal(refused(["something_new"]))).toBe(plain);
    expect(typedTextRefusal(refused(undefined))).toBe(plain);
  });

  it("never shows the server's own sentence", () => {
    expect(typedTextRefusal(refused(["phone"]))).not.toContain("personal data");
  });

  it("is null for any other failure, so the screen keeps its own words", () => {
    expect(typedTextRefusal(new ApiError(422, "validation_error", "too long", { field: "title" }))).toBeNull();
    expect(
      typedTextRefusal(new ApiError(409, "conflict", "taken", { reason: "personal_data" })),
    ).toBeNull();
    expect(typedTextRefusal(new Error("network"))).toBeNull();
    expect(typedTextRefusal(undefined)).toBeNull();
  });
});
