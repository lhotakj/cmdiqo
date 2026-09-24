#!/usr/bin/env bash
# build.sh — Forwarding script for Cmdiqo build system
# All build scripts and logic are maintained in the development/ directory.

set -e
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${ROOT_DIR}/development/build.sh" "$@"
