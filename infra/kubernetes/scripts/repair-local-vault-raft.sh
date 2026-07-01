#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/backend-common.sh
source "$SCRIPT_DIR/backend-common.sh"

MODE="diagnose"
CONFIRM_RESET=0

usage() {
  cat <<'EOF'
Usage: scripts/repair-local-vault-raft.sh [option]

Options:
  --diagnose
      Show Vault Raft container state and recent logs. This is the default.

  --reset-volumes
      Delete only the local Vault Raft data/bootstrap Docker volumes and
      recreate the Vault Raft bootstrap state. This is destructive for local
      Vault Raft tokens/quorum state, but does not delete PostgreSQL or Redis.

  --yes-reset-local-vault-raft
      Acknowledge local Vault Raft data loss for --reset-volumes.
      You can also set:
      KEROSENE_LOCAL_VAULT_RAFT_RESET_CONFIRMATION=KEROSENE_LOCAL_RESET_OK

  -h, --help
      Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --diagnose)
      MODE="diagnose"
      ;;
    --reset-volumes)
      MODE="reset-volumes"
      ;;
    --yes-reset-local-vault-raft)
      CONFIRM_RESET=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown option: $1"
      ;;
  esac
  shift
done

load_backend_env
require_docker

vault_raft_services=(
  vault-raft-data-init
  vault-raft-bootstrap
  vault-raft-1
  vault-raft-2
  vault-raft-3
)

app_services=(
  kerosene-app-is
  kerosene-app-ch
  kerosene-app-sg
)

vault_raft_volume_suffixes=(
  vault_raft_1_data
  vault_raft_2_data
  vault_raft_3_data
  vault_raft_bootstrap
)

diagnose_vault_raft() {
  info "Vault Raft compose status:"
  compose ps -a "${vault_raft_services[@]}" >&2 || true

  warn "Recent Vault Raft logs:"
  compose logs --no-color --tail 120 "${vault_raft_services[@]}" >&2 || true
}

find_volume_name() {
  local suffix="$1"
  local prefixed="${COMPOSE_PROJECT_NAME}_${suffix}"

  if docker volume inspect "$prefixed" >/dev/null 2>&1; then
    printf '%s\n' "$prefixed"
    return 0
  fi

  docker volume ls --format '{{.Name}}' \
    | awk -v suffix="$suffix" '$0 ~ "(^|_)" suffix "$" { print; exit }'
}

reset_vault_raft_volumes() {
  if [[ "$CONFIRM_RESET" -ne 1 &&
        "${KEROSENE_LOCAL_VAULT_RAFT_RESET_CONFIRMATION:-}" != "KEROSENE_LOCAL_RESET_OK" ]]; then
    fail "Refusing to delete Vault Raft volumes. Pass --yes-reset-local-vault-raft or set KEROSENE_LOCAL_VAULT_RAFT_RESET_CONFIRMATION=KEROSENE_LOCAL_RESET_OK."
  fi

  warn "Stopping/removing app and Vault Raft containers that reference Vault Raft volumes."
  compose stop "${app_services[@]}" "${vault_raft_services[@]}" >/dev/null 2>&1 || true
  compose rm -sf "${app_services[@]}" "${vault_raft_services[@]}" >/dev/null 2>&1 || true

  local suffix volume
  for suffix in "${vault_raft_volume_suffixes[@]}"; do
    volume="$(find_volume_name "$suffix" || true)"
    if [[ -z "$volume" ]]; then
      warn "Vault Raft volume not found for suffix: $suffix"
      continue
    fi

    warn "Deleting local Vault Raft volume: $volume"
    docker volume rm "$volume" >/dev/null
  done

  info "Recreating Vault Raft quorum/bootstrap state..."
  compose up -d vault-raft-1 vault-raft-2 vault-raft-3 vault-raft-bootstrap

  local bootstrap_container exit_file
  bootstrap_container="$(compose ps -a -q vault-raft-bootstrap 2>/dev/null | head -n 1 || true)"
  if [[ -z "$bootstrap_container" ]]; then
    fail "vault-raft-bootstrap container was not created."
  fi
  exit_file="$(mktemp "${TMPDIR:-/tmp}/kerosene-vault-raft-bootstrap-repair.XXXXXX")"

  if ! timeout 180s docker wait "$bootstrap_container" >"$exit_file" 2>/dev/null; then
    compose logs --no-color --tail 120 "${vault_raft_services[@]}" >&2 || true
    rm -f "$exit_file"
    fail "Timed out waiting for Vault Raft bootstrap repair to complete."
  fi

  local exit_code
  exit_code="$(tr -d '[:space:]' < "$exit_file" || true)"
  rm -f "$exit_file"
  if [[ "$exit_code" != "0" ]]; then
    compose logs --no-color --tail 120 "${vault_raft_services[@]}" >&2 || true
    fail "Vault Raft bootstrap repair exited with code ${exit_code:-unknown}."
  fi

  info "Vault Raft repair completed. Start the backend again with:"
  info "  bash infra/scripts/local/control.sh start"
}

case "$MODE" in
  diagnose)
    diagnose_vault_raft
    ;;
  reset-volumes)
    reset_vault_raft_volumes
    ;;
esac
