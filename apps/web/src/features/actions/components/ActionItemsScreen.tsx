"use client";

import { useState } from "react";

import { ActionBoard } from "./ActionBoard";
import { ActionDetailDrawer } from "./ActionDetailDrawer";
import { useActionItems } from "../hooks/useActionItems";

/**
 * S17 for one meeting: the board, and the drawer for the item a person opens.
 *
 * The screen lives in the feature rather than in the route file: `apps/` is
 * assembly, and a page that knew how the board and the drawer fit together would
 * be module B's screen kept in the team's shared tree. The route mounts this and
 * passes a meeting id — the same split `features/gap` and `features/transcript`
 * use.
 *
 * The board stays mounted while a refresh runs: it once unmounted whenever an
 * empty meeting was re-read, and took the half-typed add form with it. Only a
 * meeting that has never settled reads as loading. Raised in review of #292.
 *
 * **Loading, failed and empty are three different sentences.** A meeting the
 * pipeline has not reached yet, one whose read failed, and one that genuinely
 * produced no items all render an empty board otherwise, and only the last is
 * true. Items stay on screen when a later reload fails, for the same reason
 * `GapReportScreen` keeps the last good answer.
 *
 * The drawer takes its item from the list rather than holding a copy, so an edit
 * made through it shows on the board and in the drawer at once, and a deleted
 * item closes it.
 *
 * **Column until there is room for two.** The board is `flex-1 min-w-0`, so its
 * flex-shrink weight is zero: beside it on a narrow screen the drawer takes the
 * whole width and the board renders at zero. Raised in review of #292.
 */
export function ActionItemsScreen({ meetingId }: { meetingId: string }) {
  const { items, settled, error, add, edit, remove } = useActionItems({
    meeting_id: meetingId,
  });
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined);
  const selected = items.find((item) => item.id === selectedId);

  return (
    <main
      className="mx-auto flex max-w-[1200px] flex-col gap-6 md:flex-row"
      style={{ padding: "var(--space-page)" }}
    >
      <div className="min-w-0 flex-1">
        <h1
          className="text-[var(--color-ink-strong)]"
          style={{
            fontSize: "var(--text-title)",
            fontWeight: "var(--text-title-weight)",
            letterSpacing: "var(--text-title-tracking)",
          }}
        >
          액션 아이템
        </h1>
        <p
          className="mt-2 text-[var(--color-ink-muted)]"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "var(--text-metaSmall)",
          }}
        >
          {meetingId}
        </p>

        <div className="mt-6">
          {!settled ? (
            <Note>액션 아이템을 불러오는 중입니다.</Note>
          ) : error !== null && items.length === 0 ? (
            <Note>이 회의의 액션 아이템을 불러오지 못했습니다.</Note>
          ) : (
            <>
              {error !== null ? (
                <Note>최신 목록을 불러오지 못해 이전 목록을 보여주고 있습니다.</Note>
              ) : null}
              {items.length === 0 ? (
                <Note>
                  이 회의에서 추출된 액션 아이템이 없습니다. 놓친 항목은 직접 추가할 수 있습니다.
                </Note>
              ) : null}
              <ActionBoard
                items={items}
                selectedId={selectedId}
                onSelect={setSelectedId}
                add={{ meetingId, onAdd: add }}
              />
            </>
          )}
        </div>
      </div>

      {selected !== undefined ? (
        <ActionDetailDrawer
          key={selected.id}
          item={selected}
          onClose={() => setSelectedId(undefined)}
          onStatusChange={async (status) => {
            await edit(selected.id, { status });
          }}
          onDelete={async () => {
            await remove(selected.id);
            setSelectedId(undefined);
          }}
        />
      ) : null}
    </main>
  );
}

function Note({ children }: { children: string }) {
  return (
    <p
      className="mb-4 text-[var(--color-ink-muted)]"
      style={{ fontSize: "var(--text-metaSmall)" }}
    >
      {children}
    </p>
  );
}
