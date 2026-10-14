#!/usr/bin/env bash
# Deploys the checked-out release to this host. Run as root by
# .github/workflows/release-aws.yml through SSM Run Command, from the
# repository root:
#
#   bash infra/aws/deploy.sh
#
# Reads two secrets with the instance role (nothing is passed in by the
# workflow, so no secret value is in the run log or in an SSM command):
#
#   autune/db   JSON: username, password, dbname, host, port,
#               redis_host, redis_port        (RDS and ElastiCache)
#   autune/app  JSON: one entry per environment variable the stack takes,
#               e.g. AUTUNE_SECRET_KEY, AUTUNE_ENCRYPTION_KEY,
#               AUTUNE_LLM_API_KEY, AUTUNE_AUDIO_HF_TOKEN,
#               AUTUNE_GOOGLE_CLIENT_ID, AUTUNE_GOOGLE_CLIENT_SECRET ...
#
# No value is ever echoed. Raw audio never touches this script: the stack's
# audio-tmp volume is a tmpfs and swap is off on this host (invariant 11).
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
SITE_URL="${AUTUNE_SITE_URL:-https://autune.cloud}"
PROJECT=autune-app
COMPOSE=(docker compose -p "$PROJECT"
  -f infra/docker-compose.app.yml -f infra/docker-compose.release.yml)

cd "$(dirname "$0")/../.."

# One deploy at a time on this host, whatever started it.
exec 9>/var/lock/autune-deploy.lock
flock -n 9 || { echo "another deploy is running"; exit 1; }

# Raw audio stays off the disk only while swap is off (invariant 11). Swap is a
# host setting outside this repository, so a deploy refuses to go on when
# someone has turned it on. /proc/swaps is a header line plus one line per
# active swap area.
if [ "$(wc -l < /proc/swaps)" -gt 1 ]; then
  echo "swap is on: raw audio in a tmpfs could reach the disk"
  exit 1
fi

if ! command -v aws >/dev/null; then
  echo "installing the AWS CLI"
  apt-get install -y -qq unzip curl >/dev/null
  tmp="$(mktemp -d)"
  curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o "$tmp/aws.zip"
  unzip -q "$tmp/aws.zip" -d "$tmp"
  "$tmp/aws/install" >/dev/null
  rm -rf "$tmp"
fi

secret() { aws secretsmanager get-secret-value --region "$REGION" \
  --secret-id "$1" --query SecretString --output text; }

# Each secret's export lines are read into a variable first and evaluated
# after. `eval "$(...)"` takes the exit status of eval, not of the command
# inside, so a failed read (a missing permission, a refused key name) would be
# evaluated as an empty string and the deploy would go on. An assignment takes
# the status of the substitution, and `set -e` stops the script there.
#
# autune/app: each key becomes an environment variable. A name that is not an
# upper-case identifier is refused rather than exported.
app_env="$(secret autune/app | python3 -c '
import json, re, shlex, sys
for k, v in json.load(sys.stdin).items():
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k):
        sys.exit("autune/app: %r is not an environment variable name" % k)
    print("export %s=%s" % (k, shlex.quote(str(v))))
')"
eval "$app_env"
unset app_env

# autune/db: build the two URLs. RDS forces TLS (rds.force_ssl is 1 from
# PostgreSQL 15), so sslmode=require; the password is quoted for a URL.
db_env="$(secret autune/db | python3 -c '
import json, shlex, sys
from urllib.parse import quote
d = json.load(sys.stdin)
db = "postgresql+psycopg://%s:%s@%s:%s/%s?sslmode=require" % (
    quote(d["username"], safe=""), quote(d["password"], safe=""),
    d["host"], d["port"], d["dbname"])
redis = "redis://%s:%s/0" % (d["redis_host"], d["redis_port"])
print("export AUTUNE_DATABASE_URL=%s" % shlex.quote(db))
print("export AUTUNE_REDIS_URL=%s" % shlex.quote(redis))
')"
eval "$db_env"
unset db_env

# Same refusals as the "Check configuration" step of dev.yml. Production
# settings need the first two on their own; the rest is what the compose file
# switches on (module B's classifier and friends are `llm`).
missing=()
for name in AUTUNE_SECRET_KEY AUTUNE_ENCRYPTION_KEY AUTUNE_LLM_API_KEY \
            AUTUNE_AUDIO_HF_TOKEN AUTUNE_GOOGLE_CLIENT_ID \
            AUTUNE_GOOGLE_CLIENT_SECRET; do
  [ -n "${!name:-}" ] || missing+=("$name")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "autune/app is missing: ${missing[*]}"
  exit 1
fi

# The site's address, the project name (container names) and nginx's hash of
# the /dev password. Production mounts no /dev router; nginx's entrypoint
# still wants a value, so it gets one nobody knows.
export COMPOSE_PROJECT_NAME="$PROJECT"
export DEV_SITE_URL="$SITE_URL"
export DEV_LIVE_URL="${DEV_LIVE_URL:-}"
DEV_BASIC_AUTH_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
export DEV_BASIC_AUTH_PASSWORD

"${COMPOSE[@]}" build
"${COMPOSE[@]}" run --rm migrate
# A key that is set and is not a Fernet key would otherwise be found by the
# first person who connects something. The error names the variable and never
# prints it.
"${COMPOSE[@]}" run --rm migrate python -c \
  "from autune_core.crypto import ensure_configured; ensure_configured()"
"${COMPOSE[@]}" up -d --wait --wait-timeout 900 --remove-orphans

# Every deploy rebuilds the images and the python one is over 10 GB; the one
# it replaced is left untagged. Only untagged images go: the build cache
# stays, so the next build is fast.
docker image prune -f >/dev/null

"${COMPOSE[@]}" ps -a
code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/ || true)"
echo "nginx answers / with ${code}"
[ "$code" = 200 ]
