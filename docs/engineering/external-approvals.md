# External approvals and registrations

WBS 1.7, 1.8 and 1.9. These are the only W1 tasks that depend on somebody outside
the team, so they are the only ones that can be late through no fault of ours.
Start every one of them before writing the code that needs it.

Every credential below has a variable already reserved in `.env.example`. Fill
`.env`, never commit it, and never paste a token into a document, an issue, or a
Slack message.

| Task | Owner | Blocks | Lead time |
| --- | --- | --- | --- |
| 1.7 HuggingFace access, GPU instance | 김민경 | A's diarization (W2) | Model gates are usually instant to a few hours; GPU quota can take a day |
| 1.8 Slack app, Notion integration | 강민구 | Slack surfaces in A/B/E (W3), Notion sync (W4) | Self-serve, same day |
| 1.9 AI Hub data terms | 김민경 | B's classifier training (W2) | Data is already downloaded; the terms question is the open part |

---

## 1.7 HuggingFace and GPU

### Model gates

`AUTUNE_AUDIO_WHISPER_MODEL=large-v3` needs no gate — `openai/whisper-large-v3`
is open.

Pyannote is gated in **three** places, not one. The pipeline loads the other two
itself, so a token granted only the pipeline fails partway through loading with
an error naming a model you never asked for.

1. Sign in to huggingface.co and accept the conditions on all three:
   `pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0`, and
   `pyannote/speaker-diarization-community-1`. The third is new in
   pyannote.audio 4.x and appears in no 3.x tutorial, which is what makes it the
   one people miss.
2. Create a token with **read** access (Settings → Access Tokens).
3. Put it in `AUTUNE_AUDIO_HF_TOKEN`.
4. Verify before W2 starts, on the machine that will run it:

   ```bash
   uv run python -c "
   from pyannote.audio import Pipeline; import os
   Pipeline.from_pretrained('pyannote/speaker-diarization-3.1',
                            token=os.environ['AUTUNE_AUDIO_HF_TOKEN'])
   print('gate ok')"
   ```

   `token=` rather than `use_auth_token=`: the 3.x argument no longer exists in
   4.x. Details in `environments.md`.

The first successful load downloads the weights into `AUTUNE_MODEL_CACHE`. Do
that once on purpose rather than during a demo.

### GPU

`prd.md` section 9 names Railway or an AWS GPU instance. Two things to settle
before W2, because both have a waiting period:

- **What the instance is for.** Whisper `large-v3` on CPU through `whisper.cpp`
  is the documented plan for A (`modules/audio.md`, AI stack), so the GPU is for
  the training runs, not the transcription path. B's classifier fine-tune on a
  few thousand rows finishes on a free Colab T4, so the GPU requirement is
  really A's and E's.
- **Quota.** A new AWS account has no GPU instance quota by default; the
  increase is a support request, not a setting. If the answer is Railway,
  confirm it offers a GPU plan on the account tier we are on.

*Verify before committing to a provider — pricing and GPU availability on both
platforms change, and neither has been checked for this project.*

---

## 1.8 Slack app and Notion integration

### Slack

The bot runs in Socket Mode for local development
(`AUTUNE_SLACK_APP_TOKEN`, per `.env.example`). Socket Mode needs an
**app-level** token in addition to the bot token, and they are issued in
different places, which is the usual first stumble.

Create the app from a manifest at api.slack.com/apps → Create New App → From an
app manifest. This manifest covers every Slack surface the module docs describe:
`/autune` and the analysis-complete notification (A), the action-item card thread
and confirmation DMs (B), and the personal speaking-ratio DM (E).

```yaml
display_information:
  name: AUTUNE
  description: 회의를 추적 가능한 구조로 바꿉니다
  background_color: "#16264a"
features:
  bot_user:
    display_name: AUTUNE
    always_online: false
  slash_commands:
    - command: /autune
      description: 회의 분석을 시작하거나 결과를 봅니다
      usage_hint: "[start|status|results]"
      should_escape: false
oauth_config:
  scopes:
    bot:
      - commands          # /autune
      - chat:write        # action item cards, analysis-complete notice
      - im:write          # confirmation DMs, speaking-ratio DM
      - channels:read     # resolve the meeting channel
      - groups:read       # same, for private channels
settings:
  interactivity:
    is_enabled: true      # buttons on the action-item card
  socket_mode_enabled: true
  org_deploy_enabled: false
  token_rotation_enabled: false
```

Then:

1. Install to the workspace → copy the **Bot User OAuth Token** (`xoxb-`) into
   `AUTUNE_SLACK_BOT_TOKEN`.
2. Basic Information → **Signing Secret** into `AUTUNE_SLACK_SIGNING_SECRET`.
3. Basic Information → App-Level Tokens → generate one with `connections:write`
   → `AUTUNE_SLACK_APP_TOKEN`.
4. Invite the bot to the channel the team will demo in. A bot that is not a
   member cannot post, and the error is a silent `not_in_channel`.

**`users:read` and `users:read.email` are deliberately absent.** They were in an
earlier draft of this manifest, to join a Slack account to a `users` row. Between
them they read the whole workspace user directory, which is the broadest claim on
a customer's data on the list, and they buy one join key.

The app was created without them, and `auth.test` confirms what was granted:
`channels:read`, `chat:write`, `commands`, `groups:read`, `im:write`.

The join comes from account linking instead — the user signs in and links their
own Slack account, so we learn the identity of whoever linked and never read the
directory. `packages/core/oauth` is that path; Google is wired and Slack reuses
the same shape.

The cost is real and belongs here rather than in a footnote: **until account
linking ships, assignee mapping has no source.** `ui-spec.md` already expects
that state — *"Creation is held back when the assignee is unmapped."* See #70.

Do not widen this list without a reason written down.

### Notion

Notion needs three steps and each one fails differently. Creating the integration
gives you a token; the token sees nothing until you share the target database
with it; and sharing is still not enough unless the integration was granted the
capability to read.

1. notion.so/my-integrations → New integration → internal.
2. Capabilities: **Read content, Update content, Insert content**. No user
   information capability — B sends an assignee name, not a Notion identity.
3. Keep the token. It does **not** go in `.env`: #57 moved Notion and Calendar
   credentials into `team_integrations`, encrypted, one row per team,
   configured on screen S28. `.env` carries only the encryption key.
4. Create the action-item database, then open it → **⋯ → Connections → Connect
   to → AUTUNE**. Skipping this makes every API call return "could not find
   database".
5. Keep the database id from its URL — the 32-character hex between the
   workspace name and the `?v=`. It belongs in the same row's `config`, which is
   JSONB precisely because each service needs a different shape.
6. **Verify, because step 2 fails quietly.** An integration created without
   *Read content* still passes every check that looks like a check:

   ```bash
   # the token is valid                     -> 200, bot name and workspace
   GET  /v1/users/me
   # the database is shared                 -> 200, the database is listed
   POST /v1/search
   # the database can be read               -> 200 with only id and object,
   GET  /v1/databases/{id}                  #    no title and no properties
   # what actually tells you                -> 403 restricted_resource
   POST /v1/databases/{id}/query
   ```

   Three of the four look fine. The read returns **200 with an empty shell**
   rather than 403, so the obvious reading is "the database is empty" or "the
   API version is wrong", and the missing capability is the last thing anyone
   checks. Query the database: that is the call that says
   `Insufficient permissions for this endpoint`.

   Fix it at notion.so/my-integrations → the integration → **Capabilities**.
   `create_page` needs *Insert content* as well, so a write fails the same way
   later if only reading was granted.

The database needs the properties B actually sends, per
`ExtractionResult.action_items` in `../architecture/contracts.md`:

| Notion property | Type | Source field |
| --- | --- | --- |
| 작업 | Title | `description` |
| 담당자 | Text | `assignee` |
| 마감일 | Date | `due_date` |
| 상태 | Select — `needs_confirmation` / `todo` / `in_progress` / `done` | `status` |
| 회의 | Text | `meeting_id` |

Text, not Person, for 담당자: B resolves an assignee to a `user_id` in our own
`users` table, which is not a Notion account, and `privacy.md` section 6 says to
send only what the feature needs.

Nothing else goes to Notion. Not the utterance text, not the source utterance
ids, not the transcript. `packages/integrations/privacy.py` is where that is
enforced, and it is the file to change if that ever needs to move — not the
call site.

Jira was evaluated and dropped from the product (#82, 2026-09-10) — see "Who
owns a credential when its creator leaves" below for why. Nothing further to
register for it.

---

## Who owns a credential when its creator leaves

Every token below is created by a person, and the obvious question is what breaks
when that person's account goes away. Checked 2026-09-09 against each vendor's own
documentation. The answer is different for all three, and only one of them is a
real problem.

| | Survives the creator leaving? | Why |
| --- | --- | --- |
| Slack | Yes, for bot scopes | Bot users, slash commands and incoming webhooks *"will remain active"* when a member is deactivated. Only *"apps that require member-specific permissions"* deactivate, and *"API tokens are revoked"* refers to that member's own user tokens |
| Notion | Yes, guaranteed in writing | An internal connection is *"its own bot user"* scoped to the workspace, and *"Access persists independently of users. If the user who shared a page leaves the workspace, the connection retains access to that page."* Every Workspace Owner sees every internal connection in the Developer portal, *"including connections created by others"* |
| Jira | **No** — dropped (#82) | Both auth paths are personal. An API token pairs with `AUTUNE_JIRA_EMAIL` — that pairing *is* the personal identity. OAuth 2.0 (3LO) is no better: it accesses the API *"on a user's behalf"*, constrained by that user's permissions |

So the practical rules for W1:

**Slack — install as a Workspace Owner, and use bot scopes only.** Our manifest
already requests bot scopes only, so nothing is user-bound. One residual trap:
if a bot is deactivated and later re-enabled, *"their corresponding tokens are
automatically regenerated"* — a regenerated bot token means `AUTUNE_SLACK_BOT_TOKEN`
is stale and every call 401s. Read a 401 from Slack as "someone re-enabled the
app", not as "the token leaked".

**Notion — the connection is safe; the database's location is not.** Notion
guarantees the connection outlives its creator, but it says nothing about where
the page lives. A database created in somebody's **private** space disappears with
their account, and the surviving connection then has access to nothing. So create
the action-item database in a **teamspace**, not a private page. This is the actual
failure mode, and it is not a credential problem at all.

**Jira — this is why it was dropped, not deferred (#82).** The failure is not
hypothetical: Atlassian's own docs say a 3LO refresh token dies if *"The user's
Atlassian account password has been changed"*, and the only remedies offered
are *"Change the password back to the original password, or initiate the
entire authorization flow from the beginning again."* Deactivation is not even
discussed, which is worse than being discussed. Rotating refresh tokens also
expire after **90 days** of inactivity and are single-use — each exchange
disables the one you sent.

The only paths that are not person-bound — a **Forge or Connect app** (*"you
don't need to configure authentication, it is built into the app frameworks"*)
or a dedicated Atlassian **service account** — both need registration, review
or a licensed seat beyond this project's six weeks. If Jira is ever revisited,
start there rather than with the personal-token path this section describes.

### Beyond the six weeks

The variables in `.env.example` are one global set — one Slack token, one
Notion token. That shape is correct for our own workspace and wrong for a
product, where each customer authorizes their own workspace and we hold a
credential per team. E's `core/team-integrations` branch already implements
exactly that (per-team credentials, encrypted, 624 lines) and is still unmerged.
When it lands, the product path is: Slack app distribution and a Notion
**public** integration (internal connections *cannot* span workspaces) — each
writing into that per-team store rather than into `.env`.

Note what that does and does not solve. It removes *our* dependence on one
person's account. It does not remove the customer's: a 3LO grant is still tied
to whoever clicked Authorize at their company. Slack and Notion carry the grant
at workspace level and are fine.

## 1.9 AI Hub data terms

The corpus is already downloaded — 「주요 영역별 회의 음성인식 데이터」, training
and validation splits. What is not settled is what we are allowed to do with what
comes out of it.

`modules/extraction/scripts/README.md` records one hard fact about the data:
`annotation_level` is **원시**. There are no class labels in it. So the corpus is
a source of Korean meeting utterances, and every label on the evaluation set is
hand-made. That is a scope fact, not a licensing one, and it is already the
reason the evaluation-set estimate in the WBS is low.

The licence questions now have answers. They come from AI Hub's 이용정책 page,
read 2026-09-09. The dataset's own page (`dataSetSn=464`) carries no separate
licence text, so the general 개방 데이터 policy is what applies. 수행기관(주관) is
㈜솔트룩스, with ㈜소리자바, ㈜디그랩, ㈜비투엔 and 경북대학교산학협력단 participating —
that matters, because two of the four answers below name 수행기관 as the party we
have to talk to.

**1. Commercial use — permitted for development, but selling needs an agreement.**
Open AI Hub data may be used for *"영리적・비영리적 연구・개발 목적"*, so building
AUTUNE on it is fine. But the policy continues: *"판매 등 상업적 이용을 희망하는
경우 수행기관과 별도 협의가 필요합니다."* A paid plan is 판매. **This is an action,
not a note:** somebody has to open that conversation with 솔트룩스 before the paid
plan ships. It does not block the six weeks — the demo is not a sale — and it does
block the business model, so the pitch should say "협의 필요" rather than imply the
question is closed.

**2. Redistribution — forbidden, and this reaches our evaluation set.**
*"제공 받은 AI데이터 등을 수행기관 등과 한국지능정보사회진흥원의 승인을 받지 않은
다른 법인, 단체 또는 개인에게 열람하게 하거나 제공, 양도, 대여, 판매하여서는 안됩니다."*
`dataset/` being gitignored is the right handling of the corpus. The consequence
people miss is that **the hand-labelled evaluation set is the same data**: 300
utterances lifted out of the corpus are still the corpus. So the evaluation set
cannot be committed, and it cannot be shared as a file.

What works instead: each person downloads the corpus themselves under their own
AI Hub account, and what we share is **labels without text** — the utterance id
from the source JSON plus our class label, and nothing else. That is our own
annotation, keyed to data the recipient already holds legitimately. It also means
the label file needs the source file name and utterance id to be stable, which is
a requirement on B's preprocessing, not an afterthought.

**3. Attribution — required, and it extends to the model.**
*"반드시 한국지능정보사회진흥원의 사업결과임을 밝혀야 하며, 본 AI데이터 등을 이용한
2차적 저작물에도 동일하게 밝혀야 합니다."* A classifier trained on this data is a
2차적 저작물, so the attribution belongs wherever the model's output is presented:
the pitch deck, the landing page, and this repository. Draft line —
「학습 데이터: 한국지능정보사회진흥원 AI 허브 「주요 영역별 회의 음성인식 데이터」
(수행기관 ㈜솔트룩스)」.

**4. Cross-border transfer — needs a separate agreement.**
*"본 AI데이터 등의 국외 반출을 위해서는 수행기관 등 및 한국지능정보사회진흥원과
별도로 합의가 필요합니다."* Uploading the corpus to a GPU instance in a non-Korean
region is plausibly 국외 반출. **This constrains 1.7:** pick a Korean region for
any instance that touches training data, or keep training on a local machine. It
is also a reason not to hand corpus text to a hosted LLM for label generation
without checking where that endpoint runs — and B's plan does exactly that for
the *training* labels.

One thing the policy does not settle: it has no explicit clause on ownership of a
model trained on the data. Treat that as unresolved rather than permissive, and
fold it into the 수행기관 conversation in item 1.

For comparison, the other corpus is settled: AMI manual annotations v1.6.2 is
**CC BY 4.0**, which permits commercial use and requires attribution wherever
results are published. `docs/modules/extraction.md` already records that.

---

## Definition of done for W1

- `.env` on each developer's machine has `AUTUNE_AUDIO_HF_TOKEN`,
  `AUTUNE_SLACK_BOT_TOKEN`, `AUTUNE_SLACK_SIGNING_SECRET` and
  `AUTUNE_SLACK_APP_TOKEN` filled. Notion and Calendar are **not** in
  `.env` — they are per-team rows in `team_integrations` (#57).
- The pyannote gate check above prints `gate ok`.
- The bot answers `/autune` in the demo channel.
- A row can be created in the Notion database through the API.
- The AI Hub licence questions are answered above. The two that need
  somebody outside the team — selling (item 1) and cross-border transfer
  (item 4) — have an owner and a date.
