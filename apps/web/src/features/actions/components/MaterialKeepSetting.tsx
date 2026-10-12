"use client";

import { useState } from "react";

import type { MaterialSettings } from "../types";

/**
 * How the team keeps its uploaded materials (the owner, 2026-10-11): for the
 * team's retention window counted from the upload -- the default -- or until
 * a member deletes them. Shown only where the deployment takes uploads.
 *
 * Both choices are said in full before either is picked, including what
 * switching back does to what is already there: every upload is counted from
 * its own upload again, so the oldest go within the hour. Any member
 * switches it and the server keeps no name, so the screen asks no one's
 * permission and says who may.
 */
export function MaterialKeepSetting({
  settings,
  onChange,
}: {
  settings: MaterialSettings;
  /** Saves the choice; rejects when the server did not take it. */
  onChange: (keepUntilDeleted: boolean) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const meta = { fontSize: "var(--text-metaSmall)" } as const;

  const pick = async (keep: boolean) => {
    if (busy || keep === settings.keep_until_deleted) return;
    setError(null);
    setBusy(true);
    try {
      await onChange(keep);
    } catch {
      setError("보관 방식을 바꾸지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  };

  const choices = [
    {
      keep: false,
      label: "보관 기간이 지나면 삭제 (기본)",
      detail: `올린 날부터 팀의 보관 기간(${settings.retention_days}일)이 지나면 개인정보를 가린 글을 자동으로 지웁니다. 목록에 지워지는 날이 적힙니다.`,
    },
    {
      keep: true,
      label: "팀이 지울 때까지 보관",
      detail:
        "기간이 지나도 지우지 않고, 팀 구성원이 직접 삭제할 때까지 보관합니다. 나중에 기간 삭제로 되돌리면 이미 올린 자료도 올린 날부터 다시 계산해, 기간이 지난 자료는 한 시간 안에 지워집니다.",
    },
  ] as const;

  return (
    <fieldset
      aria-label="올린 자료 보관 방식"
      aria-busy={busy}
      className="flex flex-col gap-2 border border-[var(--color-hairline)]"
      style={{ ...meta, borderRadius: "var(--radius)", padding: "8px 12px" }}
    >
      <legend className="text-[var(--color-ink-strong)]">올린 자료 보관 방식</legend>
      {choices.map((choice) => (
        <label key={String(choice.keep)} className="flex items-start gap-2">
          <input
            type="radio"
            name="material-keep"
            checked={settings.keep_until_deleted === choice.keep}
            disabled={busy}
            onChange={() => void pick(choice.keep)}
          />
          <span className="flex flex-col">
            <span className="text-[var(--color-ink-body)]">{choice.label}</span>
            <span className="text-[var(--color-ink-muted)]">{choice.detail}</span>
          </span>
        </label>
      ))}
      <span className="text-[var(--color-ink-muted)]">
        팀 구성원 누구나 바꿀 수 있으며, 누가 바꿨는지는 기록하지 않습니다. 링크로
        등록한 Drive 파일은 Autune이 내용을 보관하지 않으므로 해당하지 않습니다.
      </span>
      {error ? (
        <span role="alert" className="text-[var(--color-signal-critical)]">
          {error}
        </span>
      ) : null}
    </fieldset>
  );
}
