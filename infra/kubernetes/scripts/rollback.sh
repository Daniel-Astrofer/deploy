#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<USAGE
Usage: $0 [--check|--apply] <namespace> <component> [revision]

The default --check mode lists revision history without changing the cluster.
Supported components: server, web-page, kfe-service.

Examples:
  $0 kerosene-production server
  $0 --apply kerosene-production server 3
USAGE
}

ACTION="--check"
if [[ "${1:-}" == "--check" || "${1:-}" == "--apply" ]]; then
  ACTION="$1"
  shift
fi

NAMESPACE="${1:-}"
COMPONENT="${2:-}"
REVISION="${3:-}"
KUBECTL="${KUBECTL:-kubectl}"

if [[ -z "$NAMESPACE" || -z "$COMPONENT" || "$#" -gt 3 ]]; then
  usage
  exit 2
fi
if [[ -n "$REVISION" && ! "$REVISION" =~ ^[1-9][0-9]*$ ]]; then
  echo "Revision must be a positive integer." >&2
  exit 2
fi

case "$COMPONENT" in
  server|web-page|kfe-service) KIND="deployment" ;;
  *) echo "Unsupported component: $COMPONENT" >&2; exit 2 ;;
esac

"$KUBECTL" -n "$NAMESPACE" rollout history "$KIND/$COMPONENT"
if [[ "$ACTION" == "--check" ]]; then
  echo "[check] no rollback was performed; rerun with --apply and a reviewed revision."
  exit 0
fi

if [[ -n "$REVISION" ]]; then
  "$KUBECTL" -n "$NAMESPACE" rollout undo "$KIND/$COMPONENT" --to-revision="$REVISION"
else
  "$KUBECTL" -n "$NAMESPACE" rollout undo "$KIND/$COMPONENT"
fi
"$KUBECTL" -n "$NAMESPACE" rollout status "$KIND/$COMPONENT" --timeout=10m
