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
    && cmake --version \
    && rm -rf /var/lib/apt/lists/* /tmp/cmake-linux-x86_64.tar.gz

RUN git clone https://github.com/microsoft/vcpkg.git /opt/vcpkg \
    && git -C /opt/vcpkg checkout "${VCPKG_TAG}" \
    && sed -i '/if(NOT "debug-support" IN_LIST FEATURES)/,/^endif()$/ { s|if(VCPKG_TARGET_IS_WINDOWS)|if(VCPKG_TARGET_IS_WINDOWS AND EXISTS "${CURRENT_PACKAGES_DIR}/include/wx/debug.h")|; }' /opt/vcpkg/ports/wxwidgets/portfile.cmake \
    && /opt/vcpkg/bootstrap-vcpkg.sh -disableMetrics \
    && rm -rf /opt/vcpkg/.git \
    && install --directory --mode=1777 \
        /opt/vcpkg/buildtrees \
        /opt/vcpkg/packages \
        /opt/vcpkg/downloads

ENV VCPKG_ROOT=/opt/vcpkg \
    VCPKG_INSTALLED_DIR=/opt/cmdiqo-vcpkg-installed \
    VCPKG_OVERLAY_TRIPLETS=/opt/cmdiqo-triplets \
    VCPKG_DISABLE_METRICS=1 \
    PATH=/opt/vcpkg:/usr/local/bin:${PATH}

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
        libltdl-dev \
        libzip-dev \
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
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /tmp/vcpkg-binary-cache \
    && VCPKG_DEFAULT_BINARY_CACHE=/tmp/vcpkg-binary-cache /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest \
        --triplet=x64-linux-release \
        --x-install-root="${VCPKG_INSTALLED_DIR}" \
        --x-buildtrees-root=/tmp/vcpkg-buildtrees \
        --x-packages-root=/tmp/vcpkg-packages \
        --downloads-root=/tmp/vcpkg-downloads \
    && rm -rf /tmp/vcpkg-binary-cache /tmp/vcpkg-buildtrees /tmp/vcpkg-downloads /tmp/vcpkg-packages \
    && install --directory --mode=1777 "${VCPKG_INSTALLED_DIR}/vcpkg"

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

ENV WINEDEBUG=-all

RUN mkdir -p /tmp/vcpkg-binary-cache \
    && VCPKG_DEFAULT_BINARY_CACHE=/tmp/vcpkg-binary-cache /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest \
        --triplet=x64-mingw-static-release \
        --x-install-root="${VCPKG_INSTALLED_DIR}" \
        --x-buildtrees-root=/tmp/vcpkg-buildtrees \
        --x-packages-root=/tmp/vcpkg-packages \
        --downloads-root=/tmp/vcpkg-downloads \
    && rm -rf /tmp/vcpkg-binary-cache /tmp/vcpkg-buildtrees /tmp/vcpkg-downloads /tmp/vcpkg-packages \
    && install --directory --mode=1777 "${VCPKG_INSTALLED_DIR}/vcpkg"

WORKDIR /workspace

# -----------------------------------------------------------------------------
# Unified stage: includes both Linux and Windows MinGW toolchains
# -----------------------------------------------------------------------------
FROM linux AS all

LABEL org.opencontainers.image.title="Cmdiqo native-installer unified multi-platform builder" \
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

ENV WINEDEBUG=-all

RUN mkdir -p /tmp/vcpkg-binary-cache \
    && VCPKG_DEFAULT_BINARY_CACHE=/tmp/vcpkg-binary-cache /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest \
        --triplet=x64-mingw-static-release \
        --x-install-root="${VCPKG_INSTALLED_DIR}" \
        --x-buildtrees-root=/tmp/vcpkg-buildtrees \
        --x-packages-root=/tmp/vcpkg-packages \
        --downloads-root=/tmp/vcpkg-downloads \
    && rm -rf /tmp/vcpkg-binary-cache /tmp/vcpkg-buildtrees /tmp/vcpkg-downloads /tmp/vcpkg-packages \
    && install --directory --mode=1777 "${VCPKG_INSTALLED_DIR}/vcpkg"

WORKDIR /workspace
