import type { ButtonHTMLAttributes, ReactNode } from "react";

/**
 * Everything clickable is one accent. There is no black filled button, and red
 * is never a button fill — it belongs to elapsing time and failure.
 *
 * At most one primary button per screen.
 * See docs/design/ui-spec.md section 0.
 */
export type ButtonTone = "primary" | "secondary" | "text" | "quiet" | "destructiveText";
export type ButtonSize = "compact" | "default" | "hero";

const TONE: Record<ButtonTone, string> = {
  primary: "bg-[var(--color-accent-default)] text-[var(--color-accent-on-accent)] hover:bg-[var(--color-accent-hover)]",
  secondary: "bg-[var(--color-surface-sunken)] text-[var(--color-ink-strong)]",
  text: "bg-transparent text-[var(--color-accent-default)] hover:text-[var(--color-accent-hover)]",
  quiet: "bg-transparent text-[var(--color-ink-muted)]",
  // Destructive actions are red text, then a confirmation modal whose final
  // button is an accent fill. Red never fills a button.
  destructiveText: "bg-transparent text-[var(--color-signal-critical)]",
};

export function Button({
  tone = "secondary",
  size = "default",
  loading = false,
  disabled,
  children,
  className = "",
  ...rest
}: {
  tone?: ButtonTone;
  size?: ButtonSize;
  loading?: boolean;
  children: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const isText = tone === "text" || tone === "quiet" || tone === "destructiveText";
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex items-center justify-center rounded-[var(--radius)] whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:bg-[var(--color-surface-sunken)] disabled:text-[color-mix(in_srgb,var(--color-ink-strong)_35%,transparent)] focus-visible:outline-none focus-visible:ring-[1.5px] focus-visible:ring-[var(--color-accent-default)] ${TONE[tone]} ${className}`}
      style={{
        height: `var(--control-h-${size})`,
        paddingInline: isText ? "var(--control-px-text)" : `var(--control-px-${size})`,
        fontSize: `var(--control-text-${size})`,
        fontWeight: "var(--control-weight)",
      }}
    >
      {children}
    </button>
  );
}
