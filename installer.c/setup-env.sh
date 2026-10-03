#!/usr/bin/env bash
# setup-env.sh — Prepares Docker builder images for native C++ installer compilation.
# All toolchains, tools, and vcpkg dependencies are built directly inside the container.
# No checkouts or package installations are performed on the host.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKERFILE="${ROOT}/Dockerfile"
REBUILD_IMAGE=0
PUSH=0
DOCKER_USER="${DOCKERHUB_USER:-${DOCKERHUB_USERNAME:-lhotakj}}"
DOCKERHUB_LOGIN_USER="${DOCKERHUB_USER:-${DOCKERHUB_USERNAME:-}}"
DOCKERHUB_LOGIN_PASS="${DOCKERHUB_PASS:-${DOCKERHUB_TOKEN:-}}"
PLATFORM="all"
DEBUG_MODE="${DEBUG:-0}"

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  COLOR_RESET=$'\033[0m'
  COLOR_INFO=$'\033[0;36m'
  COLOR_DEBUG=$'\033[0;35m'
  COLOR_SUCCESS=$'\033[0;32m'
  COLOR_WARNING=$'\033[0;33m'
  COLOR_ERROR=$'\033[0;31m'
else
  COLOR_RESET=""
  COLOR_INFO=""
  COLOR_DEBUG=""
  COLOR_SUCCESS=""
  COLOR_WARNING=""
  COLOR_ERROR=""
fi

log() {
  local level="$1"
  local color="$2"
  shift 2
  printf '%b[%s]%b %s\n' "${color}" "${level}" "${COLOR_RESET}" "$*"
}

info() {
  log "INFO" "${COLOR_INFO}" "$*"
}

debug() {
  if [[ "${DEBUG_MODE}" == "1" ]]; then
    log "DEBUG" "${COLOR_DEBUG}" "$*"
  fi
}

success() {
  log "SUCCESS" "${COLOR_SUCCESS}" "$*"
}

warn() {
  log "WARN" "${COLOR_WARNING}" "$*"
}

error() {
  log "ERROR" "${COLOR_ERROR}" "$*" >&2
}

usage() {
  cat <<'EOF'
Usage: ./setup-env.sh [options]

Prepares the Docker builder images using the multi-stage installer.c/Dockerfile.
All toolchains and dependencies are built directly inside the container.
No host checkouts or package installations are performed.

Options:
  --platform, -p <str>       Target builder platform: 'linux', 'windows', or 'all' (default: all)
  --docker-user <username>   Docker Hub username/organization (default: $DOCKERHUB_USER, $DOCKERHUB_USERNAME, or 'lhotakj')
  --push                     Push built builder images to Docker Hub after building
  --rebuild-image            Refresh the Docker builder image with its base image (--pull)
  --help, -h                 Show this help text.

Environment:
  DOCKERHUB_USER             Docker Hub username for local --push authentication
  DOCKERHUB_PASS             Docker Hub access token used with DOCKERHUB_USER
  DOCKERHUB_USERNAME         GitHub Actions-compatible alias for DOCKERHUB_USER
  DOCKERHUB_TOKEN            GitHub Actions-compatible alias for DOCKERHUB_PASS
  DEBUG=1                    Show build-input and image-cache diagnostics
  NO_COLOR=1                 Disable colored log prefixes

Examples:
  ./setup-env.sh                                  # Build unified Linux and Windows builder locally
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
      error "Unknown option: $1"
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ! command -v docker >/dev/null 2>&1; then
  error "Docker is required. Install Docker Engine, then rerun this script."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  error "Docker daemon is not running or unavailable. Start Docker, then rerun this script."
  exit 1
fi

if [[ "${PUSH}" -eq 1 ]]; then
  if [[ -z "${DOCKERHUB_LOGIN_USER}" || -z "${DOCKERHUB_LOGIN_PASS}" ]]; then
    error "Set DOCKERHUB_USER and DOCKERHUB_PASS, or DOCKERHUB_USERNAME and DOCKERHUB_TOKEN, when using --push."
    exit 1
  fi

  info "Authenticating with Docker Hub as ${DOCKERHUB_LOGIN_USER}..."
  printf '%s' "${DOCKERHUB_LOGIN_PASS}" | docker login --username "${DOCKERHUB_LOGIN_USER}" --password-stdin
  success "Docker Hub authentication succeeded."
fi

build_docker_image() {
  local target_stage="$1"
  local label="$2"
  local local_tag="$3"
  local remote_tag="$4"
  local alt_remote_tag="${5:-}"

  local build_input_hash
  build_input_hash="$(
    {
      cat "${DOCKERFILE}" "${ROOT}/vcpkg.json"
      find "${ROOT}/triplets" -type f | LC_ALL=C sort |
        while IFS= read -r triplet; do cat "${triplet}"; done
    } | tr -d '\r' | {
      if command -v sha256sum >/dev/null 2>&1; then
        sha256sum
      else
        shasum -a 256
      fi
    } | awk '{print $1}'
  )"
  local image_build_input_hash=""
  local existing_image=""
  local candidate
  for candidate in "${local_tag}" "${remote_tag}"; do
    if docker image inspect "${candidate}" >/dev/null 2>&1; then
      image_build_input_hash="$(docker image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' "${candidate}")"
      if [[ "${image_build_input_hash}" == "${build_input_hash}" ]]; then
        existing_image="${candidate}"
        break
      fi
    fi
  done
  debug "Build input hash: ${build_input_hash}; current image hash: ${image_build_input_hash:-none}"

  if [[ "${REBUILD_IMAGE}" -eq 1 || "${image_build_input_hash}" != "${build_input_hash}" ]]; then
    info "Building ${label} Docker builder image (target: ${target_stage})."
    # Use 'docker buildx build' so the active BuildKit driver (docker-container or
    # otherwise) is honoured correctly.  With the docker-container driver, a plain
    # 'docker build' without --push or --load discards the result entirely.
    # --push  → image is written directly to the registry (CI / push path)
    # --load  → image is exported into the local Docker daemon (local dev path)
    local build_args=(
      buildx build
      --load
      --platform linux/amd64
      --file "${DOCKERFILE}"
      --target "${target_stage}"
      --tag "${local_tag}"
      --tag "${remote_tag}"
      --build-arg "BUILDER_INPUT_SHA=${build_input_hash}"
    )
    if [[ -n "${alt_remote_tag}" ]]; then
      build_args+=(--tag "${alt_remote_tag}")
    fi
    if [[ "${REBUILD_IMAGE}" -eq 1 ]]; then
      build_args+=(--pull --no-cache)
    fi
    docker "${build_args[@]}" "${ROOT}"
    if [[ "${PUSH}" -eq 1 ]]; then
      info "Pushing ${label} builder image to Docker Hub (${remote_tag})."
      docker push "${remote_tag}"
      if [[ -n "${alt_remote_tag}" ]]; then
        docker push "${alt_remote_tag}"
      fi
    fi
  else
    # Image is already up-to-date; just re-tag and optionally push.
    if [[ "${existing_image}" != "${local_tag}" ]]; then
      docker tag "${existing_image}" "${local_tag}"
    fi
    docker tag "${local_tag}" "${remote_tag}"
    if [[ -n "${alt_remote_tag}" ]]; then
      docker tag "${local_tag}" "${alt_remote_tag}"
    fi
    if [[ "${PUSH}" -eq 1 ]]; then
      info "Pushing existing ${label} builder image to Docker Hub (${remote_tag})."
      docker push "${remote_tag}"
      if [[ -n "${alt_remote_tag}" ]]; then
        docker push "${alt_remote_tag}"
      fi
    else
      info "Reusing current ${label} Docker builder image."
    fi
  fi

  success "${label} Docker builder is ready: ${remote_tag}"
}

case "${PLATFORM}" in
  linux|linux64)
    build_docker_image "linux" "Linux" "cmdiqo-installer-builder:ubuntu-22.04" "${DOCKER_USER}/cmdiqo-installer-builder:ubuntu-22.04" "${DOCKER_USER}/cmdiqo-installer-builder:linux"
    info "Build from repository root with: ./build.sh --installer"
    ;;
  windows|win64)
    build_docker_image "windows" "Windows (MinGW-w64)" "cmdiqo-installer-builder:windows-mingw" "${DOCKER_USER}/cmdiqo-installer-builder:windows-mingw" "${DOCKER_USER}/cmdiqo-installer-builder:windows"
    info "Build from repository root with: ./build.sh --installer --platform win64"
    ;;
  all)
    build_docker_image "all" "Unified Multi-Toolchain" "cmdiqo-installer-builder:all" "${DOCKER_USER}/cmdiqo-installer-builder:all" "${DOCKER_USER}/cmdiqo-installer-builder:ubuntu-22.04-all"
    info "Build from repository root with: ./build.sh --installer --platform all"
    ;;
  *)
    error "Invalid platform '${PLATFORM}'. Expected 'linux', 'windows', or 'all'."
    exit 1
    ;;
esac
