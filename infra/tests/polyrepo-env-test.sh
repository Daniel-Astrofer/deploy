#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUBJECT="${REPO_ROOT}/infra/scripts/polyrepo-env.sh"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf -- "${TMP_DIR}"
}
trap cleanup EXIT

fail() {
  echo "[FAIL] $*" >&2
  exit 1
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  [[ "${actual}" == "${expected}" ]] || fail "${label}: expected ${expected}, got ${actual}"
}

clear_polyrepo_env() {
  unset KEROSENE_WORKSPACE_ROOT KEROSENE_DEPLOY_DIR
  unset KEROSENE_CORE_DIR KEROSENE_CLIENTS_DIR KEROSENE_VAULT_DIR
  unset KEROSENE_NODE_DIR KEROSENE_CONTRACTS_DIR KEROSENE_ADMIN_DIR
  unset KEROSENE_RAILS_DIR KEROSENE_KFE_DIR KEROSENE_SHARED_DIR
  unset KEROSENE_BACKEND_DIR KEROSENE_FRONTEND_DIR
  unset CORE_DIR CLIENTS_DIR VAULT_DIR NODE_DIR CONTRACTS_DIR
  unset ADMIN_DIR RAILS_DIR KFE_DIR SHARED_DIR BACKEND_DIR FRONTEND_DIR
}

make_repository() {
  mkdir -p "$1/.git"
}

test_flat_short_layout() (
  clear_polyrepo_env
  local workspace="${TMP_DIR}/flat-short"
  local name
  for name in deploy core clients vault node contracts admin rails kfe shared; do
    make_repository "${workspace}/${name}"
  done

  REPO_ROOT="${workspace}/deploy"
  source "${SUBJECT}"

  assert_eq "${workspace}" "${KEROSENE_WORKSPACE_ROOT}" "flat workspace root"
  assert_eq "${workspace}/core" "${CORE_DIR}" "flat core"
  assert_eq "${workspace}/contracts" "${CONTRACTS_DIR}" "flat contracts"
  assert_eq "${workspace}/shared" "${SHARED_DIR}" "flat shared"
)

test_flat_prefixed_layout() (
  clear_polyrepo_env
  local workspace="${TMP_DIR}/flat-prefixed"
  local name
  for name in deploy core clients vault node contracts admin rails kfe shared; do
    make_repository "${workspace}/kerosene-${name}"
  done

  REPO_ROOT="${workspace}/kerosene-deploy"
  source "${SUBJECT}"

  assert_eq "${workspace}" "${KEROSENE_WORKSPACE_ROOT}" "prefixed workspace root"
  assert_eq "${workspace}/kerosene-core" "${CORE_DIR}" "prefixed core"
  assert_eq "${workspace}/kerosene-contracts" "${CONTRACTS_DIR}" "prefixed contracts"
)

test_grouped_layout() (
  clear_polyrepo_env
  local workspace="${TMP_DIR}/grouped"
  local name
  for name in core kfe rails node vault; do
    make_repository "${workspace}/services/kerosene-${name}"
  done
  for name in clients contracts shared admin deploy; do
    make_repository "${workspace}/platform/kerosene-${name}"
  done

  REPO_ROOT="${workspace}/platform/kerosene-deploy"
  source "${SUBJECT}"

  assert_eq "${workspace}" "${KEROSENE_WORKSPACE_ROOT}" "grouped workspace root"
  assert_eq "${workspace}/services/kerosene-core" "${CORE_DIR}" "grouped core"
  assert_eq "${workspace}/platform/kerosene-contracts" "${CONTRACTS_DIR}" "grouped contracts"
)

test_override_and_worktree_marker() (
  clear_polyrepo_env
  local workspace="${TMP_DIR}/override"
  local override="${TMP_DIR}/linked-core"
  mkdir -p "${workspace}/deploy/.git" "${override}"
  : > "${override}/.git"

  REPO_ROOT="${workspace}/deploy"
  KEROSENE_CORE_DIR="${override}"
  source "${SUBJECT}"

  assert_eq "${override}" "${CORE_DIR}" "explicit override"
  require_kerosene_repo core "${CORE_DIR}"
)

test_flat_renamed_services_layout() (
  clear_polyrepo_env
  local workspace="${TMP_DIR}/flat-renamed"
  local name
  for name in deploy users-authentication clients vault discoveryng-node contracts server-administration financial-rails krinse-engine shared; do
    make_repository "${workspace}/${name}"
  done

  REPO_ROOT="${workspace}/deploy"
  source "${SUBJECT}"

  assert_eq "${workspace}" "${KEROSENE_WORKSPACE_ROOT}" "renamed workspace root"
  assert_eq "${workspace}/users-authentication" "${CORE_DIR}" "renamed core"
  assert_eq "${workspace}/discoveryng-node" "${NODE_DIR}" "renamed node"
  assert_eq "${workspace}/financial-rails" "${RAILS_DIR}" "renamed rails"
  assert_eq "${workspace}/krinse-engine" "${KFE_DIR}" "renamed kfe"
  assert_eq "${workspace}/server-administration" "${ADMIN_DIR}" "renamed admin"
)

test_flat_short_layout
test_flat_prefixed_layout
test_grouped_layout
test_flat_renamed_services_layout
test_override_and_worktree_marker

echo "[PASS] polyrepo workspace resolution"

