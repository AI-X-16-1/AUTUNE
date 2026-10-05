import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NameSuggestions } from "./NameSuggestions";
import type { Project, ProjectDraft } from "../types";

// Words said often that no project is named by (the user, 2026-10-04): each
// becomes a new project or an alias of an existing one.

const listProjectSuggestions =
  vi.fn<(teamId: string) => Promise<{ word: string; count: number }[]>>();
const createProject =
  vi.fn<(teamId: string, draft: ProjectDraft) => Promise<Project>>();
const updateProject =
  vi.fn<
    (teamId: string, id: string, draft: ProjectDraft) => Promise<Project>
  >();
vi.mock("../api", () => ({
  listProjectSuggestions: (teamId: string) => listProjectSuggestions(teamId),
  createProject: (teamId: string, draft: ProjectDraft) =>
    createProject(teamId, draft),
  updateProject: (teamId: string, id: string, draft: ProjectDraft) =>
    updateProject(teamId, id, draft),
}));

const ALPHA: Project = {
  id: "prj_a",
  name: "알파",
  aliases: ["alpha"],
  jira_project_key: "ALP",
} as Project;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("NameSuggestions", () => {
  it("renders nothing when there is nothing to suggest", async () => {
    listProjectSuggestions.mockResolvedValue([]);
    const { container } = render(
      <NameSuggestions teamId="team_1" projects={[]} onChanged={() => {}} />,
    );
    await waitFor(() => expect(listProjectSuggestions).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("turns a word into a new project", async () => {
    listProjectSuggestions.mockResolvedValue([{ word: "베타", count: 5 }]);
    const saved = { ...ALPHA, id: "prj_b", name: "베타", aliases: [] };
    createProject.mockResolvedValue(saved);
    const onChanged = vi.fn();
    render(
      <NameSuggestions
        teamId="team_1"
        projects={[ALPHA]}
        onChanged={onChanged}
      />,
    );
    const select = await screen.findByLabelText("베타 넣기");
    expect(screen.getByText("회의 5번")).toBeTruthy();
    fireEvent.change(select, { target: { value: "__new__" } });
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(saved));
    expect(createProject).toHaveBeenCalledWith("team_1", {
      name: "베타",
      aliases: [],
    });
    expect(screen.queryByLabelText("베타 넣기")).toBeNull();
  });

  it("adds a word to an existing project's aliases, keeping its fields", async () => {
    listProjectSuggestions.mockResolvedValue([{ word: "에이", count: 3 }]);
    const saved = { ...ALPHA, aliases: ["alpha", "에이"] };
    updateProject.mockResolvedValue(saved);
    const onChanged = vi.fn();
    render(
      <NameSuggestions
        teamId="team_1"
        projects={[ALPHA]}
        onChanged={onChanged}
      />,
    );
    fireEvent.change(await screen.findByLabelText("에이 넣기"), {
      target: { value: "prj_a" },
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(saved));
    expect(updateProject).toHaveBeenCalledWith("team_1", "prj_a", {
      name: "알파",
      aliases: ["alpha", "에이"],
      jira_project_key: "ALP",
    });
    expect(screen.getByRole("status").textContent).toContain("알파의 별칭");
  });

  it("says so when the save is refused", async () => {
    listProjectSuggestions.mockResolvedValue([{ word: "베타", count: 4 }]);
    createProject.mockRejectedValue(new Error("409"));
    render(
      <NameSuggestions teamId="team_1" projects={[]} onChanged={() => {}} />,
    );
    fireEvent.change(await screen.findByLabelText("베타 넣기"), {
      target: { value: "__new__" },
    });
    expect((await screen.findByRole("status")).textContent).toContain(
      "넣지 못했습니다",
    );
    expect(screen.getByLabelText("베타 넣기")).toBeTruthy();
  });
});
