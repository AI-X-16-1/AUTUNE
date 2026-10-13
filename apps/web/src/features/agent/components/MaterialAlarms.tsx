"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/shared/api/client";
import { Button } from "@/shared/ui/Button";

import { acknowledgeMaterialAlarm, listMaterialAlarms } from "../api";
import type { MaterialAlarm } from "../types";

const MARKING_LABEL: Record<MaterialAlarm["marking"], string> = {
  korean_marking: "한글 표시 (대외비·기밀 등)",
  english_marking: "영문 표시 (CONFIDENTIAL 등)",
};

const TRY_LATER = "확인하지 못했습니다. 잠시 후 다시 시도해 주세요.";

function when(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Uploads that were stopped because the file carried a confidentiality
 * marking (#817, #1201) -- the section of 승인 대기 the approvers' DM links to.
 *
 * A row is the time and the kind of marking, which is all the server keeps:
 * no file name, no title and nobody's name. 확인 deletes the row for every
 * approver, and who pressed it is not recorded.
 *
 * Draws nothing while there is nothing to show. Someone who is not an
 * approver is answered an empty list, so this section does not exist for
 * them; a failed load is silent for the same reason -- the page's own list
 * already says when the server cannot be reached.
 */
export function MaterialAlarms() {
  const [alarms, setAlarms] = useState<MaterialAlarm[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    listMaterialAlarms()
      .then((list) => current && setAlarms(list))
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, []);

  async function acknowledge(alarm: MaterialAlarm) {
    setBusy(alarm.id);
    setFailed(null);
    try {
      await acknowledgeMaterialAlarm(alarm.team_id, alarm.id);
      setAlarms((shown) => shown.filter((one) => one.id !== alarm.id));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // Another approver acknowledged it first: it is gone, which is what was asked.
        setAlarms((shown) => shown.filter((one) => one.id !== alarm.id));
      } else {
        setFailed(alarm.id);
      }
    } finally {
      setBusy(null);
    }
  }

  if (alarms.length === 0) return null;

  return (
    <section aria-labelledby="material-alarms-title" className="mt-8">
      <h2
        id="material-alarms-title"
        className="text-[var(--color-ink-strong)]"
        style={{ fontSize: "var(--text-rowTitle)" }}
      >
        받지 않은 자료 업로드
      </h2>
      <p
        className="mt-1 text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-meta)" }}
      >
        기밀·대외비 표시가 있어 받지 않은 파일입니다. 파일의 내용과 이름, 올린
        사람은 저장되지 않았습니다. 확인을 누르면 이 줄이 지워지며, 누가
        확인했는지는 기록되지 않습니다. 확인하지 않은 줄은 30일 뒤에
        지워집니다.
      </p>
      <ul className="mt-3 flex flex-col gap-2">
        {alarms.map((alarm) => (
          <li
            key={alarm.id}
            className="flex items-center justify-between gap-4 rounded-[var(--radius)] border border-[var(--color-hairline)] p-3"
            style={{ background: "var(--color-surface-panel)" }}
          >
            <div>
              <span className="text-[var(--color-ink-strong)]">
                {when(alarm.created_at)}
              </span>
              <span
                className="ml-2 text-[var(--color-ink-muted)]"
                style={{ fontSize: "var(--text-meta)" }}
              >
                {MARKING_LABEL[alarm.marking] ?? "보안 표시"}
              </span>
              {failed === alarm.id ? (
                <p
                  role="alert"
                  className="mt-1"
                  style={{
                    fontSize: "var(--text-meta)",
                    color: "var(--color-signal-critical)",
                  }}
                >
                  {TRY_LATER}
                </p>
              ) : null}
            </div>
            <Button
              loading={busy === alarm.id}
              disabled={busy !== null}
              onClick={() => acknowledge(alarm)}
            >
              확인
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
