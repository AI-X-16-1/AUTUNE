# S34 Autune assistant — floating, bottom right (variant 1a adopted)

Design source: `#1a` in `AUTUNE Spec 05 에이전트.dc.html` (not yet in this
folder). Tokens: [`design-tokens.json`](design-tokens.json). Shared rules:
[`ui-spec.md`](ui-spec.md) section 0.

> **Status: screen spec only.** The layout, states and copy below are settled.
> Several behaviours assume backend work the agent layer does not have yet;
> section 9 lists each one against `../architecture/agent-layer.md`. Build the
> screen against what exists and leave those behaviours out until each is
> decided.

UI copy is Korean and is quoted as it appears on screen.

## 1. Placement

- A `position: fixed` layer above the app shell (200px sidebar + content). On
  every page, **hidden on S13 live transcription, S01 landing and the
  onboarding modal**.
- z-index: content < toast < **assistant** < modal. When a modal opens, the
  panel stays but is inert (no dim, pointer events blocked).
- While the assistant is open, toasts move to the bottom left.

## 2. Launcher (closed)

| Property | Value |
|---|---|
| Position | right 24 · bottom 24 |
| Size | height 48 · padding 0 18 0 14 · radius 24 (an exception reserved for the floating launcher) |
| Background / text | `ink.strong #16191F` / `#FFFFFF` · 600 13.5px |
| Contents | 22px accent circle ("AT", 700 9px) · "비서" · shortcut `⌘J` (mono 11.5, opacity .6) |
| Shadow | `0 2px 12px rgba(22,25,31,.18)` |
| Hover | background `#000` |
| Alert | when the assistant has something to say first (for example, an item became overdue): a 6px `signal.critical` dot at the circle's top right. No numeric badge |
| Shortcuts | `⌘J` / `Ctrl+J` toggles, `Esc` closes |

## 3. Panel (open)

Container: right 24 · bottom 92 (20px above the launcher) · **400 × 600** ·
`surface.panel` · radius 4 · shadow `0 2px 12px rgba(22,25,31,.10)` plus
`0 0 0 1px hairline`. Opens in 160ms ease-out, translateY 8 → 0 with opacity.
When the viewport is shorter than 760, height = `100vh - 116`.

### 3.1 Header (52)

- Left: "Autune 비서" (600 14) and a context label (400 12, muted) built from
  the current route: `"{page name} 보고 있음"`, or `"{meeting title} 보고 있음"`
  on a meeting page.
- Right: `↗` expand (32 square, quiet) and `×` close (32 square, quiet). `↗`
  switches to a right-docked 380-wide panel (Phase 2, variant 1b). The button
  may be hidden in the MVP.
- A hairline below.

### 3.2 Message area (flex 1, vertical scroll, padding 16, gap 14)

**User message**
- Right-aligned · max-width 82% · padding 10 12 · radius 4 · background
  `accent.selection #E9EBF6` · text `ink.strong` 400 13.5/1.6.

**Assistant message** (no bubble, full width from the left)
1. Body: 400 13.5/1.65 `ink.body`. Only key figures and names at 600
   `ink.strong`.
2. **Evidence rows** (optional): a hairline above, and one below each row.
   - Row = `[StatusDot] [title 500 13 ink.strong / meta 400 12 muted]`.
   - Meta: `{meeting or source} · {time code, mono} · {speaker}`.
   - Dots follow the five utterance kinds (decision = 7px hollow ring,
     commitment = ink dot, question = accent, concern = red, ambiguous = ochre).
   - Clicking a row opens that utterance on its meeting page (time-code
     anchor); the panel stays open.
3. **Action proposal block** (optional): padding 12 · radius 4 ·
   `surface.paper`.
   - Title 600 12.5 ink.strong, phrased as a question ("Jira SRCH에 2건
     생성할까요?").
   - Description 400 12/1.6 body: what is created, for whom, and how.
   - Buttons (compact 32): `생성` (primary) · `수정 후 생성` (secondary, sunken
     fill) · `아니요` (quiet).
   - After it runs, the block becomes a result row: ink dot + "SRCH-491,
     SRCH-492 생성됨" + an `열기` text button. On failure: red dot + reason +
     `다시 시도`.
4. While streaming: three dots after the body (6px, muted, opacity in sequence
   over 1.2s). Evidence rows and the proposal block appear together once the
   stream ends.

**System status message**: centred, 400 12 muted — "새 대화", "연결 끊김 · 다시
시도" and the like.

### 3.3 Composer (padding 12 16, hairline above)

- **Suggested question chips** (above the input, gap 6, wrapping): height 28 ·
  padding 0 10 · radius 4 · `surface.sunken` · 500 12 body. Two or three, built
  for the current page. A click sends at once. Once a conversation starts they
  follow the last answer.
- Input: height 40 · 1px `rgba(22,25,31,.2)` · focus 1.5px accent ·
  placeholder "회의·결정·액션에 대해 물어보세요". Enter sends, Shift+Enter adds a
  line (grows to four lines).
- `보내기`, primary default (40). Disabled when the input is empty or while
  streaming. While streaming the label becomes `중지` (secondary).

## 4. Context and suggested questions per page

| Route | Context label | Suggested questions |
|---|---|---|
| Home S05 | 홈 | 오늘 회의 전에 알아야 할 것 · 내 기한 초과 정리 |
| Meeting S15/S20 | {meeting title} | 이 회의에서 미정으로 남은 것 · 참석 안 한 사람에게 공유할 요약 |
| Action board S17 | 액션아이템 | 기한 초과만 보여줘 · {assignee} 담당 정리 |
| Gap report | 갭 리포트 | HIGH 갭 해소용 질문 초안 |
| Decision lineage S22 | {topic} | 이 결정이 바뀐 이유 · 관련 자료 |
| Dashboard S26 | 대시보드 | 이번 주 품질 점수가 바뀐 이유 |

Context payload: `{ route, entityType, entityId, selectedRowIds[] }`. The
design names module D's retrieval as the filter; see section 9.

## 5. Behaviour rules

1. **Evidence is required.** Every answer carries at least one piece of
   evidence (meeting and time code / material page / Slack thread). With none,
   it says "기록된 내용이 없습니다" and names what was searched.
2. **Nothing runs without confirmation.** Creating or changing a Jira issue, a
   Notion comment or page, a Slack DM, adding an agenda item and changing a due
   date all go through the action proposal block. Nothing runs on its own. What
   ran is recorded in the item's history (S18) as "비서를 통해 {user}가 실행".
3. **Permissions.** Never propose what the user could not do themselves (for
   example, changing an integration's settings).
4. **No per-person evaluation.** A question about one person's speaking volume
   or rank ("누가 제일 말 안 했어") is declined in one line: "개인별 발언량은
   본인에게만 제공됩니다." A question about the asker's own figures is answered
   to the asker only.
5. **PII.** Answer text follows the masked-token rule too (mono + dotted
   underline, `PiiToken`).
6. **Conversation history.** Per user, the last 30 days, never longer than the
   workspace retention policy. No header menu: opening the panel shows the last
   conversation, and "새 대화" is a text button under a system message.

## 6. States

| State | Shown as |
|---|---|
| Closed | Launcher only |
| Closed with an alert | Red 6px dot at the launcher circle's top right |
| Open, empty conversation | One greeting line in the message area ("{name}님, 무엇을 확인할까요?", 600 14) and three larger suggested-question chips (height 32) |
| Streaming | Three dots · `보내기` → `중지` |
| Action waiting | Proposal block shown; the input stays usable |
| Action running | The block's primary button in loading state (spinner + "생성 중") |
| Action done / failed | Result row (ink dot / red dot + `다시 시도`) |
| Offline or error | System message, input disabled |
| Width < 768 | Panel as a full-screen sheet (Phase 2) |

## 7. Accessibility

- Launcher `aria-label="Autune 비서 열기 (⌘J)"`; panel `role="dialog"
  aria-modal="false"`.
- Focus moves to the input on open and back to the launcher on close. The
  message area is `aria-live="polite"`.
- Every control at least 32px. Contrast as the tokens give it (text 4.5:1 or
  more).

## 8. Components

```
<AssistantLauncher hasAlert onToggle />
<AssistantPanel context>
  <AssistantHeader title contextLabel onExpand onClose />
  <MessageList>
    <UserMessage text />
    <AssistantMessage body streaming>
      <EvidenceList items: {kind, title, source, timecode, speaker, href}[] />
      <ActionProposal title description actions status result />
    </AssistantMessage>
    <SystemMessage text />
  </MessageList>
  <Composer suggestions onSend onStop disabled />
</AssistantPanel>
```

Reuse `StatusDot`, `Button`, `Input` and `ChipToggle` from `ui-spec.md`
section 2. The screen belongs in `apps/web/src/features/agent/`; the app shell
mounts it once.

## 9. Open against the agent layer as built

Each item is a place where this spec assumes something `agent/` does not do
today. None blocks drawing the screen; each blocks the behaviour named.

1. **Conversation history (5.6) versus storing no answer.** `agent/CLAUDE.md`
   rule 8 and `main/store.py` keep no answer text in `agent_runs`: an answer
   about one meeting can quote another, and would outlive that meeting's
   deletion (#449 review). Keeping 30 days of conversation reopens that
   decision. Until it is reopened, the conversation lives in the browser tab
   only.
2. **Free-form questions.** `/api/agent/chat` routes a question to one
   subagent and answers "no subagent fits this request" otherwise. Most
   suggested questions in section 4 ("기한 초과만 보여줘", "이 회의에서 미정으로
   남은 것") need an answer composed from read tools, not a subagent. This is
   where agent-layer.md section 3.3 names `create_agent` as the first thing to
   try.
3. **Who confirms an action (5.2).** Plan mode sends an L2 proposal to an
   approver holding its scope (agent-layer.md section 8). A `생성` button
   pressed by whoever asked is a different rule: either a new level the asker
   may confirm for themselves, or the block queues the proposal for the
   approvers and says so.
4. **Streaming and `중지`.** `/api/agent/chat` returns one response. Streaming
   is new work on the endpoint and the client.
5. **Context as a retrieval filter (section 4).** There is no module-D
   retrieval filter. The workable reading: the client sends
   `{ route, entityType, entityId }` and the run binds it as its scope, the way
   a triggered run already binds its meeting (`RunScope`).
6. **History in S18 (5.2).** "비서를 통해 {user}가 실행" names who confirmed.
   Module B's item history records which fields changed and when, and on
   purpose never who (`EditHistoryEntry`, #109, ADR 0003). Showing the name
   reopens that decision with B; "비서를 통해 실행" without a name does not.
7. **The launcher alert.** "Something to say first" has no source yet. The
   approvals queue (`GET /api/agent/pending`) is the nearest existing signal.
