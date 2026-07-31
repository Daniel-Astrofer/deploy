#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: infra/kubernetes/scripts/cleanup-stale-pods.sh [--apply]

Checks for pods with AGE > 1d and status ImagePullBackOff / ErrImagePull / Error.
Use --apply to delete them.

USAGE
}

APPLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unsupported option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

KUBECTL="${KUBECTL:-kubectl}"
KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=(--kubeconfig "$KUBECONFIG")
fi

kubectl_cmd() {
  "$KUBECTL" "${KUBECTL_ARGS[@]}" "$@"
}

stale_pods="$(kubectl_cmd get pods --all-namespaces --no-headers 2>/dev/null \
  | awk '{
    status=$4
    age=$6
    if ((status ~ /ImagePullBackOff|ErrImagePull|Error|CrashLoopBackOff/) && age ~ /d/) {
      print $1, $2, status, age
    }
  }' || true)"

if [[ -z "$stale_pods" ]]; then
  echo "[*] No stale pods found"
  exit 0
fi

echo "[*] Stale pods found:"
echo "$stale_pods"

if [[ "$APPLY" -eq 1 ]]; then
  echo "$stale_pods" | while read -r ns pod status age; do
    echo "[*] Deleting pod $ns/$pod ($status, $age)"
    kubectl_cmd -n "$ns" delete pod "$pod" --ignore-not-found
  done
  echo "[+] Cleanup complete"
else
  echo "[*] Run with --apply to delete these pods"
fi
