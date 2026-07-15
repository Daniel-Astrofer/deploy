#!/usr/bin/env bash
set -euo pipefail

LOCAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=infra/scripts/common.sh
source "$LOCAL_SCRIPT_DIR/../common.sh"

usage() {
  cat <<'EOF'
Usage: infra/scripts/local/control.sh <command> [options] [compose-service...]

Commands:
  init       Check local infra prerequisites and required files.
  start      Start local infrastructure/services.
  stop       Stop local infrastructure/services.
  restart    Stop then start local infrastructure/services.
  recreate   Force-recreate local infrastructure/services.
  status     Show compose status, or dashboard with --dashboard.
  logs       Show compose logs.
  capture    Restart local infra and capture startup logs.
  backup-db  Back up running local database/cache services.
  migrate-db Apply local database migrations.
  repair-bitcoin Repair local Bitcoin Core/LND runtime volumes or reindex.
  recreate-mpc Recreate all local MPC sidecars.

Global options accepted by start/recreate/status/logs:
  --kfe        Include infra/docker/compose/local.kfe.compose.yaml.
  --no-limits  Do not include infra/docker/compose/local.limits.compose.yaml.

Examples:
  infra/scripts/local/control.sh start
  infra/scripts/local/control.sh stop --volumes
  infra/scripts/local/control.sh recreate server-wvo
  infra/scripts/local/control.sh logs --tail 200 server-wvo
  infra/scripts/local/control.sh capture --minutes 10 -- --no-build
  infra/scripts/local/control.sh backup-db
  infra/scripts/local/control.sh migrate-db
  infra/scripts/local/control.sh repair-bitcoin --reindex
  infra/scripts/local/recreate-mpc-sidecars.sh
EOF
}

apply_global_option() {
  case "$1" in
    --kfe)
      KEROSENE_COMPOSE_EXTRA_FILES="${KEROSENE_COMPOSE_EXTRA_FILES:+$KEROSENE_COMPOSE_EXTRA_FILES:}$INFRA_DIR/docker/compose/local.kfe.compose.yaml"
      export KEROSENE_COMPOSE_EXTRA_FILES
      return 0
      ;;
    --no-limits)
      export KEROSENE_COMPOSE_RESOURCE_LIMITS=0
      return 0
      ;;
  esac
  return 1
}

init_local() {
  info "Repository: $REPO_ROOT"
  info "Infra: $INFRA_DIR"
  info "Compose: $COMPOSE_FILE"
  info "Limits: $COMPOSE_LIMITS_FILE"
  info "Env: $ENV_FILE"

  require_file "$COMPOSE_FILE"
  [[ "${KEROSENE_COMPOSE_RESOURCE_LIMITS:-1}" == "0" ]] || require_file "$COMPOSE_LIMITS_FILE"

  if [[ ! -f "$ENV_FILE" ]]; then
    fail "Local env file not found: $ENV_FILE. Create it from backend/kerosene/.env.example or your secure local source."
  fi

  require_docker
  detect_compose
  info "Compose command: ${COMPOSE_CMD[*]}"
  info "Local infra prerequisites look consistent."
}

start_local() {
  local detach=1 build=1 arg
  local services=()

  while [[ $# -gt 0 ]]; do
    arg="$1"
    if apply_global_option "$arg"; then
      shift
      continue
    fi
    case "$arg" in
      --foreground) detach=0 ;;
      --no-build) build=0 ;;
      -h|--help) usage; exit 0 ;;
      *) services+=("$arg") ;;
    esac
    shift
  done

  local args=(up --remove-orphans)
  [[ "$detach" -eq 1 ]] && args+=(-d)
  [[ "$build" -eq 1 ]] && args+=(--build)
  compose "${args[@]}" "${services[@]}"
}

stop_local() {
  local remove_volumes=0 arg
  while [[ $# -gt 0 ]]; do
    arg="$1"
    if apply_global_option "$arg"; then
      shift
      continue
    fi
    case "$arg" in
      --volumes|-v) remove_volumes=1 ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown stop option: $arg" ;;
    esac
    shift
  done

  local args=(down --remove-orphans)
  [[ "$remove_volumes" -eq 1 ]] && args+=(--volumes)
  compose "${args[@]}"
}

recreate_local() {
  local build=1 arg
  local services=()

  while [[ $# -gt 0 ]]; do
    arg="$1"
    if apply_global_option "$arg"; then
      shift
      continue
    fi
    case "$arg" in
      --no-build) build=0 ;;
      -h|--help) usage; exit 0 ;;
      *) services+=("$arg") ;;
    esac
    shift
  done

  local args=(up -d --force-recreate --remove-orphans)
  [[ "$build" -eq 1 ]] && args+=(--build)
  compose "${args[@]}" "${services[@]}"
}

restart_local() {
  stop_local
  start_local "$@"
}

status_local() {
  local dashboard=0 arg
  local passthrough=()

  while [[ $# -gt 0 ]]; do
    arg="$1"
    if apply_global_option "$arg"; then
      shift
      continue
    fi
    case "$arg" in
      --dashboard) dashboard=1 ;;
      -h|--help) usage; exit 0 ;;
      *) passthrough+=("$arg") ;;
    esac
    shift
  done

  if [[ "$dashboard" -eq 1 ]]; then
    command -v python3 >/dev/null 2>&1 || fail "python3 is required for the local dashboard."
    require_file "$REPO_ROOT/scripts/status_dashboard.py"
    require_file "$ENV_FILE"
    detect_compose

    local py_args=(
      "$REPO_ROOT/scripts/status_dashboard.py"
      --compose-command "$(compose_command_string)"
      --project-name "$COMPOSE_PROJECT_NAME"
      --env-file "$ENV_FILE"
    )
    local compose_file
    while IFS= read -r compose_file; do
      py_args+=(--compose-file "$compose_file")
    done < <(compose_files)
    exec python3 "${py_args[@]}" "${passthrough[@]}"
  fi

  compose ps -a "${passthrough[@]}"
}

logs_local() {
  local follow=1 tail="${KEROSENE_LOG_TAIL:-180}" raw=0 dashboard=0 arg
  local services=()

  while [[ $# -gt 0 ]]; do
    arg="$1"
    if apply_global_option "$arg"; then
      shift
      continue
    fi
    case "$arg" in
      --dashboard) dashboard=1 ;;
      --raw|--include-noisy) raw=1 ;;
      --no-follow) follow=0 ;;
      --tail)
        shift
        [[ $# -gt 0 ]] || fail "--tail requires a number."
        tail="$1"
        ;;
      -h|--help) usage; exit 0 ;;
      *) services+=("$arg") ;;
    esac
    shift
  done

  [[ "$dashboard" -eq 0 ]] || exec bash "$LOCAL_SCRIPT_DIR/control.sh" status --dashboard
  [[ "$tail" =~ ^[0-9]+$ ]] || fail "--tail must be numeric."

  local args=(logs --no-color --tail "$tail")
  [[ "$follow" -eq 1 ]] && args+=(-f)

  if [[ "$raw" -eq 1 ]]; then
    compose "${args[@]}" "${services[@]}"
    return
  fi

  compose "${args[@]}" "${services[@]}" | awk '
    /UpdateTip: new best=/ { next }
    /checkpoint (starting|complete):/ { next }
    /Successfully deleted 0 expired ephemeral ledger transaction history records\./ { next }
    /Starting cleanup of ephemeral ledger transaction history older than/ { next }
    /Vault is already armed\./ { next }
    /Vault Raft bootstrap complete/ { next }
    /Waiting for Tor control socket/ { next }
    /Tor control socket authenticated\. Starting Vanguards/ { next }
    /Ready to accept connections tcp/ { next }
    { print; fflush() }
  '
}

command="${1:-}"
[[ -n "$command" ]] || { usage; exit 1; }
shift || true

case "$command" in
  init) init_local "$@" ;;
  start|up) start_local "$@" ;;
  stop|down) stop_local "$@" ;;
  restart) restart_local "$@" ;;
  recreate|rebuild) recreate_local "$@" ;;
  status|state|ps) status_local "$@" ;;
  logs|log) logs_local "$@" ;;
  capture|capture-logs) exec bash "$LOCAL_SCRIPT_DIR/capture-logs.sh" "$@" ;;
  backup-db|db-backup) exec bash "$LOCAL_SCRIPT_DIR/db-backup.sh" "$@" ;;
  migrate-db|db-migrate) exec bash "$LOCAL_SCRIPT_DIR/db-migrate.sh" "$@" ;;
  repair-bitcoin|repair-lnd) exec bash "$LOCAL_SCRIPT_DIR/repair-bitcoin.sh" "$@" ;;
  recreate-mpc|recreate-mpc-sidecars) exec bash "$LOCAL_SCRIPT_DIR/recreate-mpc-sidecars.sh" "$@" ;;
  -h|--help|help) usage ;;
  *) fail "Unknown local infra command: $command" ;;
esac
