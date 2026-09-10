import { Button } from "@/shared/ui";

/**
 * The three ways to put a name to a voice the pipeline separated but could not
 * identify.
 *
 * All three end with a person saying who it is. None of them guesses: a
 * similarity score high enough to show is not high enough to write into the
 * record, because the cost of being wrong is a commitment filed under somebody
 * who never made it.
 *
 * "확인 DM 보내기" is the one that also enrols the voice for next time (S16), so
 * it leads. It is a text button rather than a fill: `ui-spec.md` allows one
 * primary per screen, and on S13 that is 녹음 종료 in the right rail — the
 * action you cannot take back.
 */
export function UnidentifiedSpeaker({
  speaker,
  utteranceCount,
  onAssign,
  onEnterName,
  onSendConfirmation,
}: {
  speaker: string;
  utteranceCount: number;
  onAssign?: () => void;
  onEnterName?: () => void;
  onSendConfirmation?: () => void;
}) {
  return (
    <div
      className="flex flex-wrap items-center gap-2"
      style={{
        paddingBlock: "var(--space-12)",
        color: "var(--color-signal-attention)",
        fontSize: "var(--text-status)",
      }}
    >
      <span>
        {speaker} · 발화 {utteranceCount}건 · 누구인지 확인이 필요합니다
      </span>
      <Button tone="text" size="compact" onClick={onSendConfirmation}>
        확인 DM 보내기
      </Button>
      <Button tone="quiet" size="compact" onClick={onAssign}>
        참석자 중에서 지정
      </Button>
      <Button tone="quiet" size="compact" onClick={onEnterName}>
        직접 입력
      </Button>
    </div>
  );
}
