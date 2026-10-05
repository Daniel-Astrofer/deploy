#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
deployment="${repo_root}/infra/kubernetes/base/server/deployment.yaml"
server_configmap="${repo_root}/infra/kubernetes/base/server/configmap.yaml"
kfe_configmap="${repo_root}/infra/kubernetes/base/kfe-service/configmap.yaml"
base_directory="${repo_root}/infra/kubernetes/base"
images="${repo_root}/infra/docker/images.yaml"
vault_dockerfile="${repo_root}/infra/docker/images/kerosene-vault/Dockerfile"
web_nginx="${repo_root}/infra/runtime/web/nginx.k8s.conf"

test -f "${deployment}"
test -f "${server_configmap}"
test -f "${kfe_configmap}"
test -f "${images}"
test -f "${vault_dockerfile}"
test -f "${web_nginx}"

grep -q 'SPRING_PROFILES_ACTIVE: "prod"' "${server_configmap}" "${deployment}"
grep -q 'name: SPRING_DATASOURCE_URL' "${server_configmap}" "${deployment}"
grep -q 'name: SPRING_DATASOURCE_USERNAME' "${server_configmap}" "${deployment}"
grep -q 'name: SPRING_DATASOURCE_PASSWORD' "${server_configmap}" "${deployment}"
grep -q 'SPRING_DATA_REDIS_HOST' "${server_configmap}" "${deployment}"
grep -q 'name: SPRING_DATA_REDIS_PASSWORD' "${server_configmap}" "${deployment}"
grep -q 'LIGHTNING_LND_HOST' "${server_configmap}" "${deployment}"
grep -q 'name: LIGHTNING_LND_MACAROON' "${server_configmap}" "${deployment}"

if grep -Eq 'SPRING_PROFILES_ACTIVE: "production"|VAULT_RAFT_URL|MPC_SIDECAR_HOST|name: (POSTGRES_URL|REDIS_HOST|LND_HOST|LND_MACAROON_HEX)' \
  "${server_configmap}" "${deployment}"; then
  echo "Deployment contains a forbidden legacy environment variable."
  exit 1
fi

for configmap in "${server_configmap}" "${kfe_configmap}"; do
  if ! grep -q 'BITCOIN_NETWORK: "testnet3"' "${configmap}"; then
    echo "Base runtime must select Bitcoin testnet3 explicitly: ${configmap}" >&2
    exit 1
  fi
  grep -q 'BITCOIN_ZMQ_RAWTX: "tcp://bitcoin-core:28333"' "${configmap}"
  grep -q 'BITCOIN_ZMQ_HASHBLOCK: "tcp://bitcoin-core:28332"' "${configmap}"
done

if grep -RInE \
  --include='*.yaml' \
  'BITCOIN_NETWORK:[[:space:]]*"?(mainnet|regtest|signet)"?|(^|[^0-9])833[23]([^0-9]|$)' \
  "${base_directory}"
then
  echo "Base runtime contains a forbidden Bitcoin network or mainnet port." >&2
  exit 1
fi

if grep -Eiq 'ATTESTATION_MODE[=:][[:space:]]*sim|CARGO_FEATURES|dealer_lab|static_token' \
  "${vault_dockerfile}"; then
  echo "Vault image exposes a laboratory or mutable feature default." >&2
  exit 1
fi

grep -q '^USER 65532:65532$' "${vault_dockerfile}"

for obsolete_vault_resource in \
  vault-deployment.yaml \
  vault-service.yaml \
  vault-servicemonitor.yaml
do
  if [[ -e "${base_directory}/${obsolete_vault_resource}" ]]; then
    echo "Public base contains obsolete Vault resource: ${obsolete_vault_resource}" >&2
    exit 1
  fi
done

if grep -Eiq 'localhost|127\.0\.0\.1' "${web_nginx}"; then
  echo "Production web policy contains a loopback exception." >&2
  exit 1
fi

if grep -Eq 'mpc-sidecar:|backend/mpc-sidecar' "${images}"; then
  echo "Deployment contains a removed mpc-sidecar reference."
  exit 1
fi

if grep -RInE \
  --exclude-dir=.git \
  --exclude-dir=tests \
  --exclude='*.bak' \
  --exclude='check_architecture_guardrails.sh' \
  '(/home/[^/]+/Kerosene|\\$REPO_ROOT/(backend/kerosene(-vault)?|frontend)|\\.\\./\\.\\./\\.\\./backend/kerosene-vault)' \
  "${repo_root}/infra"
then
  echo "Deploy contains a forbidden monorepo source path."
  exit 1
fi

echo "Deployment architecture guardrails passed."
