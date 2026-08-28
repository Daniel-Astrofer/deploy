#!/usr/bin/env bash
set -euo pipefail

# LEGACY COMPATIBILITY WRAPPER.
# Use wait-local-full.sh for the local-full environment.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec bash "$SCRIPT_DIR/wait-local-full.sh" "$@"
