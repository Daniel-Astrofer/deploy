#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
NS="${KEROSENE_NAMESPACE:-kerosene-local}"
TAIL="${KEROSENE_LOG_TAIL:-200}"
FOLLOW="${KEROSENE_LOG_FOLLOW:-1}"
SPLIT=0
TARGET="all"
TARGET_SET=0
OUTPUT_DIR=""
LOG_RECONNECT="${KEROSENE_LOG_RECONNECT:-1}"
LOG_RECONNECT_DELAY="${KEROSENE_LOG_RECONNECT_DELAY:-2}"
HOST_HOME="${KEROSENE_HOST_HOME:-/home/omega}"
DEFAULT_KUBECONFIG="${KEROSENE_DEFAULT_KUBECONFIG:-$HOST_HOME/.kube/config}"
if [[ -z "${KUBECONFIG:-}" && -f "$DEFAULT_KUBECONFIG" ]]; then
  export KUBECONFIG="$DEFAULT_KUBECONFIG"
fi
KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=(--kubeconfig "$KUBECONFIG")
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." 2>/dev/null && pwd || pwd)"

CORE_TARGETS=(server kfe-service web-page mpc-sidecar tor-onion)
FULL_TARGETS=(server kfe-service web-page mpc-sidecar tor-onion local-postgres local-redis local-vault local-bitcoin local-lnd-placeholder)

usage() {
  cat <<'USAGE'
Usage: infra/logs.sh [target] [--follow|--snapshot] [--split] [--tail N] [--output-dir DIR]

Targets:
  all, full        Complete local-full runtime.
  core             server, kfe-service, web-page, mpc-sidecar, tor-onion.
  server, kfe-service, web-page, mpc-sidecar, tor-onion
  local-postgres, local-redis, local-vault, local-bitcoin, local-lnd-placeholder

Examples:
  bash infra/logs.sh
  bash infra/logs.sh --follow --split
  bash infra/logs.sh --snapshot --tail 200
  bash infra/logs.sh server --follow
USAGE
}

set_target() {
  if [[ "$TARGET_SET" -eq 1 ]]; then
    echo "Only one logs target can be provided" >&2
    usage >&2
    exit 2
  fi
  TARGET="$1"
  TARGET_SET=1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -f|--follow)
      FOLLOW=1
      ;;
    --snapshot|--no-follow)
      FOLLOW=0
      ;;
    --split)
      SPLIT=1
      ;;
    --tail)
      shift
      [[ $# -gt 0 ]] || { echo "--tail requires a value" >&2; exit 2; }
      TAIL="$1"
      ;;
    --tail=*)
      TAIL="${1#--tail=}"
      ;;
    --output-dir)
      shift
      [[ $# -gt 0 ]] || { echo "--output-dir requires a value" >&2; exit 2; }
      OUTPUT_DIR="$1"
      ;;
    --output-dir=*)
      OUTPUT_DIR="${1#--output-dir=}"
      ;;
    --*)
      echo "Unsupported logs option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      set_target "$1"
      ;;
  esac
  shift
done

kubectl_cmd() {
  "$KUBECTL" "${KUBECTL_ARGS[@]}" "$@"
}

logs_args() {
  local resource="$1"
  local args=(-n "$NS" logs "$resource" --all-containers --tail="$TAIL")
  if [[ "$FOLLOW" -eq 1 ]]; then
    args+=(--follow)
  fi
  printf '%s\0' "${args[@]}"
}

run_logs_once() {
  local resource="$1"
  mapfile -d '' -t args < <(logs_args "$resource")
  kubectl_cmd "${args[@]}"
}

run_logs_realtime() {
  local resource="$1"
  local status

  while true; do
    set +e
    run_logs_once "$resource"
    status=$?
    set -e
    if [[ "$LOG_RECONNECT" -ne 1 ]]; then
      return "$status"
    fi
    echo "[*] Log stream ended for $resource with status $status; reconnecting in ${LOG_RECONNECT_DELAY}s..." >&2
    sleep "$LOG_RECONNECT_DELAY"
  done
}

run_logs_to_file() {
  local resource="$1"
  local file="$2"
  local status

  while true; do
    mapfile -d '' -t args < <(logs_args "$resource")
    set +e
    kubectl_cmd "${args[@]}" >>"$file" 2>&1
    status=$?
    set -e
    if [[ "$FOLLOW" -ne 1 || "$LOG_RECONNECT" -ne 1 ]]; then
      return "$status"
    fi
    printf '[*] Log stream ended for %s with status %s; reconnecting in %ss...\n' \
      "$resource" "$status" "$LOG_RECONNECT_DELAY" >>"$file"
    sleep "$LOG_RECONNECT_DELAY"
  done
}

resource_for() {
  case "$1" in
    server) echo "deployment/server" ;;
    kfe|kfe-service) echo "deployment/kfe-service" ;;
    web|web-page) echo "deployment/web-page" ;;
    mpc|mpc-sidecar) echo "statefulset/mpc-sidecar" ;;
    tor|tor-onion) echo "deployment/tor-onion" ;;
    postgres|local-postgres) echo "statefulset/local-postgres" ;;
    redis|local-redis) echo "deployment/local-redis" ;;
    vault|local-vault) echo "deployment/local-vault" ;;
    bitcoin|local-bitcoin) echo "deployment/local-bitcoin" ;;
    lnd|local-lnd-placeholder) echo "deployment/local-lnd-placeholder" ;;
    *)
      return 1
      ;;
  esac
}

targets_for() {
  case "$1" in
    all|full)
      printf '%s\n' "${FULL_TARGETS[@]}"
      ;;
    core)
      printf '%s\n' "${CORE_TARGETS[@]}"
      ;;
    *)
      resource_for "$1" >/dev/null || return 1
      printf '%s\n' "$1"
      ;;
  esac
}

show_logs() {
  local name="$1"
  local resource
  resource="$(resource_for "$name")" || {
    echo "Unknown quorum log target: $name" >&2
    usage >&2
    exit 2
  }
  echo "[*] Logs for $resource"
  if [[ "$FOLLOW" -eq 1 ]]; then
    run_logs_realtime "$resource"
  else
    run_logs_once "$resource"
  fi
}

follow_split_logs() {
  local targets=("$@")
  local timestamp
  local index_file
  local name resource file
  local files=()
  local pids=()
  local pid
  local tail_pid=""
  local status=0

  if [[ -z "$OUTPUT_DIR" ]]; then
    timestamp="$(date +%Y%m%d-%H%M%S)"
    OUTPUT_DIR="$REPO_ROOT/infra/runtime/logs/kubernetes/$timestamp"
  fi

  mkdir -p "$OUTPUT_DIR"
  index_file="$OUTPUT_DIR/index.txt"
  : > "$index_file"

  for name in "${targets[@]}"; do
    resource="$(resource_for "$name")" || {
      echo "Unknown quorum log target: $name" >&2
      usage >&2
      exit 2
    }
    file="$OUTPUT_DIR/$name.log"
    : > "$file"
    printf '%s %s %s\n' "$name" "$resource" "$file" >> "$index_file"
    files+=("$file")
    run_logs_to_file "$resource" "$file" &
    pids+=("$!")
  done

  echo "[*] Split logs directory: $OUTPUT_DIR"
  echo "[*] Index: $index_file"
  printf '[*] Files:\n'
  printf '    %s\n' "${files[@]}"

  tail -n +1 -F "${files[@]}" &
  tail_pid="$!"

  cleanup_split_logs() {
    local child
    if [[ -n "$tail_pid" ]]; then
      kill "$tail_pid" >/dev/null 2>&1 || true
    fi
    for child in "${pids[@]}"; do
      kill "$child" >/dev/null 2>&1 || true
    done
  }
  trap cleanup_split_logs INT TERM EXIT

  set +e
  for pid in "${pids[@]}"; do
    wait "$pid"
    pid_status=$?
    if [[ "$pid_status" -ne 0 ]]; then
      status="$pid_status"
    fi
  done
  kill "$tail_pid" >/dev/null 2>&1 || true
  wait "$tail_pid" >/dev/null 2>&1 || true
  set -e

  trap - INT TERM EXIT
  return "$status"
}

mapfile -t SELECTED_TARGETS < <(targets_for "$TARGET") || {
  echo "Unknown quorum log target: $TARGET" >&2
  usage >&2
  exit 2
}

if [[ "$FOLLOW" -eq 1 && ( "$TARGET" == "all" || "$TARGET" == "full" || "$TARGET" == "core" ) ]]; then
  SPLIT=1
fi

if [[ "$SPLIT" -eq 1 ]]; then
  follow_split_logs "${SELECTED_TARGETS[@]}"
elif [[ "${#SELECTED_TARGETS[@]}" -gt 1 ]]; then
  for name in "${SELECTED_TARGETS[@]}"; do
    show_logs "$name"
  done
else
  show_logs "${SELECTED_TARGETS[0]}"
fi
