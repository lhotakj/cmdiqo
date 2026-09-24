#!/usr/bin/env bash
# ==============================================================================
# Install cmdiqo-agent as a systemd service on Linux
# ==============================================================================
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "[ERROR] This installer must be run as root (sudo ./install-service.sh)"
   exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_PATH="/usr/local/bin/cmdiqo-agent"
CONF_DIR="/etc/cmdiqo-agent"
SERVICE_DEST="/etc/systemd/system/cmdiqo-agent.service"

echo "=== Installing Cmdiqo Agent (cmdiqo-agent) ==="

# 1. Install Binary or Python script
if [[ -f "${SCRIPT_DIR}/../dist/cmdiqo-agent" ]]; then
    echo "[+] Installing binary from dist/cmdiqo-agent to ${BIN_PATH}..."
    cp "${SCRIPT_DIR}/../dist/cmdiqo-agent" "${BIN_PATH}"
    chmod +x "${BIN_PATH}"
elif [[ -f "${SCRIPT_DIR}/cmdiqo-agent" ]]; then
    echo "[+] Installing binary from ./cmdiqo-agent to ${BIN_PATH}..."
    cp "${SCRIPT_DIR}/cmdiqo-agent" "${BIN_PATH}"
    chmod +x "${BIN_PATH}"
elif [[ -f "${SCRIPT_DIR}/cmdiqo-agent-linux-x64" ]]; then
    echo "[+] Installing binary from ./cmdiqo-agent-linux-x64 to ${BIN_PATH}..."
    cp "${SCRIPT_DIR}/cmdiqo-agent-linux-x64" "${BIN_PATH}"
    chmod +x "${BIN_PATH}"
else
    echo "[+] No pre-built binary found. Setting up Python virtual environment..."
    mkdir -p /opt/cmdiqo-agent
    cp "${SCRIPT_DIR}/agent.py" /opt/cmdiqo-agent/
    python3 -m venv /opt/cmdiqo-agent/venv
    /opt/cmdiqo-agent/venv/bin/pip install --upgrade pip
    /opt/cmdiqo-agent/venv/bin/pip install -r "${SCRIPT_DIR}/requirements.txt"
    cat << 'EOF' > "${BIN_PATH}"
#!/usr/bin/env bash
exec /opt/cmdiqo-agent/venv/bin/python /opt/cmdiqo-agent/agent.py "$@"
EOF
    chmod +x "${BIN_PATH}"
fi

# 2. Setup configuration directory & environment file
mkdir -p "${CONF_DIR}"

OLD_NOTIFY="no"
if [[ -f "${CONF_DIR}/agent.env" ]]; then
    if grep -qi -E "^(AGENT_NOTIFICATION_NOTIFY|AGENT_CONNECTION_NOTIFY)=(true|1|yes)" "${CONF_DIR}/agent.env" 2>/dev/null; then
        OLD_NOTIFY="yes"
    fi
fi

if [[ -t 0 ]]; then
    read -rp "Enable Slack notification on agent connect/disconnect? (yes/no) [Default: ${OLD_NOTIFY}]: " NOTIFY_RESP || true
fi
NOTIFY_RESP="${NOTIFY_RESP:-${OLD_NOTIFY}}"

case "$(echo "${NOTIFY_RESP}" | tr '[:upper:]' '[:lower:]')" in
    yes|y|true|1) NOTIFY_VAL="true" ;;
    *) NOTIFY_VAL="false" ;;
esac

if [[ ! -f "${CONF_DIR}/agent.env" ]]; then
    echo "[+] Creating configuration template at ${CONF_DIR}/agent.env..."
    cp "${SCRIPT_DIR}/agent.env.example" "${CONF_DIR}/agent.env"
    echo "AGENT_NOTIFICATION_NOTIFY=${NOTIFY_VAL}" >> "${CONF_DIR}/agent.env"
    chmod 600 "${CONF_DIR}/agent.env"
    echo "[!] Please edit ${CONF_DIR}/agent.env with your real HUB_URL and AGENT_TOKEN!"
else
    echo "[*] Existing configuration found at ${CONF_DIR}/agent.env."
    if grep -q -E "^(AGENT_NOTIFICATION_NOTIFY|AGENT_CONNECTION_NOTIFY)=" "${CONF_DIR}/agent.env" 2>/dev/null; then
        sed -i -E "s/^(AGENT_NOTIFICATION_NOTIFY|AGENT_CONNECTION_NOTIFY)=.*/AGENT_NOTIFICATION_NOTIFY=${NOTIFY_VAL}/" "${CONF_DIR}/agent.env"
    else
        echo "AGENT_NOTIFICATION_NOTIFY=${NOTIFY_VAL}" >> "${CONF_DIR}/agent.env"
    fi
fi

# 3. Install systemd service
echo "[+] Installing systemd service unit to ${SERVICE_DEST}..."
cp "${SCRIPT_DIR}/cmdiqo-agent.service" "${SERVICE_DEST}"

# 4. Enable and start service
echo "[+] Reloading systemd and enabling service..."
systemctl daemon-reload
systemctl enable cmdiqo-agent.service
systemctl restart cmdiqo-agent.service

echo "=== Installation Complete ==="
echo "Check service status:  sudo systemctl status cmdiqo-agent"
echo "View live agent logs:  sudo journalctl -u cmdiqo-agent -f"
