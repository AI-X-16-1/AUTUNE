/** Tabs are a 2px accent underline. Counts are monospace. */
export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: ReadonlyArray<{ id: T; label: string; count?: number }>;
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div role="tablist" className="flex gap-5 border-b border-[var(--color-hairline)]">
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab.id)}
            className="-mb-px flex items-center gap-1.5 pb-2"
            style={{
              fontSize: "var(--text-heading)",
              fontWeight: "var(--text-heading-weight)",
              color: selected ? "var(--color-ink-strong)" : "var(--color-ink-muted)",
              borderBottom: `2px solid ${selected ? "var(--color-accent-default)" : "transparent"}`,
            }}
          >
            {tab.label}
            {tab.count === undefined ? null : (
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-dataSmall)" }}>
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
