import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import type { Material } from "../types";
import { MaterialsScreen } from "./MaterialsScreen";

// The sidebar's "자료" (#817): a team registers Drive links and opens them in
// Google's own preview. What is under test is what the screen sends and what
// it builds: a link that names no Drive file never reaches the server, and
// every address on screen comes from the id the server kept, not from text a
// person pasted.

const session = vi.fn();
const list = vi.fn();
const register = vi.fn();
const remove = vi.fn();
const rules = vi.fn();
vi.mock("@/shared/api/auth", () => ({ getSession: () => session() }));
vi.mock("../api", () => ({
  getMaterialUploadRules: (teamId: string) => rules(teamId),
  listMaterials: (teamId: string) => list(teamId),
  registerMaterial: (teamId: string, draft: unknown) => register(teamId, draft),
  deleteMaterial: (teamId: string, id: string) => remove(teamId, id),
}));

const TEAMS = [
  { id: "team_a", name: "가 팀" },
  { id: "team_b", name: "나 팀" },
];
const me = (teams = TEAMS) => ({ id: "user_me", email: "me@example.com", display_name: "Me", teams });

const FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345";
const DOC_ID = "1ZyXwVuTsRqPoNmLkJiHgFeDcBa987654";
const material = (over: Partial<Material> = {}): Material => ({
  id: "mat_1",
  team_id: "team_a",
  title: "3분기 로드맵",
  source: "drive_link",
  drive_file_id: DOC_ID,
  drive_kind: "document",
  created_at: "2026-10-07T03:00:00Z",
  expires_at: null,
  not_read: [],
  ...over,
});

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const submit = () => fireEvent.click(screen.getByRole("button", { name: "자료 등록" }));
const rows = () => within(screen.getByRole("list", { name: "등록한 자료" })).getAllByRole("listitem");

// A server that knows links alone: it has no upload-rules route, and the
// screen is the link shelf. Uploads are in `MaterialUpload.test.tsx`.
beforeEach(() => {
  rules.mockRejectedValue(new Error("no such route"));
});

afterEach(() => {
  cleanup();
  rules.mockReset();
  session.mockReset();
  list.mockReset();
  register.mockReset();
  remove.mockReset();
});

describe("MaterialsScreen, the team's shelf", () => {
  it("lists the chosen team's materials and says what Autune does not do", async () => {
    session.mockResolvedValue(me());
    list.mockResolvedValue([material()]);

    render(<MaterialsScreen chosenTeamId="team_b" />);

    expect(await screen.findByText("3분기 로드맵")).toBeTruthy();
    expect(list).toHaveBeenCalledExactlyOnceWith("team_b");
    expect(screen.getByText(/파일의 내용을 읽거나 저장하지 않습니다/)).toBeTruthy();
    expect(screen.getByText(/Google 문서/)).toBeTruthy();
  });

  it("asks again for the other team when the choice moves", async () => {
    session.mockResolvedValue(me());
    list.mockResolvedValue([]);
    const view = render(<MaterialsScreen chosenTeamId="team_a" />);
    await screen.findByText(/아직 등록한 자료가 없습니다/);

    view.rerender(<MaterialsScreen chosenTeamId="team_b" />);

    await waitFor(() => expect(list).toHaveBeenLastCalledWith("team_b"));
  });

  it("says so when the person is on no team, and asks the server for nothing", async () => {
    session.mockResolvedValue(me([]));

    render(<MaterialsScreen />);

    expect(await screen.findByText(/속한 팀이 없습니다/)).toBeTruthy();
    expect(list).not.toHaveBeenCalled();
  });

  it("says the list could not be read rather than showing an empty shelf", async () => {
    session.mockResolvedValue(me());
    list.mockRejectedValue(new ApiError(500, "internal", "boom"));

    render(<MaterialsScreen />);

    expect((await screen.findByRole("alert")).textContent).toContain("불러오지 못했습니다");
    expect(screen.queryByText(/아직 등록한 자료가 없습니다/)).toBeNull();
  });
});

describe("MaterialsScreen, registering", () => {
  it("sends the title and the pasted link, and puts the new row first", async () => {
    session.mockResolvedValue(me());
    list.mockResolvedValue([material()]);
    register.mockResolvedValue(
      material({ id: "mat_2", title: "예산안", drive_file_id: FILE_ID, drive_kind: "file" }),
    );
    render(<MaterialsScreen />);
    await screen.findByText("3분기 로드맵");
    const pasted = `https://drive.google.com/file/d/${FILE_ID}/view?usp=sharing`;

    type("자료 제목", "  예산안 ");
    type("Drive 링크", pasted);
    submit();

    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(register).toHaveBeenCalledExactlyOnceWith("team_a", { title: "예산안", link: pasted });
    expect(rows()[0]?.textContent).toContain("예산안");
    expect((screen.getByLabelText("Drive 링크") as HTMLInputElement).value).toBe("");
  });

  it.each([
    "https://example.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view",
    "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    "회의 자료",
  ])("does not send %s: it names no Drive file", async (pasted) => {
    session.mockResolvedValue(me());
    list.mockResolvedValue([]);
    render(<MaterialsScreen />);
    await screen.findByText(/아직 등록한 자료가 없습니다/);

    type("자료 제목", "자료");
    type("Drive 링크", pasted);
    submit();

    expect((await screen.findByRole("alert")).textContent).toContain("링크가 아닙니다");
    expect(register).not.toHaveBeenCalled();
  });

  it.each([
    [409, "이미 등록된 파일"],
    [422, "제목과 링크를 확인"],
    [500, "잠시 후 다시"],
  ])("on a %s says why and keeps what was typed", async (status, words) => {
    session.mockResolvedValue(me());
    list.mockResolvedValue([]);
    register.mockRejectedValue(new ApiError(status, "refused", "no"));
    render(<MaterialsScreen />);
    await screen.findByText(/아직 등록한 자료가 없습니다/);

    type("자료 제목", "자료");
    type("Drive 링크", FILE_ID);
    submit();

    expect((await screen.findByRole("alert")).textContent).toContain(words);
    expect((screen.getByLabelText("자료 제목") as HTMLInputElement).value).toBe("자료");
  });

  it("says which kind of value to take out of a title refused as personal data (#1130)", async () => {
    session.mockResolvedValue(me());
    list.mockResolvedValue([]);
    register.mockRejectedValue(
      new ApiError(422, "validation_error", "this text looks like it holds personal data", {
        field: "title",
        reason: "personal_data",
        categories: ["phone"],
      }),
    );
    render(<MaterialsScreen />);
    await screen.findByText(/아직 등록한 자료가 없습니다/);

    type("자료 제목", "문의 010-1234-5678");
    type("Drive 링크", FILE_ID);
    submit();

    expect((await screen.findByRole("alert")).textContent).toBe(
      "전화번호로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.",
    );
    expect((screen.getByLabelText("자료 제목") as HTMLInputElement).value).toBe(
      "문의 010-1234-5678",
    );
  });

  it("cannot be sent without a title", async () => {
    session.mockResolvedValue(me());
    list.mockResolvedValue([]);
    render(<MaterialsScreen />);
    await screen.findByText(/아직 등록한 자료가 없습니다/);

    type("Drive 링크", FILE_ID);

    expect((screen.getByRole("button", { name: "자료 등록" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});

describe("MaterialsScreen, a row", () => {
  it("opens Google's preview of the file, built from the id", async () => {
    session.mockResolvedValue(me());
    list.mockResolvedValue([material()]);
    render(<MaterialsScreen />);
    await screen.findByText("3분기 로드맵");
    expect(document.querySelector("iframe")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "미리보기" }));

    expect(document.querySelector("iframe")?.getAttribute("src")).toBe(
      `https://docs.google.com/document/d/${DOC_ID}/preview`,
    );
    const open = screen.getAllByRole("link", { name: "Drive에서 열기" })[0];
    expect(open?.getAttribute("href")).toBe(`https://docs.google.com/document/d/${DOC_ID}/edit`);
    expect(open?.getAttribute("rel")).toBe("noopener noreferrer");

    fireEvent.click(screen.getByRole("button", { name: "미리보기 닫기" }));
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("deletes only after the question is answered, and says the Drive file stays", async () => {
    session.mockResolvedValue(me());
    list.mockResolvedValue([material(), material({ id: "mat_2", title: "예산안" })]);
    remove.mockResolvedValue(undefined);
    render(<MaterialsScreen />);
    await screen.findByText("3분기 로드맵");

    fireEvent.click(within(rows()[0] as HTMLElement).getByRole("button", { name: "삭제" }));
    expect(remove).not.toHaveBeenCalled();
    expect(screen.getByText(/Drive의 파일은 그대로 남습니다/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "목록에서 빼기" }));

    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(remove).toHaveBeenCalledExactlyOnceWith("team_a", "mat_1");
    expect(rows()[0]?.textContent).toContain("예산안");
  });

  it("keeps the row when the question is cancelled or the delete fails", async () => {
    session.mockResolvedValue(me());
    list.mockResolvedValue([material()]);
    remove.mockRejectedValue(new ApiError(500, "internal", "boom"));
    render(<MaterialsScreen />);
    await screen.findByText("3분기 로드맵");

    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    fireEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(remove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "삭제" }));
    fireEvent.click(screen.getByRole("button", { name: "목록에서 빼기" }));

    expect((await screen.findByRole("status")).textContent).toContain("빼지 못했습니다");
    expect(rows()).toHaveLength(1);
  });
});
