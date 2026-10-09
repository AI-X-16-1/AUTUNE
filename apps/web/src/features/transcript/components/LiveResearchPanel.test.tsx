import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { LiveResearchDocument } from "../types";
import { LiveResearchPanel } from "./LiveResearchPanel";

afterEach(cleanup);

const DONE: LiveResearchDocument = {
  id: "alr_1",
  origin: "auto",
  status: "done",
  question: "지난달 가격 정책 결정",
  body: "가격 정책\n- 9월 10일 회의에서 월 구독으로 정했습니다",
  web_sources: [{ title: "pricing.example.com", url: "https://a.test/1" }],
  meeting_sources: [{ meeting_id: "mtg_9", title: "2026-09-10 가격 회의" }],
  created_at: "2026-10-09T01:00:00Z",
};

describe("LiveResearchPanel", () => {
  it("draws the title, the lines and both kinds of source", () => {
    render(<LiveResearchPanel docs={[DONE]} />);

    expect(screen.getByText("가격 정책")).toBeTruthy();
    expect(screen.getByText("9월 10일 회의에서 월 구독으로 정했습니다")).toBeTruthy();
    expect(screen.getByText("2026-09-10 가격 회의")).toBeTruthy();
    const link = screen.getByRole("link", { name: "pricing.example.com" });
    expect(link.getAttribute("href")).toBe("https://a.test/1");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.getByText("자동")).toBeTruthy();
  });

  it("says a running one is being researched and a failed one could not be", () => {
    render(
      <LiveResearchPanel
        docs={[
          { ...DONE, id: "a", status: "running", body: null },
          { ...DONE, id: "b", status: "failed", body: null, origin: "manual" },
        ]}
      />,
    );

    expect(screen.getByText("조사 중…")).toBeTruthy();
    expect(screen.getByText("조사하지 못했습니다")).toBeTruthy();
    expect(screen.getByText("요청")).toBeTruthy();
  });

  it("explains itself when there is nothing yet", () => {
    render(<LiveResearchPanel docs={[]} />);

    expect(screen.getByText(/확인이 필요한 질문이 나오면/)).toBeTruthy();
  });
});
