#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "Source infra/scripts/polyrepo-env.sh from a Deploy script." >&2
  exit 1
fi

: "${REPO_ROOT:?REPO_ROOT must point to kerosene-deploy before loading infra/scripts/polyrepo-env.sh}"

resolve_kerosene_repo() {
  local override="$1"
  local group="$2"
  local repository="$3"
  local flat_candidate="${REPO_ROOT}/../${repository}"
  local canonical_candidate="${REPO_ROOT}/../../${group}/${repository}"

  if [[ -n "${override}" ]]; then
    printf '%s\n' "${override}"
  elif [[ -d "${canonical_candidate}/.git" ]]; then
    printf '%s\n' "${canonical_candidate}"
  else
    printf '%s\n' "${flat_candidate}"
  fi
}

KEROSENE_WORKSPACE_ROOT="${KEROSENE_WORKSPACE_ROOT:-$(cd "${REPO_ROOT}/../.." 2>/dev/null && pwd || dirname "${REPO_ROOT}")}"
KEROSENE_DEPLOY_DIR="${REPO_ROOT}"
CORE_DIR="$(resolve_kerosene_repo "${KEROSENE_CORE_DIR:-}" services kerosene-core)"
CLIENTS_DIR="$(resolve_kerosene_repo "${KEROSENE_CLIENTS_DIR:-}" platform kerosene-clients)"
VAULT_DIR="$(resolve_kerosene_repo "${KEROSENE_VAULT_DIR:-}" services kerosene-vault)"
NODE_DIR="$(resolve_kerosene_repo "${KEROSENE_NODE_DIR:-}" services kerosene-node)"
CONTRACTS_DIR="$(resolve_kerosene_repo "${KEROSENE_CONTRACTS_DIR:-}" platform kerosene-contracts)"
KEROSENE_CORE_DIR="${CORE_DIR}"
KEROSENE_CLIENTS_DIR="${CLIENTS_DIR}"
KEROSENE_VAULT_DIR="${VAULT_DIR}"
KEROSENE_NODE_DIR="${NODE_DIR}"
KEROSENE_CONTRACTS_DIR="${CONTRACTS_DIR}"

# Backward-compatible variable names used by existing Deploy helpers.
BACKEND_DIR="${KEROSENE_BACKEND_DIR:-${CORE_DIR}}"
FRONTEND_DIR="${KEROSENE_FRONTEND_DIR:-${CLIENTS_DIR}}"

export KEROSENE_WORKSPACE_ROOT CORE_DIR CLIENTS_DIR VAULT_DIR NODE_DIR CONTRACTS_DIR
export KEROSENE_DEPLOY_DIR
export KEROSENE_CORE_DIR KEROSENE_CLIENTS_DIR KEROSENE_VAULT_DIR
export KEROSENE_NODE_DIR KEROSENE_CONTRACTS_DIR
export BACKEND_DIR FRONTEND_DIR

require_kerosene_repo() {
  local label="$1"
  local directory="$2"

  if [[ ! -d "${directory}/.git" ]]; then
    echo "[infra][error] ${label} repository not found at ${directory}." >&2
    echo "[infra][error] Set KEROSENE_${label^^}_DIR explicitly or run inside the canonical workspace." >&2
    return 1
  fi
}
