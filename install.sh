#!/usr/bin/env bash
# Secure native installer for Debian and Ubuntu.

set -euo pipefail

INSTALL_DIR="/opt/2rtk"
CONFIG_DIR="/etc/2rtk"
CONFIG_PATH="$CONFIG_DIR/config.ini"
LOG_DIR="/var/log/2rtk"
SERVICE_NAME="2rtk"
SERVICE_USER="ntripcaster"
SOURCE_URL="https://github.com/Rampump/NTRIPcaster.git"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "錯誤：請使用 sudo 執行此安裝腳本。" >&2
    exit 1
fi

if [[ ! -f /etc/debian_version ]]; then
    echo "錯誤：此腳本只支援 Debian 或 Ubuntu。" >&2
    exit 1
fi

umask 077
echo "安裝系統套件與 Python 執行環境..."
apt-get update
apt-get install -y python3 python3-pip python3-venv git

if ! python3 -c "import struct, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and struct.calcsize('P') * 8 == 64 else 1)"; then
    echo "錯誤：此版本要求 64-bit Python 3.11；請先使用可信任的系統套件來源安裝。" >&2
    exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

install -d -m 0755 "$INSTALL_DIR"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$INSTALL_DIR/data"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$CONFIG_DIR" "$LOG_DIR"

TEMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TEMP_ROOT"' EXIT
SOURCE_DIR="$TEMP_ROOT/NTRIPcaster"

echo "下載專案來源..."
git clone --depth 1 "$SOURCE_URL" "$SOURCE_DIR"
cp -a "$SOURCE_DIR/." "$INSTALL_DIR/"

echo "建立 64-bit Python 3.11 相容虛擬環境並安裝依賴..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

if [[ -z "${NTRIP_ADMIN_PASSWORD:-}" ]]; then
    if [[ ! -t 0 ]]; then
        echo "錯誤：非互動安裝必須先設定 NTRIP_ADMIN_PASSWORD；腳本不提供公開預設密碼。" >&2
        exit 1
    fi

    read -r -s -p "請輸入至少 16 個字元的管理員密碼：" NTRIP_ADMIN_PASSWORD
    echo
    read -r -s -p "請再次輸入管理員密碼：" NTRIP_ADMIN_PASSWORD_CONFIRM
    echo
    if [[ "$NTRIP_ADMIN_PASSWORD" != "$NTRIP_ADMIN_PASSWORD_CONFIRM" ]]; then
        echo "錯誤：兩次輸入不一致。" >&2
        exit 1
    fi
    unset NTRIP_ADMIN_PASSWORD_CONFIRM
fi

export NTRIP_ADMIN_PASSWORD
echo "建立安全設定檔（不顯示密碼或密鑰）..."
"$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/scripts/deployment_config.py" write-config \
    --output "$CONFIG_PATH" \
    --ntrip-host 0.0.0.0 \
    --web-host 127.0.0.1 \
    --ntrip-port 2101 \
    --web-port 5757 \
    --database-path "$INSTALL_DIR/data/2rtk.db" \
    --log-dir "$LOG_DIR"
unset NTRIP_ADMIN_PASSWORD NTRIP_SECRET_KEY SECRET_KEY

chown -R root:root "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/data" "$LOG_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_PATH"
chmod 0600 "$CONFIG_PATH"
chmod 0750 "$INSTALL_DIR/data" "$CONFIG_DIR" "$LOG_DIR"
chmod 0755 "$INSTALL_DIR/main.py" "$INSTALL_DIR/healthcheck.py"

cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=2RTK NTRIP Caster
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=NTRIP_CONFIG_FILE=$CONFIG_PATH
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/main.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$INSTALL_DIR/data $LOG_DIR

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/logrotate.d/2rtk <<EOF
$LOG_DIR/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 $SERVICE_USER $SERVICE_USER
}
EOF

if command -v ufw >/dev/null 2>&1; then
    ufw allow 2101/tcp
elif command -v firewall-cmd >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port=2101/tcp
    firewall-cmd --reload
else
    echo "警告：未偵測到支援的防火牆，請只開放必要的 TCP 2101 連接埠。"
fi

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "錯誤：服務啟動失敗。請使用 journalctl -u $SERVICE_NAME 檢查，不要張貼設定檔內容。" >&2
    exit 1
fi

echo "安裝完成。"
echo "設定檔：$CONFIG_PATH（權限 0600；請勿張貼內容）"
echo "NTRIP：TCP 2101，預設接受遠端連線；請使用防火牆限制來源。"
echo "Web：僅監聽 127.0.0.1:5757，遠端管理請透過已加固的反向代理。"
echo "本腳本未顯示任何密碼或密鑰。"
