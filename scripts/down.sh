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
    # `up.sh` runs `set -m` before starting these, so each one leads its own
    # process group and the recorded pid is that leader. The thing listening on
    # the port is its child (`uv run` execs uvicorn, `pnpm` execs next), so the
    # group has to be signalled and not just the parent -- otherwise the next
    # `up.sh` finds the port taken. The fallback covers a process started
    # without job control, where the negative pid is not a group.
    kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
    rm -f "$pid_file"
    say "stopped $name ($pid)"
  fi
done

# Belt and braces: a previous run that died without writing a pid file, or one
# started by hand from the runbook. macOS and Linux only -- Git Bash on Windows
# has no `pkill`, so the `2>/dev/null` makes this a no-op there and the pid
# files above are all that stops anything.
pkill -f "uvicorn autune_api.main:app" 2>/dev/null && say "stopped a stray api"
pkill -f "celery -A autune_worker.celery_app worker" 2>/dev/null && say "stopped a stray worker"
pkill -f "next dev" 2>/dev/null && say "stopped a stray web"

if [ "${1:-}" = "--docker" ]; then
  say "stopping postgres + redis"
  docker compose -f infra/docker-compose.yml down
fi

say "down"
