/**
 * A masked span in a transcript. Monospace with a dotted underline.
 *
 * There is deliberately no "reveal original" affordance: the unmasked text was
 * never stored. See docs/architecture/privacy.md section 2.
 */
export function PiiToken({ children }: { children: string }) {
  return (
    <span
      title="개인정보 자동 마스킹 · 원문 미저장"
      className="cursor-help"
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "var(--text-data)",
        textDecoration: "underline dotted",
        textUnderlineOffset: 3,
      }}
    >
      {children}
    </span>
  );
}
