#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VCPKG="${ROOT}/.vcpkg"
LOCAL_CMAKE_VENV="${ROOT}/.cache/cmake-venv"
IMAGE="cmdiqo-installer-builder:ubuntu-22.04"
DOCKERFILE="${ROOT}/Dockerfile.linux"
LOCAL=0
REBUILD_IMAGE=0

usage() {
  cat <<'EOF'
Usage: ./setup-env.sh [--local] [--rebuild-image]

By default, prepares Docker and the Ubuntu 22.04 Linux builder image. It does
not add the current user to the docker group or otherwise change host access
configuration. Use --local only to install host build prerequisites and a
local installer.c/.vcpkg checkout.

Options:
  --local           Prepare a native Linux CMake/vcpkg environment.
  --rebuild-image   Refresh the Docker builder image with its base image.
  --help            Show this help text.
EOF
}

while (($#)); do
  case "$1" in
    --local) LOCAL=1 ;;
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

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "setup-env.sh supports Linux only. Use setup-env.ps1 on Windows." >&2
  exit 1
fi

run_privileged() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "Root privileges are required to install packages, but sudo is unavailable." >&2
    exit 1
  fi
}

is_ubuntu_or_debian() {
  [[ -r /etc/os-release ]] || return 1
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" || "${ID:-}" == "debian" || "${ID_LIKE:-}" == *debian* ]]
}

install_local_prerequisites() {
  if ! is_ubuntu_or_debian; then
    echo "Automatic local dependency installation is supported on Ubuntu/Debian only." >&2
    echo "Install a C++20 compiler, CMake 3.24+, Ninja, Python/pip, GTK3/X11/OpenGL development headers, and vcpkg prerequisites." >&2
    return
  fi

  run_privileged env DEBIAN_FRONTEND=noninteractive apt-get update
  run_privileged env DEBIAN_FRONTEND=noninteractive apt-get install --yes \
    autoconf autoconf-archive automake bison build-essential cmake flex gettext git gperf \
    libasound2-dev libbz2-dev libcairo2-dev libdbus-1-dev libegl1-mesa-dev libexpat1-dev \
    libfontconfig1-dev libfreetype6-dev libgdk-pixbuf-2.0-dev libgl1-mesa-dev \
    libgles2-mesa-dev libglu1-mesa-dev libgtk-3-dev libharfbuzz-dev libjpeg-dev liblzma-dev \
    libncurses-dev libpango1.0-dev libpng-dev libpulse-dev libreadline-dev libssl-dev \
    libtiff-dev libtool libtool-bin libwayland-dev libwebp-dev libx11-dev libxcb1-dev \
    libltdl-dev \
    libxcursor-dev libxext-dev libxi-dev libxinerama-dev libxkbcommon-dev \
    libxkbcommon-x11-dev libxrandr-dev nasm ninja-build patch perl pkg-config python3 \
    python3-pip python3-venv unzip wget zip
}

cmake_is_supported() {
  command -v cmake >/dev/null 2>&1 \
    && [[ "$(cmake --version | awk 'NR == 1 { print $3 }')" =~ ^([3-9]|[1-9][0-9]+)\. ]] \
    && [[ "$(printf '%s\n%s\n' "3.24.0" "$(cmake --version | awk 'NR == 1 { print $3 }')" | sort -V | head -n1)" == "3.24.0" ]]
}

prepare_local() {
  install_local_prerequisites
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required to install a current CMake for the local build." >&2
    exit 1
  fi
  if [[ -x "${LOCAL_CMAKE_VENV}/bin/cmake" ]]; then
    export PATH="${LOCAL_CMAKE_VENV}/bin:${PATH}"
  fi
  if ! cmake_is_supported; then
    echo "Installing CMake 3.24+ in the project-local Python environment."
    python3 -m venv "${LOCAL_CMAKE_VENV}"
    "${LOCAL_CMAKE_VENV}/bin/python" -m pip install --upgrade "cmake>=3.24,<4"
    export PATH="${LOCAL_CMAKE_VENV}/bin:${PATH}"
  fi
  if ! cmake_is_supported; then
    echo "CMake 3.24 or newer is required but was not found after installation." >&2
    exit 1
  fi
  for tool in git ninja; do
    command -v "${tool}" >/dev/null 2>&1 || {
      echo "${tool} is required but was not found." >&2
      exit 1
    }
  done

  export VCPKG_ROOT="${VCPKG}"
  if [[ ! -d "${VCPKG}" ]]; then
    git clone https://github.com/microsoft/vcpkg.git "${VCPKG}"
  fi
  if [[ ! -x "${VCPKG}/vcpkg" ]]; then
    "${VCPKG}/bootstrap-vcpkg.sh" -disableMetrics
  fi
  "${VCPKG}/vcpkg" install --x-manifest-root="${ROOT}" --triplet=x64-linux
  echo "Local environment is ready. Build from the repository root with: ./build.sh --installer"
}

prepare_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    if ! is_ubuntu_or_debian; then
      echo "Docker is required. Install Docker Engine for this distribution, then rerun this script." >&2
      exit 1
    fi
    echo "Installing the Ubuntu/Debian docker.io package. This script does not modify docker group membership."
    run_privileged env DEBIAN_FRONTEND=noninteractive apt-get update
    run_privileged env DEBIAN_FRONTEND=noninteractive apt-get install --yes docker.io
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "Docker is installed but unavailable to the current user." >&2
    echo "Start the Docker daemon and configure access explicitly (for example, add the user to the docker group and sign in again), then rerun this script." >&2
    exit 1
  fi

  local build_input_hash
  build_input_hash="$(cat "${DOCKERFILE}" "${ROOT}/vcpkg.json" | sha256sum | awk '{print $1}')"
  local image_build_input_hash=""
  if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
    image_build_input_hash="$(docker image inspect --format '{{ index .Config.Labels "io.cmdiqo.installer-builder.build-input-sha256" }}' "${IMAGE}")"
  fi
  if [[ "${REBUILD_IMAGE}" -eq 1 || "${image_build_input_hash}" != "${build_input_hash}" ]]; then
    echo "Building Docker image ${IMAGE}"
    build_args=(build --tag "${IMAGE}" --file "${DOCKERFILE}" --build-arg "BUILDER_INPUT_SHA=${build_input_hash}")
    if [[ "${REBUILD_IMAGE}" -eq 1 ]]; then
      build_args+=(--pull)
    fi
    docker "${build_args[@]}" "${ROOT}"
  fi
  echo "Docker environment is ready. Build from the repository root with: ./build.sh --installer"
}

if [[ "${LOCAL}" -eq 1 ]]; then
  if [[ "${REBUILD_IMAGE}" -eq 1 ]]; then
    echo "--rebuild-image cannot be combined with --local." >&2
    exit 2
  fi
  prepare_local
else
  prepare_docker
fi
