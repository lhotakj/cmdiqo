# syntax=docker/dockerfile:1

# ------------------------------------------------------------------------------
# Stage 1: Builder & Asset Compression
# ------------------------------------------------------------------------------
FROM python:3.12-alpine AS builder

WORKDIR /build

# 1. Install asset compression tools
RUN pip install --no-cache-dir \
        minify-html \
        rcssmin \
        rjsmin

# 2. Build isolated runtime virtual environment
COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# 3. Strip package managers and unneeded cache/test files to minimize image footprint:
#    - Remove pip, setuptools, wheel (~15MB saved in runtime image)
#    - Prune tests, caches, and bytecode (.pyc)
RUN /opt/venv/bin/pip uninstall -y pip setuptools wheel && \
    find /opt/venv -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    find /opt/venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /opt/venv -type f -name "*.pyc" -delete 2>/dev/null || true && \
    find /opt/venv -type f -name "*.pyo" -delete 2>/dev/null || true && \
    find /opt/venv -type f -name "*.dist-info/RECORD" -delete 2>/dev/null || true

# 4. Copy bot, config, version info, and compression utility
COPY bot ./bot
COPY config ./config
COPY docker/compress_assets.py ./compress_assets.py
COPY version.txt ./version.txt

# 5. In-place minify all HTML (including inline CSS & JS), standalone CSS, and JS
RUN python compress_assets.py bot

# ------------------------------------------------------------------------------
# Stage 2: Minimalist Production Runtime
# ------------------------------------------------------------------------------
FROM python:3.12-alpine AS runtime

# System runtime dependencies:
# docker-cli + compose plugin  -> manage host docker via /var/run/docker.sock
# arp-scan + iproute2          -> LAN device discovery
# bash                         -> custom shell commands
# libcap                       -> enable non-root capabilities for arp-scan
RUN apk add --no-cache \
        docker-cli docker-cli-compose \
        arp-scan iproute2 libcap \
        bash

WORKDIR /app

# Copy optimized runtime virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy minified bot application, config, and version
COPY --from=builder /build/bot ./bot
COPY --from=builder /build/config ./config
COPY --from=builder /build/version.txt ./version.txt

ENV PATH="/opt/venv/bin:$PATH" \
    COMMANDS_CONFIG=/app/config/commands.yaml \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# SonarQube S6437: Non-root container execution
# Create non-root user and grant raw network capabilities for arp-scan
RUN addgroup -g 1000 cmdiqo && \
    adduser -u 1000 -G cmdiqo -s /bin/sh -D cmdiqo && \
    chown -R cmdiqo:cmdiqo /app && \
    setcap cap_net_raw,cap_net_admin+eip /usr/bin/arp-scan || true

USER cmdiqo

CMD ["python", "-m", "bot.app"]
