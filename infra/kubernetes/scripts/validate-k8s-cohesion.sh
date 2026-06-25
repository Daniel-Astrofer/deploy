#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

legacy_server="kerosene-app"
legacy_page="web-admin"

for env in local staging production; do
  manifest="$TMP_DIR/$env.yaml"
  "$KUBECTL" kustomize "$K8S_ROOT/overlays/$env" > "$manifest"

  for required in \
    'kind: Deployment|  name: server' \
    'kind: Deployment|  name: kfe-service' \
    'kind: Deployment|  name: web-page' \
    'kind: StatefulSet|  name: mpc-sidecar' \
    'kind: Service|  name: server' \
    'kind: Service|  name: kfe-service' \
    'kind: Service|  name: web-page' \
    'kind: HorizontalPodAutoscaler|  name: server' \
    'kind: HorizontalPodAutoscaler|  name: kfe-service'; do
    kind="${required%%|*}"
    name="${required##*|}"
    if ! awk -v k="$kind" -v n="$name" '$0 == k {seen=1} seen && $0 == n {found=1} /^---$/ {seen=0} END {exit found ? 0 : 1}' "$manifest"; then
      echo "Missing $required in $env render" >&2
      exit 1
    fi
  done

  if grep -qE "${legacy_server}|${legacy_page}" "$manifest"; then
    echo "Legacy workload name found in rendered $env manifest" >&2
    exit 1
  fi

  if ! grep -q 'KFE_REMOTE_BASE_URL: http://kfe-service:8080' "$manifest"; then
    echo "Core render for $env does not point KFE remote traffic to kfe-service" >&2
    exit 1
  fi

  if ! grep -Eq 'SPRING_PROFILES_ACTIVE: (prod|staging|docker),kfe' "$manifest"; then
    echo "KFE render for $env does not activate the kfe profile" >&2
    exit 1
  fi

done

if find "$K8S_ROOT/base" -maxdepth 1 -type d \( -name "$legacy_server" -o -name "$legacy_page" \) | grep -q .; then
  echo "Legacy workload directory still exists under base/" >&2
  exit 1
fi

echo "[+] Kubernetes cohesion validation passed for local, staging and production."
