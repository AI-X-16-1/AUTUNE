"""Creating the Notion databases a team's sync writes to, under one page.

A team connects Notion by sharing one page with the integration. Under it this
module makes a page of Autune's own, titled "Autune", and everything Autune
writes lives in databases inside that page -- so the team finds it all in one
place and the page they shared keeps only that one child (decided with the
user, 2026-10-01). Three databases: "할 일", "결정" and "회의록", each
with exactly the property names
the sync uses -- ``service.NOTION_PROPERTIES``,
``service.DECISION_NOTION_PROPERTIES`` and ``MINUTES_NOTION_PROPERTIES`` below
-- so the schema created here and the pages written later cannot drift apart.
The first two also get 내용 (``ACTION_PROPERTIES``, ``DECISION_PROPERTIES``):
a database made before it existed is given it once (``ensure_content``), and
``property_names`` is the one place that says whether a team's pages may name
it.

**Why this is not in the dev route any more.** It started inside
``dev/routes.py`` (#402), the local-only page that connects Notion by hand.
S28's one-click connect (#428) needs the same step after its OAuth callback, and
``packages/core``, which holds that callback, cannot import a module
(invariant 2). No event carries the connection across: the callback sends the
browser back to the screen with ``?notion=connected``, the screen calls this
module's own routes, and ``notion_connect.set_up`` calls ``provision_databases``
from here. The dev route is one more caller of it, not its owner.

**Why httpx and not ``packages/integrations``.** ``NotionClient`` has
``create_page`` and ``update_page`` -- what the sync needs. Creating a database
is a one-time setup call only B makes, and putting it in the shared package
would need team approval for code one module uses. What leaves here is the
"Autune" page's title and three fixed database titles and property names; no
meeting content, so the outbound check ``packages/integrations`` applies has
nothing to catch.

**The 회의록 database is written by ``project_send``.** It was made here before
anything wrote to it, so that a team connected then would not have to reconnect
when a writer landed. The writer is a project's minutes: one page for each
project a person sends from the 요약 tab, holding that project's confirmed
decisions and items -- never a transcript.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from autune_core import get_logger
from autune_core.integrations_config import IntegrationConfig
from autune_extraction.models import ExtNotionTarget
from autune_extraction.service import (
    DECISION_NOTION_PROPERTIES,
    NOTION_PROPERTIES,
    NOTION_STATUS_LABELS,
)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

log = get_logger(__name__)

MINUTES_NOTION_PROPERTIES: Mapping[str, str] = {
    "title": "제목",
    "date": "날짜",
    "meeting": "회의",
}
"""The 회의록 database's properties, by the same key-to-name scheme as the other
two. ``meeting`` holds the meeting id, as it does in the action and decision
databases. The writer (#428 item 7) must import this map rather than spell the
names again."""

_ACTION_STATUS_OPTIONS = list(NOTION_STATUS_LABELS.values())

CONTENT_PROPERTY = "내용"
"""The text property that holds an item's or a decision's sentence, once the
page's title is its short title (the owner, 2026-10-09: "'내용' 칸을 추가")."""

ACTION_PROPERTIES: Mapping[str, str] = {**NOTION_PROPERTIES, "content": CONTENT_PROPERTY}
DECISION_PROPERTIES: Mapping[str, str] = {
    **DECISION_NOTION_PROPERTIES,
    "content": CONTENT_PROPERTY,
}
"""The sync's name maps with 내용 added: what a database made here has, and the
map in force for a team whose database is known to have it (``property_names``).

Not ``service``'s defaults themselves. A database made before the property
existed does not have it, and Notion refuses a whole page for one property its
database lacks -- so the default, which every team without a record gets, must
not name it."""

ContentKind = Literal["action", "decision"]
CONTENT_KINDS: tuple[ContentKind, ...] = ("action", "decision")
"""The databases that get 내용. Not 회의록: its pages are a project's minutes,
written whole by ``project_send``."""

HOME_TITLE = "Autune"
HOME_INTRO = (
    "Autune이 회의에서 확정한 할 일과 결정을 이 페이지 아래 데이터베이스에 "
    "정리합니다. 데이터베이스와 속성 이름을 바꾸면 동기화가 멈추니 그대로 두세요."
)
"""The page every database goes in, and the one line on it. Fixed text: no
meeting content leaves here."""

DATABASES: tuple[tuple[str, str, Mapping[str, str], bool], ...] = (
    ("action_db_id", "할 일", ACTION_PROPERTIES, True),
    ("decision_db_id", "결정", DECISION_PROPERTIES, False),
    ("minutes_db_id", "회의록", MINUTES_NOTION_PROPERTIES, False),
)
"""(config key, database title, property names, status as a select) for each
database a connection gets, in creation order."""


class NotionSetupError(Exception):
    """Notion refused a setup call. ``message`` is Notion's own, when it sent one:
    it names what was wrong (bad page id, page not shared with the integration),
    which is what the person connecting needs to read."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def notion_client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=NOTION_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        timeout=10.0,
    )


def schema(names: Mapping[str, str], *, status_select: bool) -> dict[str, Any]:
    """A Notion database property schema from the name map the sync uses."""
    properties: dict[str, Any] = {names["title"]: {"title": {}}}
    for key, name in names.items():
        if key == "title":
            continue
        if key == "status" and status_select:
            properties[name] = {
                "select": {"options": [{"name": value} for value in _ACTION_STATUS_OPTIONS]}
            }
        elif key in ("confidence", "sources"):
            properties[name] = {"number": {}}
        elif key in ("due", "date"):
            properties[name] = {"date": {}}
        else:
            properties[name] = {"rich_text": {}}
    return properties


def _refused(resp: httpx.Response) -> NotionSetupError:
    # A proxy's HTML page instead of Notion's JSON must not turn into a 500
    # (lsh2217, review of #402).
    try:
        message = resp.json().get("message")
    except ValueError:
        message = None
    return NotionSetupError(resp.status_code, message or f"Notion answered {resp.status_code}")


def create_home_page(client: httpx.Client, *, page_id: str | None) -> str:
    """The "Autune" page holding the databases: under the page the team shared,
    or -- ``page_id`` ``None``, nothing shared -- at the top of the workspace,
    which Notion makes one of the connecting person's private pages. Only a
    public (OAuth) integration may do that, which the one-click connect is."""
    body = {
        "parent": {"page_id": page_id} if page_id else {"workspace": True},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": HOME_TITLE}}]}},
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": HOME_INTRO}}]},
            }
        ],
    }
    resp = client.post("/pages", json=body)
    if resp.status_code >= 400:
        raise _refused(resp)
    return str(resp.json()["id"])


def home_of(client: httpx.Client, database_id: str) -> str | None:
    """The page a database Autune made sits in, so a missing one is added
    beside it. ``None`` when Notion no longer has it under a page, or no
    longer gives it to Autune at all -- deleted or unshared: the missing one
    then goes in a new "Autune" page rather than the whole setup failing (#622
    review)."""
    resp = client.get(f"/databases/{database_id}")
    if 400 <= resp.status_code < 500:
        log.info("extraction_notion_kept_database_unreachable", status=resp.status_code)
        return None
    if resp.status_code >= 400:
        raise _refused(resp)
    parent = resp.json().get("parent") or {}
    return str(parent["page_id"]) if parent.get("type") == "page_id" else None


def retire_status_codes(client: httpx.Client, database_id: str) -> None:
    """Drop the status codes from a kept action database's 상태 options.

    Pages went out with ``todo``/``done`` until #622, which writes the board's
    labels instead; Notion adds a select option it has not seen, so a database
    set up before then ended with eight options, four of them the codes (#622
    review). The pages still on a code lose it here and get their label back
    from the fill that every setup queues. Best effort: a database Notion will
    not answer for, or a refused update, leaves the options as they are -- the
    sync is unaffected either way.
    """
    name = NOTION_PROPERTIES["status"]
    resp = client.get(f"/databases/{database_id}")
    if resp.status_code >= 400:
        return
    status = (resp.json().get("properties") or {}).get(name) or {}
    options = (status.get("select") or {}).get("options") or []
    keep = [
        {"id": option["id"], "name": option["name"]}
        for option in options
        if option.get("name") not in NOTION_STATUS_LABELS
    ]
    if len(keep) == len(options):
        return
    resp = client.patch(
        f"/databases/{database_id}",
        json={"properties": {name: {"select": {"options": keep}}}},
    )
    if resp.status_code >= 400:
        log.info("extraction_notion_status_codes_kept", status=resp.status_code)


def add_content_property(client: httpx.Client, database_id: str) -> bool | None:
    """Give a database made before 내용 existed that property. ``True``: it has
    it as text now -- added here, or already there. ``False``: Notion answered
    and it does not -- the database is gone or unshared, the change was
    refused, or a property of that name holds something other than text.
    ``None``: no answer worth keeping (Notion down or busy); ask again.

    Read before written: changing a property a person made under that name to
    text would empty their column, so one that is there and is not text is
    left alone and the team's pages keep today's shape. What is sent is the
    database id and the fixed name; of the answer only that one property's
    type is looked at, and nothing of it is kept or logged.
    """
    try:
        resp = client.get(f"/databases/{database_id}")
        if not _answered(resp):
            return None
        if resp.status_code >= 400:
            log.info("extraction_notion_content_database_unreachable", status=resp.status_code)
            return False
        found = (resp.json().get("properties") or {}).get(CONTENT_PROPERTY)
        if found is not None:
            if found.get("type") != "rich_text":
                log.info("extraction_notion_content_name_taken")
            return bool(found.get("type") == "rich_text")
        resp = client.patch(
            f"/databases/{database_id}",
            json={"properties": {CONTENT_PROPERTY: {"rich_text": {}}}},
        )
    except (httpx.HTTPError, ValueError) as exc:
        # A timeout, or a proxy's page where Notion's JSON should be.
        log.info("extraction_notion_content_unanswered", error=type(exc).__name__)
        return None
    if not _answered(resp):
        return None
    if resp.status_code >= 400:
        log.info("extraction_notion_content_refused", status=resp.status_code)
        return False
    return True


def _answered(resp: httpx.Response) -> bool:
    """Whether Notion said something about the database, as opposed to being
    busy (429) or broken (5xx) -- which says nothing and is asked again."""
    return resp.status_code != 429 and resp.status_code < 500


def content_of(
    client: httpx.Client, config: Mapping[str, str], created: list[str]
) -> dict[ContentKind, bool | None]:
    """Whether each of a team's action and decision databases has 내용, for
    ``save_targets``: one this setup made has it from its schema, a kept one is
    asked and given it (``add_content_property``)."""
    return {
        kind: True
        if f"{kind}_db_id" in created
        else add_content_property(client, config[f"{kind}_db_id"])
        for kind in CONTENT_KINDS
    }


def create_database(
    client: httpx.Client,
    *,
    page_id: str,
    title: str,
    names: Mapping[str, str],
    status_select: bool,
) -> str:
    body = {
        "parent": {"page_id": page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": schema(names, status_select=status_select),
    }
    resp = client.post("/databases", json=body)
    if resp.status_code >= 400:
        raise _refused(resp)
    return str(resp.json()["id"])


def same_page(stored: object, page_id: str) -> bool:
    """Notion takes a page id with or without its dashes; so does this."""
    return isinstance(stored, str) and stored.replace("-", "") == page_id.replace("-", "")


def provision_databases(
    client: httpx.Client, *, page_id: str, stored: Mapping[str, Any], home: str | None = None
) -> tuple[dict[str, str], list[str]]:
    """The Notion config for a connection to ``page_id``, and the config keys of
    the databases this call had to create.

    Idempotent for the same page: a database already recorded under it is kept,
    and only a missing one is created -- so a team connected before the 회의록
    database existed gets that one and keeps the other two. A different page,
    or a stored config that never recorded its page, gets all new databases,
    because the old ones may not be shared with the new token (lsh2217, review
    of #402).

    New databases go in a new "Autune" page under ``page_id``
    (``create_home_page``); a missing one goes beside the ones kept, in
    whatever page holds them -- a team set up before the "Autune" page existed
    keeps its databases directly under the page it shared. ``home`` is an
    "Autune" page already made, for ``page_id`` itself to hold them
    (``notion_connect.set_up`` with nothing shared).
    """
    reuse = same_page(stored.get("parent_page_id"), page_id)
    config: dict[str, str] = {}
    for key, *_ in DATABASES:
        existing = stored.get(key) if reuse else None
        if isinstance(existing, str) and existing:
            config[key] = existing
    if "action_db_id" in config:
        retire_status_codes(client, config["action_db_id"])
    created: list[str] = []
    missing = [database for database in DATABASES if database[0] not in config]
    if missing:
        kept = next(iter(config.values()), None)
        home = (
            home
            or (home_of(client, kept) if kept else None)
            or create_home_page(client, page_id=page_id)
        )
        for key, title, names, status_select in missing:
            config[key] = create_database(
                client, page_id=home, title=title, names=names, status_select=status_select
            )
            created.append(key)
    config["parent_page_id"] = page_id
    return config, created


def shared_pages(client: httpx.Client, *, limit: int = 50) -> list[dict[str, str]]:
    """The pages the workspace shared with Autune on Notion's consent screen --
    what the team chooses a parent from. Rows of a database are left out: a
    database's pages (our own action items among them) are not a place to put
    new databases."""
    resp = client.post(
        "/search",
        json={"filter": {"property": "object", "value": "page"}, "page_size": limit},
    )
    if resp.status_code >= 400:
        raise NotionSetupError(resp.status_code, f"Notion answered {resp.status_code}")
    pages: list[dict[str, str]] = []
    for page in resp.json().get("results", []):
        if page.get("parent", {}).get("type") == "database_id" or page.get("archived"):
            continue
        pages.append({"id": str(page["id"]), "title": _title(page) or "(제목 없음)"})
    return pages


def _title(page: Mapping[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in prop.get("title", []))
    return ""


def database_id(
    session: Session, team_id: str, config: IntegrationConfig | None, key: str
) -> str | None:
    """A team's database id: from B's own ``ext_notion_targets`` (one-click
    setup), else from the connection's config (the local dev page, #401).

    A target row made in another workspace than the current connection's is
    not used: after a reconnect elsewhere its ids are databases the new token
    cannot see, and every sync would be refused (#467 review)."""
    target = current_target(session, team_id, config)
    if target is not None:
        value = getattr(target, key, None)
        return str(value) if value else None
    stored = config.config.get(key) if config is not None else None
    return str(stored) if stored else None


def workspace_of(config: IntegrationConfig | None) -> str | None:
    value = config.config.get("workspace_id") if config is not None else None
    return str(value) if value else None


def current_target(
    session: Session, team_id: str, config: IntegrationConfig | None
) -> ExtNotionTarget | None:
    """The team's target row, when it belongs to the workspace the team is
    connected to now."""
    target = session.get(ExtNotionTarget, team_id)
    if target is None or target.workspace_id != workspace_of(config):
        return None
    return target


def stored_targets(
    session: Session, team_id: str, config: IntegrationConfig | None
) -> dict[str, str]:
    """What ``provision_databases`` should treat as already made for this team
    -- nothing from another workspace."""
    target = current_target(session, team_id, config)
    if target is None:
        return {}
    return {
        "parent_page_id": target.parent_page_id,
        "action_db_id": target.action_db_id,
        "decision_db_id": target.decision_db_id,
        "minutes_db_id": target.minutes_db_id,
    }


def lock_setup(session: Session, team_id: str) -> None:
    """Hold the team's setup lock for the rest of ``session``'s transaction.

    Two people finishing the first setup at once both find no
    ``ext_notion_targets`` row, so a row lock has nothing to hold: both would
    make databases, leaving three orphans in Notion and one request failing on
    the primary key (#481). A transaction-scoped advisory lock keyed by team
    makes the second wait, then read the first one's row and reuse its
    databases. The key carries B's own namespace -- D keys its lineage lock on
    the bare team id, and the two have no reason to wait on each other.
    PostgreSQL only; SQLite (unit tests) has no such lock and runs one writer
    anyway."""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"extraction.notion_setup:{team_id}"},
    )


def save_targets(
    session: Session,
    team_id: str,
    config: Mapping[str, str],
    *,
    workspace_id: str | None,
    content: Mapping[ContentKind, bool | None] | None = None,
) -> ExtNotionTarget:
    """Record a team's databases, and what is known of their 내용 property.

    ``content`` is ``content_of``'s answer for these databases. Without one, or
    with a database Notion did not answer for, nothing is known: the row says
    "not asked" and the team's next sync asks (``ensure_content``). The ids may
    be new databases, so what an earlier row knew is never carried over."""
    target = session.get(ExtNotionTarget, team_id)
    if target is None:
        target = ExtNotionTarget(team_id=team_id)
        session.add(target)
    target.workspace_id = workspace_id
    target.parent_page_id = config["parent_page_id"]
    target.action_db_id = config["action_db_id"]
    target.decision_db_id = config["decision_db_id"]
    target.minutes_db_id = config["minutes_db_id"]
    _record_content(target, content or {})
    session.flush()
    return target


def _record_content(target: ExtNotionTarget, content: Mapping[ContentKind, bool | None]) -> None:
    answered = all(content.get(kind) is not None for kind in CONTENT_KINDS)
    target.action_content = answered and bool(content["action"])
    target.decision_content = answered and bool(content["decision"])
    target.content_asked_at = datetime.now(UTC) if answered else None


def ensure_content(session: Session, team_id: str, config: IntegrationConfig | None) -> None:
    """Ask once whether the team's databases have 내용, adding it where it is
    missing, and record the answer. Called where a page is about to be written.

    **Why at a sync and not only at setup.** A team connected before the
    property existed may never open the setup screen again; its databases get
    the property at its next sync instead, with no beat process needed. The
    record (``content_asked_at``) is what keeps every later sync from asking.

    **It never fails the sync.** Refused, the answer is recorded as "does not
    have it" and the team's pages keep today's shape until someone sets up
    again. Unanswered, nothing is recorded and the next sync asks. A team with
    no target row -- the local dev page, whose ids sit in the connection's
    config -- has nowhere to keep the answer and is not asked.
    """
    target = current_target(session, team_id, config)
    if target is None or target.content_asked_at is not None:
        return
    if config is None or not config.secret:
        return
    ids = {"action_db_id": target.action_db_id, "decision_db_id": target.decision_db_id}
    with notion_client(config.secret) as client:
        content = content_of(client, ids, created=[])
    _record_content(target, content)
    session.flush()
    log.info(
        "extraction_notion_content_asked",
        team_id=team_id,
        action=content["action"],
        decision=content["decision"],
    )


def property_names(
    session: Session, team_id: str, config: IntegrationConfig | None, kind: ContentKind
) -> Mapping[str, str] | None:
    """The name map in force for a team's item or decision pages; ``None`` is
    ``service``'s default.

    A team's own map (``action_properties`` / ``decision_properties`` in its
    connection's config) replaces everything, as it always has: it names
    ``content`` itself or its pages do without. Otherwise the default, with
    내용 added only when the record says that database has it.
    """
    own = config.config.get(f"{kind}_properties") if config is not None else None
    if own:
        return own
    target = current_target(session, team_id, config)
    if target is None:
        return None
    if kind == "action":
        return ACTION_PROPERTIES if target.action_content else None
    return DECISION_PROPERTIES if target.decision_content else None
