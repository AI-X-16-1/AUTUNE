"use client";

import { useState } from "react";

import type { MaterialSettings } from "../types";

/**
 * How the team keeps its uploaded materials (the owner, 2026-10-11): for the
 * team's retention window counted from the upload -- the default -- or until
 * a member deletes them. Shown where the deployment takes uploads, and, where
 * it does not, to a team that still has one: the server takes the choice and
 * deletes by it whatever the upload switch says, so a team that chose to keep
 * everything could otherwise not take that back (#1295). There the screen has
 * no upload form above this, and `uploadsOff` says so in one sentence.
 *
 * Both choices are said in full before either is picked, including what
 * switching back does to what is already there: every upload has kept the
 * day it was given at its upload, so those past it go at an hourly check.
 * The sentences say how often that check is made and give no deadline: a
 * check can miss a row stored after it read the team, and one that fails is
 * not made up before the next (the owner, 2026-10-13). While
 * the team keeps everything, the choice that would switch back says how many
 * that is -- the server's count -- before it is picked, since picking saves
 * (docs/architecture/privacy.md section 4; the owner, 2026-10-12). Any
 * member switches it and the server keeps no name, so the screen asks no
 * one's permission and says who may.
 */
export function MaterialKeepSetting({
  settings,
  uploadsOff = false,
  onChange,
}: {
  settings: MaterialSettings;
  /** The server said it takes no upload now; the choice is over those it has. */
  uploadsOff?: boolean;
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

  const past = settings.uploads_past_their_day;
  // What picking the window would delete, said only while it is the other
  // choice that holds. A server that does not answer the count gets the
  // sentence without a number rather than a guessed zero.
  const switchingBack = !settings.keep_until_deleted
    ? ""
    : past === undefined
      ? " 지금 이 방식으로 바꾸면 지워지는 날이 이미 지난 자료는 1시간마다 확인하여 지우며, 복구할 수 없습니다."
      : past > 0
        ? ` 지금 이 방식으로 바꾸면 지워지는 날이 이미 지난 자료 ${past}건은 1시간마다 확인하여 지우며, 복구할 수 없습니다.`
        : " 지금 이 방식으로 바꿔도 곧바로 지워지는 자료는 없습니다.";

  const choices = [
    {
      keep: false,
      label: "보관 기간이 지나면 삭제 (기본)",
      detail: `올린 날부터 팀의 보관 기간(${settings.retention_days}일)이 지나면 개인정보를 가린 글을 자동으로 지웁니다. 목록에 지워지는 날이 적힙니다. 지워지는 날은 올릴 때 정해지며, 팀의 보관 기간을 나중에 바꿔도 이미 올린 자료의 날은 달라지지 않습니다.${switchingBack}`,
    },
    {
      keep: true,
      label: "팀이 지울 때까지 보관",
      detail:
        "기간이 지나도 지우지 않고, 팀 구성원이 직접 삭제할 때까지 보관합니다. 나중에 기간 삭제로 되돌리면 이미 올린 자료에도 각각 올릴 때 정해진 지워지는 날이 다시 적용되어, 그날이 지난 자료는 1시간마다 확인하여 지웁니다. 팀에 구성원이 한 명도 남지 않으면 이 선택과 관계없이 1시간마다 확인하여 팀의 자료를 모두 지웁니다.",
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
      {uploadsOff ? (
        <span className="text-[var(--color-ink-body)]">
          지금은 파일을 새로 올릴 수 없습니다. 이 선택은 이미 올린 자료에 그대로 적용됩니다.
        </span>
      ) : null}
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
