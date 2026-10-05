import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionDetailDrawer } from "./ActionDetailDrawer";
import type { ActionItemRead } from "../types";

// The drawer's link to the reader's own Slack confirmation DM (#680). The
// server sends it only to the person the DM went to; the drawer shows what
// it is given.

const detail = vi.fn();
vi.mock("../hooks/useSourceUtterances", () => ({
  useSourceUtterances: () => detail(),
}));

afterEach(() => {
  cleanup();
  detail.mockReset();
});

const ITEM = {
  id: "a",
  meeting_id: "mtg_1",
  description: "배포 일정 공유",
  status: "todo",
  confidence: 0.92,
  is_candidate: false,
} as ActionItemRead;

const LOADED = {
  sources: [],
  context: [],
  related: [],
  loading: false,
  error: null,
  history: [],
};

describe("ActionDetailDrawer, the confirmation DM", () => {
  it("links the reader to their own DM", () => {
    detail.mockReturnValue({
      ...LOADED,
      dmUrl: "https://slack.com/app_redirect?team=T1&channel=D1",
    });

    render(<ActionDetailDrawer item={ITEM} onClose={vi.fn()} />);

    expect(screen.getByText("Slack 확인 DM")).toBeTruthy();
    const link = screen.getByRole("link", { name: "열기" });
    expect(link.getAttribute("href")).toBe(
      "https://slack.com/app_redirect?team=T1&channel=D1",
    );
  });

  it("shows no Slack line when there is no DM for this reader", () => {
    detail.mockReturnValue({ ...LOADED, dmUrl: null });

    render(<ActionDetailDrawer item={ITEM} onClose={vi.fn()} />);

    expect(screen.queryByText("Slack 확인 DM")).toBeNull();
  });
});
