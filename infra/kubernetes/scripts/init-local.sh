#!/usr/bin/env bash
set -euo pipefail
# LEGACY COMPOSE WRAPPER.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
exec bash "$REPO_ROOT/infra/scripts/local/init.sh" "$@"
