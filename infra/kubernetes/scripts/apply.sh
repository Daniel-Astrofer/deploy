#!/usr/bin/env bash
set -euo pipefail

# LEGACY COMPATIBILITY WRAPPER. Use infra/start.sh for local-full.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec bash "$SCRIPT_DIR/deploy-local-full.sh" "$@"
