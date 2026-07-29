#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
deployment="${repo_root}/infra/kubernetes/base/server/deployment.yaml"
configmap="${repo_root}/infra/kubernetes/base/server/configmap.yaml"
images="${repo_root}/infra/docker/images.yaml"
vault_compose="${repo_root}/infra/docker/compose/vault-mesh-lab.compose.yaml"

test -f "${deployment}"
test -f "${configmap}"
test -f "${images}"
test -f "${vault_compose}"

grep -q 'SPRING_PROFILES_ACTIVE: "prod"' "${configmap}" "${deployment}"
grep -q 'name: SPRING_DATASOURCE_URL' "${configmap}" "${deployment}"
grep -q 'name: SPRING_DATASOURCE_USERNAME' "${configmap}" "${deployment}"
grep -q 'name: SPRING_DATASOURCE_PASSWORD' "${configmap}" "${deployment}"
grep -q 'SPRING_DATA_REDIS_HOST' "${configmap}" "${deployment}"
grep -q 'name: SPRING_DATA_REDIS_PASSWORD' "${configmap}" "${deployment}"
grep -q 'LIGHTNING_LND_HOST' "${configmap}" "${deployment}"
grep -q 'name: LIGHTNING_LND_MACAROON' "${configmap}" "${deployment}"

if grep -Eq 'SPRING_PROFILES_ACTIVE: "production"|VAULT_RAFT_URL|MPC_SIDECAR_HOST|name: (POSTGRES_URL|REDIS_HOST|LND_HOST|LND_MACAROON_HEX)' \
  "${configmap}" "${deployment}"; then
  echo "Deployment contains a forbidden legacy environment variable."
  exit 1
fi

if grep -Eq 'mpc-sidecar:|backend/mpc-sidecar' "${images}" "${vault_compose}"; then
  echo "Deployment contains a removed mpc-sidecar reference."
  exit 1
fi

grep -Eq 'kerosene-vault|vault-1' "${vault_compose}"
echo "Deployment architecture guardrails passed."
