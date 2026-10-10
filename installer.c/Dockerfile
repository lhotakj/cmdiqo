# syntax=docker/dockerfile:1
# Unified and multi-stage C++ native installer builder for Cmdiqo.
# Targets:
#   - linux:   Ubuntu 22.04 native Linux GCC + GTK3 toolchain (x64-linux)
#   - windows: Ubuntu 22.04 MinGW-w64 GCC cross-toolchain (x64-mingw-static)
#   - all:     Combined unified image with both Linux and Windows toolchains

# -----------------------------------------------------------------------------
# Base stage: common build tools, CMake 3.24+, Ninja, Python, and vcpkg
# -----------------------------------------------------------------------------
FROM ubuntu:22.04 AS base

ARG DEBIAN_FRONTEND=noninteractive
ARG VCPKG_TAG=2026.07.29
ARG CMAKE_VERSION=4.4.0
ARG CMAKE_SHA256=3864eb649b4466ae126a64bbde1657adad78efbbaa068bf38201de5cf1b5349f
ARG BUILDER_INPUT_SHA=unknown

LABEL io.cmdiqo.installer-builder.build-input-sha256="${BUILDER_INPUT_SHA}"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        autoconf \
        autoconf-archive \
        automake \
        bison \
        build-essential \
        ca-certificates \
        ccache \
        cmake \
        curl \
        file \
        flex \
        gettext \
        git \
        gperf \
        m4 \
        nasm \
        ninja-build \
        patch \
        perl \
        pkg-config \
        python3 \
        python3-venv \
        tar \
        unzip \
        wget \
        xz-utils \
        zip \
    # Install a checksum-verified CMake release so vcpkg does not download its own copy.
    && curl -fsSL -o /tmp/cmake-linux-x86_64.tar.gz "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/cmake-${CMAKE_VERSION}-linux-x86_64.tar.gz" \
    && echo "${CMAKE_SHA256}  /tmp/cmake-linux-x86_64.tar.gz" | sha256sum --check --strict \
    && tar -xzf /tmp/cmake-linux-x86_64.tar.gz -C /opt \
    && ln -sf "/opt/cmake-${CMAKE_VERSION}-linux-x86_64/bin/cmake" /usr/local/bin/cmake \
    && ln -sf "/opt/cmake-${CMAKE_VERSION}-linux-x86_64/bin/ctest" /usr/local/bin/ctest \
    && cmake --version \
    && rm -rf /var/lib/apt/lists/* /tmp/cmake-linux-x86_64.tar.gz

RUN git clone https://github.com/microsoft/vcpkg.git /opt/vcpkg \
    && git -C /opt/vcpkg checkout "${VCPKG_TAG}" \
    && /opt/vcpkg/bootstrap-vcpkg.sh -disableMetrics \
    && rm -rf /opt/vcpkg/.git \
    && install --directory --mode=1777 \
        /opt/vcpkg/buildtrees \
        /opt/vcpkg/packages \
        /opt/vcpkg/downloads

# vcpkg manifest-mode installs behave like "x-set-installed": every run makes the
# install root match the requested plan exactly and removes everything else from it.
# Each triplet therefore needs its own install root, otherwise the MinGW install in
# the windows/all stages deletes the pre-built Linux packages again.
ENV HOME=/tmp \
    VCPKG_ROOT=/opt/vcpkg \
    VCPKG_INSTALLED_DIR_LINUX=/opt/cmdiqo-vcpkg-installed/linux \
    VCPKG_INSTALLED_DIR_LINUX_ARM64=/opt/cmdiqo-vcpkg-installed/linux-arm64 \
    VCPKG_INSTALLED_DIR_LINUX_ARMV6=/opt/cmdiqo-vcpkg-installed/linux-armv6 \
    VCPKG_INSTALLED_DIR_WINDOWS=/opt/cmdiqo-vcpkg-installed/windows-mingw \
    VCPKG_INSTALLED_DIR=/opt/cmdiqo-vcpkg-installed/linux \
    VCPKG_OVERLAY_TRIPLETS=/opt/cmdiqo-triplets \
    VCPKG_DISABLE_METRICS=1 \
    PATH=/opt/vcpkg:/usr/local/bin:/usr/lib/wine:${PATH}

COPY vcpkg.json /tmp/cmdiqo-manifest/vcpkg.json
COPY triplets /opt/cmdiqo-triplets

# -----------------------------------------------------------------------------
# Linux stage: GTK3/X11 stack and pre-built x64-linux vcpkg ports
# -----------------------------------------------------------------------------
FROM base AS linux

LABEL org.opencontainers.image.title="Cmdiqo native-installer Linux builder" \
      org.opencontainers.image.vendor="Cmdiqo"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libasound2-dev \
        libbz2-dev \
        libcairo2-dev \
        libdbus-1-dev \
        libegl1-mesa-dev \
        libexpat1-dev \
        libfontconfig1-dev \
        libfreetype6-dev \
        libgdk-pixbuf-2.0-dev \
        libgl1-mesa-dev \
        libgles2-mesa-dev \
        libglu1-mesa-dev \
        libgtk-3-dev \
        libharfbuzz-dev \
        libjpeg-dev \
        libltdl-dev \
        liblzma-dev \
        libncurses-dev \
        libnotify-dev \
        libpango1.0-dev \
        libpng-dev \
        libpulse-dev \
        libreadline-dev \
        libsecret-1-dev \
        libssl-dev \
        libtiff-dev \
        libtool \
        libtool-bin \
        libwayland-dev \
        libwebp-dev \
        libx11-dev \
        libxcb1-dev \
        libxcursor-dev \
        libxext-dev \
        libxi-dev \
        libxinerama-dev \
        libxkbcommon-dev \
        libxkbcommon-x11-dev \
        libxrandr-dev \
        libzip-dev \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/vcpkg-binary-cache \
    && VCPKG_DEFAULT_BINARY_CACHE=/tmp/vcpkg-binary-cache /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest \
        --triplet=x64-linux-release \
        --x-install-root="${VCPKG_INSTALLED_DIR_LINUX}" \
        --x-buildtrees-root=/tmp/vcpkg-buildtrees \
        --x-packages-root=/tmp/vcpkg-packages \
        --downloads-root=/tmp/vcpkg-downloads \
    && rm -rf /tmp/vcpkg-binary-cache /tmp/vcpkg-buildtrees /tmp/vcpkg-downloads /tmp/vcpkg-packages \
    && install --directory --mode=0777 "${VCPKG_INSTALLED_DIR_LINUX}/vcpkg" \
    && find "${VCPKG_INSTALLED_DIR_LINUX}" -type d -exec chmod a+rwx {} + \
    && chmod -R a+rwX "${VCPKG_INSTALLED_DIR_LINUX}/vcpkg"

WORKDIR /workspace

# -----------------------------------------------------------------------------
# Windows stage: MinGW-w64 toolchain, Wine, and pre-built x64-mingw-static ports
# -----------------------------------------------------------------------------
FROM base AS windows

LABEL org.opencontainers.image.title="Cmdiqo native-installer Windows MinGW builder" \
      org.opencontainers.image.vendor="Cmdiqo"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        g++-mingw-w64-x86-64 \
        gcc-mingw-w64-x86-64 \
        binutils-mingw-w64-x86-64 \
        mingw-w64 \
        mingw-w64-tools \
        wine \
        wine64 \
    && update-alternatives --set x86_64-w64-mingw32-gcc /usr/bin/x86_64-w64-mingw32-gcc-posix \
    && update-alternatives --set x86_64-w64-mingw32-g++ /usr/bin/x86_64-w64-mingw32-g++-posix \
    && rm -rf /var/lib/apt/lists/*

ENV WINEDEBUG=-all \
    VCPKG_INSTALLED_DIR=/opt/cmdiqo-vcpkg-installed/windows-mingw

RUN mkdir -p /tmp/vcpkg-binary-cache \
    && VCPKG_DEFAULT_BINARY_CACHE=/tmp/vcpkg-binary-cache /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest \
        --triplet=x64-mingw-static-release \
        --x-install-root="${VCPKG_INSTALLED_DIR_WINDOWS}" \
        --x-buildtrees-root=/tmp/vcpkg-buildtrees \
        --x-packages-root=/tmp/vcpkg-packages \
        --downloads-root=/tmp/vcpkg-downloads \
    && rm -rf /tmp/vcpkg-binary-cache /tmp/vcpkg-buildtrees /tmp/vcpkg-downloads /tmp/vcpkg-packages \
    && install --directory --mode=0777 "${VCPKG_INSTALLED_DIR_WINDOWS}/vcpkg" \
    && find "${VCPKG_INSTALLED_DIR_WINDOWS}" -type d -exec chmod a+rwx {} + \
    && chmod -R a+rwX "${VCPKG_INSTALLED_DIR_WINDOWS}/vcpkg"

WORKDIR /workspace

# -----------------------------------------------------------------------------
# Unified stage: includes both Linux and Windows MinGW toolchains
# -----------------------------------------------------------------------------
FROM linux AS all

LABEL org.opencontainers.image.title="Cmdiqo native-installer unified multi-platform builder" \
      org.opencontainers.image.vendor="Cmdiqo"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        binutils-aarch64-linux-gnu \
        binutils-mingw-w64-x86-64 \
        g++-aarch64-linux-gnu \
        g++-mingw-w64-x86-64 \
        gcc-aarch64-linux-gnu \
        gcc-mingw-w64-x86-64 \
        mingw-w64 \
        mingw-w64-tools \
        qemu-user-static \
        wine \
        wine64 \
    && update-alternatives --set x86_64-w64-mingw32-gcc /usr/bin/x86_64-w64-mingw32-gcc-posix \
    && update-alternatives --set x86_64-w64-mingw32-g++ /usr/bin/x86_64-w64-mingw32-g++-posix \
    && curl -fsSL -o /tmp/armv6-musl.tgz "https://musl.cc/armv6-linux-musleabihf-cross.tgz" \
    && echo "bcf47e82ec0c0c620c7b47aeb03355fe3f22d9dd9f719a77e2b21322291e04f7a35d1592275f487db4f754f3aede7f51bb0c2f07c1a3b3ce880b3341ec947bea  /tmp/armv6-musl.tgz" | sha512sum --check --strict \
    && tar -xzf /tmp/armv6-musl.tgz -C /opt \
    && for f in /opt/armv6-linux-musleabihf-cross/bin/*; do ln -sf "$f" "/usr/local/bin/$(basename "$f")"; done \
    && rm -f /tmp/armv6-musl.tgz \
    && rm -rf /var/lib/apt/lists/*

ENV WINEDEBUG=-all \
    PATH=/opt/armv6-linux-musleabihf-cross/bin:${PATH}

RUN mkdir -p /tmp/vcpkg-binary-cache \
    && VCPKG_DEFAULT_BINARY_CACHE=/tmp/vcpkg-binary-cache /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest \
        --triplet=x64-mingw-static-release \
        --x-install-root="${VCPKG_INSTALLED_DIR_WINDOWS}" \
        --x-buildtrees-root=/tmp/vcpkg-buildtrees \
        --x-packages-root=/tmp/vcpkg-packages \
        --downloads-root=/tmp/vcpkg-downloads \
    && rm -rf /tmp/vcpkg-binary-cache /tmp/vcpkg-buildtrees /tmp/vcpkg-downloads /tmp/vcpkg-packages \
    && install --directory --mode=0777 "${VCPKG_INSTALLED_DIR_WINDOWS}/vcpkg" \
    && find "${VCPKG_INSTALLED_DIR_WINDOWS}" -type d -exec chmod a+rwx {} + \
    && chmod -R a+rwX "${VCPKG_INSTALLED_DIR_WINDOWS}/vcpkg"

RUN mkdir -p /tmp/cmdiqo-manifest-arm64 /tmp/vcpkg-binary-cache \
    && printf '{"name":"cmdiqo-arm64","version-string":"1.0.0","dependencies":["catch2","cli11","ixwebsocket","libzip","nlohmann-json"]}\n' > /tmp/cmdiqo-manifest-arm64/vcpkg.json \
    && VCPKG_DEFAULT_BINARY_CACHE=/tmp/vcpkg-binary-cache /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest-arm64 \
        --triplet=arm64-linux-release \
        --x-install-root="${VCPKG_INSTALLED_DIR_LINUX_ARM64}" \
        --x-buildtrees-root=/tmp/vcpkg-buildtrees \
        --x-packages-root=/tmp/vcpkg-packages \
        --downloads-root=/tmp/vcpkg-downloads \
    && rm -rf /tmp/vcpkg-binary-cache /tmp/vcpkg-buildtrees /tmp/vcpkg-downloads /tmp/vcpkg-packages /tmp/cmdiqo-manifest-arm64 \
    && install --directory --mode=0777 "${VCPKG_INSTALLED_DIR_LINUX_ARM64}/vcpkg" \
    && find "${VCPKG_INSTALLED_DIR_LINUX_ARM64}" -type d -exec chmod a+rwx {} + \
    && chmod -R a+rwX "${VCPKG_INSTALLED_DIR_LINUX_ARM64}/vcpkg"

RUN mkdir -p /tmp/cmdiqo-manifest-armv6 /tmp/vcpkg-binary-cache \
    && printf '{"name":"cmdiqo-armv6","version-string":"1.0.0","dependencies":["catch2","cli11","ixwebsocket","libzip","nlohmann-json"]}\n' > /tmp/cmdiqo-manifest-armv6/vcpkg.json \
    && VCPKG_DEFAULT_BINARY_CACHE=/tmp/vcpkg-binary-cache /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest-armv6 \
        --triplet=armv6-linux-release \
        --x-install-root="${VCPKG_INSTALLED_DIR_LINUX_ARMV6}" \
        --x-buildtrees-root=/tmp/vcpkg-buildtrees \
        --x-packages-root=/tmp/vcpkg-packages \
        --downloads-root=/tmp/vcpkg-downloads \
    && rm -rf /tmp/vcpkg-binary-cache /tmp/vcpkg-buildtrees /tmp/vcpkg-downloads /tmp/vcpkg-packages /tmp/cmdiqo-manifest-armv6 \
    && install --directory --mode=0777 "${VCPKG_INSTALLED_DIR_LINUX_ARMV6}/vcpkg" \
    && find "${VCPKG_INSTALLED_DIR_LINUX_ARMV6}" -type d -exec chmod a+rwx {} + \
    && chmod -R a+rwX "${VCPKG_INSTALLED_DIR_LINUX_ARMV6}/vcpkg"

WORKDIR /workspace
