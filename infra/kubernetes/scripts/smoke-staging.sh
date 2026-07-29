#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "$SCRIPT_DIR/smoke-staging-login.sh"

echo "[+] Core staging smoke suite passed (Vault quorum is an independent gate)."
