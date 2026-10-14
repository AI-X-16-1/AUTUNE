import { cleanup, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IntegrationSettingsScreen } from "./IntegrationSettingsScreen";
import { JiraConnect } from "./JiraConnect";
import { NotionConnect } from "./NotionConnect";
import { SlackConnect } from "./SlackConnect";

// 설정 › 연동 told apart (#1183): what is connected, which messages come and
// when, and the projects a team registers -- and a connection that is not made
// says so, in the place where "연결됨" would be.

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("@/shared/api/auth", () => ({
  getSession: () =>
    Promise.resolve({
      id: "u",
      email: "me@example.com",
      display_name: "Me",
      teams: [{ id: "team_a", name: "가 팀" }],
    }),
  getSlackConnection: () => Promise.resolve({ connected: false }),
  getJiraConnection: () => Promise.resolve({ connected: false }),
  getNotionConnection: () => Promise.resolve({ connected: false }),
  slackConnectUrl: () => "/connect",
  jiraConnectUrl: () => "/connect",
  notionConnectUrl: () => "/connect",
  disconnectSlack: vi.fn(),
  disconnectJira: vi.fn(),
  disconnectNotion: vi.fn(),
}));
vi.mock("../api", () => ({
  backfillJira: vi.fn(),
  getNotionSetup: () => Promise.resolve({ connected: false }),
  setUpNotion: vi.fn(),
}));
vi.mock("./CalendarConnect", () => ({ CalendarConnect: () => <div>calendar</div> }));
vi.mock("./DueReminderSetting", () => ({ DueReminderSetting: () => <div>due reminder</div> }));
vi.mock("./NotificationPauseSetting", () => ({
  NotificationPauseSetting: () => <div>vacation</div>,
}));
vi.mock("./ProjectSettings", () => ({ ProjectSettings: () => <div>projects form</div> }));
vi.mock("./SlackMeConnect", () => ({ SlackMeConnect: () => null }));
vi.mock("./SyncLogDrawer", () => ({ SyncLogDrawer: () => null }));

afterEach(cleanup);

describe("IntegrationSettingsScreen, the three sections", () => {
  it("keeps connections, notifications and projects apart, in that order", async () => {
    render(<IntegrationSettingsScreen />);
    await screen.findByText("projects form");

    const sections = screen
      .getAllByRole("region")
      .map((section) => section.getAttribute("aria-label"));
    expect(sections).toEqual(["연결", "알림", "프로젝트"]);

    const connections = screen.getByRole("region", { name: "연결" });
    expect(within(connections).getByText("calendar")).toBeTruthy();
    expect(within(connections).queryByText("due reminder")).toBeNull();
    expect(within(connections).queryByText("vacation")).toBeNull();

    const notifications = screen.getByRole("region", { name: "알림" });
    expect(within(notifications).getByText("due reminder")).toBeTruthy();
    expect(within(notifications).getByText("vacation")).toBeTruthy();

    const projects = screen.getByRole("region", { name: "프로젝트" });
    expect(within(projects).getByText("projects form")).toBeTruthy();
  });

  it("keeps the privacy notice with the connections", async () => {
    render(<IntegrationSettingsScreen />);
    await screen.findByText("projects form");

    const connections = screen.getByRole("region", { name: "연결" });
    expect(within(connections).getByRole("link", { name: "개인정보 처리방침" })).toBeTruthy();
  });
});

describe("a connection that is not made", () => {
  it("says so for Slack, Jira and Notion, beside the button that makes it", async () => {
    render(
      <>
        <SlackConnect teamId="team_a" />
        <JiraConnect teamId="team_a" />
        <NotionConnect teamId="team_a" />
      </>,
    );

    expect(await screen.findByText("Slack 연결 안 됨")).toBeTruthy();
    expect(await screen.findByText("Jira 연결 안 됨")).toBeTruthy();
    expect(await screen.findByText("Notion 연결 안 됨")).toBeTruthy();
    expect(screen.getByRole("button", { name: "팀 Slack 연결" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "팀 Jira 연결" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "팀 Notion 연결" })).toBeTruthy();
  });
});
