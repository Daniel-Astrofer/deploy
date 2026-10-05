#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage: $0 [--check|--apply] <namespace> <component> [mode]

component:
  server | web-page | kfe-service

mode:
  rollout      Restart controller safely. Default.
  pods         Delete Pods and let the controller recreate them.
  one-pod      Delete one selected Pod.

The default --check mode only shows the target and intended action.

Examples:
  $0 kerosene-production server rollout
  $0 --apply kerosene-production server rollout
  $0 --apply kerosene-production kfe-service one-pod
USAGE
}

ACTION="--check"
if [[ "${1:-}" == "--check" || "${1:-}" == "--apply" ]]; then
  ACTION="$1"
  shift
fi

NAMESPACE="${1:-}"
COMPONENT="${2:-}"
MODE="${3:-rollout}"
KUBECTL="${KUBECTL:-kubectl}"

if [[ -z "$NAMESPACE" || -z "$COMPONENT" || "$#" -gt 3 ]]; then
  usage
  exit 2
fi

case "$COMPONENT" in
  server|web-page|kfe-service) KIND="deployment" ;;
  *) echo "Unsupported component: $COMPONENT" >&2; usage; exit 2 ;;
esac

case "$MODE" in
  rollout|pods|one-pod) ;;
  *) echo "Unsupported mode: $MODE" >&2; usage; exit 2 ;;
esac

POD=""
if [[ "$MODE" == "one-pod" ]]; then
  POD="$("$KUBECTL" -n "$NAMESPACE" get pod \
    -l "app.kubernetes.io/name=$COMPONENT" \
    -o jsonpath='{.items[0].metadata.name}')"
  if [[ -z "$POD" ]]; then
    echo "No pod found for $COMPONENT in $NAMESPACE" >&2
    exit 1
  fi
fi

if [[ "$ACTION" == "--check" ]]; then
  "$KUBECTL" -n "$NAMESPACE" get "$KIND/$COMPONENT"
  case "$MODE" in
    rollout) echo "[check] would restart $KIND/$COMPONENT in $NAMESPACE" ;;
    pods) echo "[check] would delete Pods selected by app.kubernetes.io/name=$COMPONENT in $NAMESPACE" ;;
    one-pod) echo "[check] would delete pod/$POD in $NAMESPACE" ;;
  esac
  echo "[check] no cluster resource was changed; rerun with --apply."
  exit 0
fi

case "$MODE" in
  rollout)
    "$KUBECTL" -n "$NAMESPACE" rollout restart "$KIND/$COMPONENT"
    ;;
  pods)
    "$KUBECTL" -n "$NAMESPACE" delete pod -l "app.kubernetes.io/name=$COMPONENT"
    ;;
  one-pod)
    echo "Deleting pod: $POD"
    "$KUBECTL" -n "$NAMESPACE" delete pod "$POD"
    ;;
esac
"$KUBECTL" -n "$NAMESPACE" rollout status "$KIND/$COMPONENT" --timeout=10m
