#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage: $0 <namespace> <component> [--shell|--logs|--describe|--port-forward]

component:
  server | web-page | mpc-sidecar

Modes:
  --logs          Current and previous logs. Default.
  --describe      kubectl describe pod.
  --shell         Start an ephemeral debug container attached to the target Pod.
  --port-forward  Port-forward the component Service locally.

Examples:
  $0 kerosene-production server --logs
  $0 kerosene-production server --shell
  $0 kerosene-production server --port-forward
USAGE
}

NAMESPACE="${1:-}"
COMPONENT="${2:-}"
MODE="${3:---logs}"
KUBECTL="${KUBECTL:-kubectl}"
DEBUG_IMAGE="${DEBUG_IMAGE:-nicolaka/netshoot:latest}"

if [[ -z "$NAMESPACE" || -z "$COMPONENT" ]]; then
  usage
  exit 2
fi

POD="$($KUBECTL -n "$NAMESPACE" get pod -l "app.kubernetes.io/name=$COMPONENT" -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "$POD" ]]; then
  echo "No pod found for component $COMPONENT in namespace $NAMESPACE" >&2
  exit 1
fi

case "$COMPONENT" in
  server) CONTAINER="server"; LOCAL_PORT=18080; REMOTE_PORT=8080 ;;
  web-page) CONTAINER="web-page"; LOCAL_PORT=18081; REMOTE_PORT=8080 ;;
  mpc-sidecar) CONTAINER="mpc-sidecar"; LOCAL_PORT=18082; REMOTE_PORT=8081 ;;
  *) echo "Unsupported component: $COMPONENT" >&2; usage; exit 2 ;;
esac

case "$MODE" in
  --logs)
    echo "[*] Logs for $POD/$CONTAINER"
    "$KUBECTL" -n "$NAMESPACE" logs "$POD" -c "$CONTAINER" --tail=300 || true
    echo "[*] Previous logs for $POD/$CONTAINER"
    "$KUBECTL" -n "$NAMESPACE" logs "$POD" -c "$CONTAINER" --previous --tail=300 || true
    ;;
  --describe)
    "$KUBECTL" -n "$NAMESPACE" describe pod "$POD"
    ;;
  --shell)
    cat <<WARN
[*] Starting ephemeral debug container.
    Target pod: $POD
    Target container: $CONTAINER
    Debug image: $DEBUG_IMAGE

    For production, prefer DEBUG_IMAGE pinned by digest instead of latest.
WARN
    "$KUBECTL" -n "$NAMESPACE" debug -it "$POD" \
      --target="$CONTAINER" \
      --image="$DEBUG_IMAGE" \
      --share-processes
    ;;
  --port-forward)
    echo "[*] Forwarding http://127.0.0.1:$LOCAL_PORT -> svc/$COMPONENT:$REMOTE_PORT"
    "$KUBECTL" -n "$NAMESPACE" port-forward "svc/$COMPONENT" "$LOCAL_PORT:$REMOTE_PORT"
    ;;
  *) usage; exit 2 ;;
esac
