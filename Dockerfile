FROM python:3.12-alpine

# docker-cli + compose plugin  -> manage host docker via /var/run/docker.sock
# arp-scan + iproute2          -> LAN device discovery
# bash                         -> handy for custom shell commands
# libcap                       -> enable non-root capabilities for arp-scan
RUN apk add --no-cache \
        docker-cli docker-cli-compose \
        arp-scan iproute2 libcap \
        bash

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY config ./config

ENV COMMANDS_CONFIG=/app/config/commands.yaml \
    PYTHONUNBUFFERED=1

# SonarQube S6437: Non-root container execution
# Create non-root user and grant raw network capabilities for arp-scan
RUN addgroup -g 1000 shango && \
    adduser -u 1000 -G shango -s /bin/sh -D shango && \
    chown -R shango:shango /app && \
    setcap cap_net_raw,cap_net_admin+eip /usr/bin/arp-scan || true

USER shango

CMD ["python", "-m", "bot.app"]
