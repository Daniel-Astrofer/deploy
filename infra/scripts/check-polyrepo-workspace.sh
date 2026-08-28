#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=infra/scripts/polyrepo-env.sh
source "${SCRIPT_DIR}/polyrepo-env.sh"

require_kerosene_repo core "${CORE_DIR}"
require_kerosene_repo clients "${CLIENTS_DIR}"
require_kerosene_repo vault "${VAULT_DIR}"
require_kerosene_repo node "${NODE_DIR}"
require_kerosene_repo contracts "${CONTRACTS_DIR}"
require_kerosene_repo admin "${ADMIN_DIR}"
require_kerosene_repo rails "${RAILS_DIR}"
require_kerosene_repo kfe "${KFE_DIR}"
require_kerosene_repo shared "${SHARED_DIR}"

for repository_dir in \
  "${CORE_DIR}" \
  "${CLIENTS_DIR}" \
  "${VAULT_DIR}" \
  "${NODE_DIR}" \
  "${CONTRACTS_DIR}" \
  "${ADMIN_DIR}" \
  "${RAILS_DIR}" \
  "${KFE_DIR}" \
  "${SHARED_DIR}" \
  "${REPO_ROOT}"
do
  git -C "${repository_dir}" rev-parse --is-inside-work-tree >/dev/null
  git -C "${repository_dir}" remote get-url origin >/dev/null
done

echo "Kerosene polyrepo workspace is valid."
