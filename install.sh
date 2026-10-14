#!/usr/bin/env bash
# ==============================================================================
# Cmdiqo Agent Universal Bootstrap Installer for Linux
#
# Usage (One-Liner using Pipe):
#   curl -fsSL https://raw.githubusercontent.com/lhotakj/cmdiqo/main/install.sh | sudo bash -s -- --hub-url ws://<HUB_IP>:8765 --token <AGENT_TOKEN> --name <HOST_NAME>
#
# Environment variables:
#   HUB_URL       - WebSocket Hub URL (alternative to --hub-url)
#   AGENT_TOKEN   - Agent authentication token (alternative to --token)
#   AGENT_NAME    - Host/agent name (alternative to --name)
#   CMDIQO_VERSION - Specific version tag (defaults to "latest")
#   CMDIQO_REPO    - GitHub repository (defaults to "lhotakj/cmdiqo")
# ==============================================================================
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "[ERROR] Cmdiqo Agent installer must be run as root (e.g. curl ... | sudo bash)" >&2
    exit 1
fi

REPO="${CMDIQO_REPO:-lhotakj/cmdiqo}"
VERSION="${CMDIQO_VERSION:-latest}"

# 1. Detect CPU Architecture and select appropriate binary
ARCH="$(uname -m)"
case "${ARCH}" in
    x86_64|amd64)
        BINARY_NAME="cmdiqo-agent-linux-x64-setup-cli"
        ;;
    aarch64|arm64)
        BINARY_NAME="cmdiqo-agent-linux-arm64-setup"
        ;;
    armv6l|armv7l|armhf)
        BINARY_NAME="cmdiqo-agent-linux-armv6-setup"
        ;;
    *)
        echo "[ERROR] Unsupported CPU architecture: ${ARCH}. Supported: x86_64, aarch64, armv6l." >&2
        exit 1
        ;;
esac

# 2. Resolve Download URL
if [[ "${VERSION}" == "latest" ]]; then
    DOWNLOAD_URL="https://github.com/${REPO}/releases/latest/download/${BINARY_NAME}"
else
    TAG="${VERSION}"
    [[ "${TAG}" != v* ]] && TAG="v${TAG}"
    DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${TAG}/${BINARY_NAME}"
fi

TMP_DIR="$(mktemp -d /tmp/cmdiqo-setup-XXXXXX)"
TMP_EXE="${TMP_DIR}/${BINARY_NAME}"
cleanup() {
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT INT TERM

echo "=== Cmdiqo Agent Bootstrap Installer ==="
echo "[+] Detected architecture: ${ARCH} (${BINARY_NAME})"
echo "[+] Fetching native installer from ${DOWNLOAD_URL}..."

if command -v curl >/dev/null 2>&1; then
    curl -fsSL "${DOWNLOAD_URL}" -o "${TMP_EXE}"
elif command -v wget >/dev/null 2>&1; then
    wget -qO "${TMP_EXE}" "${DOWNLOAD_URL}"
else
    echo "[ERROR] Neither curl nor wget is installed. Please install curl or wget." >&2
    exit 1
fi

chmod +x "${TMP_EXE}"

# 3. Assemble installer arguments
ARGS=("$@")
HAS_ACTION=false
for arg in "${ARGS[@]}"; do
    case "${arg}" in
        --install|--upgrade|--fresh|--configure|--uninstall|--purge|--status|--test-hub|--start|--restart|--stop|--help|-h)
            HAS_ACTION=true
            break
            ;;
    esac
done

if [[ "${HAS_ACTION}" = false ]]; then
    ARGS=("--install" "${ARGS[@]}")
fi

# Apply environment variable fallbacks if corresponding flags were not provided
if [[ -n "${HUB_URL:-}" ]] && [[ ! " ${ARGS[*]} " =~ " --hub-url " ]]; then
    ARGS+=("--hub-url" "${HUB_URL}")
fi
if [[ -n "${AGENT_TOKEN:-}" ]] && [[ ! " ${ARGS[*]} " =~ " --token " ]]; then
    ARGS+=("--token" "${AGENT_TOKEN}")
fi
if [[ -n "${AGENT_NAME:-}" ]] && [[ ! " ${ARGS[*]} " =~ " --name " ]]; then
    ARGS+=("--name" "${AGENT_NAME}")
fi

echo "[+] Launching installer..."
"${TMP_EXE}" "${ARGS[@]}"
