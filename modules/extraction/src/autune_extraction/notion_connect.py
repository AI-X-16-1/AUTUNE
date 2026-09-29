"""After a team connects Notion in one click (core, #428): choose a page, make
Autune's databases under it, and fill them with everything already confirmed.

The screen calls ``pages_for`` right after ``?notion=connected``; when the team
shared exactly one page on Notion's consent screen it calls ``set_up`` with it
straight away, so the whole connection is one click. ``set_up`` is also what
moving to another page (or another workspace) runs:

1. ``notion_setup.provision_databases`` makes "액션 아이템", "결정" and "회의록"
   under the page -- or keeps the ones already made under that same page.
2. The ids go in B's own ``ext_notion_targets``.
3. Every confirmed action item and decision of the team is sent through the
   ordinary sync (``notion_backfill``): a page that still exists is updated, one
   Notion no longer finds -- another workspace, a deleted page -- is made again
   in the new database (#403), and one never sent is created.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from autune_core import get_logger, load_integration, session_scope

from . import notion_backfill, notion_setup
from .service import notion_url

log = get_logger(__name__)

NOTION = "notion"


def _urls(target: dict[str, str]) -> dict[str, str]:
    return {
        "action_db_url": notion_url(target["action_db_id"]),
        "decision_db_url": notion_url(target["decision_db_id"]),
        "minutes_db_url": notion_url(target["minutes_db_id"]),
    }


def pages_for(team_id: str) -> dict[str, Any]:
    """The pages the team may put Autune's databases under, and where they are
    now if they are anywhere.

    ``target`` is ``None`` -- so the screen sets up again or asks for a page --
    when the stored databases belong to another workspace than the current
    connection, or their parent page is no longer shared with Autune (#467
    review). A Notion that refuses the token answers ``needs_reconnect``."""
    with session_scope() as session:
        config = load_integration(session, team_id, NOTION)
        target = notion_setup.stored_targets(session, team_id, config)
    if config is None or not config.secret:
        return {"connected": False}
    with notion_setup.notion_client(config.secret) as client:
        try:
            pages = notion_setup.shared_pages(client)
        except notion_setup.NotionSetupError as exc:
            if 400 <= exc.status_code < 500:
                log.info(
                    "extraction_notion_needs_reconnect", team_id=team_id, status=exc.status_code
                )
                return {"connected": True, "needs_reconnect": True, "pages": [], "target": None}
            raise
    if target and target["parent_page_id"] not in {page["id"] for page in pages}:
        target = {}
    return {
        "connected": True,
        "pages": pages,
        "target": (
            {"parent_page_id": target["parent_page_id"], **_urls(target)} if target else None
        ),
    }


def set_up(team_id: str, page_id: str) -> dict[str, Any]:
    """Databases under ``page_id``, recorded, and filled. Raises
    ``notion_setup.NotionSetupError`` with Notion's own message when Notion
    refuses the page."""
    with session_scope() as session:
        config = load_integration(session, team_id, NOTION)
        if config is None or not config.secret:
            raise notion_setup.NotionSetupError(409, "Notion is not connected for this team")
        with notion_setup.notion_client(config.secret) as client:
            target, created = notion_setup.provision_databases(
                client,
                page_id=page_id,
                stored=notion_setup.stored_targets(session, team_id, config),
            )
        notion_setup.save_targets(
            session, team_id, target, workspace_id=notion_setup.workspace_of(config)
        )

    items = notion_backfill.Stats()
    notion_backfill.backfill_action_items(notion_backfill._confirmed_action_items(team_id), items)
    decisions = notion_backfill.Stats()
    notion_backfill.backfill_decisions(notion_backfill._confirmed_decisions(team_id), decisions)
    log.info(
        "extraction_notion_set_up",
        team_id=team_id,
        created=len(created),
        items_sent=items.sent,
        items_replaced=items.replaced,
        decisions_sent=decisions.sent,
    )
    return {
        "databases": "reused" if not created else "created" if len(created) == 3 else "added",
        **_urls(target),
        "action_items": asdict(items),
        "decisions": asdict(decisions),
    }
