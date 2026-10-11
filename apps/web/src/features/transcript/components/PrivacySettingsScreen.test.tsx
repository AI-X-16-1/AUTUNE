import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import type { SoleTeam } from "../types";
import { PrivacySettingsScreen } from "./PrivacySettingsScreen";

// #817: deleting my account when I am the last member of a team deletes the
// team with it. The server refuses the first request with the teams it would
// take; the same modal then asks for each team's name, as the members screen
// asks for one team's deletion (#1007), and sends them.

const removeAccount =
  vi.fn<(teams?: SoleTeam[]) => Promise<{ utterances: number }>>();
vi.mock("../api", () => ({
  getMyData: () =>
    Promise.resolve({
      meetings_with_my_speech: 2,
      voice_profile_rows: 0,
      voice_profile_since: null,
      consents_attested: 0,
    }),
  deleteAccount: (teams?: SoleTeam[]) => removeAccount(teams),
  deleteMySpeech: vi.fn(),
  deleteVoiceProfile: vi.fn(),
  downloadMyData: vi.fn(),
  getTeamPrivacy: vi.fn(),
  setTeamRetention: vi.fn(),
  listMaskingRules: vi.fn(),
  deleteMaskingRule: vi.fn(),
}));
vi.mock("./TeamScope", () => ({ TeamScope: () => null }));
const replace = vi.fn<(to: string) => void>();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const SOLO: SoleTeam = { team_id: "team_1", name: "고객사 A 프로젝트" };
const refusal = (teams: SoleTeam[]) =>
  new ApiError(409, "last_member_of_teams", "refused", { teams });

beforeEach(() => {
  removeAccount.mockReset();
  replace.mockReset();
});

afterEach(cleanup);

const askToDeleteAccount = async () => {
  render(<PrivacySettingsScreen />);
  const button = await screen.findByRole<HTMLButtonElement>("button", {
    name: "계정 삭제",
  });
  await waitFor(() => expect(button.disabled).toBe(false));
  fireEvent.click(button);
  fireEvent.click(screen.getByRole("button", { name: "삭제" }));
};

describe("PrivacySettingsScreen account deletion", () => {
  it("deletes at once when I am the last member of no team", async () => {
    removeAccount.mockResolvedValue({ utterances: 3 });

    await askToDeleteAccount();

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(removeAccount).toHaveBeenCalledWith([]);
  });

  it("asks for each team's name when the server lists teams, then sends them", async () => {
    removeAccount
      .mockRejectedValueOnce(refusal([SOLO]))
      .mockResolvedValueOnce({ utterances: 0 });

    await askToDeleteAccount();

    await screen.findByRole("dialog", { name: "팀도 함께 삭제할까요?" });
    const field = screen.getByLabelText(
      /고객사 A 프로젝트.*팀의 이름을 입력해 주세요/,
    );
    const confirm = screen.getByRole<HTMLButtonElement>("button", {
      name: "삭제",
    });
    expect(confirm.disabled).toBe(true);
    expect(replace).not.toHaveBeenCalled();

    fireEvent.change(field, { target: { value: "고객사 A" } });
    expect(confirm.disabled).toBe(true);
    fireEvent.change(field, { target: { value: " 고객사 A 프로젝트 " } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(removeAccount).toHaveBeenLastCalledWith([
      { team_id: "team_1", name: " 고객사 A 프로젝트 " },
    ]);
  });

  it("says a meeting in progress plainly and closes", async () => {
    removeAccount
      .mockRejectedValueOnce(refusal([SOLO]))
      .mockRejectedValueOnce(
        new ApiError(409, "team_meeting_in_progress", "refused"),
      );

    await askToDeleteAccount();
    fireEvent.change(
      await screen.findByLabelText(/팀의 이름을 입력해 주세요/),
      {
        target: { value: SOLO.name },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "삭제" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "진행 중인 회의",
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });
});
