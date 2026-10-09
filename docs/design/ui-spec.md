# AUTUNE UI Spec v2

Based on product plan v2. Screen IDs (S01–S34) map 1:1 to the design files.
Tokens: [`design-tokens.json`](design-tokens.json).
Design source: `AUTUNE Spec 00~05 *.dc.html`, `AUTUNE 실시간 전사.dc.html`.

> The design files are the source of truth for pixels. This document is the
> source of truth for rules. When they disagree, the design file wins on
> appearance and this document wins on behavior — and one of the two gets fixed.
>
> UI copy in the design files is Korean, which is correct: user-facing copy is
> Korean, while documents and code stay English. See `../engineering/workflow.md`.

## 0. Rules at a glance

| Item | Rule |
|---|---|
| Action color | One accent, `#3B4A9E`. Primary buttons, text buttons, links, active tabs, checks, focus rings. "Start recording" is accent too |
| Red | Recording dot and timer, overdue, processing failure, gap HIGH, delete confirmation. Never a button fill |
| Status expression | P1 inverted ink band (one per screen) / P2 6px dot + 12.5px/500 text / P3 colored figure + mono value + 2px bar |
| Dot colors | ink = confirmed (a decision is a hollow ring) · accent = in progress · ochre = needs a person · red = time and risk · hollow ring = pending |
| Buttons | 1 accent fill · 2 sunken fill (`#EBEAE5`) · 3 accent text · 4 muted text. Heights 32/40/48, radius 4, weight 600. At most one primary per screen |
| Destructive | Red text button → confirmation modal → the final button is an accent-filled "Delete" |
| Inputs | 40px, 1px `rgba(22,25,31,.2)`, focus 1.5px accent, error 1.5px red + message below |
| Tabs | 2px accent underline. Counts in mono. Selected toggle chip = `#E9EBF6` |
| Row | dot → title → meta → actions (right-aligned, compact, at most one primary). Separated by hairlines |
| Quotation | A single paper-colored block. No left bar, no decorative quote marks |
| Mono | Time codes · scores · IDs · dates · percentages only |
| PII token | Mono + dotted underline, hover tooltip "개인정보 자동 마스킹 · 원문 미저장" |
| Dark theme | A user preference. Unrelated to recording state |
| While recording | Red inset glow on the window frame, 3s cycle + REC timer in the top bar + timer block on the right. Theme-independent |
| Privacy | Per-person speech volume and ranking appear nowhere except the subject's own DM (S23) |

**App shell:** sidebar 200 (paper) — logo · "Start meeting" accent button · Home /
Action items / Gap reports / Decision lineage / Materials (P2) /
Dashboard · Settings · user. There is no Meetings entry: the meeting list is
Home, which is the lit entry inside a meeting too. Content panel top bar 56, hairline only. Top-bar
action order, left to right: quiet → text → secondary → primary.

**Logo:** the lowercase stroke wordmark in `docs/design/brand/` (chosen
2026-10-06). `autune-wordmark.svg` is canonical — ink `#16191F` (ink.strong)
with the "un" wave in `#3B4A9E` (accent); `autune-wordmark-dark.svg` swaps the
ink for the dark theme's ink.strong `#EDEEF0`. The wave stays accent in both
themes. `autune-icon.svg` is the square mark — the "un" wave alone, in accent,
so it needs no dark variant — used as the favicon. In the web app the wordmark is `Wordmark` in `shared/ui`, 18px tall in
the sidebar and the live top bar, 20px in the signed-out headers. Never set the
name as text in its place.

**Canvas widths:** 1280 for standard screens, 1440 for S13 (live transcript),
380 for S33 (desktop mini window).

## 1. Screen inventory

MVP / P2 follow section 11 (MVP vs Phase 2) of the product plan.
Modules: A Audio · B Extraction · C Gap · D Context · E Intelligence.

### Entry · Home (Spec 01)
| ID | Screen | Stage | Key elements and states |
|---|---|---|---|
| S01 | Landing · sign-in | MVP | Headline "회의는 끝났는데 실행은 시작되지 않았다면" · Google / Slack / magic link · email-sent state (resend after 60s) · error state · no team branding |
| S02 | Create workspace | MVP | Name (2–40) · role chips (= the `utterances.role` enum) · invite-email chips · import Slack members · 4-step progress |
| S03 | Onboarding empty home | MVP | 3-item checklist (connect Slack · enrol voice · Notion/Jira); done = ink dot, pending = hollow ring · dropzone (mp3/wav/m4a, 500MB; no duration limit, since nothing enforces one) · sample meeting |
| S04 | Voice enrolment modal | MVP | Two sentences · waveform · circular accent button · timer (red dot) · quality verdict · disabled under 8 seconds · only the embedding vector is stored |
| S05 | Home | MVP | Next meeting (single paper block) · "Things for me" (overdue → needs confirmation → due soon → in progress) · unresolved gaps · recent meetings (retention expiry D-n) |
| S06 | Create meeting modal | MVP | Title · date · start · end (optional) · attendee chips (warn when a voice is not enrolled) · audio-source radio (web mic / file) · Notion DB and Jira project checkboxes (per-meeting override) · P2 items shown disabled, except the end-of-meeting alert row, which can be ticked under the web-mic source with an end time (S14's small cut, #1147), and the agenda row, "어젠다 초안 자동 생성", which the form ticks itself and nobody can tick by hand: it is on when the chosen team has something a draft can be gathered from (open Jira issues, the team's decisions, open HIGH gaps of earlier meetings) and says where from. It stores nothing and no model is asked; the draft itself is drawn on S08 (#1147) |

### Before · during the meeting (Spec 02)
| ID | Screen | Stage | Key elements and states |
|---|---|---|---|
| S07 | Material library | P2 | List (name · source · linked meeting · updated) · tabs (all / documents / minutes / Notion / analyzing) · link a Notion page (children included, re-index on webhook) · failed rows in red + retry |
| S08 | Meeting detail (pre-meeting) · agenda | P2 | Draft agenda rows (number · title · rationale · minutes) · ochre dot = carried-over undecided item or missing material · row 05 awaiting input · linked materials on the right · carried-over items · regenerate. **A small cut is built (#1147):** the pre-meeting brief in the 컨텍스트 tab always carries an agenda-draft section, "이번 회의에서 다룰 문제", each entry under the name of where it came from and none of it written by a model: the open Jira issues the brief already had, then, for the one earlier meeting the brief names, its gaps nobody closed ("지난 회의의 미해결 갭") and its to-dos nobody finished ("지난 회의의 미완료 할 일" — 진행 전 and 진행 중, by title only, no assignee and no due date). A group shows its count, five entries and "외 n건"; with nothing from any source the section says "엮을 것이 없습니다." No numbered rows, rationale, minutes, materials or regenerate; nothing is stored |
| S09 | Pre-meeting brief | P2 | Slack, 30 minutes before · overdue actions · 2 undecided items · previous-decision summary · pattern warning · 3 buttons |
| S10 | Start recording modal | MVP | Input device and level · scheduled-end chip (P2) · attendee consent table (ink / ochre) · exclude non-consenting checkbox · start disabled at zero consent · "Upload a file instead" |
| S11 | Attendee consent DM | MVP | 4 explanatory rows · "동의합니다" (accent) / "이번 회의는 제외" (sunken) · no response = logged only, excluded from analysis · follow-up message when no voice is enrolled |
| S12 | File processing pipeline | MVP | 6 stages in the order the task runs them (upload → STT → diarization → delete original → PII masking · save → B/C/D); the original is deleted when diarization ends, before masking · done = ink + elapsed · running = accent · queued = hollow ring · failed = red + reason · detection counts on the right |
| S13 | Live transcript | MVP | **Light theme + recording frame glow.** REC timer in top bar · P1 undecided band (P2) · transcript rows (time code · speaker · body · 5-kind tag) · unidentified-speaker row in ochre (assign / enter manually / send confirmation DM) · related-material card (quotation + open material / cite in minutes / not related) · PII tokens · right rail: 56px timer · elapsed bar · waveform · pause/stop · "Decided so far" (10 min) · "Needs confirmation" · 5-kind detection counts |
| S14 | Undecided alert | P2 | Inverted ink band "종료 5분 전 · 결정되지 않은 사항 n건" · "질문으로 띄우기" inserts a suggested-question row into the transcript. **A small cut is built (#1147):** on a live recording only, five minutes before an end time typed on S06 and kept in the browser tab (not stored), the band reads "종료 5분 전 · 이전 회의의 미해결 갭 n건" and lists the team's open HIGH gaps from earlier meetings. It does not find what this meeting left undecided, and "질문으로 띄우기" is not built |

### After the meeting (Spec 03)
| ID | Screen | Stage | Key elements and states |
|---|---|---|---|
| S15 | Review · edit summary | MVP | Tabs (summary / actions / gaps / context / transcript) · inline summary editing · 3 decisions (ambiguous ones ochre + confirm as decision / resend DM / delete) · 4 actions (unassigned = accent "담당 지정") · right rail "needs confirmation" ×3 (speaker / ambiguous / low-confidence span) · PII count + report · delivery targets (Slack · Notion · personal DM) · editable for 24h after confirmation |
| S16 | Speaker confirmation DM | MVP | Quoted candidate utterance · similarity · "제 발화입니다" (accent) / "아닙니다" / "다른 발화 듣기" · yes → store the embedding |
| S17 | Action board | MVP | 4 columns (Needs confirmation = Autune-only · To Do · In Progress · Done) · card (title → reason → assignee and due date → Jira, Notion) · selected card 1.5px accent · a status change also moves the Jira issue · a card is dragged to another column: the same change as the drawer's status select, applied at once; a drop out of Needs confirmation confirms the item and one line says so afterwards; a drop on the card's own column does nothing; a candidate and a card whose drawer is open cannot be dragged; there is no keyboard or touch drag, the status select is that path · broken link in red text · the board is a worksheet (ADR 0006): an item is added by hand, edited and deleted here, and an item below the confidence threshold sits in a candidate section instead of being dropped |
| S18 | Action detail drawer | MVP | Assignee / due date / status · source utterance quotation + confidence + the raw text the due date was parsed from · integration rows (Jira issue / Notion page, checkbox / Slack thread) · history |
| S19 | Ambiguous agreement DM | MVP | Quoted utterance + context · a link to the meeting's 액션 tab, where the speaker's own open questions show the 3 choices (confirm = accent / decision / deny) — in-Slack buttons wait for a click receiver (#585) · no response in 72h = undecided |
| S20 | Gap report | MVP | HIGH expanded (title · level as text + score + 2px bar · description · resolving-question block · 3 buttons) · MEDIUM collapsed · LOW listed separately · right rail template comparison (covered / partial / missing) · topic × role density (never per person) |
| S21 | Gap question thread | MVP | Slack thread · answer → classified by B → decision recorded automatically + confirmation reply · "결정 아님" reverts it |
| S22 | Decision lineage | MVP (graph P2) | Topic list on the left (with revision count) · timeline nodes (hollow ring = original · ochre = changed, with a reason block naming absentees and the NLI label · ink = current) · linked materials · Notion page links |
| S23 | Speaking-ratio DM | MVP | Subject only · large percentage + 2px bar · even-share baseline · notice that it is not stored server-side · recent trend (local) · turn off · (P2, once built) influence map delivered the same way — subject only, never a shared card (#28) |
| S24 | Role-specific summary | P2 | Slack tabs (developer / business / design) · 4 rows (decisions · undecided · my actions · materials) · one source, different renderings |
| S25 | Next agenda · scheduling | P2 | Proposal modal · title and time (when everyone is free) · draft agenda (gaps · actions · carried-over) · Calendar invite and Notion page checkboxes · room booking (P2) |

### Analytics · settings · Phase 2 (Spec 04)
| ID | Screen | Stage | Key elements and states |
|---|---|---|---|
| S26 | Dashboard | MVP basic | Quality score (A–F + 8-week bars) · role-pair heatmap (5-step greyscale) · gap-type distribution (2px bars) · prediction (P2, probability in mono) · topic recurrence (red/ochre figures) · action completion rate. No influence map here — subject-only, delivered like S23 (#28) |
| S27 | Weekly report | MVP basic | Slack, Mondays 09:00 · 3-metric grid · 3 rows (carried over · PM–Data gap · decision change) |
| S28 | Settings › Integrations | MVP | Slack (channel · DM items · slash command) · **Notion** (minutes DB · property mapping · action DB · decision DB · material source P2 · PII masking always applied) · **Jira** (one-click connect · project · reconnect when the grant lapses) · Calendar (not connected = hollow ring + accent button) · 동기화 기록 (a text button under the team's three connections; a centred window like S18's, not a side drawer, for the team the screen is on: "보내지 못한 것" -- each copy of an action item that failed and still stands, with its tool, the kind of failure and the time, leading to that meeting's 액션 tab, where the card has "다시 시도" -- and "최근에 보낸 것" -- the latest copies made, each with "Notion에서 열기" / "Jira에서 열기" or "내 캘린더". Newest first, thirty of each; a calendar row only on the reader's own calendar. Not a log of every attempt, and nothing is retried from the window. Choosing another team closes it) |
| S29 | Settings › Privacy and retention | MVP | 7 policy rows (delete original · masking · extra categories · retention 30/90/180/365 days · speaking ratio · consent every meeting or first only · account deletion) — rows that cannot be changed show an "항상 켬" dot · my data (meeting count · embeddings · consents · DMs) · download / delete embeddings / delete everything (red text → modal) |
| S30 | PII miss report modal | MVP | Selected span highlighted (`#E9EBF6`) · category chips · masked immediately and propagated · add a pattern rule · scan for similar spans |
| S31 | Meeting-needed proposal DM | P2 | 3 trigger-rationale rows · proposal block (time · attendees · room · congestion avoided) · "이대로 예약" (accent) / "시간 바꾸기" / "필요 없음" |
| S32 | Room booking | P2 | Date chips · attendee-overlap and congestion heat rows · recommended-slot radio (selected = selection background) · Teams Rooms / in-house API |
| S33 | Desktop mini window | P2 | 380 wide · dark · timer · waveform · 3 rows of recent decisions/commitments/materials · full screen / pause / stop |

### Agent assistant (Spec 05 · variant 1a adopted)
| ID | Screen | Stage | Key elements and states |
|---|---|---|---|
| S34 | Assistant — floating launcher and panel, bottom right | MVP | On every page except S01, S13 and onboarding · launcher → 400×600 panel · the current page attached as context · evidence rows · action proposal block · suggested-question chips · details and what is still open against the agent layer: [`agent-assistant.md`](agent-assistant.md) |

## 2. Shared components

**StatusDot** `size=6 | variant=confirmed|progress|attention|critical|idle | hollow` (for decisions: 7px with a 1.5px border)

**Row** `[dot] [title / meta] [actions…]` — actions are compact, right-aligned, at most one primary. Hairline beneath.

**Band (P1)** ink background + white text + red dot + white-filled compact button. One per screen.

**ScoreLabel (P3)** colored word (high / medium / low) + mono score + 2px bar. Below the threshold, only the value's color changes.

**Button** `tone=primary|secondary|text|quiet|destructiveText · size=compact|default|hero · loading · disabled` (`#EBEAE5` + ink at 35%)

**Tabs** 2px accent underline, counts in mono.
**ChipToggle** selected `#E9EBF6` / `#2C3878`; unselected `#EBEAE5` / `#3A3F47`.

**Input** 40px, border at 0.2 alpha, focus 1.5px accent, error 1.5px red.
**Search** sunken fill, no border.

**Checkbox / Radio** 16px; selected = accent fill / 5px ring.
**Quote** paper block.
**PiiToken** mono + dotted underline + `title` tooltip.

**RecordingFrame** a top-level `pointer-events: none` overlay on the window, driven by `motion.recording.glow`. Paused = animation stopped + grey line. Ended = removed. No input = red text "입력 없음" where REC sits. `prefers-reduced-motion` = a static 2px line.

**Waveform** 3–4px bars on the amplitude ramp (`color.*.waveform`), aligned to the baseline, gap 2–3px.

**SlackBlocks** status uses "●" + text rather than emoji, hierarchy through weight, at most 3 buttons, only the first is primary.

## 3. Data and state mapping

- **Utterance kinds (B):** commitment = ink dot · decision = hollow ring · open question = accent dot · concern = red dot · ambiguous = ochre dot. Ambiguous → DM → promoted to commitment or decision when confirmed, demoted to concern when denied.
- **Meeting state:** scheduled (hollow ring) · recording (red) · analyzing n% (accent) · awaiting confirmation (ochre) · analysis complete (ink) · delivered (ink) · retention expiry D-n (grey dot, muted text).
- **Gap level:** HIGH ≥ 0.7 red · MEDIUM 0.5–0.7 ochre · LOW grey. HIGH shown by default; the threshold is configurable.
- **Action column ↔ Jira:** Needs confirmation (Autune-only, not yet created) · To Do · In Progress · Done. An assignee Jira cannot match leaves the issue unassigned.
- **Notion:** minutes DB (a page on confirmation) · action DB (optional, two-way) · decision DB (optional) · material source (P2, re-index on webhook). PII masking applies to every write and cannot be disabled.
- **Privacy:** the original audio is deleted immediately · unmasked text is never stored · speaking ratios are not stored server-side · retention defaults to 90 days · consent logs are kept for audit · on account deletion, utterances and embeddings are deleted while actions and decisions are anonymized to "전 멤버".

## 4. Implementation notes

- The dark theme swaps the token set only, via `data-theme="dark"`. RecordingFrame, Band, and signal semantics are identical in both themes.
- Every list uses the Row component. No card inside a card; a card is one layer, used only where a surface break is needed.
- Charts use the 5-step greyscale ramp (`color.*.chart`) only. Signal colors appear on numeric text, never as chart fills. The ramp is the surface/ink scale rather than a separate palette, so the dark ramp follows from the dark theme with no additional design needed.
- Do not use uppercase letter-spaced labels, emoji, gradients, or shadows outside modals and drawers.
- Feature folders in `apps/web/src/features/` map to backend modules; shared components above live in `src/shared/ui/`. See `../engineering/conventions.md`.

## 5. Privacy constraints that are UI-visible

These are the same rules as `../architecture/privacy.md`, stated as interface obligations:

- No screen, export, or Slack surface may show one person's speaking ratio to anyone else. S23 is the only surface, and it is a DM to the subject.
- The topic × role density on S20 and the heatmap on S26 are role-level. Never render them per person, and never let a role of size one be selectable in a way that identifies an individual.
- Masked spans render as `PiiToken`. There is no "reveal original" affordance, because the original is not stored.
- Any screen showing a transcript also shows the retention expiry, so a user is never surprised by deletion.
