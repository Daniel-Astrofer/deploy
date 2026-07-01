#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

exec bash "$ROOT/infra/kubernetes/scripts/import-local-docker-images.sh" "$@"
