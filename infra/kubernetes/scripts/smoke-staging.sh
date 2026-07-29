#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "$SCRIPT_DIR/smoke-staging-frost-quorum.sh"
bash "$SCRIPT_DIR/smoke-staging-login.sh"

echo "[+] Canonical staging smoke suite passed."
