#!/usr/bin/env bash
set -euo pipefail

# LEGACY COMPATIBILITY WRAPPER.
# New local automation should use infra/start.sh. Environment-specific
# automation may call infra/kubernetes/scripts/deploy.sh explicitly.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

exec "$REPO_ROOT/infra/kubernetes/deploy.sh" "$@"
