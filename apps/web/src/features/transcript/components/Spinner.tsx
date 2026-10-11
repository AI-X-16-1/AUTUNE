/**
 * A small ring that turns while the live screen waits on something it cannot
 * show yet: the last row after 녹음 종료, the upload, the move to the meeting
 * page. Always beside a sentence saying what it waits for; under reduced
 * motion it stands still and the sentence carries it.
 */
export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <span
      aria-hidden
      className="inline-block shrink-0 animate-spin rounded-full motion-reduce:animate-none"
      style={{
        width: size,
        height: size,
        border: "2px solid currentColor",
        borderRightColor: "transparent",
      }}
    />
  );
}
