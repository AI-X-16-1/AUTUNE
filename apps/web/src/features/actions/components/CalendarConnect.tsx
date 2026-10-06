"use client";

import { useEffect, useState } from "react";

import {
  disconnectCalendar,
  getCalendarConnection,
  googleCalendarConnectUrl,
} from "@/shared/api/auth";
import { Button } from "@/shared/ui";

import { getNotificationPause } from "../api";

/**
 * One button to put the person's own due dates on their own Google Calendar
 * (#435). Connected, a date they drag there comes back here within ten minutes.
 *
 * Only their own tasks go on it -- team work stays on this board -- and only
 * the person themselves can see whether they are connected. Auth is not a
 * module, so the calls go through `@/shared/api/auth` rather than
 * `/api/extraction`, the same way the sign-in screen does.
 *
 * Nothing shows until the status is known, and nothing shows for a visitor
 * without a session: a button that could only fail is worse than none.
 *
 * **Where this server also reads out-of-office time, it says so here** --
 * beside the button, before the person connects, and beside "연결됨" for
 * someone who connected earlier (PARKJAEKYUNG0525, review of #838). Putting
 * due dates in is what the connection was asked for; reading when somebody is
 * away is more than that, and a person agrees to what they were told. Where
 * the server does not read it (`calendar_leave` false, the default) the line
 * is absent, because it would be untrue.
 *
 * **What the same grant is used for by "다음 회의 잡기" is said here too**
 * (review of #872): that button is module C's, on the gap screen, and it
 * reads the titles of the person's coming events and writes into the one
 * they pick. It is said on every server, connected or not, as what happens
 * when that button is pressed -- so it claims nothing where the button is
 * absent. It includes that Google then sends its change notice to the people
 * already invited to that event, in the presser's name (#872's
 * `sendUpdates=all`): something done to other people with this grant is said
 * where the grant is asked for. C's notice on the team's Slack channel is not
 * said here -- it is not a use of the calendar connection. Each sentence
 * about what is *not* read is about one read
 * only: "no title, no other event" is true of the out-of-office read and
 * false of the picker.
 */
/** The person's own calendar: Google shows whichever account the browser is
 * signed in to, which is where a connected person's due dates were put. */
const GOOGLE_CALENDAR = "https://calendar.google.com/calendar/";

export function CalendarConnect() {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [readsLeave, setReadsLeave] = useState(false);

  useEffect(() => {
    let alive = true;
    // Not told is not read: a failed answer leaves the line off, and the
    // settings screen says the same thing from the same answer.
    getNotificationPause()
      .then((pause) => {
        if (alive) setReadsLeave(pause.calendar_leave === true);
      })
      .catch(() => {});
    void getCalendarConnection().then((status) => {
      if (!alive || status === null) return;
      setConnected(status.connected);
      const url = new URL(window.location.href);
      const result = url.searchParams.get("calendar");
      if (result === "connected" && status.connected) {
        setNote("캘린더를 연결했습니다. 내가 담당인 항목의 마감일이 내 캘린더에 들어갑니다.");
      } else if (result === "failed") {
        setNote("캘린더를 연결하지 못했습니다. Google 화면에서 캘린더 권한에 체크한 채로 다시 시도해 주세요.");
      } else if (status.connected && status.needs_reconnect) {
        // Connected with a Google client this server no longer uses: the stored
        // grant cannot be refreshed, so no due date goes out until a new one.
        setNote("캘린더 연결이 끊겼습니다. 연결을 해제한 뒤 다시 연결해 주세요.");
      }
      if (result !== null) {
        url.searchParams.delete("calendar");
        window.history.replaceState(null, "", url.toString());
      }
    });
    return () => {
      alive = false;
    };
  }, []);

  if (connected === null) return null;

  const connect = () => {
    const here = window.location.pathname + window.location.search;
    window.location.assign(googleCalendarConnectUrl(here));
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      const { revoked } = await disconnectCalendar();
      setConnected(false);
      setNote(
        revoked
          ? "캘린더 연결을 해제했습니다. 같은 Google 계정의 Gmail 연결도 다시 해야 할 수 있습니다."
          : "연결을 해제했습니다. Google 계정 설정에서 Autune 접근도 확인해 주세요.",
      );
    } catch {
      setNote("연결을 해제하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      {connected ? (
        <>
          <span
            className="text-[var(--color-ink-muted)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
          >
            내 Google 캘린더 연결됨
          </span>
          <a
            className="text-[var(--color-accent-default)]"
            style={{ fontSize: "var(--text-metaSmall)" }}
            href={GOOGLE_CALENDAR}
            target="_blank"
            rel="noopener noreferrer"
          >
            캘린더 열기
          </a>
          <Button tone="quiet" size="compact" loading={busy} onClick={disconnect}>
            연결 해제
          </Button>
        </>
      ) : (
        <Button tone="text" size="compact" onClick={connect}>
          내 Google 캘린더에 마감일 넣기
        </Button>
      )}
      {readsLeave ? (
        <span
          className="basis-full text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {connected ? "연결되어 있는 동안" : "연결하면"} 마감일을 넣는 것 외에, 내 캘린더의
          &lsquo;부재중&rsquo; 일정이 언제부터 언제까지인지도 읽습니다. 그 시간에는 아침
          요약과 월요일 요약을 보내지 않기 위해서입니다. 이때는 일정의 제목이나 부재중이
          아닌 일정은 받지 않으며, 읽은 시간은 저장하지 않습니다.
        </span>
      ) : null}
      <span
        className="basis-full text-[var(--color-ink-muted)]"
        style={{ fontSize: "var(--text-metaSmall)" }}
      >
        갭 화면의 &lsquo;다음 회의 잡기&rsquo;를 직접 누를 때에만, 내 캘린더의 앞으로 2주
        일정(제목과 시간)을 읽어 고를 수 있게 보여 주고, 고른 일정의 설명과 참석자 주소를 읽어
        그 설명에 갭 질문을 적습니다. 이때 그 일정에 이미 초대된 사람들에게 Google이 내
        이름으로 일정 변경 알림을 보냅니다. 읽은 일정 목록·설명·주소는 저장하지 않고, 어느
        일정에 적었는지만 나중에 지울 수 있도록 기록합니다.
      </span>
      {note ? (
        <span
          role="status"
          className="text-[var(--color-ink-muted)]"
          style={{ fontSize: "var(--text-metaSmall)" }}
        >
          {note}
        </span>
      ) : null}
    </div>
  );
}
