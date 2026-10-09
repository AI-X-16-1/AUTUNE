import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/shared/api/client";

import { NewMeetingScreen } from "./NewMeetingScreen";

// #1161: the server screens a title when a meeting is opened. A title it
// refuses as personal data is said in the screen's own words -- what to take
// out -- and not as the server's English sentence.

const push = vi.fn<(href: string) => void>();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

type Created = { title: string; team_id: string; started_at?: string };
const create = vi.fn<(payload: Created) => Promise<{ meeting_id: string }>>();
const attest = vi.fn<(meetingId: string) => Promise<void>>();
const upload = vi.fn<(meetingId: string, file: File) => Promise<void>>();
vi.mock("../api", () => ({
  attestConsent: (meetingId: string) => attest(meetingId),
  createMeeting: (payload: Created) => create(payload),
  listTeams: () => Promise.resolve([{ team_id: "team_1", name: "검색팀", pinned: false }]),
  uploadRecording: (meetingId: string, file: File) => upload(meetingId, file),
}));

afterEach(() => {
  cleanup();
  push.mockReset();
  create.mockReset();
  attest.mockReset();
  upload.mockReset();
  window.sessionStorage.clear();
});

const TYPED = "김 대리 010-1234-5678 통화";
const SENTENCE =
  "전화번호로 보이는 값이 있어 저장하지 않았습니다. 그 값을 지우고 다시 저장해 주세요.";
const refused = () =>
  new ApiError(
    422,
    "validation_error",
    "this title looks like it holds personal data and was not saved",
    { field: "title", reason: "personal_data", categories: ["phone"] },
  );

async function typed() {
  render(<NewMeetingScreen />);
  await screen.findByRole("option", { name: "검색팀" });
  fireEvent.change(screen.getByPlaceholderText(/스프린트 킥오프/), {
    target: { value: TYPED },
  });
}

describe("NewMeetingScreen, a title the server refuses", () => {
  it("says what to take out when a live meeting is not opened, and goes nowhere", async () => {
    create.mockRejectedValue(refused());
    await typed();

    fireEvent.click(screen.getByRole("button", { name: "지금 녹음 시작" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(SENTENCE);
    expect(alert.textContent).not.toContain("personal data");
    expect(push).not.toHaveBeenCalled();
    // The title stays in the field to be fixed.
    expect((screen.getByPlaceholderText(/스프린트 킥오프/) as HTMLInputElement).value).toBe(TYPED);
  });

  it("says the same when a meeting is only saved for later", async () => {
    create.mockRejectedValue(refused());
    await typed();
    const later = screen.getByRole("button", { name: "저장만" }) as HTMLButtonElement;
    await waitFor(() => expect(later.disabled).toBe(false));

    fireEvent.click(later);

    expect((await screen.findByRole("alert")).textContent).toBe(SENTENCE);
    expect(push).not.toHaveBeenCalled();
  });

  it("says the same on the upload path, and uploads nothing", async () => {
    create.mockRejectedValue(refused());
    const { container } = render(<NewMeetingScreen />);
    await screen.findByRole("option", { name: "검색팀" });
    fireEvent.change(screen.getByPlaceholderText(/스프린트 킥오프/), {
      target: { value: TYPED },
    });
    fireEvent.click(screen.getByText("녹음 파일 업로드"));
    fireEvent.change(container.querySelector("input[type=file]")!, {
      target: { files: [new File(["x"], "meeting.mp3", { type: "audio/mpeg" })] },
    });
    fireEvent.click(
      screen.getByRole("checkbox", { name: /모든 참석자가 녹음과 분석에 동의/ }),
    );

    fireEvent.click(screen.getByRole("button", { name: "업로드하고 분석 시작" }));

    expect((await screen.findByRole("alert")).textContent).toBe(SENTENCE);
    expect(create).toHaveBeenCalledTimes(1);
    expect(attest).not.toHaveBeenCalled();
    expect(upload).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("still shows any other failure as it did", async () => {
    create.mockRejectedValue(new ApiError(403, "permission_denied", "you are not a member"));
    await typed();

    fireEvent.click(screen.getByRole("button", { name: "지금 녹음 시작" }));

    expect((await screen.findByRole("alert")).textContent).toBe("you are not a member");
  });
});
