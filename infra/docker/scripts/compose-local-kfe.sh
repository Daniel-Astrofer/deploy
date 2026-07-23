#!/usr/bin/env bash
set -euo pipefail

# Legacy local.kfe.compose.yaml (mpc-sidecar shards) was removed.
# Settlement compose is vault-mesh-lab; KFE runs via local-full / kfe-service image.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export KEROSENE_INFRA_DIR="${KEROSENE_INFRA_DIR:-$ROOT/infra}"
export KEROSENE_COMPOSE_FILE="${KEROSENE_COMPOSE_FILE:-$ROOT/infra/docker/compose/vault-mesh-lab.compose.yaml}"

# shellcheck source=infra/scripts/backend-common.sh
source "$ROOT/infra/scripts/backend-common.sh"

require_docker
load_backend_env
echo "[i] compose-local-kfe.sh now aliases vault-mesh-lab (mpc-sidecar compose removed)." >&2
compose "$@"
