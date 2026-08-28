#!/usr/bin/env bash
set -euo pipefail

# LEGACY COMPOSE WRAPPER.
# Use infra/logs.sh for the canonical local-full runtime.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/logs-local.sh" kerosene-vault "$@"
