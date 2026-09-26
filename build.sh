#!/usr/bin/env bash
# build.sh — Cross-platform local build script for Cmdiqo.
#
# Usage:
#   ./build.sh                                     # Build standalone cmdiqo-agent binary (default)
#   ./build.sh --bot                               # Build Docker image for cmdiqo-bot
#   ./build.sh --all                               # Build both agent binary and bot container
#   ./build.sh --clean                             # Clean build artifacts before compiling
#   ./build.sh --dev                               # Build with .dev version suffix
#   ./build.sh --push                              # Build and push multi-arch Docker image
#   ./build.sh -v 1.2.0                            # Build with explicit version override
#   ./build.sh --copy-to /path/to/destination      # Copy compiled binary to destination

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

show_help() {
    cat << 'EOF'
Usage: ./build.sh [options]

Build Targets:
  --agent, -a          Build standalone cmdiqo-agent binary (default)
  --bot, -b, --docker  Build Docker container image for cmdiqo-bot
  --all                Build both agent binary and bot container
  --target <string>    Explicit target: 'agent', 'bot', or 'all'

Versioning:
  -v, --version <str>  Override version string (e.g. '1.2.0')
  --dev                Tag version as development build (<major>.<minor>.dev<HEX>)

Compilation & Packaging:
  --clean              Clean build/ and dist/ directories before building
  --skip-sign          Skip digital signing on Windows
  --copy-to <path>     Copy compiled binary to destination path upon completion

Docker Options:
  --push               Build and push multi-arch Docker images to registry (linux/amd64,linux/arm64)
  --docker-user <usr>  Docker Hub registry username (default: $DOCKERHUB_USERNAME or 'lhotakj')

Help:
  -h, --help           Show this help message and exit

Examples:
  ./build.sh                                       # Build standalone agent binary
  ./build.sh --bot                                 # Build bot Docker container
  ./build.sh --all --clean                         # Clean build artifacts and build both
  ./build.sh -v 1.2.0 --copy-to /tmp/cmdiqo-agent  # Build specific version and copy
  ./build.sh --bot --push                          # Build and push multi-arch container
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Defaults
BUILD_AGENT=false
BUILD_BOT=false
PUSH_DOCKER=false
DEV_FLAG=false
CLEAN=false
SKIP_SIGN=false
COPY_TO=""
VERSION_OVERRIDE=""
DOCKER_USERNAME="${DOCKERHUB_USERNAME:-lhotakj}"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent|-a)
            BUILD_AGENT=true
            shift
            ;;
        --bot|-b|--docker)
            BUILD_BOT=true
            shift
            ;;
        --all)
            BUILD_AGENT=true
            BUILD_BOT=true
            shift
            ;;
        --target)
            TARGET_ARG="$(echo "$2" | tr '[:upper:]' '[:lower:]')"
            case "$TARGET_ARG" in
                agent) BUILD_AGENT=true ;;
                bot)   BUILD_BOT=true ;;
                all)   BUILD_AGENT=true; BUILD_BOT=true ;;
                *)     error "Invalid target '$2'. Must be 'agent', 'bot', or 'all'."; exit 1 ;;
            esac
            shift 2
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --skip-sign)
            SKIP_SIGN=true
            shift
            ;;
        --copy-to)
            COPY_TO="$2"
            shift 2
            ;;
        --push)
            PUSH_DOCKER=true
            BUILD_BOT=true
            shift
            ;;
        --docker-user)
            DOCKER_USERNAME="$2"
            shift 2
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
            show_help
            exit 0
            ;;
        *)
            error "Unknown argument '$1'. Run with --help for available options."
            exit 1
            ;;
    esac
done

# Default to building agent if neither specified
if [[ "$BUILD_AGENT" = false && "$BUILD_BOT" = false ]]; then
    BUILD_AGENT=true
fi

# 0. Clean old build artifacts if requested
if [[ "$CLEAN" = true ]]; then
    info "Cleaning previous build artifacts in build/ and dist/ ..."
    rm -rf "$ROOT_DIR/build" "$ROOT_DIR/dist"
fi

# 1. Synchronize version numbers
info "Resolving and synchronizing version numbers ..."
PYTHON_CMD="python3"
if ! command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

VERSION_CMD=("$PYTHON_CMD" "development/update_version.py" "--print-version")
if [[ -n "$VERSION_OVERRIDE" ]]; then
    VERSION_CMD+=("--version" "$VERSION_OVERRIDE")
elif [[ "$DEV_FLAG" = true ]]; then
    VERSION_CMD+=("--dev")
fi

VERSION=$("${VERSION_CMD[@]}" | tr -d '\r\n')
SHORT_VERSION=$(echo "$VERSION" | cut -d. -f1,2)
TIMESTAMP=$(date +%Y%m%d%H%M%S)

info "Build target version: \033[34m${VERSION}\033[0m (Short: ${SHORT_VERSION})"

# 2. Build standalone agent binary
if [[ "$BUILD_AGENT" = true ]]; then
    info "=== Building standalone cmdiqo-agent binary ==="
    AGENT_BUILD_ARGS=("$PYTHON_CMD" "development/build_agent.py")
    if [[ -n "$VERSION_OVERRIDE" ]]; then
        AGENT_BUILD_ARGS+=(--version "$VERSION_OVERRIDE")
    fi
    if [[ "$SKIP_SIGN" = true ]]; then
        AGENT_BUILD_ARGS+=(--skip-sign)
    fi
    if [[ -n "$COPY_TO" ]]; then
        AGENT_BUILD_ARGS+=(--copy-to "$COPY_TO")
    fi
    "${AGENT_BUILD_ARGS[@]}"

    DIST_DIR="$ROOT_DIR/dist"
    BIN_NAME="cmdiqo-agent"
    [[ "$(uname -s)" == *"MINGW"* || "$(uname -s)" == *"MSYS"* || "$(uname -s)" == *"CYGWIN"* ]] && BIN_NAME="cmdiqo-agent.exe"

    DIST_BIN="$DIST_DIR/$BIN_NAME"
    if [[ -f "$DIST_BIN" ]]; then
        info ""
        info "=== Artifact Verification ($DIST_BIN) ==="
        BIN_SIZE=$(ls -lh "$DIST_BIN" | awk '{print $5}')
        info "Binary size: $BIN_SIZE"

        if command -v sha256sum >/dev/null 2>&1; then
            HASH_BIN=$(sha256sum "$DIST_BIN" | awk '{print $1}')
            info "Binary SHA256 Checksum: $HASH_BIN"
        elif command -v shasum >/dev/null 2>&1; then
            HASH_BIN=$(shasum -a 256 "$DIST_BIN" | awk '{print $1}')
            info "Binary SHA256 Checksum: $HASH_BIN"
        fi
    fi

    # Check for zip package
    ZIP_FILE=$(find "$DIST_DIR" -name "cmdiqo-agent-*.zip" -type f 2>/dev/null | head -n 1 || true)
    if [[ -n "$ZIP_FILE" && -f "$ZIP_FILE" ]]; then
        if command -v sha256sum >/dev/null 2>&1; then
            HASH_ZIP=$(sha256sum "$ZIP_FILE" | awk '{print $1}')
            info "Zip Package ($ZIP_FILE) SHA256 Checksum: $HASH_ZIP"
        elif command -v shasum >/dev/null 2>&1; then
            HASH_ZIP=$(shasum -a 256 "$ZIP_FILE" | awk '{print $1}')
            info "Zip Package ($ZIP_FILE) SHA256 Checksum: $HASH_ZIP"
        fi
    fi
fi

# 3. Build Docker container for bot (if requested)
if [[ "$BUILD_BOT" = true ]]; then
    info "=== Building Docker image for cmdiqo-bot ==="
    if ! command -v docker >/dev/null 2>&1; then
        error "Docker CLI is not installed or not in PATH."
        exit 1
    fi

    TAG_LATEST="${DOCKER_USERNAME}/cmdiqo-bot:latest"
    TAG_SHORT="${DOCKER_USERNAME}/cmdiqo-bot:${SHORT_VERSION}"
    TAG_FULL="${DOCKER_USERNAME}/cmdiqo-bot:${VERSION}"
    TAG_TS="${DOCKER_USERNAME}/cmdiqo-bot:${TIMESTAMP}"

    if [[ "$PUSH_DOCKER" = true ]]; then
        info "Building and pushing multi-arch images (${TAG_FULL}) via buildx ..."
        docker buildx build \
            --platform linux/amd64,linux/arm64 \
            --no-cache \
            -t "$TAG_LATEST" \
            -t "$TAG_SHORT" \
            -t "$TAG_FULL" \
            -t "$TAG_TS" \
            -f docker/Dockerfile \
            --push \
            .
        info "Docker multi-arch images built and pushed successfully."
    else
        info "Building local container image (${TAG_FULL}) ..."
        docker build \
            -t "$TAG_LATEST" \
            -t "$TAG_SHORT" \
            -t "$TAG_FULL" \
            -t "$TAG_TS" \
            -f docker/Dockerfile \
            .
        info "Built container image: ${TAG_FULL}"
    fi
fi

info "\033[0;32m[+] Build complete successfully for version ${VERSION}!\033[0m"
