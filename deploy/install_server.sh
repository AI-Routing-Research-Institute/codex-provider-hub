#!/usr/bin/env bash
set -euo pipefail

APP_ROOT=/opt/codex-provider-probe
APP_USER=codex-provider
APP_WEB_USER=codex-provider-web
CONTROL_GROUP=codex-provider-control
CONFIG_ROOT=/etc/codex-provider-probe
DATA_ROOT=/var/lib/codex-provider-probe
PRIVATE_ROOT=$DATA_ROOT/private
PUBLIC_ROOT=$DATA_ROOT/public
CONTROL_ROOT=$DATA_ROOT/control
CONTROL_DATABASE=$CONTROL_ROOT/manual-probes.sqlite3
LEGACY_DATABASE=$DATA_ROOT/status.sqlite3
PRIVATE_DATABASE=$PRIVATE_ROOT/status.sqlite3
SOURCE_DIR=
CODEX_VERSION=0.144.5
WEB_PORT=18765
PUBLIC_IP=

usage() {
    printf '%s\n' \
        "Usage: $0 --source PATH --public-ip ADDRESS [--codex-version VERSION] [--web-port PORT]"
}

while (($#)); do
    case "$1" in
        --source)
            SOURCE_DIR=${2:-}
            shift 2
            ;;
        --codex-version)
            CODEX_VERSION=${2:-}
            shift 2
            ;;
        --web-port)
            WEB_PORT=${2:-}
            shift 2
            ;;
        --public-ip)
            PUBLIC_IP=${2:-}
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    printf '%s\n' "This installer must run as root." >&2
    exit 1
fi
if [[ -z $SOURCE_DIR || ! -d $SOURCE_DIR ]]; then
    printf '%s\n' "--source must point to the project directory." >&2
    exit 2
fi
if [[ -z $PUBLIC_IP || ! $PUBLIC_IP =~ ^[0-9A-Fa-f:.]+$ ]]; then
    printf '%s\n' "--public-ip must be an IPv4 or IPv6 address." >&2
    exit 2
fi
if [[ ! $WEB_PORT =~ ^[0-9]+$ ]] || ((WEB_PORT < 1024 || WEB_PORT > 65535)); then
    printf '%s\n' "--web-port must be between 1024 and 65535." >&2
    exit 2
fi
if [[ ! $CODEX_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf '%s\n' "--codex-version must use numeric semantic versioning." >&2
    exit 2
fi
for command_name in python3 npm rsync sed systemctl getent groupadd usermod; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Required command is missing: %s\n' "$command_name" >&2
        exit 1
    fi
done

if ! getent group "$CONTROL_GROUP" >/dev/null 2>&1; then
    groupadd --system "$CONTROL_GROUP"
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$PRIVATE_ROOT" \
        --shell /usr/sbin/nologin "$APP_USER"
fi
if ! id "$APP_WEB_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /nonexistent \
        --shell /usr/sbin/nologin "$APP_WEB_USER"
fi
usermod -a -G "$CONTROL_GROUP" "$APP_USER"
usermod -a -G "$CONTROL_GROUP" "$APP_WEB_USER"

install -d -o root -g root -m 0755 "$APP_ROOT" "$APP_ROOT/app" "$APP_ROOT/runtime"
install -d -o root -g root -m 0755 "$CONFIG_ROOT"
install -d -o root -g root -m 0700 "$CONFIG_ROOT/secrets"
install -d -o root -g root -m 0755 "$DATA_ROOT"
install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "$PRIVATE_ROOT" "$PRIVATE_ROOT/tmp"
install -d -o "$APP_USER" -g "$APP_WEB_USER" -m 2750 "$PUBLIC_ROOT"
install -d -o root -g "$CONTROL_GROUP" -m 2770 "$CONTROL_ROOT"
if [[ ! -e $CONTROL_DATABASE ]]; then
    install -o root -g "$CONTROL_GROUP" -m 0660 /dev/null "$CONTROL_DATABASE"
else
    chgrp "$CONTROL_GROUP" "$CONTROL_DATABASE"
    chmod 0660 "$CONTROL_DATABASE"
fi

if [[ -f $LEGACY_DATABASE && ! -f $PRIVATE_DATABASE ]]; then
    printf '%s\n' \
        "SQLite backup migration required before upgrading the legacy database." >&2
    exit 1
fi

rsync -a --delete \
    --exclude .git/ \
    --exclude .worktrees/ \
    --exclude __pycache__/ \
    --exclude '*.pyc' \
    --exclude '*.log' \
    --exclude 'probe-result*.json' \
    "$SOURCE_DIR/" "$APP_ROOT/app/"
chown -R root:root "$APP_ROOT/app"
# rsync -a 会保留 source 根目录权限，确保 systemd 用户可以进入应用目录。
chmod 0755 "$APP_ROOT/app"

if [[ ! -x $APP_ROOT/venv/bin/python ]]; then
    python3 -m venv "$APP_ROOT/venv"
fi
"$APP_ROOT/venv/bin/python" -m pip install --disable-pip-version-check \
    -r "$APP_ROOT/app/requirements-status.txt"

CODEX_BIN=$APP_ROOT/runtime/node_modules/.bin/codex
INSTALLED_CODEX_VERSION=
if [[ -x $CODEX_BIN ]]; then
    INSTALLED_CODEX_VERSION=$($CODEX_BIN --version 2>/dev/null || true)
fi
if [[ $INSTALLED_CODEX_VERSION != *"$CODEX_VERSION"* ]]; then
    npm install --prefix "$APP_ROOT/runtime" "@openai/codex@$CODEX_VERSION"
fi

if [[ ! -e $CONFIG_ROOT/providers.toml ]]; then
    install -o root -g root -m 0644 \
        "$APP_ROOT/app/config/providers.example.toml" \
        "$CONFIG_ROOT/providers.toml"
fi
install -o root -g root -m 0644 \
    "$APP_ROOT/app/deploy/systemd/codex-provider-worker.service" \
    /etc/systemd/system/codex-provider-worker.service
sed "s/__WEB_PORT__/$WEB_PORT/g" \
    "$APP_ROOT/app/deploy/systemd/codex-provider-web.service" \
    > /etc/systemd/system/codex-provider-web.service
chmod 0644 /etc/systemd/system/codex-provider-web.service
sed -e "s/__WEB_PORT__/$WEB_PORT/g" -e "s/__PUBLIC_IP__/$PUBLIC_IP/g" \
    "$APP_ROOT/app/deploy/nginx/codex-provider-status.conf" \
    > "$CONFIG_ROOT/nginx.conf"
chmod 0644 "$CONFIG_ROOT/nginx.conf"

systemctl daemon-reload
systemctl enable codex-provider-worker.service
systemctl enable codex-provider-web.service

printf '%s\n' "Application installed. Credentials were not created or changed."
printf 'Rendered nginx config: %s\n' "$CONFIG_ROOT/nginx.conf"
