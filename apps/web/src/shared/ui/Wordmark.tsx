/**
 * The Autune wordmark: lowercase "autune" drawn in strokes, its "un" a wave in
 * the accent colour. The canonical file is docs/design/brand/autune-wordmark.svg
 * (the user, 2026-10-06); these are its paths, unchanged.
 *
 * Two departures from that file, both for use inside a layout:
 * - The viewBox is cropped to the strokes (stroke width included). The
 *   canonical file keeps clear space around them, which here would push the
 *   mark off the edge it aligns to.
 * - The ink stroke is `currentColor` and the wave reads the accent token, so
 *   the mark follows the theme instead of staying #16191F on a dark surface.
 *
 * `height` sets the size; the width follows the 620 : 138.5 ratio.
 */
export function Wordmark({ height = 20, className = "" }: { height?: number; className?: string }) {
  return (
    <svg
      viewBox="0 -36 620 138.5"
      height={height}
      width={(height * 620) / 138.5}
      role="img"
      aria-label="Autune"
      className={`block flex-none ${className}`}
      fill="none"
      strokeWidth={14}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path
        d="M7 50A45.5 45.5 0 1 0 98 50A45.5 45.5 0 1 0 7 50ZM98 7V93M138 7V58.5A37 37 0 0 0 212 58.5M212 7V93M272 -29V69A24 24 0 0 0 296 93H300M246 7H300M522 50H613A45.5 45.5 0 1 0 602.36 79.25"
        stroke="currentColor"
      />
      <path
        d="M336 7V58.5A37 37 0 0 0 410 58.5V41.5A37 37 0 0 1 484 41.5V93"
        stroke="var(--color-accent-default)"
      />
    </svg>
  );
}
