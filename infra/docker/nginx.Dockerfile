# syntax=docker/dockerfile:1
FROM nginx:1.27-alpine
# openssl hashes the /dev routes' password at start (40-htpasswd.sh).
RUN apk add --no-cache openssl
COPY --chmod=755 infra/nginx/40-htpasswd.sh /docker-entrypoint.d/40-htpasswd.sh
COPY infra/nginx/default.conf /etc/nginx/conf.d/default.conf
