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
 *
 * **No utterance count.** This line used to read `화자 2 · 발화 41건`, which is
 * a per-person speech volume wearing a number instead of a name. Everyone in
 * the room knows who 화자 2 is, so the anonymity is not real —
 * `privacy.md` section 3 names this exact shape in its Forbidden list: "An
 * 'anonymized' distribution across a small meeting — in a four-person meeting,
 * a distribution identifies everyone." The count was not doing any work either:
 * the prompt asks who a voice belongs to, and how much it said does not help
 * answer that.
 */
export function UnidentifiedSpeaker({
  speaker,
  onAssign,
  onEnterName,
  onSendConfirmation,
}: {
  speaker: string;
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
      <span>{speaker} · 누구인지 확인이 필요합니다</span>
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
