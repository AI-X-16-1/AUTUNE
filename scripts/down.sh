#!/usr/bin/env bash
# Stop what scripts/up.sh started. Leaves docker running -- postgres holds the
# meetings, and starting it again is slower than leaving it up.
#
#   ./scripts/down.sh            # stop api, worker, web
#   ./scripts/down.sh --docker   # and stop postgres + redis
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
LOGS="$(pwd)/.logs"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

say() { [ "$QUIET" = 1 ] || printf '%s\n' "$*"; }

for name in api worker web; do
  pid_file="$LOGS/$name.pid"
  if [ -f "$pid_file" ]; then
    pid=$(cat "$pid_file")
    # The recorded pid is the process group's leader (`uv run`, `pnpm`), and
    # the thing listening is its child, so kill the group. Without the minus
    # sign `up.sh` finds the port still taken on the next run.
    kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    rm -f "$pid_file"
    say "stopped $name ($pid)"
  fi
done

# Belt and braces: a previous run that died without writing a pid file, or one
# started by hand from the runbook.
pkill -f "uvicorn autune_api.main:app" 2>/dev/null && say "stopped a stray api"
pkill -f "celery -A autune_worker.celery_app worker" 2>/dev/null && say "stopped a stray worker"

if [ "${1:-}" = "--docker" ]; then
  say "stopping postgres + redis"
  docker compose -f infra/docker-compose.yml down
fi

say "down"
