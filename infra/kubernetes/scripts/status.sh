#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
NS="${KEROSENE_NAMESPACE:-kerosene-local}"
ONION_KEYS_PATH="${KEROSENE_LOCAL_ONION_KEYS_PATH:-/home/omega/.local/state/kerosene/tor/keys/local-full}"
KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=(--kubeconfig "$KUBECONFIG")
fi

kubectl_cmd() {
  "$KUBECTL" "${KUBECTL_ARGS[@]}" "$@"
}

echo "[*] Kubernetes context"
kubectl_cmd config current-context

if ! kubectl_cmd get namespace "$NS" >/dev/null 2>&1; then
  echo "[!] Namespace $NS does not exist."
  echo "    Start the local runtime with: bash infra/start.sh"
  exit 0
fi

echo "[*] Local quorum workloads in $NS"
kubectl_cmd -n "$NS" get deploy,sts,pod,svc,hpa,pdb

echo "[*] Access"
echo "clear-net service exposure: disabled"
echo "onion keys: $ONION_KEYS_PATH"

if kubectl_cmd -n "$NS" get deploy/tor-onion >/dev/null 2>&1; then
  onion_hostname="$(kubectl_cmd -n "$NS" exec deploy/tor-onion -- sh -c 'cat /var/lib/tor/kerosene_service/hostname' 2>/dev/null || true)"
  if [[ -n "$onion_hostname" ]]; then
    echo "tor onion: http://$onion_hostname"
  else
    echo "tor onion: hostname not ready"
  fi
fi
