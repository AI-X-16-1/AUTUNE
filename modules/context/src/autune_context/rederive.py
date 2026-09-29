"""``python -m autune_context.rederive [--team TEAM_ID] [--dry-run]``.

Re-derives topic linking for every analysed meeting from its stored
transcript, oldest first -- the backfill for topics written before #439
filtered by consent, and the procedure to run after consent changes for
meetings D already analysed. See ``service.rederive_topics``.

Runs in this process, one meeting at a time, rather than fanning out to the
``cpu_heavy`` workers: each meeting's links are scored against the meetings
before it, and concurrent workers would score some of them against
predecessors not yet re-derived. So it needs what a ``cpu_heavy`` worker
needs -- the database, the model settings (``AUTUNE_CONTEXT_*_IMPL`` and
endpoints) and the broker, which the republish to module E goes through.

Prints counts and meeting ids only: a title or a topic label is meeting
content.
"""

from __future__ import annotations

import argparse

from autune_core import session_scope
from autune_core.celery_app import make_celery_app


def main() -> int:
    parser = argparse.ArgumentParser(prog="autune_context.rederive")
    parser.add_argument("--team", help="only this team's meetings")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list the meetings it would re-derive, change nothing",
    )
    args = parser.parse_args()

    # A client app on the real broker, so the republish below reaches E's
    # worker; imported after it so ``shared_task`` binds to this app.
    make_celery_app(include_tasks=False)
    from autune_context import service, tasks

    with session_scope() as session:
        meeting_ids = service.rederivable_meeting_ids(session, team_id=args.team)
    print(f"{len(meeting_ids)} analysed meeting(s){' (dry run)' if args.dry_run else ''}")
    if args.dry_run:
        for meeting_id in meeting_ids:
            print(meeting_id)
        return 0

    for index, meeting_id in enumerate(meeting_ids, start=1):
        # The task body, run here: same routing as the worker, in time order.
        tasks.rederive_topics(meeting_id)
        print(f"[{index}/{len(meeting_ids)}] {meeting_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
