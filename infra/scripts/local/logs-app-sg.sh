#!/usr/bin/env bash
set -euo pipefail
LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$LOCAL_SCRIPT_DIR/control.sh" logs kerosene-app-sg "$@"
