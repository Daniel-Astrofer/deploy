#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$ROOT/backend/kerosene-infrastructure/k8s/scripts/validate-local-ha.sh" "$@"
