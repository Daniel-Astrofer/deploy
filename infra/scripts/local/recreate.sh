#!/usr/bin/env bash
set -euo pipefail
# LEGACY COMPOSE WRAPPER. Canonical entrypoint: infra/recreate.sh.
LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$LOCAL_SCRIPT_DIR/control.sh" recreate "$@"
