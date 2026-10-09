import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../api";
import type { MeetingState } from "../hooks/useMeeting";
import type {
  LiveResearchDocument,
  MeetingDetail,
  MeetingStatus,
} from "../types";
import { StoredMeetingScreen } from "./StoredMeetingScreen";

let state: MeetingState = { status: "loading" };
vi.mock("../hooks/useMeeting", () => ({ useMeeting: () => state }));
// Each of these fetches on its own; what is under test is where the list goes.
vi.mock("./ProcessingStages", () => ({ ProcessingStages: () => null }));
vi.mock("./ResearchCard", () => ({ ResearchCard: () => null }));
vi.mock("./StoredTranscript", () => ({ StoredTranscript: () => null }));

afterEach(cleanup);

const DONE: LiveResearchDocument = {
  id: "alr_1",
  origin: "auto",
  status: "done",
  question: "지난달 가격 정책 결정",
  body: "가격 정책\n- 9월 10일 회의에서 월 구독으로 정했습니다",
  web_sources: [],
  meeting_sources: [],
  created_at: "2026-10-09T01:00:00Z",
};

function meeting(status: MeetingStatus): MeetingDetail {
  return {
    meeting_id: "mtg_1",
    title: "주간 회의",
    status,
    team_id: "team_1",
  } as unknown as MeetingDetail;
}

describe("StoredMeetingScreen", () => {
  it.each([
    "scheduled",
    "recording",
    "analyzing",
    "failed",
    "complete",
  ] as const)("lists the live documents of a %s meeting", async (status) => {
    vi.spyOn(api, "listLiveResearch").mockResolvedValueOnce([DONE]);
    state = { status: "ready", meeting: meeting(status) };

    render(<StoredMeetingScreen meetingId="mtg_1" />);

    expect(await screen.findByText("가격 정책")).toBeTruthy();
  });
});
