#!/usr/bin/env bash
# build.sh — Linux agent and bot-container build script for Cmdiqo.

set -e

info() {
    printf "\033[0;32m[INFO]\033[0m %s\n" "$1"
}

error() {
    printf "\033[0;31m[ERROR]\033[0m %s\n" "$1"
}

warn() {
    printf "\033[0;33m[WARN]\033[0m %s\n" "$1"
}

show_help() {
    cat << 'EOF'
Usage: ./build.sh [options]

Build Targets:
  --agent, -a          Build the standalone Linux agent binary and ZIP package (default)
  --installer, -i      Build the agent binary, ZIP package, and native Linux installer
  --bot, -b, --docker  Build Docker container image for cmdiqo-bot
  --all                Build the agent package, native installer, and bot container
  --target <string>    Explicit target: 'agent', 'installer', 'bot', or 'all'

Versioning:
  -v, --version <str>  Override version string (e.g. '1.2.0')
  --dev                Tag version as development build (<major>.<minor>.dev<HEX>)

Platform Selection:
  --platform, -p <str> Target platform: 'linux64' (default: linux64)

Compilation & Packaging:
  --clean              Clean build outputs, Linux agent artifacts, and native installer artifacts
  --skip-agent-build   With --installer, reuse dist/cmdiqo-agent-linux-x64.zip
                       and build only the native C++ installer
  --copy-to <path>     Copy the compiled binary to destination path upon completion

Docker Options:
  --push               Build and push multi-arch Docker images to registry (linux/amd64,linux/arm64)
  --docker-user <usr>  Docker Hub registry username (default: $DOCKERHUB_USERNAME or 'lhotakj')

Help:
  -h, --help           Show this help message and exit

Examples:
  ./build.sh                                      # Build Linux agent binary and ZIP package
  ./build.sh --installer                          # Build the agent package and native installer
  ./build.sh --installer --skip-agent-build       # Rebuild only the native installer
  ./build.sh --bot                                # Build bot Docker container
  ./build.sh --all --clean                        # Clean and build agent package, native installer, and bot
  ./build.sh -v 1.2.0 --copy-to ~/bin/cmdiqo-agent
EOF
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

BUILD_AGENT=false
BUILD_INSTALLER=false
BUILD_BOT=false
PUSH_DOCKER=false
DEV_FLAG=false
CLEAN=false
SKIP_AGENT_BUILD=false
COPY_TO=""
VERSION_OVERRIDE=""
DOCKER_USERNAME="${DOCKERHUB_USERNAME:-lhotakj}"
PLATFORM="linux64"
DIST_DIR="$ROOT_DIR/dist"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform|-p|--plarform)
            PLATFORM="$(echo "$2" | tr '[:upper:]' '[:lower:]')"
            case "$PLATFORM" in
                linux64|linux) PLATFORM="linux64" ;;
                *) error "build.sh builds Linux binaries only. Use build.ps1 or the Windows GitHub Actions job for Windows PE executables."; exit 1 ;;
            esac
            shift 2
            ;;
        --platform=*|--plarform=*)
            PLATFORM="$(echo "${1#*=}" | tr '[:upper:]' '[:lower:]')"
            case "$PLATFORM" in
                linux64|linux) PLATFORM="linux64" ;;
                *) error "build.sh builds Linux binaries only. Use build.ps1 or the Windows GitHub Actions job for Windows PE executables."; exit 1 ;;
            esac
            shift
            ;;
        --agent|-a)
            BUILD_AGENT=true
            shift
            ;;
        --installer|-i)
            BUILD_AGENT=true
            BUILD_INSTALLER=true
            shift
            ;;
        --bot|-b|--docker)
            BUILD_BOT=true
            shift
            ;;
        --all)
            BUILD_AGENT=true
            BUILD_INSTALLER=true
            BUILD_BOT=true
            shift
            ;;
        --target)
            TARGET_ARG="$(echo "$2" | tr '[:upper:]' '[:lower:]')"
            case "$TARGET_ARG" in
                agent) BUILD_AGENT=true ;;
                installer) BUILD_AGENT=true; BUILD_INSTALLER=true ;;
                bot) BUILD_BOT=true ;;
                all) BUILD_AGENT=true; BUILD_INSTALLER=true; BUILD_BOT=true ;;
                *) error "Invalid target '$2'. Must be 'agent', 'installer', 'bot', or 'all'."; exit 1 ;;
            esac
            shift 2
            ;;
        --target=*)
            TARGET_ARG="$(echo "${1#*=}" | tr '[:upper:]' '[:lower:]')"
            case "$TARGET_ARG" in
                agent) BUILD_AGENT=true ;;
                installer) BUILD_AGENT=true; BUILD_INSTALLER=true ;;
                bot) BUILD_BOT=true ;;
                all) BUILD_AGENT=true; BUILD_INSTALLER=true; BUILD_BOT=true ;;
                *) error "Invalid target '${1#*=}'. Must be 'agent', 'installer', 'bot', or 'all'."; exit 1 ;;
            esac
            shift
            ;;
        --clean) CLEAN=true; shift ;;
        --skip-agent-build) SKIP_AGENT_BUILD=true; shift ;;
        --copy-to) COPY_TO="$2"; shift 2 ;;
        --copy-to=*) COPY_TO="${1#*=}"; shift ;;
        --push) PUSH_DOCKER=true; BUILD_BOT=true; shift ;;
        --docker-user) DOCKER_USERNAME="$2"; shift 2 ;;
        --docker-user=*) DOCKER_USERNAME="${1#*=}"; shift ;;
        --dev) DEV_FLAG=true; shift ;;
        --version|-v) VERSION_OVERRIDE="$2"; shift 2 ;;
        --version=*) VERSION_OVERRIDE="${1#*=}"; shift ;;
        -h|--help) show_help; exit 0 ;;
        *) error "Unknown argument '$1'. Run with --help for available options."; exit 1 ;;
    esac
done

if [[ "$BUILD_AGENT" = false && "$BUILD_BOT" = false && "$BUILD_INSTALLER" = false ]]; then
    BUILD_AGENT=true
fi

if [[ "$SKIP_AGENT_BUILD" = true ]]; then
    if [[ "$BUILD_INSTALLER" != true || "$BUILD_BOT" = true ]]; then
        error "--skip-agent-build is only valid with --installer."
        exit 2
    fi
    BUILD_AGENT=false
fi

if [[ "$CLEAN" = true ]]; then
    info "Cleaning previous build artifacts in build/ and dist/ ..."
    rm -rf "$ROOT_DIR/build" "$DIST_DIR/package"
    rm -f "$DIST_DIR/cmdiqo-agent"
    if [[ "$SKIP_AGENT_BUILD" != true ]]; then
        rm -f "$DIST_DIR/cmdiqo-agent-linux-x64.zip"
    fi
    rm -rf "$ROOT_DIR/installer.c/out/build/linux-x64"
fi

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
info "Build target version: ${VERSION} (Short: ${SHORT_VERSION})"
info "Platform selection: ${PLATFORM}"

DOCKER_RUNNING=false
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    DOCKER_RUNNING=true
fi

if [[ "$BUILD_AGENT" = true ]]; then
    mkdir -p "$DIST_DIR"
    if [[ "$DOCKER_RUNNING" = true ]]; then
        info "=== Building standalone cmdiqo-agent in Debian trixie container ==="
        BUILDER_IMAGE="cmdiqo-builder-linux:trixie"
        info "Refreshing Linux builder image ($BUILDER_IMAGE)..."
        docker build -t "$BUILDER_IMAGE" -f "$ROOT_DIR/docker/Dockerfile.builder-linux" "$ROOT_DIR"
        docker run --rm \
            --user "$(id -u):$(id -g)" \
            -v "$ROOT_DIR:/workspace" \
            -w /workspace \
            -e HOME=/workspace \
            "$BUILDER_IMAGE" \
            python3 development/build_agent.py --version "$VERSION"
    else
        warn "Docker is not running; building with host Python."
        warn "Building in Docker produces a more broadly compatible binary."
        AGENT_BUILD_ARGS=("$PYTHON_CMD" "development/build_agent.py" "--version" "$VERSION")
        if [[ -n "$COPY_TO" ]]; then
            AGENT_BUILD_ARGS+=(--copy-to "$COPY_TO")
        fi
        "${AGENT_BUILD_ARGS[@]}"
    fi

    if [[ ! -f "$DIST_DIR/cmdiqo-agent" ]]; then
        error "Requested Linux agent binary was not produced."
        exit 1
    fi
    if [[ ! -f "$DIST_DIR/cmdiqo-agent-linux-x64.zip" ]]; then
        error "Requested Linux agent ZIP package was not produced."
        exit 1
    fi
fi

build_native_linux_installer() {
    local installer_root="$ROOT_DIR/installer.c"
    local dockerfile="$installer_root/Dockerfile.linux"
    local image="cmdiqo-installer-builder:ubuntu-22.04"
    local payload="$DIST_DIR/cmdiqo-agent-linux-x64.zip"
    local payload_version
    local build_input_hash
    local image_build_input_hash=""

    if [[ "$DOCKER_RUNNING" != true ]]; then
        error "Docker is required to build the native Linux installer."
        error "Start Docker, then rerun ./build.sh --installer."
        exit 1
    fi
    if [[ ! -f "$payload" ]]; then
        error "Native installer packaging requires $payload."
        exit 1
    fi

    if ! payload_version="$("$PYTHON_CMD" -c 'import sys, zipfile; archive = zipfile.ZipFile(sys.argv[1]); name = "version.txt"; sys.exit("Payload is missing version.txt.") if name not in archive.namelist() else print(archive.read(name).decode("utf-8").strip())' "$payload")"; then
        error "The Linux agent payload is missing version.txt. Rebuild it with ./build.sh --agent."
        exit 1
    fi
    if [[ ! "$payload_version" =~ ^[0-9]+\.[0-9]+\.(devel\+[0-9A-F]+|[0-9A-Fa-f]{7,40})$ ]]; then
        error "The Linux agent payload has an invalid version: $payload_version"
        exit 1
    fi

    mkdir -p "$installer_root/.cache/ccache" "$installer_root/.cache/vcpkg/binary" \
        "$installer_root/.cache/vcpkg/downloads" "$installer_root/.cache/home"
    build_input_hash="$(cat "$dockerfile" "$installer_root/vcpkg.json" | sha256sum | awk '{print $1}')"
    if docker image inspect "$image" >/dev/null 2>&1; then
        image_build_input_hash="$(docker image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' "$image")"
    fi
    if [[ "$image_build_input_hash" != "$build_input_hash" ]]; then
        info "=== Building native installer Docker image ($image) ==="
        docker build --tag "$image" --file "$dockerfile" --build-arg "BUILDER_INPUT_SHA=$build_input_hash" "$installer_root"
    fi

    info "=== Building native Linux installer ==="
    local docker_args=(
        run --rm
        --workdir /workspace/installer.c
        --volume "$ROOT_DIR:/workspace"
        --env "CMDIQO_BUILD_VERSION=$payload_version"
        --env VCPKG_ROOT=/opt/vcpkg
        --env VCPKG_INSTALLED_DIR=/opt/cmdiqo-vcpkg-installed
        --env VCPKG_DEFAULT_BINARY_CACHE=/workspace/installer.c/.cache/vcpkg/binary
        --env VCPKG_DOWNLOADS=/workspace/installer.c/.cache/vcpkg/downloads
        --env CCACHE_DIR=/workspace/installer.c/.cache/ccache
        --env HOME=/workspace/installer.c/.cache/home
    )
    if command -v id >/dev/null 2>&1; then
        docker_args+=(--user "$(id -u):$(id -g)")
    fi
    docker "${docker_args[@]}" "$image" bash -c '
        set -euo pipefail
        cmake --fresh --preset linux-x64 \
            -DCMAKE_BUILD_TYPE=Release \
            "-DCMDIQO_BUILD_VERSION=${CMDIQO_BUILD_VERSION}" \
            "-DVCPKG_INSTALLED_DIR=${VCPKG_INSTALLED_DIR}" \
            -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
        cmake --build --preset build-linux-x64 --config Release
        ctest --preset test-linux-x64 -C Release
    '
    cp -f "$installer_root/out/build/linux-x64/cmdiqo-agent-setup" \
        "$DIST_DIR/cmdiqo-agent-linux-x64-setup"
    if [[ ! -x "$DIST_DIR/cmdiqo-agent-linux-x64-setup" ]]; then
        error "Native Linux installer binary was not produced."
        exit 1
    fi
    info "Native installer: $DIST_DIR/cmdiqo-agent-linux-x64-setup"
}

if [[ "$BUILD_INSTALLER" = true ]]; then
    build_native_linux_installer
fi

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

info ""
info "=== Distribution Agent Artifacts (dist/) ==="
if [[ "$BUILD_AGENT" = true || "$BUILD_INSTALLER" = true ]]; then
    artifacts=()
    if [[ "$BUILD_AGENT" = true ]]; then
        artifacts+=("$DIST_DIR/cmdiqo-agent")
    fi
    artifacts+=("$DIST_DIR/cmdiqo-agent-linux-x64.zip")
    if [[ "$BUILD_INSTALLER" = true ]]; then
        artifacts+=("$DIST_DIR/cmdiqo-agent-linux-x64-setup")
    fi
    for artifact in "${artifacts[@]}"; do
        if [[ -f "$artifact" ]]; then
            info "  • $(basename "$artifact"): $artifact ($(ls -lh "$artifact" | awk '{print $5}'))"
            if command -v sha256sum >/dev/null 2>&1; then
                info "    SHA256: $(sha256sum "$artifact" | awk '{print $1}')"
            fi
        fi
    done
fi
info "[+] Build complete successfully for version ${VERSION}!"
