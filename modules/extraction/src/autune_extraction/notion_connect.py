"""After a team connects Notion in one click (core, #428): choose a page, make
Autune's databases under it, and fill them with everything already confirmed.

The screen calls ``pages_for`` right after ``?notion=connected``; when the team
shared exactly one page on Notion's consent screen it calls ``set_up`` with it
straight away, so the whole connection is one click. ``set_up`` is also what
moving to another page (or another workspace) runs:

1. ``notion_setup.provision_databases`` makes "할 일", "결정" and "회의록"
   under the page -- or keeps the ones already made under that same page.
2. The ids go in B's own ``ext_notion_targets``.
3. ``tasks.backfill_notion`` is queued: every confirmed action item and decision
   of the team goes through the ordinary sync (``notion_backfill``) -- a page
   that still exists is updated, one Notion no longer finds (another workspace,
   a deleted page) is made again in the new database (#403), and one never
   sent is created. It runs in the worker, not in this request: Notion takes
   about three requests a second, and a team with hundreds of confirmed rows
   would outlast the request (#481).

Steps 1 and 2 run under the team's setup lock (``notion_setup.lock_setup``), so
two people finishing the first setup at once make one set of databases.
"""

from __future__ import annotations

from typing import Any

from autune_core import get_logger, load_integration, session_scope

from . import notion_setup, tasks
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


def set_up(team_id: str, page_id: str | None = None) -> dict[str, Any]:
    """Databases under ``page_id``, recorded, and the fill queued. Raises
    ``notion_setup.NotionSetupError`` with Notion's own message when Notion
    refuses the page.

    **No ``page_id``: the person shared no page** on Notion's consent screen.
    The databases are still made, in an "Autune" page at the top of the
    workspace -- the connecting person's private pages, from where they can
    move it into a teamspace (decided with the user, 2026-10-01). A setup that
    already exists and is still shared is kept instead, so opening the screen
    again does not make a second one."""
    with session_scope() as session:
        notion_setup.lock_setup(session, team_id)
        config = load_integration(session, team_id, NOTION)
        if config is None or not config.secret:
            raise notion_setup.NotionSetupError(409, "Notion is not connected for this team")
        stored = notion_setup.stored_targets(session, team_id, config)
        with notion_setup.notion_client(config.secret) as client:
            home = None
            if page_id is None:
                shared = {page["id"] for page in notion_setup.shared_pages(client)}
                kept = stored.get("parent_page_id")
                if kept in shared:
                    page_id = kept
                else:
                    page_id = home = notion_setup.create_home_page(client, page_id=None)
                    stored = {}
            target, created = notion_setup.provision_databases(
                client, page_id=page_id, stored=stored, home=home
            )
            content = notion_setup.content_of(client, target, created)
        notion_setup.save_targets(
            session,
            team_id,
            target,
            workspace_id=notion_setup.workspace_of(config),
            content=content,
        )

    # After the commit, so the worker reads the databases just recorded.
    tasks.backfill_notion.delay(team_id)
    log.info("extraction_notion_set_up", team_id=team_id, created=len(created))
    return {
        "databases": "reused" if not created else "created" if len(created) == 3 else "added",
        **_urls(target),
        "backfill": "queued",
    }
