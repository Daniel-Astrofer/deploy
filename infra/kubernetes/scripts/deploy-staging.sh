#!/usr/bin/env bash
set -euo pipefail
# LEGACY COMPATIBILITY WRAPPER. CI may call deploy.sh staging directly.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/deploy.sh" staging "$@"
