#!/usr/bin/env bash
# ==============================================================================
# Install shango-agent as a systemd service on Linux
# ==============================================================================
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "[ERROR] This installer must be run as root (sudo ./install-service.sh)"
   exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_PATH="/usr/local/bin/shango-agent"
CONF_DIR="/etc/shango-agent"
SERVICE_DEST="/etc/systemd/system/shango-agent.service"

echo "=== Installing Shango Agent (shango-agent) ==="

# 1. Install Binary or Python script
if [[ -f "${SCRIPT_DIR}/../dist/shango-agent" ]]; then
    echo "[+] Installing binary from dist/shango-agent to ${BIN_PATH}..."
    cp "${SCRIPT_DIR}/../dist/shango-agent" "${BIN_PATH}"
    chmod +x "${BIN_PATH}"
elif [[ -f "${SCRIPT_DIR}/shango-agent" ]]; then
    echo "[+] Installing binary from ./shango-agent to ${BIN_PATH}..."
    cp "${SCRIPT_DIR}/shango-agent" "${BIN_PATH}"
    chmod +x "${BIN_PATH}"
else
    echo "[+] No pre-built binary found. Setting up Python virtual environment..."
    mkdir -p /opt/shango-agent
    cp "${SCRIPT_DIR}/agent.py" /opt/shango-agent/
    python3 -m venv /opt/shango-agent/venv
    /opt/shango-agent/venv/bin/pip install --upgrade pip
    /opt/shango-agent/venv/bin/pip install -r "${SCRIPT_DIR}/requirements.txt"
    cat << 'EOF' > "${BIN_PATH}"
#!/usr/bin/env bash
exec /opt/shango-agent/venv/bin/python /opt/shango-agent/agent.py "$@"
EOF
    chmod +x "${BIN_PATH}"
fi

# 2. Setup configuration directory & environment file
mkdir -p "${CONF_DIR}"
if [[ ! -f "${CONF_DIR}/agent.env" ]]; then
    echo "[+] Creating configuration template at ${CONF_DIR}/agent.env..."
    cp "${SCRIPT_DIR}/agent.env.example" "${CONF_DIR}/agent.env"
    chmod 600 "${CONF_DIR}/agent.env"
    echo "[!] Please edit ${CONF_DIR}/agent.env with your real HUB_URL and AGENT_TOKEN!"
else
    echo "[*] Existing configuration found at ${CONF_DIR}/agent.env (preserving)."
fi

# 3. Install systemd service
echo "[+] Installing systemd service unit to ${SERVICE_DEST}..."
cp "${SCRIPT_DIR}/shango-agent.service" "${SERVICE_DEST}"

# 4. Enable and start service
echo "[+] Reloading systemd and enabling service..."
systemctl daemon-reload
systemctl enable shango-agent.service
systemctl restart shango-agent.service

echo "=== Installation Complete ==="
echo "Check service status:  sudo systemctl status shango-agent"
echo "View live agent logs:  sudo journalctl -u shango-agent -f"
