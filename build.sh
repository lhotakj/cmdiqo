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
  --agent, -a          Build standalone cmdiqo-agent binary and installer (default)
  --installer, -i      Build Linux agent binary and Tkinter installer
  --bot, -b, --docker  Build Docker container image for cmdiqo-bot
  --all                Build agent binary, installer package, and bot container
  --target <string>    Explicit target: 'agent', 'installer', 'bot', or 'all'


Versioning:
  -v, --version <str>  Override version string (e.g. '1.2.0')
  --dev                Tag version as development build (<major>.<minor>.dev<HEX>)

Platform Selection:
  --platform, -p <str> Target platform: 'linux64' (default: linux64)

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
BUILD_INSTALLER=false
BUILD_BOT=false
PUSH_DOCKER=false
DEV_FLAG=false
CLEAN=false
SKIP_SIGN=false
COPY_TO=""
VERSION_OVERRIDE=""
DOCKER_USERNAME="${DOCKERHUB_USERNAME:-lhotakj}"
PLATFORM="linux64"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform|-p|--plarform)
            PLATFORM="$(echo "$2" | tr '[:upper:]' '[:lower:]')"
            case "$PLATFORM" in
                linux64|linux)  PLATFORM="linux64" ;;
                *)              error "build.sh builds Linux binaries only. Use build.ps1 or the Windows GitHub Actions job for Windows PE executables."; exit 1 ;;
            esac
            shift 2
            ;;
        --platform=*|--plarform=*)
            PLAT_VAL="${1#*=}"
            PLATFORM="$(echo "$PLAT_VAL" | tr '[:upper:]' '[:lower:]')"
            case "$PLATFORM" in
                linux64|linux)  PLATFORM="linux64" ;;
                *)              error "build.sh builds Linux binaries only. Use build.ps1 or the Windows GitHub Actions job for Windows PE executables."; exit 1 ;;
            esac
            shift
            ;;
        --agent|-a)
            BUILD_AGENT=true
            BUILD_INSTALLER=true
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
                agent)     BUILD_AGENT=true; BUILD_INSTALLER=true ;;
                installer) BUILD_AGENT=true; BUILD_INSTALLER=true ;;
                bot)       BUILD_BOT=true ;;
                all)       BUILD_AGENT=true; BUILD_INSTALLER=true; BUILD_BOT=true ;;
                *)         error "Invalid target '$2'. Must be 'agent', 'installer', 'bot', or 'all'."; exit 1 ;;
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

# Default to building agent and installer if neither specified
if [[ "$BUILD_AGENT" = false && "$BUILD_BOT" = false && "$BUILD_INSTALLER" = false ]]; then
    BUILD_AGENT=true
    BUILD_INSTALLER=true
fi

# Determine host before cleaning so --clean never leaves stale foreign artifacts
# that can be mistaken for outputs from the current build.
IS_HOST_WINDOWS=false
[[ "$(uname -s)" == *"MINGW"* || "$(uname -s)" == *"MSYS"* || "$(uname -s)" == *"CYGWIN"* ]] && IS_HOST_WINDOWS=true
# 0. Clean old build artifacts if requested
if [[ "$CLEAN" = true ]]; then
    info "Cleaning previous build artifacts in build/ and dist/ ..."
    rm -rf "$ROOT_DIR/build"
    rm -rf "$ROOT_DIR/dist/cmdiqo-agent-setup" "$ROOT_DIR/dist/_delete_me_on_linux" "$ROOT_DIR/dist/package" 2>/dev/null || true
    if [[ "$PLATFORM" == "linux64" ]]; then
        rm -rf "$ROOT_DIR/dist/cmdiqo-agent" "$ROOT_DIR/dist/cmdiqo-agent-setup" "$ROOT_DIR/dist/cmdiqo-agent-linux-x64.zip" 2>/dev/null || true
    elif [[ "$PLATFORM" == "win64" ]]; then
        rm -rf "$ROOT_DIR/dist/cmdiqo-agent.exe" "$ROOT_DIR/dist/cmdiqo-agent-setup.exe" "$ROOT_DIR/dist/cmdiqo-agent-windows-x64.zip" 2>/dev/null || true
    else
        rm -rf "$ROOT_DIR/dist/cmdiqo-agent" "$ROOT_DIR/dist/cmdiqo-agent-setup" \
            "$ROOT_DIR/dist/cmdiqo-agent-linux-x64.zip" "$ROOT_DIR/dist/cmdiqo-agent.exe" \
            "$ROOT_DIR/dist/cmdiqo-agent-setup.exe" "$ROOT_DIR/dist/cmdiqo-agent-windows-x64.zip" 2>/dev/null || true
    fi
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

info "Build target version: ${VERSION} (Short: ${SHORT_VERSION})"

# 1.5 build.sh produces native Linux artifacts only. Windows PE files are
# generated by build.ps1 or the Windows GitHub Actions runner.
BUILD_WIN64=false
BUILD_LINUX64=true

info "Platform selection: ${PLATFORM} (win64: ${BUILD_WIN64}, linux64: ${BUILD_LINUX64})"

# Check Docker availability for containerized glibc 2.31 Linux builds
DOCKER_RUNNING=false
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    DOCKER_RUNNING=true
fi

# 2. Build standalone agent binary
if [[ "$BUILD_AGENT" = true ]]; then
    DIST_DIR="$ROOT_DIR/dist"
    mkdir -p "$DIST_DIR"

    if [[ "$IS_HOST_WINDOWS" = false && "$BUILD_LINUX64" = true ]]; then
        if [[ "$DOCKER_RUNNING" = true ]]; then
            info "=== Building standalone cmdiqo-agent binary in glibc 2.31 container (openSUSE/SLES/Ubuntu compatible) ==="
            BUILDER_IMAGE="cmdiqo-builder-linux:bullseye"
            info "Refreshing Linux builder image ($BUILDER_IMAGE)..."
            docker build -t "$BUILDER_IMAGE" -f "$ROOT_DIR/docker/Dockerfile.builder-linux" "$ROOT_DIR"
            docker run --rm \
                --user "$(id -u):$(id -g)" \
                -v "$ROOT_DIR:/workspace" \
                -w /workspace \
                -e HOME=/tmp \
                "$BUILDER_IMAGE" \
                python3 development/build_agent.py --version "$VERSION"
        else
            warn "Docker is not running; building with host Python."
            warn "Note: To guarantee compatibility with openSUSE Leap 15 / SLES 15 (glibc 2.31), start Docker before building."
            AGENT_BUILD_ARGS=("$PYTHON_CMD" "development/build_agent.py")
            if [[ -n "$VERSION_OVERRIDE" ]]; then
                AGENT_BUILD_ARGS+=(--version "$VERSION_OVERRIDE")
            fi
            if [[ -n "$COPY_TO" ]]; then
                AGENT_BUILD_ARGS+=(--copy-to "$COPY_TO")
            fi
            "${AGENT_BUILD_ARGS[@]}"
        fi
    elif [[ "$IS_HOST_WINDOWS" = true && "$BUILD_WIN64" = true ]]; then
        info "=== Building standalone cmdiqo-agent Windows executable ==="
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
    fi

    BIN_NAME="cmdiqo-agent"
    [[ "$IS_HOST_WINDOWS" = true ]] && BIN_NAME="cmdiqo-agent.exe"

    DIST_BIN="$DIST_DIR/$BIN_NAME"
    if [[ -f "$DIST_BIN" ]]; then
        chmod +x "$DIST_BIN" 2>/dev/null || true
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

# 2b. Build standalone installer executable (single executable in dist/)
if [[ "$BUILD_INSTALLER" = true ]]; then
    DIST_DIR="$ROOT_DIR/dist"
    mkdir -p "$DIST_DIR"

    if [[ "$IS_HOST_WINDOWS" = false && "$BUILD_LINUX64" = true ]]; then
        PAYLOAD_ZIP="$DIST_DIR/cmdiqo-agent-linux-x64.zip"
        if [[ ! -f "$PAYLOAD_ZIP" ]]; then
            info "Payload zip not found. Running build_agent.py first..."
            "$PYTHON_CMD" "$ROOT_DIR/development/build_agent.py"
        fi

        # Remove any lingering directory named cmdiqo-agent-setup so PyInstaller outputs a single binary file
        if [[ -d "$DIST_DIR/cmdiqo-agent-setup" ]]; then
            rm -rf "$DIST_DIR/cmdiqo-agent-setup" 2>/dev/null || (command -v docker >/dev/null 2>&1 && docker run --rm -v "$ROOT_DIR:/workspace" alpine rm -rf /workspace/dist/cmdiqo-agent-setup 2>/dev/null) || true
        fi
        rm -rf "$DIST_DIR/_delete_me_on_linux" 2>/dev/null || true

        if [[ "$DOCKER_RUNNING" = true ]]; then
            info "=== Building standalone cmdiqo-agent-setup Linux executable in glibc 2.31 container ==="
            BUILDER_IMAGE="cmdiqo-builder-linux:bullseye"
            docker run --rm \
                --user "$(id -u):$(id -g)" \
                -v "$ROOT_DIR:/workspace" \
                -w /workspace \
                -e HOME=/tmp \
                "$BUILDER_IMAGE" \
                bash -c "rm -rf /workspace/dist/cmdiqo-agent-setup /workspace/dist/_delete_me_on_linux 2>/dev/null || true; \
                         python3 -m PyInstaller --onefile --clean --optimize 2 \
                             --exclude-module unittest --exclude-module pydoc --exclude-module test --exclude-module sqlite3 \
                             --name cmdiqo-agent-setup \
                             --paths /workspace --paths /workspace/installer \
                             --add-data '/workspace/dist/cmdiqo-agent-linux-x64.zip:.' \
                             --add-data '/workspace/version.txt:.' \
                             --hidden-import installer.backend --hidden-import installer.gui \
                             --strip /workspace/installer/gui.py && \
                         chmod +x /workspace/dist/cmdiqo-agent /workspace/dist/cmdiqo-agent-setup"
        else
            warn "Docker is not running; compiling installer with host Python."
            if ! "$PYTHON_CMD" -c "import tkinter; tkinter.Tcl()" >/dev/null 2>&1; then
                error "Tkinter is required to build the GUI installer. Install your distribution's Tk package or start Docker."
                exit 1
            fi
            rm -rf "$DIST_DIR/cmdiqo-agent-setup" 2>/dev/null || true
            # Use local temp dirs for PyInstaller work to avoid network-share localpycs failures
            LOCAL_WORK_DIR="${TMPDIR:-/tmp}/cmdiqo-pyi-setup"
            LOCAL_SPEC_DIR="${TMPDIR:-/tmp}/cmdiqo-pyi-setup-spec"
            mkdir -p "$LOCAL_WORK_DIR" "$LOCAL_SPEC_DIR"
            INSTALLER_PYARGS=(
                "$PYTHON_CMD" "-m" "PyInstaller"
                "--onefile"
                "--clean"
                "--optimize" "2"
                "--workpath" "$LOCAL_WORK_DIR"
                "--specpath" "$LOCAL_SPEC_DIR"
                "--distpath" "$DIST_DIR"
                "--exclude-module" "unittest"
                "--exclude-module" "pydoc"
                "--exclude-module" "test"
                "--exclude-module" "sqlite3"
                "--name" "cmdiqo-agent-setup"
                "--paths" "$ROOT_DIR"
                "--paths" "$ROOT_DIR/installer"
                "--add-data" "${PAYLOAD_ZIP}:."
                "--add-data" "${ROOT_DIR}/version.txt:."
                "--hidden-import" "installer.backend"
                "--hidden-import" "installer.gui"
                "--strip"
                "$ROOT_DIR/installer/gui.py"
            )
            "${INSTALLER_PYARGS[@]}"
            chmod +x "$DIST_DIR/cmdiqo-agent-setup" 2>/dev/null || true
        fi
        # Also apply chmod on the host side in case Docker's chmod didn't propagate through the volume
        chmod +x "$DIST_DIR/cmdiqo-agent" "$DIST_DIR/cmdiqo-agent-setup" 2>/dev/null || true

        DIST_SETUP_BIN="$DIST_DIR/cmdiqo-agent-setup"

    elif [[ "$IS_HOST_WINDOWS" = true && "$BUILD_WIN64" = true ]]; then
        info "=== Building standalone cmdiqo-agent-setup Windows executable ==="
        if ! "$PYTHON_CMD" -c "import tkinter; tkinter.Tcl()" >/dev/null 2>&1; then
            error "Tkinter is required to build the GUI installer. Install a standard Python distribution with Tcl/Tk."
            exit 1
        fi
        PAYLOAD_ZIP="$DIST_DIR/cmdiqo-agent-windows-x64.zip"
        if [[ ! -f "$PAYLOAD_ZIP" ]]; then
            info "Payload zip not found. Running build_agent.py first..."
            "$PYTHON_CMD" "$ROOT_DIR/development/build_agent.py"
        fi

        # Use local temp dirs for PyInstaller work to avoid network-share localpycs failures
        LOCAL_WORK_DIR="${TEMP:-/tmp}/cmdiqo-pyi-setup"
        LOCAL_SPEC_DIR="${TEMP:-/tmp}/cmdiqo-pyi-setup-spec"
        mkdir -p "$LOCAL_WORK_DIR" "$LOCAL_SPEC_DIR"
        INSTALLER_PYARGS=(
            "$PYTHON_CMD" "-m" "PyInstaller"
            "--onefile"
            "--clean"
            "--optimize" "2"
            "--workpath" "$LOCAL_WORK_DIR"
            "--specpath" "$LOCAL_SPEC_DIR"
            "--distpath" "$DIST_DIR"
            "--exclude-module" "unittest"
            "--exclude-module" "pydoc"
            "--exclude-module" "test"
            "--exclude-module" "sqlite3"
            "--name" "cmdiqo-agent-setup"
            "--paths" "$ROOT_DIR"
            "--paths" "$ROOT_DIR/installer"
            "--add-data" "${PAYLOAD_ZIP};."
            "--add-data" "${ROOT_DIR}/version.txt;."
            "--hidden-import" "installer.backend"
            "--hidden-import" "installer.gui"
            "--uac-admin"
            "--windowed"
        )
        if [[ -f "$ROOT_DIR/development/cmdiqo.ico" ]]; then
            INSTALLER_PYARGS+=("--icon" "$ROOT_DIR/development/cmdiqo.ico")
        fi
        if [[ -f "$ROOT_DIR/agent/version_info.txt" ]]; then
            INSTALLER_PYARGS+=("--version-file" "$ROOT_DIR/agent/version_info.txt")
        fi
        INSTALLER_PYARGS+=("$ROOT_DIR/installer/gui.py")

        "${INSTALLER_PYARGS[@]}"

        DIST_SETUP_BIN="$DIST_DIR/cmdiqo-agent-setup.exe"
        if [[ "$SKIP_SIGN" != true ]]; then
            info "Applying Authenticode digital signature to installer..."
            "$PYTHON_CMD" -c "from development.build_agent import sign_windows_binary; from pathlib import Path; sign_windows_binary(Path(r'$DIST_SETUP_BIN'))" || true
        fi
    fi

    # Display verification for all installer executables in dist/
    info ""
    info "=== Installer Artifacts Verification in dist/ ==="
    for inst in "$DIST_DIR/cmdiqo-agent-setup" "$DIST_DIR/cmdiqo-agent-setup.exe"; do
        if [[ -f "$inst" ]]; then
            chmod +x "$inst" 2>/dev/null || true
            info "  + Found: $inst ($(ls -lh "$inst" | awk '{print $5}'))"
            if command -v sha256sum >/dev/null 2>&1; then
                info "    SHA256: $(sha256sum "$inst" | awk '{print $1}')"
            fi
        fi
    done
    if [[ "$BUILD_WIN64" = true && ! -f "$DIST_DIR/cmdiqo-agent-setup.exe" ]]; then
        info "  * Note: Windows installer (cmdiqo-agent-setup.exe) can be built on Windows (.\\build.ps1 -Installer) or with Wine on Linux."
    fi
    if [[ "$BUILD_LINUX64" = true && ! -f "$DIST_DIR/cmdiqo-agent-setup" ]]; then
        info "  * Note: Linux installer (cmdiqo-agent-setup) can be built on Linux (./build.sh --installer) or on Windows with Docker Desktop."
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

info ""
info "=== Distribution Binary Artifacts (dist/) ==="
if [[ "$BUILD_LINUX64" = true ]]; then
    info "Linux (x86_64):"
    [[ -f "$DIST_DIR/cmdiqo-agent" ]] && info "  • Application Binary:     $DIST_DIR/cmdiqo-agent ($(ls -lh "$DIST_DIR/cmdiqo-agent" | awk '{print $5}'))" || warn "  • Application Binary:     Missing"
    [[ -f "$DIST_DIR/cmdiqo-agent-setup" ]] && info "  • Installer Binary:       $DIST_DIR/cmdiqo-agent-setup ($(ls -lh "$DIST_DIR/cmdiqo-agent-setup" | awk '{print $5}'))" || warn "  • Installer Binary:       Missing"
fi
if [[ "$BUILD_WIN64" = true ]]; then
    info "Windows (x64):"
    [[ -f "$DIST_DIR/cmdiqo-agent.exe" ]] && info "  • Application Binary:     $DIST_DIR/cmdiqo-agent.exe ($(ls -lh "$DIST_DIR/cmdiqo-agent.exe" | awk '{print $5}'))" || warn "  • Application Binary:     Missing"
    [[ -f "$DIST_DIR/cmdiqo-agent-setup.exe" ]] && info "  • Installer Binary:       $DIST_DIR/cmdiqo-agent-setup.exe ($(ls -lh "$DIST_DIR/cmdiqo-agent-setup.exe" | awk '{print $5}'))" || warn "  • Installer Binary:       Missing"
else
    info "Windows (x64): not requested; build on Windows or use the Windows CI job."
fi

if [[ "$BUILD_AGENT" = true && "$BUILD_LINUX64" = true && ! -f "$DIST_DIR/cmdiqo-agent" ]]; then
    error "Requested Linux agent binary was not produced."
    exit 1
fi
if [[ "$BUILD_INSTALLER" = true && "$BUILD_LINUX64" = true && ! -f "$DIST_DIR/cmdiqo-agent-setup" ]]; then
    error "Requested Linux installer was not produced."
    exit 1
fi
info "[+] Build complete successfully for version ${VERSION}!"
