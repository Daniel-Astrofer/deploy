#!/usr/bin/env bash
set -euo pipefail
# LEGACY COMPOSE WRAPPER. Use infra/recreate.sh for the canonical runtime.
LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$LOCAL_SCRIPT_DIR/control.sh" restart "$@"
