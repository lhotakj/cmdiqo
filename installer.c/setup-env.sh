#!/usr/bin/env bash
# setup-env.sh — Prepares Docker builder images for native C++ installer compilation.
# All toolchains, tools, and vcpkg dependencies are built directly inside the container.
# No checkouts or package installations are performed on the host.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKERFILE="${ROOT}/Dockerfile"
REBUILD_IMAGE=0
PUSH=0
DOCKER_USER="${DOCKERHUB_USERNAME:-lhotakj}"
PLATFORM="linux"

usage() {
  cat <<'EOF'
Usage: ./setup-env.sh [options]

Prepares the Docker builder images using the multi-stage installer.c/Dockerfile.
All toolchains and dependencies are built directly inside the container.
No host checkouts or package installations are performed.

Options:
  --platform, -p <str>       Target builder platform: 'linux', 'windows', or 'all' (default: linux)
  --docker-user <username>   Docker Hub username/organization (default: $DOCKERHUB_USERNAME or 'lhotakj')
  --push                     Push built builder images to Docker Hub after building
  --rebuild-image            Refresh the Docker builder image with its base image (--pull)
  --help, -h                 Show this help text.

Examples:
  ./setup-env.sh                                  # Build Linux builder locally
  ./setup-env.sh --platform windows               # Build Windows MinGW builder locally
  ./setup-env.sh --platform all --push            # Build and push Linux & Windows builder images to Docker Hub
EOF
}

while (($#)); do
  case "$1" in
    --platform|-p)
      PLATFORM="$(echo "$2" | tr '[:upper:]' '[:lower:]')"
      shift
      ;;
    --platform=*)
      PLATFORM="$(echo "${1#*=}" | tr '[:upper:]' '[:lower:]')"
      ;;
    --docker-user)
      DOCKER_USER="$2"
      shift
      ;;
    --docker-user=*)
      DOCKER_USER="${1#*=}"
      ;;
    --push) PUSH=1 ;;
    --rebuild-image) REBUILD_IMAGE=1 ;;
    --help|-h) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Engine, then rerun this script." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running or unavailable. Start Docker, then rerun this script." >&2
  exit 1
fi

build_docker_image() {
  local target_stage="$1"
  local label="$2"
  local local_tag="$3"
  local remote_tag="$4"
  local alt_remote_tag="${5:-}"

  local build_input_hash
  build_input_hash="$(cat "${DOCKERFILE}" "${ROOT}/vcpkg.json" | sha256sum | awk '{print $1}')"
  local image_build_input_hash=""
  if docker image inspect "${local_tag}" >/dev/null 2>&1; then
    image_build_input_hash="$(docker image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' "${local_tag}")"
  elif docker image inspect "${remote_tag}" >/dev/null 2>&1; then
    image_build_input_hash="$(docker image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' "${remote_tag}")"
  fi

  if [[ "${REBUILD_IMAGE}" -eq 1 || "${image_build_input_hash}" != "${build_input_hash}" ]]; then
    echo "=== Building ${label} Docker builder image (target: ${target_stage}) ==="
    # Use 'docker buildx build' so the active BuildKit driver (docker-container or
    # otherwise) is honoured correctly.  With the docker-container driver, a plain
    # 'docker build' without --push or --load discards the result entirely.
    # --push  → image is written directly to the registry (CI / push path)
    # --load  → image is exported into the local Docker daemon (local dev path)
    local build_args=(
      buildx build
      --file "${DOCKERFILE}"
      --target "${target_stage}"
      --build-arg "BUILDER_INPUT_SHA=${build_input_hash}"
    )
    if [[ "${REBUILD_IMAGE}" -eq 1 ]]; then
      build_args+=(--pull)
    fi
    if [[ "${PUSH}" -eq 1 ]]; then
      # Direct registry push: no intermediate local image required.
      build_args+=(--tag "${remote_tag}")
      if [[ -n "${alt_remote_tag}" ]]; then
        build_args+=(--tag "${alt_remote_tag}")
      fi
      build_args+=(--push)
    else
      # Load into local daemon for immediate use / testing.
      # Note: --load supports only a single platform and a single tag.
      build_args+=(--tag "${local_tag}" --load)
    fi
    docker "${build_args[@]}" "${ROOT}"
  else
    # Image is already up-to-date; just re-tag and optionally push.
    if [[ "${PUSH}" -eq 1 ]]; then
      echo "=== Pushing existing ${label} builder image to Docker Hub (${remote_tag}) ==="
      docker tag "${local_tag}" "${remote_tag}" 2>/dev/null \
        || docker tag "${remote_tag}" "${local_tag}" 2>/dev/null \
        || true
      docker push "${remote_tag}"
      if [[ -n "${alt_remote_tag}" ]]; then
        docker tag "${remote_tag}" "${alt_remote_tag}" 2>/dev/null || true
        docker push "${alt_remote_tag}"
      fi
    else
      docker tag "${local_tag}" "${remote_tag}" 2>/dev/null \
        || docker tag "${remote_tag}" "${local_tag}" 2>/dev/null \
        || true
      if [[ -n "${alt_remote_tag}" ]]; then
        docker tag "${remote_tag}" "${alt_remote_tag}" 2>/dev/null || true
      fi
    fi
  fi

  echo "[+] ${label} Docker builder is ready: ${remote_tag}"
}

case "${PLATFORM}" in
  linux|linux64)
    build_docker_image "linux" "Linux" "cmdiqo-installer-builder:ubuntu-22.04" "${DOCKER_USER}/cmdiqo-installer-builder:ubuntu-22.04" "${DOCKER_USER}/cmdiqo-installer-builder:linux"
    echo "Build from repository root with: ./build.sh --installer"
    ;;
  windows|win64)
    build_docker_image "windows" "Windows (MinGW-w64)" "cmdiqo-installer-builder:windows-mingw" "${DOCKER_USER}/cmdiqo-installer-builder:windows-mingw" "${DOCKER_USER}/cmdiqo-installer-builder:windows"
    echo "Build from repository root with: ./build.sh --installer --platform win64"
    ;;
  all)
    build_docker_image "linux" "Linux" "cmdiqo-installer-builder:ubuntu-22.04" "${DOCKER_USER}/cmdiqo-installer-builder:ubuntu-22.04" "${DOCKER_USER}/cmdiqo-installer-builder:linux"
    build_docker_image "windows" "Windows (MinGW-w64)" "cmdiqo-installer-builder:windows-mingw" "${DOCKER_USER}/cmdiqo-installer-builder:windows-mingw" "${DOCKER_USER}/cmdiqo-installer-builder:windows"
    build_docker_image "all" "Unified Multi-Toolchain" "cmdiqo-installer-builder:all" "${DOCKER_USER}/cmdiqo-installer-builder:all" "${DOCKER_USER}/cmdiqo-installer-builder:ubuntu-22.04-all"
    echo "Build from repository root with: ./build.sh --installer --platform all"
    ;;
  *)
    echo "Invalid platform '${PLATFORM}'. Expected 'linux', 'windows', or 'all'." >&2
    exit 1
    ;;
esac
