#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
K8S_SCRIPTS="$ROOT/infra/kubernetes/scripts"
NS="${KEROSENE_NAMESPACE:-kerosene-local}"
KUBECTL="${KUBECTL:-kubectl}"
KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=(--kubeconfig "$KUBECONFIG")
fi

usage() {
  cat <<'USAGE'
Usage: infra/scripts/quorum.sh <start|stop|recreate|status|logs|test> [options]

Internal dispatcher for the local integrated Kerosene quorum.
Humans and agents should use infra/start.sh, infra/stop.sh, infra/recreate.sh,
infra/status.sh, infra/logs.sh, and infra/test.sh.
USAGE
}

kubectl_cmd() {
  "$KUBECTL" "${KUBECTL_ARGS[@]}" "$@"
}

scale_if_present() {
  local resource="$1"
  local replicas="$2"
  if kubectl_cmd -n "$NS" get "$resource" >/dev/null 2>&1; then
    echo "[*] Scaling $resource to $replicas"
    kubectl_cmd -n "$NS" scale "$resource" --replicas="$replicas"
  fi
}

stop_quorum() {
  echo "[*] Stopping local quorum workloads in namespace $NS"
  scale_if_present deployment/kfe-service 0
  scale_if_present deployment/server 0
  scale_if_present deployment/web-page 0
  scale_if_present deployment/tor-onion 0
  scale_if_present statefulset/mpc-sidecar 0
  scale_if_present statefulset/local-postgres 0
  scale_if_present deployment/local-redis 0
  scale_if_present deployment/local-vault 0
  scale_if_present deployment/local-bitcoin 0
  scale_if_present deployment/local-lnd-placeholder 0
  echo "[+] Local quorum workloads stopped. Persistent local data is preserved."
}

start_quorum() {
  exec bash "$K8S_SCRIPTS/apply.sh" --wait "$@"
}

recreate_quorum() {
  local no_build=0
  local args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-build)
        no_build=1
        ;;
      *)
        args+=("$1")
        ;;
    esac
    shift
  done

  stop_quorum
  if [[ "$no_build" -eq 1 ]]; then
    args=(--skip-image-import "${args[@]}")
  fi
  bash "$K8S_SCRIPTS/apply.sh" --wait "${args[@]}"
}

test_quorum() {
  bash "$K8S_SCRIPTS/validate-local-full.sh"
  local test_script
  for test_script in "$ROOT"/infra/kubernetes/tests/*.sh "$ROOT"/infra/tests/*.sh; do
    [[ -f "$test_script" ]] || continue
    bash "$test_script"
  done
}

command="${1:-}"
if [[ -z "$command" || "$command" == "-h" || "$command" == "--help" ]]; then
  usage
  exit 0
fi
shift

case "$command" in
  start)
    start_quorum "$@"
    ;;
  stop)
    stop_quorum "$@"
    ;;
  recreate)
    recreate_quorum "$@"
    ;;
  status)
    exec bash "$K8S_SCRIPTS/status.sh" "$@"
    ;;
  logs)
    exec bash "$K8S_SCRIPTS/logs.sh" "$@"
    ;;
  test)
    test_quorum "$@"
    ;;
  *)
    echo "Unsupported quorum command: $command" >&2
    usage >&2
    exit 2
    ;;
esac
