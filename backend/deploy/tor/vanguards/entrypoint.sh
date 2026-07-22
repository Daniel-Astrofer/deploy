#!/bin/sh

set -eu

CONTROL_SOCKET="${VANGUARDS_CONTROL_SOCKET:-/var/run/tor/control/control}"
CONTROL_AUTH_TIMEOUT_SECS="${VANGUARDS_CONTROL_TIMEOUT_SECS:-90}"
READY_FILE="/tmp/vanguards-ready"

rm -f "$READY_FILE"

echo "==> Waiting for Tor control socket at $CONTROL_SOCKET ..."

python3 - <<'PY'
import os
import sys
import time

from stem.control import Controller

control_socket = os.environ.get("VANGUARDS_CONTROL_SOCKET", "/var/run/tor/control/control")
deadline = time.time() + int(os.environ.get("VANGUARDS_CONTROL_TIMEOUT_SECS", "90"))
last_error = None

while time.time() < deadline:
    try:
        with Controller.from_socket_file(path=control_socket) as controller:
            controller.authenticate()
        sys.exit(0)
    except Exception as exc:  # pragma: no cover - runtime path
        last_error = exc
        time.sleep(1)

print(f"FATAL: unable to authenticate to Tor control socket {control_socket}: {last_error}", file=sys.stderr)
sys.exit(1)
PY

touch "$READY_FILE"
echo "==> Tor control socket authenticated. Starting Vanguards..."

export VANGUARDS_CONFIG="${VANGUARDS_CONFIG:-/etc/vanguards/vanguards.conf}"
exec vanguards
