#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export KEROSENE_INFRA_DIR="${KEROSENE_INFRA_DIR:-$ROOT/infra}"
export KEROSENE_COMPOSE_FILE="${KEROSENE_COMPOSE_FILE:-$ROOT/infra/docker/compose/local.compose.yaml}"
export KEROSENE_COMPOSE_LIMITS_FILE="${KEROSENE_COMPOSE_LIMITS_FILE:-$ROOT/infra/docker/compose/local.limits.compose.yaml}"
KFE_COMPOSE_FILE="${KEROSENE_KFE_COMPOSE_FILE:-$ROOT/infra/docker/compose/local.kfe.compose.yaml}"

# shellcheck source=scripts/backend-common.sh
source "$ROOT/scripts/backend-common.sh"

require_docker
load_backend_env
detect_compose
require_file "$KEROSENE_COMPOSE_FILE"
require_file "$KEROSENE_COMPOSE_LIMITS_FILE"
require_file "$KFE_COMPOSE_FILE"

compose_files=(-f "$KEROSENE_COMPOSE_FILE")
if [[ "${KEROSENE_COMPOSE_RESOURCE_LIMITS:-1}" != "0" ]]; then
  compose_files+=(-f "$KEROSENE_COMPOSE_LIMITS_FILE")
fi
compose_files+=(-f "$KFE_COMPOSE_FILE")

COMPOSE_PARALLEL_LIMIT="$COMPOSE_PARALLEL_LIMIT" \
COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" "${COMPOSE_CMD[@]}" \
  --project-name "$COMPOSE_PROJECT_NAME" \
  --env-file "$ENV_FILE" \
  "${compose_files[@]}" \
  "$@"
