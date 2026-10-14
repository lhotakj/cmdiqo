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
    curl -fsSL --retry 3 --retry-delay 2 --retry-connrefused "${DOWNLOAD_URL}" -o "${TMP_EXE}" || {
        echo "[ERROR] Failed to download ${BINARY_NAME} from ${DOWNLOAD_URL}" >&2
        echo "[INFO] If a new release was just published, GitHub CDN may take 1-2 minutes to replicate assets globally. Please retry shortly." >&2
        exit 1
    }
elif command -v wget >/dev/null 2>&1; then
    wget --tries=3 --waitretry=2 -qO "${TMP_EXE}" "${DOWNLOAD_URL}" || {
        echo "[ERROR] Failed to download ${BINARY_NAME} from ${DOWNLOAD_URL}" >&2
        echo "[INFO] If a new release was just published, GitHub CDN may take 1-2 minutes to replicate assets globally. Please retry shortly." >&2
        exit 1
    }
else
    echo "[ERROR] Neither curl nor wget is installed. Please install curl or wget." >&2
    exit 1
fi

if [[ ! -s "${TMP_EXE}" ]]; then
    echo "[ERROR] Downloaded installer binary is empty: ${TMP_EXE}" >&2
    exit 1
fi

chmod +x "${TMP_EXE}"

# 3. Assemble installer arguments
RAW_ARGS=("$@")
ARGS=()
HAS_ACTION=false
HAS_CLI=false

i=0
while [[ $i -lt ${#RAW_ARGS[@]} ]]; do
    arg="${RAW_ARGS[$i]}"
    case "${arg}" in
        --hub-url)
            ((i++))
            ARGS+=("--hub" "${RAW_ARGS[$i]}")
            ;;
        --hub-url=*)
            ARGS+=("--hub" "${arg#*=}")
            ;;
        --agent-token)
            ((i++))
            ARGS+=("--token" "${RAW_ARGS[$i]}")
            ;;
        --agent-name)
            ((i++))
            ARGS+=("--name" "${RAW_ARGS[$i]}")
            ;;
        --cli)
            HAS_CLI=true
            ARGS+=("--cli")
            ;;
        --install|--upgrade|--fresh|--configure|--uninstall|--purge|--status|--test-hub|--start|--restart|--stop|--help|-h)
            HAS_ACTION=true
            ARGS+=("${arg}")
            ;;
        *)
            ARGS+=("${arg}")
            ;;
    esac
    ((i++))
done

# Ensure --cli mode is explicitly active
if [[ "${HAS_CLI}" = false ]]; then
    ARGS=("--cli" "${ARGS[@]}")
fi

if [[ "${HAS_ACTION}" = false ]]; then
    ARGS+=("--install")
fi

# Apply environment variable fallbacks if corresponding flags were not provided
if [[ -n "${HUB_URL:-}" ]] && [[ ! " ${ARGS[*]} " =~ " --hub " ]] && [[ ! " ${ARGS[*]} " =~ " --hub-url " ]]; then
    ARGS+=("--hub" "${HUB_URL}")
fi
if [[ -n "${AGENT_TOKEN:-}" ]] && [[ ! " ${ARGS[*]} " =~ " --token " ]]; then
    ARGS+=("--token" "${AGENT_TOKEN}")
fi
if [[ -n "${AGENT_NAME:-}" ]] && [[ ! " ${ARGS[*]} " =~ " --name " ]]; then
    ARGS+=("--name" "${AGENT_NAME}")
fi

echo "[+] Launching installer: ${BINARY_NAME} ${ARGS[*]}"
"${TMP_EXE}" "${ARGS[@]}"

