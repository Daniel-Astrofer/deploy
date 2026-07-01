#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "infra/scripts/common.sh is a helper. Source it from another script." >&2
  exit 1
fi

set -euo pipefail

KEROSENE_INFRA_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$KEROSENE_INFRA_SCRIPTS_DIR/../.." && pwd)"
BACKEND_DIR="${KEROSENE_BACKEND_DIR:-$REPO_ROOT/backend/kerosene}"
INFRA_DIR="${KEROSENE_INFRA_DIR:-$REPO_ROOT/infra}"
COMPOSE_FILE="${KEROSENE_COMPOSE_FILE:-$INFRA_DIR/docker/compose/local.compose.yaml}"
COMPOSE_LIMITS_FILE="${KEROSENE_COMPOSE_LIMITS_FILE:-$INFRA_DIR/docker/compose/local.limits.compose.yaml}"
ENV_FILE="${KEROSENE_ENV_FILE:-$BACKEND_DIR/.env}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-kerosene}"
COMPOSE_PARALLEL_LIMIT="${COMPOSE_PARALLEL_LIMIT:-16}"
DOCKER_WAIT_TIMEOUT_SECONDS="${DOCKER_WAIT_TIMEOUT_SECONDS:-30}"

info() { echo "[infra] $*"; }
warn() { echo "[infra][warn] $*" >&2; }
fail() { echo "[infra][error] $*" >&2; exit 1; }

require_file() {
  local file="$1"
  [[ -f "$file" ]] || fail "Required file not found: $file"
}

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "Docker CLI not found."
  docker_is_available && return 0
  ensure_docker_service_started
  wait_for_docker || fail "Docker daemon was started, but did not become accessible within ${DOCKER_WAIT_TIMEOUT_SECONDS}s."
}

docker_is_available() {
  docker info >/dev/null 2>&1
}

docker_info_error() {
  docker info 2>&1 >/dev/null || true
}

ensure_docker_service_started() {
  if ! command -v systemctl >/dev/null 2>&1; then
    fail "Docker is not accessible and systemctl was not found. Start Docker manually and retry."
  fi

  local docker_error
  docker_error="$(docker_info_error)"
  if grep -Eiq "permission denied|Got permission denied|denied while trying to connect" <<<"$docker_error"; then
    fail "Docker is running, but this user cannot access it. Run with sudo or add the user to the docker group."
  fi

  info "Docker daemon is not responding. Starting docker service..."
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    systemctl start docker >/dev/null 2>&1 || fail "Failed to start Docker service with systemctl."
  elif systemctl start docker >/dev/null 2>&1; then
    :
  elif command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null; then
      sudo -n systemctl start docker >/dev/null || fail "Failed to start Docker service with sudo systemctl."
    elif [[ -t 0 ]]; then
      sudo systemctl start docker >/dev/null || fail "Failed to start Docker service with sudo systemctl."
    else
      fail "Docker is not running and sudo requires a password in this non-interactive session. Run 'sudo systemctl start docker' and retry."
    fi
  else
    fail "Docker is not running and sudo was not found. Start Docker manually and retry."
  fi
}

wait_for_docker() {
  local deadline now
  deadline=$(( $(date +%s) + DOCKER_WAIT_TIMEOUT_SECONDS ))
  while true; do
    docker_is_available && return 0
    now=$(date +%s)
    (( now >= deadline )) && return 1
    sleep 1
  done
}

load_backend_env() {
  require_file "$ENV_FILE"
  local line key value first last
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || fail "Invalid .env line: ${line%%=*}"

    key="${line%%=*}"
    value="${line#*=}"
    if [[ "${#value}" -ge 2 ]]; then
      first="${value:0:1}"
      last="${value: -1}"
      if { [[ "$first" == "'" ]] && [[ "$last" == "'" ]]; } ||
         { [[ "$first" == '"' ]] && [[ "$last" == '"' ]]; }; then
        value="${value:1:${#value}-2}"
      fi
    fi
    export "$key=$value"
  done < "$ENV_FILE"
}

detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
  elif command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(podman compose)
  else
    fail "Docker Compose was not found. Install 'docker compose' or 'docker-compose'."
  fi
}

compose_files() {
  require_file "$COMPOSE_FILE"
  printf '%s\n' "$COMPOSE_FILE"

  if [[ "${KEROSENE_COMPOSE_RESOURCE_LIMITS:-1}" != "0" ]]; then
    require_file "$COMPOSE_LIMITS_FILE"
    printf '%s\n' "$COMPOSE_LIMITS_FILE"
  fi

  if [[ -n "${KEROSENE_COMPOSE_EXTRA_FILES:-}" ]]; then
    local old_ifs="$IFS" extra
    IFS=':'
    for extra in $KEROSENE_COMPOSE_EXTRA_FILES; do
      [[ -n "$extra" ]] || continue
      require_file "$extra"
      printf '%s\n' "$extra"
    done
    IFS="$old_ifs"
  fi
}

compose() {
  require_file "$ENV_FILE"
  require_docker
  detect_compose

  local compose_file compose_args=()
  while IFS= read -r compose_file; do
    compose_args+=(-f "$compose_file")
  done < <(compose_files)

  COMPOSE_PARALLEL_LIMIT="$COMPOSE_PARALLEL_LIMIT" \
  COMPOSE_PROJECT_NAME="$COMPOSE_PROJECT_NAME" "${COMPOSE_CMD[@]}" \
    --project-name "$COMPOSE_PROJECT_NAME" \
    --env-file "$ENV_FILE" \
    "${compose_args[@]}" \
    "$@"
}

compose_command_string() {
  detect_compose
  printf '%s' "${COMPOSE_CMD[*]}"
}
