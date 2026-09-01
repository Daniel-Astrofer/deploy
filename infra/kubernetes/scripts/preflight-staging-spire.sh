#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
SCOPE="${1:-}"

fail() {
  echo "[!] SPIRE preflight failed: $*" >&2
  exit 1
}

case "$SCOPE" in
  core) registrations=(kerosene-auth kerosene-kfe kerosene-node) ;;
  vault) registrations=(kerosene-vault kerosene-node) ;;
  *) fail "usage: $0 <core|vault>" ;;
esac

command -v "$KUBECTL" >/dev/null 2>&1 || fail "kubectl is required"

"$KUBECTL" get csidriver/csi.spiffe.io >/dev/null 2>&1 \
  || fail "CSIDriver csi.spiffe.io is not installed"

server_counts="$(
  "$KUBECTL" -n spire-server get statefulset/spire-server \
    -o jsonpath='{.spec.replicas} {.status.readyReplicas}' 2>/dev/null
)" || fail "SPIRE Server StatefulSet is not installed"
read -r server_desired server_ready <<<"$server_counts"
[[ "$server_desired" =~ ^[1-9][0-9]*$ && "$server_ready" == "$server_desired" ]] \
  || fail "SPIRE Server/controller is not fully ready (${server_ready:-0}/${server_desired:-0})"

agent_counts="$(
  "$KUBECTL" -n spire-system get daemonset/spire-agent \
    -o jsonpath='{.status.desiredNumberScheduled} {.status.numberReady}' 2>/dev/null
)" || fail "SPIRE Agent DaemonSet is not installed"
read -r agent_desired agent_ready <<<"$agent_counts"
[[ "$agent_desired" =~ ^[1-9][0-9]*$ && "$agent_ready" == "$agent_desired" ]] \
  || fail "SPIRE Agent does not cover every eligible node (${agent_ready:-0}/${agent_desired:-0})"

bundle="$({
  "$KUBECTL" -n spire-system get configmap/spire-bundle \
    -o jsonpath='{.data.bundle\.crt}' 2>/dev/null
})" || fail "SPIRE trust bundle ConfigMap is not readable"
[[ -n "$bundle" ]] || fail "SPIRE trust bundle is empty"

for registration in "${registrations[@]}"; do
  class_name="$({
    "$KUBECTL" get "clusterspiffeid/${registration}" \
      -o jsonpath='{.spec.className}' 2>/dev/null
  })" || fail "registration ${registration} is not installed"
  [[ "$class_name" == "kerosene-staging" ]] \
    || fail "registration ${registration} belongs to unexpected class ${class_name:-<empty>}"

  namespaces_selected="$({
    "$KUBECTL" get "clusterspiffeid/${registration}" \
      -o jsonpath='{.status.stats.namespacesSelected}' 2>/dev/null
  })" || fail "cannot read reconciliation status for ${registration}"
  [[ "$namespaces_selected" =~ ^[1-9][0-9]*$ ]] \
    || fail "registration ${registration} has not selected an authorized namespace"

  entry_failures="$({
    "$KUBECTL" get "clusterspiffeid/${registration}" \
      -o jsonpath='{.status.stats.entryFailures}' 2>/dev/null
  })" || fail "cannot read entry failures for ${registration}"
  render_failures="$({
    "$KUBECTL" get "clusterspiffeid/${registration}" \
      -o jsonpath='{.status.stats.podEntryRenderFailures}' 2>/dev/null
  })" || fail "cannot read render failures for ${registration}"
  [[ "${entry_failures:-0}" == "0" ]] \
    || fail "registration ${registration} has ${entry_failures} SPIRE entry failures"
  [[ "${render_failures:-0}" == "0" ]] \
    || fail "registration ${registration} has ${render_failures} template render failures"
done

echo "[+] SPIRE ${SCOPE} preflight passed."
