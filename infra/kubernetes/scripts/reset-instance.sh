#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage: $0 <namespace> <component> [mode]

component:
  server | web-page | mpc-sidecar

mode:
  rollout      Restart controller safely. Default.
  pods         Delete Pods and let the controller recreate them.
  one-pod      Delete a single Pod selected interactively/first match.

Examples:
  $0 kerosene-production server rollout
  $0 kerosene-production server pods
  $0 kerosene-production mpc-sidecar one-pod
USAGE
}

NAMESPACE="${1:-}"
COMPONENT="${2:-}"
MODE="${3:-rollout}"
KUBECTL="${KUBECTL:-kubectl}"

if [[ -z "$NAMESPACE" || -z "$COMPONENT" ]]; then
  usage
  exit 2
fi

case "$COMPONENT" in
  server|web-page) KIND="deployment" ;;
  mpc-sidecar) KIND="statefulset" ;;
  *) echo "Unsupported component: $COMPONENT" >&2; usage; exit 2 ;;
esac

case "$MODE" in
  rollout)
    "$KUBECTL" -n "$NAMESPACE" rollout restart "$KIND/$COMPONENT"
    "$KUBECTL" -n "$NAMESPACE" rollout status "$KIND/$COMPONENT" --timeout=10m
    ;;
  pods)
    "$KUBECTL" -n "$NAMESPACE" delete pod -l "app.kubernetes.io/name=$COMPONENT"
    "$KUBECTL" -n "$NAMESPACE" rollout status "$KIND/$COMPONENT" --timeout=10m
    ;;
  one-pod)
    POD="$($KUBECTL -n "$NAMESPACE" get pod -l "app.kubernetes.io/name=$COMPONENT" -o jsonpath='{.items[0].metadata.name}')"
    if [[ -z "$POD" ]]; then
      echo "No pod found for $COMPONENT in $NAMESPACE" >&2
      exit 1
    fi
    echo "Deleting pod: $POD"
    "$KUBECTL" -n "$NAMESPACE" delete pod "$POD"
    "$KUBECTL" -n "$NAMESPACE" rollout status "$KIND/$COMPONENT" --timeout=10m
    ;;
  *) echo "Unsupported mode: $MODE" >&2; usage; exit 2 ;;
esac
