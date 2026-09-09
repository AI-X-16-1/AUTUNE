# Integrations

`packages/integrations` wraps every service Autune talks to: Slack, Notion,
Jira, Google Calendar. Modules call these clients rather than an API directly.

## Why a shared wrapper

All five modules notify through Slack. Without a shared client they would get
five retry policies, five ways of failing, and — the reason that matters — five
separate places where personal data can leave our infrastructure.

## The outbound boundary

This is the last code that runs before data leaves. Every client calls
`check_outbound()` on the text it is about to send:

| Guard | Refuses |
| --- | --- |
| `assert_masked` | Text still containing a phone number, national ID, card number, email or account number. Masked values keep their shape (`010-****-5678`), so the guard fires only on the unmasked original |
| `assert_within_size` | A payload over 4,000 characters — send what the feature needs, never the whole meeting |
| Nesting | Both checks run over every string in the structured payload, not only the top-level text |
| `assert_personal_delivery` | Data describing one person going anywhere but that person's own DM |

An exception from these names the categories found, never the values: an
exception message reaches error tracking, which is itself a third party.

**The guard runs in `HttpClient.request`, not in each method.** Every client
method builds a body and hands it to the transport, which checks every string in
it before the request leaves. A per-method call would be a step someone forgets
when they add the next endpoint, and forgetting it is silent — which is how the
hole below happened in the first place.

A client may declare `addressing`: keys whose values say *where* a request goes
rather than *what it carries*. `CalendarClient` declares `attendees`, because an
invitee's address is supplied by the feature, not extracted from a meeting, and
checking it would refuse every invitation. Declaring nothing checks everything,
so forgetting to declare fails closed.

**Pass the structured payload, not just the text.** A rich message carries its
content in a nested structure and leaves a bland summary at the top: a Slack
Block Kit `text` field is the notification preview, and the message is in
`blocks`. A guard reading only `text` checks the least important field, and
every rich message walks past it. `check_outbound(text, destination=...,
payload=blocks)` walks the whole structure.

This was a real hole, not a hypothetical one — it shipped, and it was found
while building the first message that used blocks.

`SlackClient.send_personal()` is the only path for data that belongs to one
person, and is what the speaking-ratio DM (S23) uses. There is no channel
variant and no administrator override.

## Testing without credentials

`autune_integrations.fakes` provides `FakeSlack`, `FakeNotion` and `FakeJira`.
They record instead of sending and **run the same privacy guards**, so a test
that would have leaked fails in tests too. Mock external services here, never
with network calls.

```python
from autune_integrations.fakes import FakeSlack

slack = FakeSlack()
slack.post_message("#squad", "갭 리포트가 준비되었습니다")
assert slack.channel_messages[0].channel == "#squad"
```

## Scope

W1 defines the boundary, the error split and the guards. The full API surface is
filled in during W3 by the owner who needs it — extraction for Notion and Jira,
context for Calendar. Writing the rest before the first real call would produce
an abstraction that fits nothing.

`TransientIntegrationError` is worth retrying (timeout, rate limit, 5xx);
`PermanentIntegrationError` is not (bad credentials, missing resource,
malformed request). Celery tasks retry only the first.

## Slack handlers

`apps/bot` is a Bolt for Python app that collects handlers by iterating the
module list, exactly as `apps/api` collects routers. Put a handler in your
module's `slack.py`; nobody edits `apps/bot`.

Registration is separated from construction (`register_all` versus
`build_app`), so handler registration is tested without Slack credentials —
which do not exist until the workspace app is created.
