"use client";

import { useState } from "react";

import { Button } from "@/shared/ui";

import { searchRefusal } from "../materialSearch";
import type { MaterialSearchAnswer } from "../types";

/**
 * A question for the team's uploaded materials (#817), and what they say
 * about it. Shown only where the server says it answers one.
 *
 * **The question is a person's words, and it is kept nowhere here.** It
 * leaves in the request's body and lives in this component's state: the
 * input asks the browser not to remember it, and nothing is written to
 * storage or to the address. The screen says nothing of what the server
 * does with it: the route and the search write nothing and log counts, but
 * the question is also what the embedding server is sent, and what that
 * server keeps is not something this repository can show.
 *
 * **What comes back is masked text, shown as text.** An excerpt is a cut of
 * what was kept of a file -- there is no original to open, so a hit names
 * its material and links to nothing. The server's notice is its own
 * sentence and is shown as it is. Masked is not anonymous, and the box says
 * so as the upload form does: values of a set shape are hidden, a name
 * written in a sentence is not -- a member who only searches reads this
 * box and never the form.
 *
 * An answer belongs to the question it was asked with: typing a new one
 * takes the old answer away. And an excerpt does not outlive its material
 * on this screen -- `removed` names the materials deleted since, whose text
 * the server dropped at once.
 */
export function MaterialSearch({
  limit,
  onSearch,
  removed,
}: {
  /** The longest question the server takes. */
  limit: number;
  onSearch: (question: string) => Promise<MaterialSearchAnswer>;
  /** Ids of materials deleted on this screen since it opened. */
  removed: readonly string[];
}) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<MaterialSearchAnswer | null>(null);
  const meta = { fontSize: "var(--text-metaSmall)" } as const;
  const input = { ...meta, borderRadius: "var(--radius)", padding: "4px 8px" } as const;

  const submit = async () => {
    // The button is disabled in both cases; Enter in the box is not.
    if (busy || !question.trim()) return;
    setError(null);
    setAnswer(null);
    setBusy(true);
    try {
      setAnswer(await onSearch(question.trim()));
    } catch (cause) {
      setError(searchRefusal(cause, limit));
    } finally {
      setBusy(false);
    }
  };

  const shown = (answer?.hits ?? []).filter((hit) => !removed.includes(hit.material_id));
  // Every material that answered was deleted since: there is nothing left to
  // say of this answer, its notice included.
  const emptied = answer !== null && answer.hits.length > 0 && shown.length === 0;

  return (
    <section aria-label="올린 파일에서 찾기" className="flex flex-col gap-2">
      <form
        aria-label="자료 검색"
        className="flex flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <input
          aria-label="자료에 물을 내용"
          placeholder="올린 파일에서 찾을 내용"
          value={question}
          maxLength={limit}
          autoComplete="off"
          onChange={(event) => {
            setQuestion(event.target.value);
            setAnswer(null);
            setError(null);
          }}
          className="min-w-0 flex-1 border border-[var(--color-hairline)]"
          style={input}
        />
        <Button
          type="submit"
          tone="secondary"
          size="compact"
          loading={busy}
          disabled={!question.trim()}
        >
          찾기
        </Button>
      </form>
      <p className="text-[var(--color-ink-muted)]" style={meta}>
        올린 파일에서 보관 중인 글만 찾습니다. 링크로 등록한 Drive 파일은 Autune이
        내용을 읽지 않으므로 찾지 않습니다. 보이는 글은 전화번호처럼 형식이 정해진
        개인정보를 가린 글의 일부입니다. 문장 속의 이름은 가려지지 않으므로 그대로
        보일 수 있습니다.
      </p>
      {error ? (
        <span role="alert" className="text-[var(--color-signal-critical)]" style={meta}>
          {error}
        </span>
      ) : null}
      {answer !== null && !emptied ? (
        <div role="region" aria-label="찾은 내용" className="flex flex-col gap-2">
          {answer.notice ? (
            <p className="text-[var(--color-ink-muted)]" style={meta}>
              {answer.notice}
            </p>
          ) : null}
          {shown.length === 0 ? (
            <p className="text-[var(--color-ink-muted)]" style={meta}>
              올린 파일에서 찾은 내용이 없습니다.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {shown.map((hit) => (
                <li
                  key={hit.material_id}
                  className="flex flex-col gap-1 border-l-2 border-[var(--color-hairline)] pl-3"
                >
                  <span className="text-[var(--color-ink)]">{hit.title}</span>
                  <span
                    className="whitespace-pre-line break-words text-[var(--color-ink-muted)]"
                    style={meta}
                  >
                    {hit.excerpt}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {answer.more && shown.length > 0 ? (
            <p className="text-[var(--color-ink-muted)]" style={meta}>
              이 밖에도 답이 될 만한 자료가 더 있습니다. 질문을 더 구체적으로 적으면
              좁혀집니다.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
