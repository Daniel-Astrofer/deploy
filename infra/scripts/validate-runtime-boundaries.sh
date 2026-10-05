#!/usr/bin/env bash
# Static boundary checks for deployment inputs. This intentionally does not
# contact production or start services, so it is safe in CI and pre-commit.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
infra_root="$(cd "${script_dir}/.." && pwd)"

require_file() {
  [[ -f "$1" ]] || { echo "missing deployment input: $1" >&2; exit 1; }
}

base="${infra_root}/kubernetes/base/server"
require_file "${base}/deployment.yaml"
require_file "${base}/configmap.yaml"
require_file "${infra_root}/docker/images.yaml"

# Deployment files may reference runtime secrets, but must never embed values
# or reintroduce retired service names.
if grep -RInE --exclude-dir=.git \
  '(^|[[:space:]])(PASSWORD|TOKEN|MACAROON|PRIVATE_KEY|SECRET)[=:][^$[:space:]{}]+' \
  "${base}"; then
  echo "deployment contains a literal secret value" >&2
  exit 1
fi

if grep -RInE --include='*.yaml' --include='*.yml' --include='*.env*' \
  'VAULT_RAFT_URL|MPC_SIDECAR_HOST|backend/mpc-sidecar' "${infra_root}/kubernetes" "${infra_root}/docker"; then
  echo "deployment references a retired boundary" >&2
  exit 1
fi

echo "Runtime boundary checks passed."
