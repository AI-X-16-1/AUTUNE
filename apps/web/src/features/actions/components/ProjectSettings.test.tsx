import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Project } from "../types";
import { ProjectSettings } from "./ProjectSettings";

const projects = vi.fn<() => Promise<Project[]>>();
vi.mock("../api", () => ({
  listProjects: () => projects(),
  listProjectSuggestions: () => Promise.resolve([]),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
}));

afterEach(() => {
  cleanup();
  projects.mockReset();
});

describe("ProjectSettings", () => {
  // jsdom lays nothing out, so this is the rule and not the result: the page
  // being no wider than a 390 px screen was measured in a browser. A text
  // field is as wide as its twenty characters whatever holds it; these two
  // were 171 px in a 134 px column and pushed the page sideways.
  it("keeps the name and alias fields within the row they are in", async () => {
    projects.mockResolvedValue([]);
    render(<ProjectSettings teamId="team_a" />);

    for (const name of ["프로젝트 이름", "별칭"]) {
      const field = await screen.findByRole("textbox", { name });
      expect(field.className.split(/\s+/), name).toContain("max-w-full");
    }
  });
});
