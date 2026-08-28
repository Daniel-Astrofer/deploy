#!/usr/bin/env bash
set -euo pipefail

# LEGACY COMPATIBILITY WRAPPER.
# Use infra/kubernetes/scripts/validate-local-ha.sh explicitly.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$ROOT/infra/kubernetes/scripts/validate-local-ha.sh" "$@"
