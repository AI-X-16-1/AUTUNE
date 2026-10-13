/**
 * The red dot beside a team in the sidebar's team menu: proposals wait in that
 * team for this person to decide (the user, 2026-10-13).
 *
 * 승인 대기 lists one team at a time, so without it a proposal in a team not
 * being looked at goes unseen. Which teams have one is the agent feature's to
 * read; the shell passes them in as ids (`TeamMenu`'s `marked`), and this
 * module only draws the mark.
 */
export function PendingMark() {
  return (
    <>
      <span
        aria-hidden
        className="shrink-0 rounded-full bg-[var(--color-signal-critical)]"
        style={{ width: 7, height: 7 }}
      />
      <span className="sr-only">, 승인 대기 있음</span>
    </>
  );
}
