# ═══════════════════════════════════════════════════════════════════════════════
# Kerosene Tor Node Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
# Provides a Tor hidden service sidecar for all shard nodes (IS, CH, SG, Vault).
# Pre-installs Tor at build time to avoid apt-get on every container startup.
# The entrypoint.sh handles permissions, binary integrity check, and tor startup.
# ═══════════════════════════════════════════════════════════════════════════════

FROM debian:bookworm-slim

# Install Tor and clean apt cache in a single layer
RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tor && \
    rm -rf /var/lib/apt/lists/*

# Create kerosene system user (UID 65532) matching distroless app containers.
# Tor requires a NAMED user (not numeric UID) in the User directive.
RUN groupadd -g 65532 kerosene && \
    useradd -r -u 65532 -g 65532 -M -s /usr/sbin/nologin kerosene

# Tor directories (will be overridden by volumes at runtime)
RUN mkdir -p /var/run/tor/socks /var/lib/tor/kerosene_service/authorized_clients && \
    chown -R kerosene:kerosene /var/run/tor /var/lib/tor && \
    chmod 700 /var/lib/tor/kerosene_service && \
    chmod 755 /var/run/tor/socks

# The entrypoint.sh and torrc are mounted as read-only volumes at runtime
# by docker-compose so we don't COPY them here — they can be edited without rebuild.

# Must run as root initially so Tor can drop privs to kerosene user internally
USER root

ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
