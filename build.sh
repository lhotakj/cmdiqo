#!/usr/bin/env bash
# build.sh — Linux agent, Windows installer, and bot-container build script for Cmdiqo.

set -euo pipefail

info() {
    printf "\033[0;32m[INFO]\033[0m %s\n" "$1"
}

error() {
    printf "\033[0;31m[ERROR]\033[0m %s\n" "$1" >&2
}

warn() {
    printf "\033[0;33m[WARN]\033[0m %s\n" "$1"
}

show_help() {
    cat << 'EOF'
Usage: ./build.sh [options]

Build Targets:
  --agent, -a          Build the standalone platform agent binary and ZIP package (default)
  --installer, -i      Build the agent binary, ZIP package, and native setup executable
  --bot, -b, --docker  Build Docker container image for cmdiqo-bot
  --all                Build the agent package, native installer, and bot container
  --build-all          Build all components for ALL platforms (linux64, linux-arm64, linux-armv6, win64)
  --target <string>    Explicit target: 'agent', 'installer', 'bot', or 'all'

Versioning:
  -v, --version <str>  Override version string (e.g. '1.2.0')
  --build-id <id>      Override patch/build number (defaults to $GITHUB_RUN_NUMBER in CI)
  --dev                Tag version as development build (<major>.<minor>.devel+<HEX>)

Platform Selection:
  --platform, -p <str> Target platform: 'linux64', 'linux-arm64', 'linux-armv6', 'win64', or 'all'
                       (default: win64 on Windows Bash, otherwise linux64)

Compilation & Packaging:
  --clean              Clean build outputs, agent artifacts, and native installer artifacts
  --skip-agent-build   With --installer, reuse existing agent ZIP package
                       and build only the native C++ setup executable
  --copy-to <path>     Copy the compiled binary to destination path upon completion
                       (agent builds for a single platform only)
  --skip-sign         Skip Authenticode signing when building with Windows Python
  --rebuild-builder    Force rebuilding the C++ builder Docker image locally
  --no-rebuild-builder Do not build or rebuild the C++ builder image; reuse existing or pulled image

Docker Options:
  --push               Build and push multi-arch Docker images to registry (linux/amd64,linux/arm64)
  --push-all           Rebuild builder image, compile all platforms, and push all images
  --docker-user <usr>  Docker Hub namespace (default: $DOCKERHUB_USER,
                       $DOCKERHUB_USERNAME, or 'lhotakj')

Help:
  -h, --help           Show this help message and exit

Examples:
  ./build.sh                                      # Build Linux agent binary and ZIP package
  ./build.sh --platform linux-arm64               # Build Linux ARM64 agent binary and ZIP package
  ./build.sh --platform linux-armv6               # Build Linux ARMv6 (RPi Zero W / Pi 1) agent binary and ZIP
  ./build.sh --installer                          # Build Linux agent package and native installer
  ./build.sh --installer --platform linux-arm64   # Build Linux ARM64 agent package and native setup
  ./build.sh --installer --platform linux-armv6   # Build Linux ARMv6 agent package and native setup
  ./build.sh --build-all                          # Build all components for ALL platforms
  ./build.sh --push-all                           # Build and push everything (builder + all platforms + bot)
  ./build.sh --installer --platform win64 --skip-agent-build
                                                   # Cross-build setup using an existing Windows ZIP
  ./build.sh --installer --platform all --skip-agent-build
                                                   # Rebuild all setups using existing platform ZIPs
  ./build.sh --installer --skip-agent-build       # Rebuild only the native installer
  ./build.sh --bot                                # Build bot Docker container
  ./build.sh --all --clean                        # Clean and build agent package, native installer, and bot
  ./build.sh -v 1.2.0 --copy-to ~/bin/cmdiqo-agent
EOF
}

require_option_value() {
    if [[ $# -lt 2 || -z "$2" || "$2" == -* ]]; then
        error "Option $1 requires a value."
        exit 2
    fi
}

normalize_platform() {
    case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
        linux64|linux) printf '%s\n' linux64 ;;
        linux-arm64|arm64-linux|arm64|aarch64) printf '%s\n' linux-arm64 ;;
        linux-armv6|linux-armv6l|armv6l|armv6|armhf|rpi-zero|rpi1) printf '%s\n' linux-armv6 ;;
        win64|windows|win) printf '%s\n' win64 ;;
        all) printf '%s\n' all ;;
        *) error "Invalid platform '$1'. Expected 'linux64', 'linux-arm64', 'linux-armv6', 'win64', or 'all'."; return 2 ;;
    esac
}

# Git Bash must not rewrite container paths such as /workspace or /tmp.
docker_cli() {
    MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' docker "$@"
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
SKIP_SIGN=false
REBUILD_BUILDER=false
NO_REBUILD_BUILDER="${NO_REBUILD_BUILDER:-false}"
COPY_TO=""
VERSION_OVERRIDE=""
BUILD_ID="${BUILD_ID:-${GITHUB_RUN_NUMBER:-}}"
export BUILD_ID
DOCKER_USERNAME="${DOCKERHUB_USER:-${DOCKERHUB_USERNAME:-lhotakj}}"
PLATFORM="linux64"
HOST_SYSTEM="$(uname -s)"
case "$HOST_SYSTEM" in
    MINGW*|MSYS*|CYGWIN*) PLATFORM="win64" ;;
esac
DIST_DIR="$ROOT_DIR/dist"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform|-p|--plarform)
            require_option_value "$@"
            PLATFORM="$(normalize_platform "$2")"
            shift 2
            ;;
        --platform=*|--plarform=*)
            PLATFORM="$(normalize_platform "${1#*=}")"
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
        --build-all)
            BUILD_AGENT=true
            BUILD_INSTALLER=true
            BUILD_BOT=true
            PLATFORM="all"
            shift
            ;;
        --target)
            require_option_value "$@"
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
        --rebuild-builder|--rebuild-image) REBUILD_BUILDER=true; NO_REBUILD_BUILDER=false; shift ;;
        --no-rebuild-builder|--no-build-builder|--skip-builder-build) NO_REBUILD_BUILDER=true; REBUILD_BUILDER=false; shift ;;
        --copy-to) require_option_value "$@"; COPY_TO="$2"; shift 2 ;;
        --copy-to=*) require_option_value --copy-to "${1#*=}"; COPY_TO="${1#*=}"; shift ;;
        --skip-sign) SKIP_SIGN=true; shift ;;
        --push) PUSH_DOCKER=true; BUILD_BOT=true; shift ;;
        --push-all)
            BUILD_AGENT=true
            BUILD_INSTALLER=true
            BUILD_BOT=true
            PLATFORM="all"
            REBUILD_BUILDER=true
            PUSH_DOCKER=true
            shift
            ;;
        --docker-user) require_option_value "$@"; DOCKER_USERNAME="$2"; shift 2 ;;
        --docker-user=*) require_option_value --docker-user "${1#*=}"; DOCKER_USERNAME="${1#*=}"; shift ;;
        --dev) DEV_FLAG=true; shift ;;
        --version|-v) require_option_value "$@"; VERSION_OVERRIDE="$2"; shift 2 ;;
        --version=*) require_option_value --version "${1#*=}"; VERSION_OVERRIDE="${1#*=}"; shift ;;
        --build-id) require_option_value "$@"; BUILD_ID="$2"; export BUILD_ID; shift 2 ;;
        --build-id=*) require_option_value --build-id "${1#*=}"; BUILD_ID="${1#*=}"; export BUILD_ID; shift ;;
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

BUILD_LINUX=false
BUILD_LINUX_ARM64=false
BUILD_LINUX_ARMV6=false
BUILD_WIN=false
if [[ "$PLATFORM" == "linux64" || "$PLATFORM" == "all" ]]; then
    BUILD_LINUX=true
fi
if [[ "$PLATFORM" == "linux-arm64" || "$PLATFORM" == "all" ]]; then
    BUILD_LINUX_ARM64=true
fi
if [[ "$PLATFORM" == "linux-armv6" || "$PLATFORM" == "all" ]]; then
    BUILD_LINUX_ARMV6=true
fi
if [[ "$PLATFORM" == "win64" || "$PLATFORM" == "all" ]]; then
    BUILD_WIN=true
fi

PYTHON_CMD="python3"
case "$HOST_SYSTEM" in
    MINGW*|MSYS*|CYGWIN*)
        PYTHON_CMD="python"
        if ! command -v python >/dev/null 2>&1; then
            PYTHON_CMD="python3"
        fi
        ;;
    *)
        if ! command -v python3 >/dev/null 2>&1; then
            PYTHON_CMD="python"
        fi
        ;;
esac
if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    error "Python 3 is required. Activate the project virtual environment first."
    exit 1
fi
PYTHON_PLATFORM="$("$PYTHON_CMD" -c 'import platform, struct; print(platform.system().lower() + " " + platform.machine().lower() + " " + str(struct.calcsize("P") * 8))' | tr -d '\r\n')"

DOCKER_RUNNING=false
if command -v docker >/dev/null 2>&1 && docker_cli info >/dev/null 2>&1; then
    DOCKER_RUNNING=true
fi
if [[ "$BUILD_AGENT" = true || "$BUILD_INSTALLER" = true || "$BUILD_BOT" = true ]]; then
    if [[ "$DOCKER_RUNNING" != true ]]; then
        error "A running Linux-container Docker daemon is required for agent, installer, and bot builds."
        exit 1
    fi
fi
if [[ -n "$COPY_TO" && ( "$BUILD_AGENT" != true || "$PLATFORM" == all ) ]]; then
    error "--copy-to requires an agent build for a single platform."
    exit 2
fi

DOCKER_VOLUME_ROOT="$ROOT_DIR"
if command -v cygpath >/dev/null 2>&1; then
    DOCKER_VOLUME_ROOT="$(cygpath -m "$ROOT_DIR")"
    if [[ -n "$COPY_TO" ]]; then
        COPY_TO="$(cygpath -u "$COPY_TO")"
    fi
fi

if [[ "$CLEAN" = true ]]; then
    info "Cleaning selected build artifacts ..."
    if [[ "$BUILD_AGENT" = true ]]; then
        rm -rf "$ROOT_DIR/build" "$DIST_DIR/package"
    fi
    if [[ "$BUILD_LINUX" = true && ( "$BUILD_AGENT" = true || "$BUILD_INSTALLER" = true ) ]]; then
        if [[ "$BUILD_AGENT" = true ]]; then
            rm -f "$DIST_DIR/cmdiqo-agent" "$DIST_DIR/cmdiqo-agent-linux-x64.zip"
            rm -rf "$ROOT_DIR/agent.c/out/build/linux-x64"
        fi
        if [[ "$BUILD_INSTALLER" = true ]]; then
            rm -f "$DIST_DIR/cmdiqo-agent-linux-x64-setup"
            rm -rf "$ROOT_DIR/installer.c/out/build/linux-x64"
        fi
    fi
    if [[ "$BUILD_LINUX_ARM64" = true && ( "$BUILD_AGENT" = true || "$BUILD_INSTALLER" = true ) ]]; then
        if [[ "$BUILD_AGENT" = true ]]; then
            rm -f "$DIST_DIR/cmdiqo-agent-linux-arm64" "$DIST_DIR/cmdiqo-agent-linux-arm64.zip"
            rm -rf "$ROOT_DIR/agent.c/out/build/linux-arm64"
        fi
        if [[ "$BUILD_INSTALLER" = true ]]; then
            rm -f "$DIST_DIR/cmdiqo-agent-linux-arm64-setup"
            rm -rf "$ROOT_DIR/installer.c/out/build/linux-arm64"
        fi
    fi
    if [[ "$BUILD_LINUX_ARMV6" = true && ( "$BUILD_AGENT" = true || "$BUILD_INSTALLER" = true ) ]]; then
        if [[ "$BUILD_AGENT" = true ]]; then
            rm -f "$DIST_DIR/cmdiqo-agent-linux-armv6" "$DIST_DIR/cmdiqo-agent-linux-armv6.zip"
            rm -rf "$ROOT_DIR/agent.c/out/build/linux-armv6"
        fi
        if [[ "$BUILD_INSTALLER" = true ]]; then
            rm -f "$DIST_DIR/cmdiqo-agent-linux-armv6-setup"
            rm -rf "$ROOT_DIR/installer.c/out/build/linux-armv6"
        fi
    fi
    if [[ "$BUILD_WIN" = true && ( "$BUILD_AGENT" = true || "$BUILD_INSTALLER" = true ) ]]; then
        if [[ "$BUILD_AGENT" = true ]]; then
            rm -f "$DIST_DIR/cmdiqo-agent.exe" "$DIST_DIR/cmdiqo-agent-windows-x64.zip"
            rm -rf "$ROOT_DIR/agent.c/out/build/windows-x64-mingw" "$ROOT_DIR/agent.c/out/build/windows-x64"
        fi
        if [[ "$BUILD_INSTALLER" = true ]]; then
            rm -f "$DIST_DIR/cmdiqo-agent-windows-x64-setup.exe"
            rm -rf "$ROOT_DIR/installer.c/out/build/windows-x64-mingw" "$ROOT_DIR/installer.c/out/build/windows-x64"
        fi
    fi
fi

VERSION_CMD=("$PYTHON_CMD" "development/update_version.py" "--print-version")
if [[ -n "$VERSION_OVERRIDE" ]]; then
    VERSION_CMD+=("--version" "$VERSION_OVERRIDE")
elif [[ "$DEV_FLAG" = true ]]; then
    VERSION_CMD+=("--dev")
fi
VERSION=$("${VERSION_CMD[@]}" | tr -d '\r\n')
if [[ -z "$VERSION" ]]; then
    error "The version updater did not produce a build version."
    exit 1
fi
SHORT_VERSION=$(printf '%s\n' "$VERSION" | cut -d. -f1,2)
TIMESTAMP=$(date +%Y%m%d%H%M%S)
info "Build target version: ${VERSION} (Short: ${SHORT_VERSION})"
info "Platform selection: ${PLATFORM} (linux64: ${BUILD_LINUX}, linux-arm64: ${BUILD_LINUX_ARM64}, linux-armv6: ${BUILD_LINUX_ARMV6}, win64: ${BUILD_WIN})"

ensure_installer_builder_image() {
    local target_stage="$1"
    local local_image="$2"
    local remote_image="$3"
    local installer_root="$4"

    local build_input_hash
    build_input_hash="$("$PYTHON_CMD" -c '
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
triplets = root / "triplets"
if not triplets.is_dir():
    sys.exit("Installer overlay triplets are missing.")
files = [root / "Dockerfile", root / "vcpkg.json", *sorted(path for path in triplets.rglob("*") if path.is_file())]
digest = hashlib.sha256()
for path in files:
    digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
print(digest.hexdigest())
' "$installer_root")"

    # 1. Check if local image exists
    if [[ "$REBUILD_BUILDER" != true ]] && docker_cli image inspect "$local_image" >/dev/null 2>&1; then
        local current_hash
        current_hash="$(docker_cli image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' "$local_image")"
        if [[ "$current_hash" == "$build_input_hash" ]]; then
            info "Pre-built builder image ($local_image) matches source checksum."
            return 0
        fi
        warn "Found existing local builder image ($local_image) with checksum mismatch; reusing without rebuilding. (Use --rebuild-builder to refresh)"
        return 0
    fi

    # 2. Check if remote-tagged image exists locally before pulling
    if [[ "$REBUILD_BUILDER" != true ]] && docker_cli image inspect "$remote_image" >/dev/null 2>&1; then
        local remote_hash
        remote_hash="$(docker_cli image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' "$remote_image")"
        if [[ "$remote_hash" == "$build_input_hash" ]]; then
            docker_cli tag "$remote_image" "$local_image"
            return 0
        fi
        warn "Found existing remote builder image ($remote_image) with checksum mismatch; tagging as $local_image and reusing. (Use --rebuild-builder to refresh)"
        docker_cli tag "$remote_image" "$local_image"
        return 0
    fi

    # 3. Try pulling pre-built image from Docker Hub
    if [[ "$REBUILD_BUILDER" != true ]]; then
        info "Attempting to pull pre-built builder image ($remote_image) from Docker Hub..."
        if docker_cli pull "$remote_image"; then
            local pulled_hash
            pulled_hash="$(docker_cli image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' "$remote_image")"
            if [[ "$pulled_hash" == "$build_input_hash" ]]; then
                info "Pre-built builder image ($remote_image) is ready."
                docker_cli tag "$remote_image" "$local_image"
                return 0
            fi
            if [[ "$NO_REBUILD_BUILDER" = true ]]; then
                warn "Pulled builder image ($remote_image) checksum differs, but builder rebuild is disabled; reusing pulled image."
                docker_cli tag "$remote_image" "$local_image"
                return 0
            fi
            warn "Pulled builder image does not match the Dockerfile, manifest, and triplets; rebuilding locally."
        else
            if [[ "$NO_REBUILD_BUILDER" = true ]]; then
                error "Pre-built builder image ($remote_image) could not be pulled and rebuilding is disabled (--no-rebuild-builder)."
                return 1
            fi
            warn "Pre-built builder image is unavailable; rebuilding locally."
        fi
    fi

    # 4. Fallback: Build image locally
    if [[ "$NO_REBUILD_BUILDER" = true ]]; then
        error "Builder image ($local_image) is missing or mismatched and rebuilding is disabled (--no-rebuild-builder)."
        return 1
    fi
    info "=== Building native installer Docker image locally ($local_image, target: $target_stage) ==="
    docker_cli buildx build --load --platform linux/amd64 \
        --tag "$local_image" \
        --tag "$remote_image" \
        --file "$DOCKER_VOLUME_ROOT/installer.c/Dockerfile" \
        --target "$target_stage" \
        --build-arg "BUILDER_INPUT_SHA=$build_input_hash" \
        "$DOCKER_VOLUME_ROOT/installer.c"

    if [[ "$PUSH_DOCKER" = true ]]; then
        info "Pushing builder image ($remote_image) to Docker Hub..."
        docker_cli push "$remote_image"
    fi
}

package_agent_zip() {
    local platform="$1"
    local version="$2"
    local binary_path="$3"

    "$PYTHON_CMD" -c '
import os, pathlib, stat, sys, zipfile, shutil

platform = sys.argv[1]
version = sys.argv[2]
binary_path = pathlib.Path(sys.argv[3]).resolve()
root_dir = pathlib.Path(sys.argv[4]).resolve()
dist_dir = binary_path.parent
agent_c_dir = root_dir / "agent.c"

pkg_dir = dist_dir / "package"
if pkg_dir.exists():
    if pkg_dir.is_dir():
        shutil.rmtree(pkg_dir)
    else:
        pkg_dir.unlink()
pkg_dir.mkdir(parents=True, exist_ok=True)

(dist_dir / "build-version.txt").write_text(version, encoding="utf-8")

if platform == "win64":
    zip_name = "cmdiqo-agent-windows-x64.zip"
    files_to_pack = [
        (binary_path, "cmdiqo-agent.exe"),
        (agent_c_dir / "install-service.ps1", "install.ps1"),
        (agent_c_dir / "uninstall-service.ps1", "uninstall.ps1"),
        (agent_c_dir / "agent.env.example", "agent.env.example"),
        (agent_c_dir / "agent.json.example", "agent.json.example"),
        (dist_dir / "build-version.txt", "version.txt"),
    ]
    cer_path = dist_dir / "cmdiqo-agent.cer"
    if cer_path.is_file():
        files_to_pack.append((cer_path, "cmdiqo-agent.cer"))
elif platform == "linux-arm64":
    zip_name = "cmdiqo-agent-linux-arm64.zip"
    files_to_pack = [
        (binary_path, "cmdiqo-agent"),
        (agent_c_dir / "install-service.sh", "install.sh"),
        (agent_c_dir / "uninstall-service.sh", "uninstall.sh"),
        (agent_c_dir / "cmdiqo-agent.service", "cmdiqo-agent.service"),
        (agent_c_dir / "agent.env.example", "agent.env.example"),
        (agent_c_dir / "agent.json.example", "agent.json.example"),
        (dist_dir / "build-version.txt", "version.txt"),
    ]
elif platform == "linux-armv6":
    zip_name = "cmdiqo-agent-linux-armv6.zip"
    files_to_pack = [
        (binary_path, "cmdiqo-agent"),
        (agent_c_dir / "install-service.sh", "install.sh"),
        (agent_c_dir / "uninstall-service.sh", "uninstall.sh"),
        (agent_c_dir / "cmdiqo-agent.service", "cmdiqo-agent.service"),
        (agent_c_dir / "agent.env.example", "agent.env.example"),
        (agent_c_dir / "agent.json.example", "agent.json.example"),
        (dist_dir / "build-version.txt", "version.txt"),
    ]
else:
    zip_name = "cmdiqo-agent-linux-x64.zip"
    files_to_pack = [
        (binary_path, "cmdiqo-agent"),
        (agent_c_dir / "install-service.sh", "install.sh"),
        (agent_c_dir / "uninstall-service.sh", "uninstall.sh"),
        (agent_c_dir / "cmdiqo-agent.service", "cmdiqo-agent.service"),
        (agent_c_dir / "agent.env.example", "agent.env.example"),
        (agent_c_dir / "agent.json.example", "agent.json.example"),
        (dist_dir / "build-version.txt", "version.txt"),
    ]

for src, arc in files_to_pack:
    if not src.is_file():
        sys.exit(f"Required package file is missing: {src}")
    staged = pkg_dir / arc
    shutil.copy2(src, staged)
    if platform != "win64" and arc in {"cmdiqo-agent", "install.sh", "uninstall.sh"}:
        try:
            os.chmod(staged, 0o755)
        except OSError:
            pass

zip_path = dist_dir / zip_name
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for src, arc in files_to_pack:
        zip_info = zipfile.ZipInfo.from_file(src, arcname=arc)
        if platform != "win64" and arc in {"cmdiqo-agent", "install.sh", "uninstall.sh"}:
            zip_info.create_system = 3
            zip_info.external_attr = (stat.S_IFREG | 0o755) << 16
        zf.writestr(zip_info, src.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)

print(f"[INFO] Agent ZIP package created: {zip_path}")
' "$platform" "$version" "$binary_path" "$ROOT_DIR"
}

build_native_agent() {
    local platform="$1"
    local version="$2"
    local preset="linux-x64"
    local artifact="cmdiqo-agent"
    local destination="cmdiqo-agent"
    local installed_dir_var="VCPKG_INSTALLED_DIR_LINUX"
    if [[ "$platform" == win64 ]]; then
        preset="windows-x64-mingw"
        artifact="cmdiqo-agent.exe"
        destination="cmdiqo-agent.exe"
        installed_dir_var="VCPKG_INSTALLED_DIR_WINDOWS"
    elif [[ "$platform" == linux-arm64 ]]; then
        preset="linux-arm64"
        artifact="cmdiqo-agent"
        destination="cmdiqo-agent-linux-arm64"
        installed_dir_var="VCPKG_INSTALLED_DIR_LINUX_ARM64"
    elif [[ "$platform" == linux-armv6 ]]; then
        preset="linux-armv6"
        artifact="cmdiqo-agent"
        destination="cmdiqo-agent-linux-armv6"
        installed_dir_var="VCPKG_INSTALLED_DIR_LINUX_ARMV6"
    fi
    info "=== Building native $platform agent (Release) ==="
    rm -f "$DIST_DIR/$destination"
    local docker_args=(
        run --rm --platform linux/amd64
        --workdir /workspace/agent.c
        --volume "$DOCKER_VOLUME_ROOT:/workspace"
        --env HOME=/tmp
        --env "PRESET=$preset"
        --env "INSTALLED_DIR_VAR=$installed_dir_var"
        --env "CMDIQO_BUILD_VERSION=$version"
    )
    if [[ "$PYTHON_PLATFORM" != "windows "* ]]; then
        docker_args+=(--user "$(id -u):$(id -g)")
    fi
    docker_cli "${docker_args[@]}" cmdiqo-installer-builder:all bash -c '
        set -euo pipefail
        export TMPDIR="/tmp/cmdiqo-tmp-$$"
        export WINEPREFIX="/tmp/cmdiqo-wine-$$"
        if [[ "$PRESET" == "linux-armv6" ]]; then
            export QEMU_CPU="arm1176"
        else
            export QEMU_LD_PREFIX="/usr/aarch64-linux-gnu"
        fi
        install --directory --mode=700 "$TMPDIR" "$WINEPREFIX"
        if [[ -z "${!INSTALLED_DIR_VAR+x}" ]]; then
            printf "[ERROR] Builder image is missing environment variable %s for %s.\n" "$INSTALLED_DIR_VAR" "$PRESET" >&2
            printf "[ERROR] The local builder image is outdated and lacks %s support. Rebuild it with --rebuild-builder.\n" "$PRESET" >&2
            exit 1
        fi
        installed_dir="${!INSTALLED_DIR_VAR}"
        if [[ ! -d "$installed_dir" ]]; then
            printf "[ERROR] Pre-built dependency root is missing: %s\n" "$installed_dir" >&2
            printf "[ERROR] Rebuild the builder image with --rebuild-builder.\n" >&2
            exit 1
        fi
        cmake --fresh --preset "$PRESET" \
            "-DVCPKG_INSTALLED_DIR=$installed_dir" \
            -DVCPKG_MANIFEST_INSTALL=OFF \
            "-DCMDIQO_BUILD_VERSION=${CMDIQO_BUILD_VERSION}"
        cmake --build --preset "build-$PRESET" --config Release
        ctest --preset "test-$PRESET" -C Release --output-on-failure
    '
    if [[ ! -f "$ROOT_DIR/agent.c/out/build/$preset/$artifact" ]]; then
        error "Native $platform agent binary was not produced."
        exit 1
    fi
    cp -f "$ROOT_DIR/agent.c/out/build/$preset/$artifact" "$DIST_DIR/$destination"
    if [[ "$platform" == linux64 || "$platform" == linux-arm64 || "$platform" == linux-armv6 ]]; then
        chmod +x "$DIST_DIR/$destination"
    fi
    info "Native agent: $DIST_DIR/$destination"

    package_agent_zip "$platform" "$version" "$DIST_DIR/$destination"
}

get_payload_version() {
    local payload="$1"
    local payload_version=""
    if [[ -f "$payload" && "$payload" == *.zip ]]; then
        if ! payload_version="$("$PYTHON_CMD" -c '
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    if "version.txt" not in archive.namelist():
        sys.exit("Payload is missing version.txt.")
    print(archive.read("version.txt").decode("utf-8-sig").strip())
' "$payload" 2>/dev/null | tr -d '\r')"; then
            error "Cannot read version.txt from $payload. Rebuild the platform agent package."
            return 1
        fi
    elif [[ -f "$DIST_DIR/build-version.txt" ]]; then
        payload_version="$(cat "$DIST_DIR/build-version.txt" | tr -d '\r\n ')"
    elif [[ -n "${VERSION:-}" ]]; then
        payload_version="$VERSION"
    elif [[ -f "$ROOT_DIR/version.txt" ]]; then
        payload_version="$(cat "$ROOT_DIR/version.txt" | tr -d '\r\n ')"
    else
        error "Native installer packaging requires $payload."
        return 1
    fi
    if [[ ! "$payload_version" =~ ^[0-9]+\.[0-9]+\.(devel\+[0-9A-Fa-f]+|[0-9A-Fa-f]{7,40}|[0-9]+)$ ]]; then
        error "The agent payload has an invalid version: $payload_version"
        return 1
    fi
    printf '%s\n' "$payload_version"
}

build_native_installer() {
    local platform="$1"
    local payload_version="$2"
    local preset="linux-x64"
    local artifact="cmdiqo-agent-setup"
    local destination="cmdiqo-agent-linux-x64-setup"
    local installed_dir_var="VCPKG_INSTALLED_DIR_LINUX"
    local agent_binary="cmdiqo-agent"
    if [[ "$platform" == win64 ]]; then
        preset="windows-x64-mingw"
        artifact="cmdiqo-agent-setup.exe"
        destination="cmdiqo-agent-windows-x64-setup.exe"
        installed_dir_var="VCPKG_INSTALLED_DIR_WINDOWS"
        agent_binary="cmdiqo-agent.exe"
    elif [[ "$platform" == linux-arm64 ]]; then
        preset="linux-arm64"
        artifact="cmdiqo-agent-setup"
        destination="cmdiqo-agent-linux-arm64-setup"
        installed_dir_var="VCPKG_INSTALLED_DIR_LINUX_ARM64"
        agent_binary="cmdiqo-agent-linux-arm64"
    elif [[ "$platform" == linux-armv6 ]]; then
        preset="linux-armv6"
        artifact="cmdiqo-agent-setup"
        destination="cmdiqo-agent-linux-armv6-setup"
        installed_dir_var="VCPKG_INSTALLED_DIR_LINUX_ARMV6"
        agent_binary="cmdiqo-agent-linux-armv6"
    fi
    info "=== Building native $platform installer (Release) ==="
    rm -f "$DIST_DIR/$destination"
    local docker_args=(
        run --rm --platform linux/amd64
        --workdir /workspace/installer.c
        --volume "$DOCKER_VOLUME_ROOT:/workspace"
        --env HOME=/tmp
        --env "PRESET=$preset"
        --env "INSTALLED_DIR_VAR=$installed_dir_var"
        --env "CMDIQO_BUILD_VERSION=$payload_version"
        --env "AGENT_BINARY=$agent_binary"
    )
    if [[ "$PYTHON_PLATFORM" != "windows "* ]]; then
        docker_args+=(--user "$(id -u):$(id -g)")
    fi
    docker_cli "${docker_args[@]}" cmdiqo-installer-builder:all bash -c '
        set -euo pipefail
        export TMPDIR="/tmp/cmdiqo-tmp-$$"
        export WINEPREFIX="/tmp/cmdiqo-wine-$$"
        if [[ "$PRESET" == "linux-armv6" ]]; then
            export QEMU_CPU="arm1176"
        else
            export QEMU_LD_PREFIX="/usr/aarch64-linux-gnu"
        fi
        install --directory --mode=700 "$TMPDIR" "$WINEPREFIX"
        if [[ -z "${!INSTALLED_DIR_VAR+x}" ]]; then
            printf "[ERROR] Builder image is missing environment variable %s for %s.\n" "$INSTALLED_DIR_VAR" "$PRESET" >&2
            printf "[ERROR] The local builder image is outdated and lacks %s support. Rebuild it with --rebuild-builder.\n" "$PRESET" >&2
            exit 1
        fi
        installed_dir="${!INSTALLED_DIR_VAR}"
        if [[ ! -d "$installed_dir" ]]; then
            printf "[ERROR] Pre-built dependency root is missing: %s\n" "$installed_dir" >&2
            printf "[ERROR] Rebuild the builder image with --rebuild-builder.\n" >&2
            exit 1
        fi
        cmake --fresh --preset "$PRESET" \
            "-DVCPKG_INSTALLED_DIR=$installed_dir" \
            -DVCPKG_MANIFEST_INSTALL=OFF \
            "-DCMDIQO_BUILD_VERSION=${CMDIQO_BUILD_VERSION}" \
            "-DCMDIQO_AGENT_BINARY=/workspace/dist/${AGENT_BINARY}"
        cmake --build --preset "build-$PRESET" --config Release
        ctest --preset "test-$PRESET" -C Release --output-on-failure
    '
    if [[ ! -f "$ROOT_DIR/installer.c/out/build/$preset/$artifact" ]]; then
        error "Native $platform installer binary was not produced."
        exit 1
    fi
    cp -f "$ROOT_DIR/installer.c/out/build/$preset/$artifact" "$DIST_DIR/$destination"
    if [[ "$platform" == linux64 || "$platform" == linux-arm64 || "$platform" == linux-armv6 ]]; then
        chmod +x "$DIST_DIR/$destination"
    fi
    info "Native installer: $DIST_DIR/$destination"
}

if [[ "$BUILD_INSTALLER" = true ]]; then
    mkdir -p "$DIST_DIR"
    # Validate every input before building either target of a combined invocation.
    if [[ "$BUILD_LINUX" = true && "$BUILD_AGENT" != true ]]; then
        linux_payload_version="$(get_payload_version "$DIST_DIR/cmdiqo-agent-linux-x64.zip")"
    fi
    if [[ "$BUILD_LINUX_ARM64" = true && "$BUILD_AGENT" != true ]]; then
        linux_arm64_payload_version="$(get_payload_version "$DIST_DIR/cmdiqo-agent-linux-arm64.zip")"
    fi
    if [[ "$BUILD_LINUX_ARMV6" = true && "$BUILD_AGENT" != true ]]; then
        linux_armv6_payload_version="$(get_payload_version "$DIST_DIR/cmdiqo-agent-linux-armv6.zip")"
    fi
    if [[ "$BUILD_WIN" = true && "$BUILD_AGENT" != true ]]; then
        windows_payload_version="$(get_payload_version "$DIST_DIR/cmdiqo-agent-windows-x64.zip")"
    fi
fi

if [[ "$BUILD_AGENT" = true || "$BUILD_INSTALLER" = true || "$REBUILD_BUILDER" = true ]]; then
    mkdir -p "$DIST_DIR"
    ensure_installer_builder_image all cmdiqo-installer-builder:all \
        "${DOCKER_USERNAME}/cmdiqo-installer-builder:all" "$ROOT_DIR/installer.c"
fi

if [[ "$BUILD_AGENT" = true ]]; then
    if [[ "$BUILD_LINUX" = true ]]; then
        build_native_agent linux64 "$VERSION"
    fi
    if [[ "$BUILD_LINUX_ARM64" = true ]]; then
        build_native_agent linux-arm64 "$VERSION"
    fi
    if [[ "$BUILD_LINUX_ARMV6" = true ]]; then
        build_native_agent linux-armv6 "$VERSION"
    fi
    if [[ "$BUILD_WIN" = true ]]; then
        build_native_agent win64 "$VERSION"
    fi
fi

if [[ -n "$COPY_TO" ]]; then
    agent_binary="$DIST_DIR/cmdiqo-agent"
    agent_zip="$DIST_DIR/cmdiqo-agent-linux-x64.zip"
    if [[ "$BUILD_WIN" = true ]]; then
        agent_binary="$DIST_DIR/cmdiqo-agent.exe"
        agent_zip="$DIST_DIR/cmdiqo-agent-windows-x64.zip"
    elif [[ "$BUILD_LINUX_ARM64" = true ]]; then
        agent_binary="$DIST_DIR/cmdiqo-agent-linux-arm64"
        agent_zip="$DIST_DIR/cmdiqo-agent-linux-arm64.zip"
    elif [[ "$BUILD_LINUX_ARMV6" = true ]]; then
        agent_binary="$DIST_DIR/cmdiqo-agent-linux-armv6"
        agent_zip="$DIST_DIR/cmdiqo-agent-linux-armv6.zip"
    fi
    if [[ -d "$COPY_TO" ]]; then
        COPY_TO="$COPY_TO/$(basename "$agent_binary")"
    fi
    mkdir -p "$(dirname "$COPY_TO")"
    if [[ ! "$agent_binary" -ef "$COPY_TO" ]]; then
        cp -f "$agent_binary" "$COPY_TO"
    fi
    copy_zip="$(dirname "$COPY_TO")/$(basename "$agent_zip")"
    if [[ ! "$agent_zip" -ef "$copy_zip" ]]; then
        cp -f "$agent_zip" "$copy_zip"
    fi
fi

if [[ "$BUILD_INSTALLER" = true ]]; then
    if [[ "$BUILD_LINUX" = true ]]; then
        linux_payload_version="$(get_payload_version "$DIST_DIR/cmdiqo-agent-linux-x64.zip")"
        build_native_installer linux64 "$linux_payload_version"
    fi
    if [[ "$BUILD_LINUX_ARM64" = true ]]; then
        linux_arm64_payload_version="$(get_payload_version "$DIST_DIR/cmdiqo-agent-linux-arm64.zip")"
        build_native_installer linux-arm64 "$linux_arm64_payload_version"
    fi
    if [[ "$BUILD_LINUX_ARMV6" = true ]]; then
        linux_armv6_payload_version="$(get_payload_version "$DIST_DIR/cmdiqo-agent-linux-armv6.zip")"
        build_native_installer linux-armv6 "$linux_armv6_payload_version"
    fi
    if [[ "$BUILD_WIN" = true ]]; then
        windows_payload_version="$(get_payload_version "$DIST_DIR/cmdiqo-agent-windows-x64.zip")"
        build_native_installer win64 "$windows_payload_version"
    fi
fi

if [[ "$BUILD_BOT" = true ]]; then
    info "=== Building Docker image for cmdiqo-bot ==="
    TAG_LATEST="${DOCKER_USERNAME}/cmdiqo-bot:latest"
    TAG_SHORT="${DOCKER_USERNAME}/cmdiqo-bot:${SHORT_VERSION}"
    TAG_FULL="${DOCKER_USERNAME}/cmdiqo-bot:${VERSION//+/-}"
    TAG_TS="${DOCKER_USERNAME}/cmdiqo-bot:${TIMESTAMP}"

    if [[ "$PUSH_DOCKER" = true ]]; then
        info "Building and pushing multi-arch images (${TAG_FULL}) via buildx ..."
        if ! docker_cli buildx inspect 2>/dev/null | grep -q "Driver:.*docker-container"; then
            if ! docker_cli buildx inspect cmdiqo-builder >/dev/null 2>&1; then
                docker_cli buildx create --name cmdiqo-builder --driver docker-container --use >/dev/null 2>&1 || true
            else
                docker_cli buildx use cmdiqo-builder >/dev/null 2>&1 || true
            fi
        fi
        docker_cli buildx build \
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
        docker_cli buildx build --load \
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
    if [[ "$BUILD_LINUX" = true ]]; then
        if [[ "$BUILD_AGENT" = true ]]; then
            artifacts+=("$DIST_DIR/cmdiqo-agent")
        fi
        artifacts+=("$DIST_DIR/cmdiqo-agent-linux-x64.zip")
        if [[ "$BUILD_INSTALLER" = true ]]; then
            artifacts+=("$DIST_DIR/cmdiqo-agent-linux-x64-setup")
        fi
    fi
    if [[ "$BUILD_LINUX_ARM64" = true ]]; then
        if [[ "$BUILD_AGENT" = true ]]; then
            artifacts+=("$DIST_DIR/cmdiqo-agent-linux-arm64")
        fi
        artifacts+=("$DIST_DIR/cmdiqo-agent-linux-arm64.zip")
        if [[ "$BUILD_INSTALLER" = true ]]; then
            artifacts+=("$DIST_DIR/cmdiqo-agent-linux-arm64-setup")
        fi
    fi
    if [[ "$BUILD_LINUX_ARMV6" = true ]]; then
        if [[ "$BUILD_AGENT" = true ]]; then
            artifacts+=("$DIST_DIR/cmdiqo-agent-linux-armv6")
        fi
        artifacts+=("$DIST_DIR/cmdiqo-agent-linux-armv6.zip")
        if [[ "$BUILD_INSTALLER" = true ]]; then
            artifacts+=("$DIST_DIR/cmdiqo-agent-linux-armv6-setup")
        fi
    fi
    if [[ "$BUILD_WIN" = true ]]; then
        if [[ "$BUILD_AGENT" = true ]]; then
            artifacts+=("$DIST_DIR/cmdiqo-agent.exe")
        fi
        artifacts+=("$DIST_DIR/cmdiqo-agent-windows-x64.zip")
        if [[ "$BUILD_INSTALLER" = true ]]; then
            artifacts+=("$DIST_DIR/cmdiqo-agent-windows-x64-setup.exe")
        fi
    fi
    for artifact in "${artifacts[@]}"; do
        if [[ -f "$artifact" ]]; then
            info "  • $(basename "$artifact"): $artifact ($(ls -lh "$artifact" | awk '{print $5}'))"
            if command -v sha256sum >/dev/null 2>&1; then
                info "    SHA256: $(sha256sum "$artifact" | awk '{print $1}')"
            elif command -v shasum >/dev/null 2>&1; then
                info "    SHA256: $(shasum -a 256 "$artifact" | awk '{print $1}')"
            fi
        else
            error "Requested artifact is missing: $artifact"
            exit 1
        fi
    done
fi
info "[+] Build complete successfully for version ${VERSION}!"
