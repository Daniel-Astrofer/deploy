#!/bin/sh
# Kerosene Tor Entrypoint
# Verifies the packaged Tor binary, prepares strict runtime permissions and
# starts the daemon.

set -e

TOR_LOG_FILE="/tmp/tor.log"
TOR_READY_FILE="/tmp/tor-ready"

command -v tor >/dev/null 2>&1 || {
    echo "Tor is missing from the immutable runtime image." >&2
    exit 1
}

echo "==> Auditing Tor binary integrity (Anti Tor-Inside Attacker)..."
TOR_BIN="/usr/bin/tor"
ACTUAL_HASH=$(sha256sum "$TOR_BIN" | awk '{print $1}')

PACKAGED_TOR_HASH="$(cat /usr/local/share/tor.sha256 2>/dev/null || true)"
REQUIRED_TOR_HASH="${EXPECTED_TOR_HASH:-$PACKAGED_TOR_HASH}"
if [ -n "$REQUIRED_TOR_HASH" ]; then
    if [ "$ACTUAL_HASH" != "$REQUIRED_TOR_HASH" ]; then
        echo "=========================================================="
        echo "CRITICAL SECURITY ALERT: Tor binary hash mismatch!"
        echo "Expected: $REQUIRED_TOR_HASH"
        echo "Actual  : $ACTUAL_HASH"
        echo "=========================================================="
        echo "The binary may be compromised. Halting container execution to protect keys."
        exit 1
    else
        echo "==> Tor binary integrity verified successfully."
    fi
else
    echo "The packaged Tor integrity reference is missing." >&2
    exit 1
fi

# Tor 0.4.7 requires a NAMED user in /etc/passwd for the "User" directive.
# Numeric UIDs are NOT supported. We create a "kerosene" system user with
# UID 65532 to match the Distroless non-root user in the app containers.
if ! id kerosene >/dev/null 2>&1; then
    groupadd -g 65532 kerosene 2>/dev/null || true
    useradd -r -u 65532 -g 65532 -M -s /usr/sbin/nologin kerosene 2>/dev/null || true
fi

# Tor requires 0700 on its HiddenServiceDir, but the SOCKS socket directory
# must be accessible by the Java app containers (UID 1000) via shared Docker volume.
# We keep HiddenServiceDir strict and relax only the SOCKS socket path.
# NOTE: Some subdirs (authorized_clients, onion_auth) may be read-only Docker
# volume mounts. We must NOT fail on chown/chmod for those.
mkdir -p /var/run/tor/socks
mkdir -p /var/run/tor/control
mkdir -p /var/lib/tor/kerosene_service/authorized_clients
# chown only top-level — avoid recursing into read-only mounted subdirs
chown kerosene:kerosene /var/run/tor /var/run/tor/socks /var/run/tor/control 2>/dev/null || true
chown kerosene:kerosene /var/lib/tor 2>/dev/null || true
chown kerosene:kerosene /var/lib/tor/kerosene_service 2>/dev/null || true
chown kerosene:kerosene /var/lib/tor/kerosene_service/authorized_clients 2>/dev/null || true
chmod 700 /var/lib/tor/kerosene_service 2>/dev/null || true
chmod 755 /var/run/tor/socks 2>/dev/null || true
chmod 750 /var/run/tor/control 2>/dev/null || true

# Authorized clients (stealth) — optional gate
if [ "${VAULT_TOR_AUTH_CLIENTS:-false}" = "true" ]; then
  echo "==> Tor stealth auth enabled (VAULT_TOR_AUTH_CLIENTS=true)"
  if ls /var/lib/tor/kerosene_service/authorized_clients/*.auth >/dev/null 2>&1; then
    echo "==> Found .auth files:"
    ls -la /var/lib/tor/kerosene_service/authorized_clients/*.auth
    echo "==> The torrc must contain: HiddenServiceAuthorizeClient stealth kerosene_service"
  else
    echo "[!] VAULT_TOR_AUTH_CLIENTS=true but no .auth files found." >&2
    echo "[!] Generate with kerosene-vault/scripts/gen_tor_auth_clients.sh from the Vault repository." >&2
    echo "[!] Continuing with v3 public onion (no client auth)." >&2
  fi
fi

echo "==> Starting Tor hidden service for Kerosene..."
echo "==> Once connected, .onion address will be in:"
echo "    /var/lib/tor/kerosene_service/hostname"
echo ""

# OnionBalance features removed for Push-Beaconing Architecture

rm -f "$TOR_READY_FILE" "$TOR_LOG_FILE"
touch "$TOR_LOG_FILE"

# Stream Tor logs to container stdout while also preserving them for readiness checks.
tail -n +1 -F "$TOR_LOG_FILE" &
TAIL_PID=$!

# Tor will drop privileges to 65532 (User option in torrc) after starting as root.
tor -f /etc/tor/torrc >"$TOR_LOG_FILE" 2>&1 &
TOR_PID=$!

echo "==> Waiting for Tor to establish UDS socket..."
# Only wait for socket if torrc configures a UDS SocksPort (vault has SocksPort 0)
if grep -q 'SocksPort unix:' /etc/tor/torrc; then
  while [ ! -S /var/run/tor/socks/tor.sock ]; do
    sleep 1
  done
  echo "==> UDS socket ready."
fi

echo "==> Waiting for Tor bootstrap to reach 100%..."
while ! grep -q 'Bootstrapped 100% (done): Done' "$TOR_LOG_FILE"; do
  if ! kill -0 "$TOR_PID" 2>/dev/null; then
    wait "$TOR_PID"
    exit $?
  fi
  sleep 1
done
touch "$TOR_READY_FILE"
echo "==> Tor bootstrap complete."

wait "$TOR_PID"
TOR_EXIT_CODE=$?
kill "$TAIL_PID" 2>/dev/null || true
wait "$TAIL_PID" 2>/dev/null || true
rm -f "$TOR_READY_FILE"
exit "$TOR_EXIT_CODE"
