import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DrivePickError } from "../drivePicker";
import type { MaterialUploadRules } from "../types";
import { MaterialUploadForm } from "./MaterialUploadForm";

// "Drive에서 선택" on the upload form (#1369). The form takes the Picker as a
// prop, so what is under test is what the form does with its answer: the
// chosen file goes through the same upload as one from disk, a closed Picker
// changes nothing, and a file the rules refuse is not sent. Files are a few
// made-up bytes.

afterEach(cleanup);

const RULES: MaterialUploadRules = {
  enabled: true,
  max_bytes: 1024,
  suffixes: [".txt", ".pdf"],
  max_title_chars: 80,
  max_materials: 200,
};

const file = (name: string, size = 10) => new File([new Uint8Array(size).fill(65)], name);

const renderForm = (pickDrive?: () => Promise<File | null>) => {
  const onUpload = vi.fn(async () => undefined);
  const onUnanswered = vi.fn();
  render(
    <MaterialUploadForm
      rules={RULES}
      onUpload={onUpload}
      onUnanswered={onUnanswered}
      pickDrive={pickDrive}
    />,
  );
  return { onUpload, onUnanswered };
};

describe("Drive에서 선택", () => {
  it("draws no button where no Picker is configured", () => {
    renderForm(undefined);
    expect(screen.queryByRole("button", { name: "Drive에서 선택" })).toBeNull();
  });

  it("uploads the chosen file as a file from disk is uploaded", async () => {
    const picked = file("계획.pdf");
    const { onUpload } = renderForm(async () => picked);
    fireEvent.change(screen.getByLabelText("올릴 자료 제목"), { target: { value: "계획" } });
    fireEvent.click(screen.getByRole("button", { name: "Drive에서 선택" }));
    await screen.findByText(/Drive에서 고른 파일: 계획\.pdf/);
    fireEvent.click(screen.getByRole("button", { name: "파일 올리기" }));
    await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(1));
    expect(onUpload).toHaveBeenCalledWith({ title: "계획", file: picked });
  });

  it("changes nothing when the Picker is closed", async () => {
    renderForm(async () => null);
    fireEvent.change(screen.getByLabelText("올릴 자료 제목"), { target: { value: "계획" } });
    fireEvent.click(screen.getByRole("button", { name: "Drive에서 선택" }));
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "Drive에서 선택" }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    expect(screen.queryByText(/Drive에서 고른 파일:/)).toBeNull();
    expect((screen.getByRole("button", { name: "파일 올리기" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("says what failed, in our words", async () => {
    renderForm(async () => {
      throw new DrivePickError("이 파일을 열 수 없습니다.");
    });
    fireEvent.click(screen.getByRole("button", { name: "Drive에서 선택" }));
    expect((await screen.findByRole("alert")).textContent).toContain("이 파일을 열 수 없습니다.");
  });

  it("hides a failure that is not ours", async () => {
    renderForm(async () => {
      throw new Error("token=abc123");
    });
    fireEvent.click(screen.getByRole("button", { name: "Drive에서 선택" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toContain("abc123");
  });

  it("does not send a file the rules refuse", async () => {
    const { onUpload } = renderForm(async () => file("표.xlsx"));
    fireEvent.change(screen.getByLabelText("올릴 자료 제목"), { target: { value: "표" } });
    fireEvent.click(screen.getByRole("button", { name: "Drive에서 선택" }));
    expect((await screen.findByRole("alert")).textContent).toContain("받지 않습니다");
    expect((screen.getByRole("button", { name: "파일 올리기" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(onUpload).not.toHaveBeenCalled();
  });

  it("says that the server does not reach Drive", () => {
    renderForm(async () => null);
    expect(screen.getByText(/Autune 서버는\s*Drive에 접속하지 않으며/)).toBeTruthy();
  });
});
