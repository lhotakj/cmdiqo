#!/usr/bin/env bash
# development/build.sh — Cross-platform local build script for Shango.
#
# Usage:
#   ./development/build.sh               # Build standalone shango-agent binary for current platform
#   ./development/build.sh --agent       # Build standalone agent binary
#   ./development/build.sh --bot         # Build Docker image for shango-bot
#   ./development/build.sh --all         # Build both agent binary and bot container
#   ./development/build.sh --dev         # Build with .dev version suffix
#   ./development/build.sh --push        # Build and push Docker image to registry
#   ./development/build.sh -v 1.2.0      # Build with explicit version override

set -e

# Logging helpers
info() {
    printf "\033[0;32m[INFO]\033[0m %s\n"  "$1"
}

debug() {
    printf "\033[0;90m[DEBUG]\033[0m %s\n" "$1"
}

error() {
    printf "\033[0;31m[ERROR]\033[0m %s\n" "$1"
}

warn() {
    printf "\033[0;33m[WARN]\033[0m %s\n"  "$1"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# Defaults
BUILD_AGENT=false
BUILD_BOT=false
PUSH_DOCKER=false
DEV_FLAG=false
VERSION_OVERRIDE=""
DOCKER_USERNAME="lhotakj"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent)
            BUILD_AGENT=true
            shift
            ;;
        --bot|--docker)
            BUILD_BOT=true
            shift
            ;;
        --all)
            BUILD_AGENT=true
            BUILD_BOT=true
            shift
            ;;
        --push)
            PUSH_DOCKER=true
            BUILD_BOT=true
            shift
            ;;
        --dev)
            DEV_FLAG=true
            shift
            ;;
        --version|-v)
            VERSION_OVERRIDE="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: ./development/build.sh [options]"
            echo "Options:"
            echo "  --agent       Build standalone agent binary (default)"
            echo "  --bot         Build Docker container for shango-bot"
            echo "  --all         Build both agent binary and bot container"
            echo "  --dev         Tag version as development build (<major>.<minor>.dev<HEX>)"
            echo "  --push        Build and push Docker image to DockerHub"
            echo "  -v, --version Override version string (e.g. 1.2.0)"
            exit 0
            ;;
        *)
            warn "Unknown argument '$1'. Defaulting to building agent."
            BUILD_AGENT=true
            shift
            ;;
    esac
done

# Default to building agent if neither specified
if [[ "$BUILD_AGENT" = false && "$BUILD_BOT" = false ]]; then
    BUILD_AGENT=true
fi

# 1. Synchronize version numbers
info "Resolving and synchronizing version numbers ..."
PYTHON_CMD="python3"
if ! command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

VERSION_CMD=("$PYTHON_CMD" "development/update_version.py")
if [[ -n "$VERSION_OVERRIDE" ]]; then
    "${VERSION_CMD[@]}" --version "$VERSION_OVERRIDE"
elif [[ "$DEV_FLAG" = true ]]; then
    "${VERSION_CMD[@]}" --dev
else
    "${VERSION_CMD[@]}"
fi

VERSION=$(cat version.txt | tr -d '\r\n')
SHORT_VERSION=$(cat short_version.txt | tr -d '\r\n')
TIMESTAMP=$(date +%Y%m%d%H%M%S)

info "Build target version: \033[34m${VERSION}\033[0m (Short: ${SHORT_VERSION})"

# 2. Build standalone agent binary
if [[ "$BUILD_AGENT" = true ]]; then
    info "=== Building standalone shango-agent binary ==="
    AGENT_BUILD_ARGS=("$PYTHON_CMD" "development/build_agent.py")
    if [[ -n "$VERSION_OVERRIDE" ]]; then
        AGENT_BUILD_ARGS+=(--version "$VERSION_OVERRIDE")
    fi
    "${AGENT_BUILD_ARGS[@]}"
fi

# 3. Build Docker container for bot (if requested)
if [[ "$BUILD_BOT" = true ]]; then
    info "=== Building Docker image for shango-bot ==="
    if ! command -v docker >/dev/null 2>&1; then
        error "Docker CLI is not installed or not in PATH."
        exit 1
    fi

    TAG_LATEST="${DOCKER_USERNAME}/shango-bot:latest"
    TAG_SHORT="${DOCKER_USERNAME}/shango-bot:${SHORT_VERSION}"
    TAG_FULL="${DOCKER_USERNAME}/shango-bot:${VERSION}"
    TAG_TS="${DOCKER_USERNAME}/shango-bot:${TIMESTAMP}"

    if [[ "$PUSH_DOCKER" = true ]]; then
        info "Building and pushing multi-arch images (${TAG_FULL}) ..."
        docker buildx build \
            --platform linux/amd64,linux/arm64 \
            --no-cache \
            -t "$TAG_LATEST" \
            -t "$TAG_SHORT" \
            -t "$TAG_FULL" \
            -t "$TAG_TS" \
            -f Dockerfile \
            --push \
            .
    else
        info "Building local container image (${TAG_FULL}) ..."
        docker build \
            -t "$TAG_LATEST" \
            -t "$TAG_SHORT" \
            -t "$TAG_FULL" \
            -t "$TAG_TS" \
            -f Dockerfile \
            .
        info "Built container image: ${TAG_FULL}"
    fi
fi

info "\033[0;32m[+] Build complete successfully for version ${VERSION}!\033[0m"
