import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import MeetingContextPage from "./page";

// The route joins the context tab (module D's) to what the brief's agenda
// draft carries from module C (#1147): the features do not import each other.
// What matters here is only the joining.

vi.mock("@/features/context", () => ({
  ContextTab: ({
    meetingId,
    agendaSources,
  }: {
    meetingId: string;
    agendaSources?: readonly { label: string }[];
  }) => (
    <div>
      <output>{meetingId}</output>
      <ol>
        {agendaSources?.map((source) => (
          <li key={source.label}>{source.label}</li>
        ))}
      </ol>
    </div>
  ),
}));
vi.mock("@/features/gap", () => ({
  EARLIER_GAPS_AGENDA: { label: "C's gaps of the earlier meeting", lines: () => Promise.resolve([]) },
}));

afterEach(cleanup);

describe("the 컨텍스트 route", () => {
  it("shows the tab for the meeting in the path", async () => {
    render(await MeetingContextPage({ params: Promise.resolve({ meetingId: "mtg_7" }) }));

    expect(screen.getByRole("status").textContent).toBe("mtg_7");
  });

  it("hands the tab what the agenda draft carries: the earlier meeting's open gaps", async () => {
    render(await MeetingContextPage({ params: Promise.resolve({ meetingId: "mtg_7" }) }));

    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "C's gaps of the earlier meeting",
    ]);
  });
});
