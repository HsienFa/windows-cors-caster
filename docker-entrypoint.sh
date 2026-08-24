#!/bin/sh
set -eu

CONFIG_PATH="${NTRIP_CONFIG_FILE:-/app/config/config.ini}"
CONFIG_DIR=$(dirname "$CONFIG_PATH")

mkdir -p /app/data /app/logs "$CONFIG_DIR"
chown -R ntrip:ntrip /app/data /app/logs "$CONFIG_DIR"

if [ ! -f "$CONFIG_PATH" ]; then
    if [ -z "${NTRIP_ADMIN_PASSWORD:-}" ]; then
        echo "ERROR: NTRIP_ADMIN_PASSWORD is required for the first container start." >&2
        echo "Set it in the ignored .env file and restart the container." >&2
        exit 1
    fi

    python /app/scripts/deployment_config.py write-config \
        --output "$CONFIG_PATH" \
        --ntrip-host "${NTRIP_LISTEN_HOST:-0.0.0.0}" \
        --web-host "${WEB_LISTEN_HOST:-0.0.0.0}" \
        --ntrip-port "${NTRIP_PORT:-2101}" \
        --web-port "${WEB_PORT:-5757}" \
        --database-path "data/2rtk.db" \
        --log-dir "logs"
fi

chown ntrip:ntrip "$CONFIG_PATH"
chmod 600 "$CONFIG_PATH"
unset NTRIP_ADMIN_PASSWORD NTRIP_SECRET_KEY SECRET_KEY

exec gosu ntrip "$@"
