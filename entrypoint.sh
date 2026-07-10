#!/bin/sh
# Starts as root only to fix ownership of the DATA_DIR bind/volume mount,
# then drops to the unprivileged `app` user (uid 1000) via su-exec.
set -e

: "${DATA_DIR:=/data}"

mkdir -p "$DATA_DIR"
chown -R app:app "$DATA_DIR"

exec su-exec app uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  --log-level "$(echo "${LOG_LEVEL:-INFO}" | tr '[:upper:]' '[:lower:]')"
