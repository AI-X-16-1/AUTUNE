import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { ResearchDocument } from "../types";
import { ResearchCard } from "./ResearchCard";

// The 2026-10-05 rehearsal showed the card printing "##" and "-" as they are.

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const DOC: ResearchDocument = {
  id: "rdoc_1",
  meeting_id: "mtg_1",
  status: "approved",
  body: "## 제기된 질문\n- 결제 테스트는 언제 끝나요?\n\n## 과거 회의에서 나온 것\n- 찾은 내용이 없습니다\n\n## 아직 모르는 것\n- 연락처 010-****-5678",
  created_at: "2026-10-05T00:00:00Z",
  decided_at: "2026-10-05T01:00:00Z",
};

describe("ResearchCard", () => {
  it("draws headings and lists instead of their Markdown", async () => {
    vi.spyOn(api, "getResearch").mockResolvedValue([DOC]);

    render(<ResearchCard meetingId="mtg_1" teamId="team_1" />);

    expect(
      await screen.findByRole("heading", { name: "제기된 질문" }),
    ).toBeTruthy();
    expect(
      screen.getAllByRole("listitem").map((li) => li.textContent),
    ).toContain("결제 테스트는 언제 끝나요?");
    expect(screen.queryByText(/##/)).toBeNull();
  });

  it("marks masked spans the way the transcript does", async () => {
    vi.spyOn(api, "getResearch").mockResolvedValue([DOC]);

    const { container } = render(
      <ResearchCard meetingId="mtg_1" teamId="team_1" />,
    );

    await screen.findByRole("heading", { name: "아직 모르는 것" });
    expect(
      [...container.querySelectorAll("span[title]")].map((t) => t.textContent),
    ).toContain("010-****-5678");
  });
});
