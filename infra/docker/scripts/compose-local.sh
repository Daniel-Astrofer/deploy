#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export KEROSENE_INFRA_DIR="${KEROSENE_INFRA_DIR:-$ROOT/infra}"
export KEROSENE_COMPOSE_FILE="${KEROSENE_COMPOSE_FILE:-$ROOT/infra/docker/compose/local.compose.yaml}"
export KEROSENE_COMPOSE_LIMITS_FILE="${KEROSENE_COMPOSE_LIMITS_FILE:-$ROOT/infra/docker/compose/local.limits.compose.yaml}"

# shellcheck source=infra/scripts/backend-common.sh
source "$ROOT/infra/scripts/backend-common.sh"

require_docker
load_backend_env
compose "$@"
