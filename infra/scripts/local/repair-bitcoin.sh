#!/usr/bin/env bash
set -euo pipefail

LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=infra/scripts/common.sh
source "$LOCAL_SCRIPT_DIR/../common.sh"

MODE=""
CONFIRM_RESET=0

usage() {
  cat <<'EOF'
Usage: infra/scripts/local/repair-bitcoin.sh [option]

Options:
  --reindex-chainstate
      Restart the local Bitcoin Core container once with -reindex-chainstate.
      Use this only when prune mode is disabled.

  --reindex
      Restart the local Bitcoin Core container once with -reindex.
      This is the repair path Bitcoin Core requires for pruned nodes.

  --reset-volume
      Delete the local bitcoin_core_data Docker volume and start Bitcoin Core
      from a clean datadir. This is destructive and only for local dev.
      Requires KEROSENE_LOCAL_BITCOIN_RESET_CONFIRMATION=KEROSENE_LOCAL_RESET_OK
      or --yes-reset-local-bitcoin-data.

  --reset-lnd-volume
      Delete the local lnd_data Docker volume and recreate the local LND wallet.
      This is destructive and only for local dev. It requires the same reset
      confirmation as --reset-volume.

  --yes-reset-local-bitcoin-data
      Acknowledge local Bitcoin Core/LND data loss for destructive options.

  -h, --help
      Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reindex-chainstate)
      MODE="reindex-chainstate"
      ;;
    --reindex)
      MODE="reindex"
      ;;
    --reset-volume)
      MODE="reset-volume"
      ;;
    --reset-lnd-volume)
      MODE="reset-lnd-volume"
      ;;
    --yes-reset-local-bitcoin-data)
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

[[ -n "$MODE" ]] || {
  usage
  exit 1
}

require_docker
load_backend_env

case "$MODE" in
  reindex-chainstate)
    info "Restarting bitcoin-core once with -reindex-chainstate."
    BITCOIN_REINDEX_CHAINSTATE_ONCE=true compose up -d --force-recreate bitcoin-core
    info "Follow progress with: bash infra/scripts/local/control.sh logs bitcoin-core"
    ;;

  reindex)
    info "Restarting bitcoin-core once with -reindex."
    BITCOIN_REINDEX_ONCE=true compose up -d --force-recreate bitcoin-core
    info "Follow progress with: bash infra/scripts/local/control.sh logs bitcoin-core"
    ;;

  reset-volume)
    if [[ "${KEROSENE_LOCAL_BITCOIN_RESET_CONFIRMATION:-}" != "KEROSENE_LOCAL_RESET_OK" && "$CONFIRM_RESET" -ne 1 ]]; then
      fail "Refusing to delete bitcoin_core_data. Set KEROSENE_LOCAL_BITCOIN_RESET_CONFIRMATION=KEROSENE_LOCAL_RESET_OK or pass --yes-reset-local-bitcoin-data."
    fi

    warn "Stopping services that depend on the local Bitcoin Core volume."
    compose stop \
      kerosene-app-is kerosene-app-ch kerosene-app-sg \
      lnd-unlocker lnd-bootstrap lnd-neutrino bitcoin-core >/dev/null 2>&1 || true

    volume_name="${COMPOSE_PROJECT_NAME}_bitcoin_core_data"
    if ! docker volume inspect "$volume_name" >/dev/null 2>&1; then
      volume_name="$(docker volume ls --format '{{.Name}}' | grep -E '(^|_)bitcoin_core_data$' | head -n 1 || true)"
    fi

    [[ -n "$volume_name" ]] || fail "Could not find a bitcoin_core_data Docker volume."

    warn "Deleting local Bitcoin Core volume: $volume_name"
    docker volume rm "$volume_name" >/dev/null

    info "Starting Bitcoin Core and Lightning bootstrap services again."
    compose up -d bitcoin-core lnd-neutrino lnd-bootstrap lnd-unlocker
    info "When Bitcoin Core is running, start the full stack with: bash infra/scripts/local/control.sh start --no-build"
    ;;

  reset-lnd-volume)
    if [[ "${KEROSENE_LOCAL_BITCOIN_RESET_CONFIRMATION:-}" != "KEROSENE_LOCAL_RESET_OK" && "$CONFIRM_RESET" -ne 1 ]]; then
      fail "Refusing to delete lnd_data. Set KEROSENE_LOCAL_BITCOIN_RESET_CONFIRMATION=KEROSENE_LOCAL_RESET_OK or pass --yes-reset-local-bitcoin-data."
    fi

    warn "Stopping local LND services."
    compose stop lnd-unlocker lnd-bootstrap lnd-neutrino >/dev/null 2>&1 || true

    volume_name="${COMPOSE_PROJECT_NAME}_lnd_data"
    if ! docker volume inspect "$volume_name" >/dev/null 2>&1; then
      volume_name="$(docker volume ls --format '{{.Name}}' | grep -E '(^|_)lnd_data$' | head -n 1 || true)"
    fi

    [[ -n "$volume_name" ]] || fail "Could not find an lnd_data Docker volume."

    warn "Deleting local LND volume: $volume_name"
    docker volume rm "$volume_name" >/dev/null

    info "Starting LND and wallet bootstrap services again."
    compose up -d lnd-neutrino lnd-bootstrap lnd-unlocker
    ;;
esac
