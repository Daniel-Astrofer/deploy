#!/usr/bin/env bash
set -euo pipefail

# LEGACY COMPATIBILITY WRAPPER.
# Use infra/kubernetes/scripts/import-local-docker-images.sh explicitly.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$ROOT/infra/kubernetes/scripts/import-local-docker-images.sh" "$@"
