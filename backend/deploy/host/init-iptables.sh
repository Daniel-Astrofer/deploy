#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Kerosene Host iptables Egress Guard
#
# Purpose: OS-level supply-chain attack mitigation.
#          Restricts outbound TCP from Docker containers to only known-good hosts.
#          Replacement for the deprecated Java SecurityManager approach.
#
# Run:  sudo bash deploy/host/init-iptables.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DOCKER_BRIDGE="172.20.0.0/16"     # Kerosene internal Docker bridge (see docker-compose networks)
DOCKER_BRIDGE2="172.21.0.0/16"    # Secondary bridge for vault network
TOR_PORT=9050                      # Tor SOCKS5 (only via kerosene-tor containers)

echo "[iptables] Setting up Kerosene egress guard..."

# ── INPUT: Accept established connections (responses to our requests)
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -i lo -j ACCEPT

# ── OUTPUT: Default DROP — then carve out allowlist
iptables -P OUTPUT DROP

# Loopback always allowed
iptables -A OUTPUT -o lo -j ACCEPT

# Docker internal networks (Postgres, Redis, MPC, Tor sidecars)
iptables -A OUTPUT -d "$DOCKER_BRIDGE"    -j ACCEPT
iptables -A OUTPUT -d "$DOCKER_BRIDGE2"   -j ACCEPT

# Established/related (TCP response packets)
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# DNS — restricted to Docker's embedded resolver ONLY (127.0.0.11).
# Allows containers to resolve internal hostnames (kerosene_db_is, etc.).
# BLOCKS all DNS to external servers, preventing:
#   - DNS tunneling data exfiltration (stolen-data.attacker.com)
#   - IP leakage to ISP/public DNS when resolving internal names
#   - Bypass of Tor for .onion resolution
iptables -A OUTPUT -p udp -d 127.0.0.11 --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp -d 127.0.0.11 --dport 53 -j ACCEPT

# Log and DROP everything else
iptables -A OUTPUT -j LOG --log-prefix "[KEROSENE EGRESS BLOCKED] " --log-level 4
iptables -A OUTPUT -j DROP

echo "[iptables] Egress guard installed. Summary:"
iptables -L OUTPUT -n --line-numbers

# ── Persist rules across reboots (Debian/Ubuntu)
if command -v iptables-save &>/dev/null; then
    iptables-save > /etc/iptables/rules.v4
    echo "[iptables] Rules persisted to /etc/iptables/rules.v4"
fi

echo "[iptables] Done. All outbound traffic not destined for Docker networks is BLOCKED."
