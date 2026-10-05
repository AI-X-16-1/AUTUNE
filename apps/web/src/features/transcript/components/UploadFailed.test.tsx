import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { UploadFailed } from "./UploadFailed";

afterEach(cleanup);

describe("UploadFailed", () => {
  it("says the recording is only in this tab, and offers both ways to keep it", () => {
    const onRetry = vi.fn();
    const onSave = vi.fn();
    render(
      <UploadFailed
        error="서버에 연결하지 못했습니다."
        onRetry={onRetry}
        onSave={onSave}
      />,
    );

    expect(screen.getByRole("alert").textContent).toContain(
      "서버에 연결하지 못했습니다.",
    );
    expect(screen.getByRole("alert").textContent).toContain("이 탭에만");

    fireEvent.click(screen.getByRole("button", { name: "파일로 저장" }));
    expect(onSave).toHaveBeenCalledOnce();
    expect(onRetry).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "다시 올리기" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
