import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IntegrationSettingsScreen } from "./IntegrationSettingsScreen";

// 설정 › 연동 opens on the team chosen elsewhere in the app, follows a new
// choice while it is open, and tells the route when a team is picked here
// (the user, 2026-10-06). The route joins it to the feature that keeps the
// choice; here that is two props.

const session = vi.fn();
vi.mock("@/shared/api/auth", () => ({ getSession: () => session() }));
vi.mock("./CalendarConnect", () => ({ CalendarConnect: () => null }));
vi.mock("./DueReminderSetting", () => ({ DueReminderSetting: () => null }));
vi.mock("./NotificationPauseSetting", () => ({ NotificationPauseSetting: () => null }));
vi.mock("./JiraConnect", () => ({ JiraConnect: () => null }));
vi.mock("./NotionConnect", () => ({ NotionConnect: () => null }));
vi.mock("./ProjectSettings", () => ({ ProjectSettings: () => null }));
vi.mock("./SlackConnect", () => ({
  SlackConnect: ({ teamId }: { teamId: string }) => <div data-testid="team">{teamId}</div>,
}));

const TEAMS = [
  { id: "team_a", name: "가 팀" },
  { id: "team_b", name: "나 팀" },
];
const me = (teams = TEAMS) => ({ id: "user_me", email: "me@example.com", display_name: "Me", teams });
const team = () => screen.getByTestId("team").textContent;
const found = async () => (await screen.findByTestId("team")).textContent;
const picker = () => screen.getByRole("combobox") as HTMLSelectElement;

afterEach(() => {
  cleanup();
  session.mockReset();
});

describe("IntegrationSettingsScreen, the team it is about", () => {
  it("opens on the team chosen elsewhere", async () => {
    session.mockResolvedValue(me());

    render(<IntegrationSettingsScreen chosenTeamId="team_b" />);

    expect(await found()).toBe("team_b");
    expect(picker().value).toBe("team_b");
  });

  it("opens on the first team when none was chosen, or the chosen one is not theirs", async () => {
    session.mockResolvedValue(me());
    const first = render(<IntegrationSettingsScreen />);
    expect(await found()).toBe("team_a");
    first.unmount();

    render(<IntegrationSettingsScreen chosenTeamId="team_left" />);
    expect(await found()).toBe("team_a");
  });

  it("follows a team chosen elsewhere while it is open", async () => {
    session.mockResolvedValue(me());
    const view = render(<IntegrationSettingsScreen chosenTeamId="team_a" />);
    expect(await found()).toBe("team_a");

    view.rerender(<IntegrationSettingsScreen chosenTeamId="team_b" />);

    await waitFor(() => expect(team()).toBe("team_b"));
  });

  it("tells the route when a team is picked here, and shows that team", async () => {
    session.mockResolvedValue(me());
    const chose = vi.fn();
    render(<IntegrationSettingsScreen chosenTeamId="team_a" onChooseTeam={chose} />);
    await found();

    fireEvent.change(picker(), { target: { value: "team_b" } });

    expect(team()).toBe("team_b");
    expect(chose).toHaveBeenCalledExactlyOnceWith("team_b");
  });
});
