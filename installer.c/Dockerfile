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
        python3-pip \
        python3-venv \
        rsync \
        tar \
        unzip \
        wget \
        xz-utils \
        zip \
    && python3 -m pip install --no-cache-dir "cmake>=3.24,<4" \
    && cmake --version \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/microsoft/vcpkg.git /opt/vcpkg \
    && git -C /opt/vcpkg checkout "${VCPKG_TAG}" \
    && /opt/vcpkg/bootstrap-vcpkg.sh -disableMetrics \
    && chmod -R a+rwx /opt/vcpkg

ENV VCPKG_ROOT=/opt/vcpkg \
    VCPKG_INSTALLED_DIR=/opt/cmdiqo-vcpkg-installed \
    VCPKG_DISABLE_METRICS=1 \
    PATH=/opt/vcpkg:/usr/local/bin:${PATH}

COPY vcpkg.json /tmp/cmdiqo-manifest/vcpkg.json

# -----------------------------------------------------------------------------
# Linux stage: GTK3/X11 stack and pre-built x64-linux vcpkg ports
# -----------------------------------------------------------------------------
FROM base AS linux

LABEL org.opencontainers.image.title="Cmdiqo native-installer Linux builder" \
      org.opencontainers.image.vendor="Cmdiqo"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        gdb \
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
        lldb \
        ltrace \
        strace \
        valgrind \
    && rm -rf /var/lib/apt/lists/*

RUN /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest \
        --triplet=x64-linux \
        --x-install-root="${VCPKG_INSTALLED_DIR}" \
    && chmod -R a+rwx "${VCPKG_INSTALLED_DIR}" /opt/vcpkg

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

RUN /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest \
        --triplet=x64-mingw-static \
        --x-install-root="${VCPKG_INSTALLED_DIR}" \
    && chmod -R a+rwx "${VCPKG_INSTALLED_DIR}" /opt/vcpkg

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

RUN /opt/vcpkg/vcpkg install \
        --x-manifest-root=/tmp/cmdiqo-manifest \
        --triplet=x64-mingw-static \
        --x-install-root="${VCPKG_INSTALLED_DIR}" \
    && chmod -R a+rwx "${VCPKG_INSTALLED_DIR}" /opt/vcpkg

WORKDIR /workspace
