#!/usr/bin/env bash
# ==============================================================================
# Uninstall cmdiqo-agent systemd service on Linux
# ==============================================================================
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
   echo "[ERROR] This script must be run as root (sudo ./uninstall-service.sh)"
   exit 1
fi

SERVICE_NAME="cmdiqo-agent.service"
SERVICE_DEST="/etc/systemd/system/${SERVICE_NAME}"
BIN_PATH="/usr/local/bin/cmdiqo-agent"
CONF_DIR="/etc/cmdiqo-agent"
OPT_DIR="/opt/cmdiqo-agent"

PURGE=false
if [[ "${1:-}" == "--purge" || "${1:-}" == "-p" ]]; then
    PURGE=true
fi

echo "=== Uninstalling Cmdiqo Agent (${SERVICE_NAME}) ==="

if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    echo "[*] Stopping systemd service..."
    systemctl stop "${SERVICE_NAME}" || true
fi

if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    echo "[*] Disabling systemd service..."
    systemctl disable "${SERVICE_NAME}" || true
fi

if [[ -f "${SERVICE_DEST}" ]]; then
    echo "[*] Removing service unit file ${SERVICE_DEST}..."
    rm -f "${SERVICE_DEST}"
    systemctl daemon-reload || true
fi

if [[ -f "${BIN_PATH}" ]]; then
    echo "[*] Removing binary at ${BIN_PATH}..."
    rm -f "${BIN_PATH}"
fi

if [[ -d "${OPT_DIR}" ]]; then
    echo "[*] Removing directory ${OPT_DIR}..."
    rm -rf "${OPT_DIR}"
fi

if [[ "$PURGE" = true ]]; then
    if [[ -d "${CONF_DIR}" ]]; then
        echo "[*] Purging configuration and credentials from ${CONF_DIR}..."
        rm -rf "${CONF_DIR}"
    fi
else
    if [[ -d "${CONF_DIR}" ]]; then
        echo "[i] Note: Configuration directory ${CONF_DIR} was preserved."
        echo "    (Run with --purge if you wish to delete configuration and credentials)."
    fi
fi

echo "=== Uninstall Complete ==="
