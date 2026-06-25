#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=(--kubeconfig "$KUBECONFIG")
fi
NS="${KEROSENE_NAMESPACE:-kerosene-local}"
TIMEOUT="${KEROSENE_WAIT_TIMEOUT:-300s}"

kubectl_cmd() {
  "$KUBECTL" "${KUBECTL_ARGS[@]}" "$@"
}

wait_for() {
  local kind="$1"
  local name="$2"
  echo "[*] Waiting for $kind/$name in $NS"
  kubectl_cmd -n "$NS" rollout status "$kind/$name" --timeout="$TIMEOUT"
}

wait_for statefulset local-postgres
wait_for deployment local-redis
wait_for deployment local-vault
wait_for deployment local-bitcoin
wait_for deployment local-lnd-placeholder
wait_for statefulset mpc-sidecar
wait_for deployment server
wait_for deployment kfe-service
wait_for deployment web-page

echo "[*] Pods"
kubectl_cmd -n "$NS" get pods -o wide

echo "[*] Services"
kubectl_cmd -n "$NS" get svc

echo "[+] local-full workloads are ready or reported by rollout status as complete."
