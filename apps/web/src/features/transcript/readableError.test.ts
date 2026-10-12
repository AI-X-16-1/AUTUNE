import { describe, expect, it } from "vitest";

import { ApiError } from "@/shared/api/client";

import { readableError } from "./readableError";

const FALLBACK = "전사를 불러오지 못했습니다.";

describe("readableError", () => {
  it("never shows a server failure's status text", () => {
    const caught = new ApiError(500, "unknown", "Internal Server Error");
    expect(readableError(caught, FALLBACK)).toBe(FALLBACK);
  });

  it("does not show a 5xx even when it is worded in Korean", () => {
    const caught = new ApiError(503, "unavailable", "서버 내부 오류");
    expect(readableError(caught, FALLBACK)).toBe(FALLBACK);
  });

  it("does not show an English refusal that may name an internal id", () => {
    const caught = new ApiError(404, "not_found", "meeting mtg_a1 not found");
    expect(readableError(caught, FALLBACK)).toBe(FALLBACK);
  });

  it("does not show a browser's network error", () => {
    expect(readableError(new TypeError("Failed to fetch"), FALLBACK)).toBe(FALLBACK);
  });

  it("keeps a sentence written in Korean for the reader", () => {
    const caught = new ApiError(409, "conflict", "이미 끝난 회의입니다.");
    expect(readableError(caught, FALLBACK)).toBe("이미 끝난 회의입니다.");
    expect(readableError(new Error("업로드할 녹음이 없습니다."), FALLBACK)).toBe(
      "업로드할 녹음이 없습니다.",
    );
  });

  it("reads the fallback for anything that is not an Error", () => {
    expect(readableError("boom", FALLBACK)).toBe(FALLBACK);
    expect(readableError(undefined, FALLBACK)).toBe(FALLBACK);
  });
});
