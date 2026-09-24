#!/usr/bin/env bash
set -e

# Logging functions
info() {
    printf "\033[0;32m[INFO]\033[0m %s\n"  "$1"
}

debug() {
    printf "\033[0;90m[DEBUG]\033[0m %s\n" "$1"
}

error() {
    printf "\033[0;31m[ERROR]\033[0m %s\n" "$1"
}

info "=== Configuring direnv for Shango ==="

# 1. Ensure direnv is installed
if ! command -v direnv >/dev/null 2>&1; then
    info "Installing direnv..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo apt update && sudo apt install -q -y direnv
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install direnv
    elif [[ "$OSTYPE" == "msys"* || "$OSTYPE" == "cygwin"* ]]; then
        if command -v choco >/dev/null 2>&1; then
            choco install direnv -y
        elif command -v winget >/dev/null 2>&1; then
            winget install direnv.direnv
        else
            error "Please install direnv using: choco install direnv (or winget install direnv.direnv)"
            exit 1
        fi
    else
        error "Unsupported OS. Install direnv manually from https://direnv.net/docs/installation.html"
        exit 1
    fi
fi

# 2. Ensure shell integration
debug "Ensuring direnv shell integration..."
SHELL_RC=""
if [[ -n "$ZSH_VERSION" ]]; then
    SHELL_RC="$HOME/.zshrc"
elif [[ -n "$BASH_VERSION" ]]; then
    SHELL_RC="$HOME/.bashrc"
fi

if [[ -n "$SHELL_RC" ]]; then
    if ! grep -q 'eval "$(direnv hook' "$SHELL_RC" 2>/dev/null; then
        info "Adding direnv hook to $SHELL_RC..."
        echo 'eval "$(direnv hook bash)"' >> "$SHELL_RC"
    fi
fi

# 3. Allow direnv in project root
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
if command -v direnv >/dev/null 2>&1; then
    direnv allow .
    info "direnv allowed for $ROOT_DIR"
fi

info "direnv setup complete. Restart your shell or run: eval \"\$(direnv hook bash)\""
