#!/usr/bin/env bash
# Independent script to view logs for kerosene-vault
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/logs-local.sh" kerosene-vault "$@"
