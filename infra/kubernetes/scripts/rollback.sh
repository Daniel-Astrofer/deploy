#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${1:-kerosene-production}"
COMPONENT="${2:-server}"
REVISION="${3:-}"
KUBECTL="${KUBECTL:-kubectl}"

case "$COMPONENT" in
  server|web-page) KIND="deployment" ;;
  mpc-sidecar) KIND="statefulset" ;;
  *) echo "Unsupported component: $COMPONENT" >&2; exit 2 ;;
esac

"$KUBECTL" -n "$NAMESPACE" rollout history "$KIND/$COMPONENT"
if [[ -n "$REVISION" ]]; then
  "$KUBECTL" -n "$NAMESPACE" rollout undo "$KIND/$COMPONENT" --to-revision="$REVISION"
else
  "$KUBECTL" -n "$NAMESPACE" rollout undo "$KIND/$COMPONENT"
fi
"$KUBECTL" -n "$NAMESPACE" rollout status "$KIND/$COMPONENT" --timeout=10m
