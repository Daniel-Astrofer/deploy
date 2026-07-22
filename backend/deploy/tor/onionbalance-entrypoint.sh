#!/bin/sh
# OnionBalance Entrypoint for Kerosene Hydra
set -e

# Install OnionBalance and netcat if not present
if ! command -v onionbalance > /dev/null 2>&1 || ! command -v nc > /dev/null 2>&1; then
    echo "==> Installing requirements (OnionBalance, netcat)..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -o Dpkg::Options::="--force-confold" onionbalance netcat-openbsd
fi

CONFIG_PATH="/tmp/onionbalance.yaml"
MASTER_KEY_DIR="/var/lib/onionbalance/master"
BACKENDS_DIR="/var/lib/tor/backends"

echo "==> OnionBalance: Waiting for regional backends to generate hostnames..."

# Wait for all 3 backend hostnames to be available
while [ ! -f "$BACKENDS_DIR/se/hostname" ] || [ ! -f "$BACKENDS_DIR/ee/hostname" ] || [ ! -f "$BACKENDS_DIR/is/hostname" ]; do
    sleep 2
done

ONION_SE=$(cat "$BACKENDS_DIR/se/hostname")
ONION_EE=$(cat "$BACKENDS_DIR/ee/hostname")
ONION_IS=$(cat "$BACKENDS_DIR/is/hostname")

echo "==> Detected Backends:"
echo "    SE: $ONION_SE"
echo "    EE: $ONION_EE"
echo "    IS: $ONION_IS"

# Generate OnionBalance Config
cat <<EOF > "$CONFIG_PATH"
# OnionBalance v3 Configuration
services:
  - key: $MASTER_KEY_DIR/hs_ed25519_secret_key
    ports:
      - 80: 80
    instances:
      - address: $ONION_SE
      - address: $ONION_EE
      - address: $ONION_IS
EOF

# Ensure master key has correct permissions if it exists
if [ -f "$MASTER_KEY_DIR/hs_ed25519_secret_key" ]; then
    chmod 600 "$MASTER_KEY_DIR/hs_ed25519_secret_key"
else
    echo "!! WARNING: Master secret key not found in $MASTER_KEY_DIR"
    echo "!! Please place your hs_ed25519_secret_key there."
fi

echo "==> Starting local Tor for OnionBalance management..."
# Ensure a clean but persistent state
mkdir -p /tmp/tor-ob-data
chmod 700 /tmp/tor-ob-data

# Optimized torrc for stable connectivity
cat <<EOF > /tmp/torrc-ob
ControlPort 9051
CookieAuthentication 0
DataDirectory /tmp/tor-ob-data
Log notice stdout
FetchUselessDescriptors 1
# Allow Tor to learn network delays for better stability
LearnCircuitBuildTimeout 1
# Standard circuit build timeout (default is usually 60s, but we'll set 45s for responsiveness)
CircuitBuildTimeout 45
# Entry nodes settings
UseEntryGuards 1
NumEntryGuards 3
# Performance tweaks
LongLivedPorts 80,443
SocksPort 9050
EOF

# Start Tor in background
tor -f /tmp/torrc-ob &
TOR_PID=$!

# Status check
echo "==> Tor Stabilization phase... Checking status..."
python3 -c "
import socket
import time
import sys

def get_info(s, key):
    try:
        s.send(f'GETINFO {key}\n'.encode())
        res = b''
        while True:
            chunk = s.recv(4096)
            res += chunk
            if b'250 OK' in chunk or b'510' in chunk or b'515' in chunk:
                break
        return res.decode()
    except: return ''

timeout = 60
start = time.time()
while time.time() - start < timeout:
    try:
        with socket.create_connection(('127.0.0.1', 9051), timeout=2) as s:
            s.send(b'AUTHENTICATE \"\"\n')
            if b'250 OK' in s.recv(1024):
                status = get_info(s, 'status/bootstrap-phases')
                if 'PROGRESS=100' in status or 'TAG=done' in status:
                    print('==> Tor Ready and Stable!', flush=True)
                    sys.exit(0)
    except: pass
    time.sleep(2)
"

echo "==> Starting OnionBalance (ULTRA-STABLE mode)..."
# Using -v info for clean logs but reliable routing
exec onionbalance -v info -c "$CONFIG_PATH" --ip 127.0.0.1 --port 9051
