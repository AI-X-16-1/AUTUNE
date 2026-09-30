#!/bin/sh
# Run by the nginx image's entrypoint before nginx starts. Hashes the team's
# password for the /dev routes (default.conf) so only the hash is on disk.
set -eu
: "${DEV_BASIC_AUTH_PASSWORD:?DEV_BASIC_AUTH_PASSWORD is not set}"
printf 'autune:%s\n' "$(openssl passwd -apr1 "$DEV_BASIC_AUTH_PASSWORD")" > /etc/nginx/htpasswd
# Workers run as `nginx`; nobody else needs to read the hash.
chown root:nginx /etc/nginx/htpasswd
chmod 640 /etc/nginx/htpasswd
