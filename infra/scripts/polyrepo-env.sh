#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "Source infra/scripts/polyrepo-env.sh from a Deploy script." >&2
  exit 1
fi

: "${REPO_ROOT:?REPO_ROOT must point to the Deploy repository before loading infra/scripts/polyrepo-env.sh}"

deploy_parent="$(cd "${REPO_ROOT}/.." && pwd)"
case "$(basename "${deploy_parent}")" in
  platform|services)
    detected_workspace_root="$(cd "${deploy_parent}/.." && pwd)"
    ;;
  *)
    detected_workspace_root="${deploy_parent}"
    ;;
esac

KEROSENE_WORKSPACE_ROOT="${KEROSENE_WORKSPACE_ROOT:-${detected_workspace_root}}"

resolve_kerosene_repo() {
  local override="$1"
  local group="$2"
  local repository="$3"
  local local_directory="$4"
  local candidate

  if [[ -n "${override}" ]]; then
    printf '%s\n' "${override}"
    return
  fi

  for candidate in \
    "${KEROSENE_WORKSPACE_ROOT}/${group}/${repository}" \
    "${KEROSENE_WORKSPACE_ROOT}/${local_directory}" \
    "${KEROSENE_WORKSPACE_ROOT}/${repository}" \
    "${KEROSENE_WORKSPACE_ROOT}/${group}/${local_directory}"
  do
    if [[ -e "${candidate}/.git" ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done

  # Return the current flat-layout location so callers emit one actionable
  # error instead of silently falling back to the archived monorepo.
  printf '%s\n' "${KEROSENE_WORKSPACE_ROOT}/${local_directory}"
}

KEROSENE_DEPLOY_DIR="${REPO_ROOT}"
CORE_DIR="$(resolve_kerosene_repo "${KEROSENE_CORE_DIR:-}" services kerosene-core core)"
CLIENTS_DIR="$(resolve_kerosene_repo "${KEROSENE_CLIENTS_DIR:-}" platform kerosene-clients clients)"
VAULT_DIR="$(resolve_kerosene_repo "${KEROSENE_VAULT_DIR:-}" services kerosene-vault vault)"
NODE_DIR="$(resolve_kerosene_repo "${KEROSENE_NODE_DIR:-}" services kerosene-node node)"
CONTRACTS_DIR="$(resolve_kerosene_repo "${KEROSENE_CONTRACTS_DIR:-}" platform kerosene-contracts contracts)"
ADMIN_DIR="$(resolve_kerosene_repo "${KEROSENE_ADMIN_DIR:-}" platform kerosene-admin admin)"
RAILS_DIR="$(resolve_kerosene_repo "${KEROSENE_RAILS_DIR:-}" services kerosene-rails rails)"
KFE_DIR="$(resolve_kerosene_repo "${KEROSENE_KFE_DIR:-}" services kerosene-kfe kfe)"
SHARED_DIR="$(resolve_kerosene_repo "${KEROSENE_SHARED_DIR:-}" platform kerosene-shared shared)"
KEROSENE_CORE_DIR="${CORE_DIR}"
KEROSENE_CLIENTS_DIR="${CLIENTS_DIR}"
KEROSENE_VAULT_DIR="${VAULT_DIR}"
KEROSENE_NODE_DIR="${NODE_DIR}"
KEROSENE_CONTRACTS_DIR="${CONTRACTS_DIR}"
KEROSENE_ADMIN_DIR="${ADMIN_DIR}"
KEROSENE_RAILS_DIR="${RAILS_DIR}"
KEROSENE_KFE_DIR="${KFE_DIR}"
KEROSENE_SHARED_DIR="${SHARED_DIR}"

# Backward-compatible variable names used by existing Deploy helpers.
BACKEND_DIR="${KEROSENE_BACKEND_DIR:-${CORE_DIR}}"
FRONTEND_DIR="${KEROSENE_FRONTEND_DIR:-${CLIENTS_DIR}}"

export KEROSENE_WORKSPACE_ROOT CORE_DIR CLIENTS_DIR VAULT_DIR NODE_DIR CONTRACTS_DIR ADMIN_DIR RAILS_DIR KFE_DIR SHARED_DIR
export KEROSENE_DEPLOY_DIR
export KEROSENE_CORE_DIR KEROSENE_CLIENTS_DIR KEROSENE_VAULT_DIR
export KEROSENE_NODE_DIR KEROSENE_CONTRACTS_DIR
export KEROSENE_ADMIN_DIR KEROSENE_RAILS_DIR
export KEROSENE_KFE_DIR KEROSENE_SHARED_DIR
export BACKEND_DIR FRONTEND_DIR

require_kerosene_repo() {
  local label="$1"
  local directory="$2"

  if [[ ! -e "${directory}/.git" ]]; then
    echo "[infra][error] ${label} repository not found at ${directory}." >&2
    echo "[infra][error] Set KEROSENE_${label^^}_DIR explicitly or run inside the canonical workspace." >&2
    return 1
  fi
}
