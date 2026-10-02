import type { CSSProperties } from "react";

import type { Assignable } from "../api";

/**
 * Who an item is assigned to, as the two places that set it see it: a member
 * of the meeting's team, a name somebody typed, or nobody.
 *
 * The difference is not cosmetic. A member is an account, and an account is
 * what puts the due date on that person's calendar and makes them the Jira
 * assignee. A typed name is a word on a card. Until this existed the screen
 * could only type a name, so nothing a person added by hand ever reached a
 * calendar -- the user's "왜 안되냐", 2026-10-02.
 */
export type AssigneeValue =
  | { kind: "none" }
  | { kind: "member"; userId: string }
  | { kind: "typed"; label: string };

export const ASSIGNEE_LABEL_MAX = 200;

const NONE = "";
const TYPED = "__typed";

/** What the API takes for a value: an account or a name, never both. */
export function assigneeFields(value: AssigneeValue): {
  assignee_id: string | null;
  assignee_label: string | null;
} {
  if (value.kind === "member") {
    return { assignee_id: value.userId, assignee_label: null };
  }
  if (value.kind === "typed") {
    return { assignee_id: null, assignee_label: value.label.trim() || null };
  }
  return { assignee_id: null, assignee_label: null };
}

/**
 * A select of the team's members, with "직접 입력" for a person who is not one
 * of them (decided with the user: both stay). `members` is null while the list
 * is unknown -- loading, or it could not be read -- and then this is the text
 * box it always was, so a failed request never stops somebody adding an item.
 */
export function AssigneeInput({
  id,
  label,
  memberName,
  members,
  value,
  onChange,
  disabled = false,
  controlClassName,
  controlStyle,
}: {
  id: string;
  /** For a place whose visible label is not a `<label for>` of this control. */
  label?: string;
  /** The name of the account `value` names, to show while it cannot be changed. */
  memberName?: string | null;
  members: Assignable[] | null;
  value: AssigneeValue;
  onChange: (value: AssigneeValue) => void;
  disabled?: boolean;
  controlClassName?: string;
  controlStyle?: CSSProperties;
}) {
  const pick = members !== null && members.length > 0;
  // A member who has left the list (no longer on the team) is not offered.
  const chosen =
    value.kind === "member" && members?.some((m) => m.user_id === value.userId)
      ? value.userId
      : value.kind === "typed"
        ? TYPED
        : NONE;
  const typing = !pick || value.kind === "typed";
  const noteId = `${id}-note`;

  // An account, and no list to pick another from (still loading, or it could
  // not be read). The text box would show that account as an empty name with
  // the "name only" warning, and saving from it would replace the account
  // with whatever was typed -- taking the item off that person's calendar.
  // So it is shown and not editable until there is a list (review of #737).
  if (!pick && value.kind === "member") {
    return (
      <div className="grid gap-1">
        <span>{memberName ?? "지정된 담당자"}</span>
        <p className="text-[var(--color-ink-muted)]" style={{ fontSize: "var(--text-metaSmall)" }}>
          팀 구성원 목록을 불러오면 바꿀 수 있습니다.
        </p>
      </div>
    );
  }

  return (
    <div className="grid gap-2">
      {pick ? (
        <select
          id={id}
          aria-label={label}
          value={chosen}
          disabled={disabled}
          onChange={(event) => {
            const next = event.target.value;
            if (next === NONE) onChange({ kind: "none" });
            else if (next === TYPED) onChange({ kind: "typed", label: "" });
            else onChange({ kind: "member", userId: next });
          }}
          className={controlClassName}
          style={controlStyle}
        >
          <option value={NONE}>미지정</option>
          {members.map((member) => (
            <option key={member.user_id} value={member.user_id}>
              {member.name}
            </option>
          ))}
          <option value={TYPED}>직접 입력</option>
        </select>
      ) : null}
      {typing ? (
        <>
          <input
            id={pick ? `${id}-typed` : id}
            aria-label={pick ? "담당자 이름" : label}
            aria-describedby={noteId}
            value={value.kind === "typed" ? value.label : ""}
            disabled={disabled}
            onChange={(event) => onChange({ kind: "typed", label: event.target.value })}
            maxLength={ASSIGNEE_LABEL_MAX}
            placeholder="이름"
            className={controlClassName}
            style={controlStyle}
          />
          <p
            id={noteId}
            className="text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            이름만 적은 담당자는 캘린더와 Jira에 연결되지 않습니다.
          </p>
        </>
      ) : null}
    </div>
  );
}
