#!/bin/sh
set -eu

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../../.." && pwd)
manifest=$(mktemp)
trap 'rm -f "$manifest"' EXIT HUP INT TERM
kubectl kustomize "$ROOT/infra/kubernetes/overlays/complete-cell" > "$manifest"

for image in core kfe web-page postgres redis bitcoin lnd; do
  grep -q "image: kerosene-cell.invalid/$image:selected" "$manifest" || {
    echo "missing selected placeholder: $image" >&2
    exit 1
  }
done

if grep -q '^kind: HorizontalPodAutoscaler$' "$manifest"; then
  echo "unqualified HPA present" >&2
  exit 1
fi
if grep -q 'curlimages/curl' "$manifest"; then
  echo "unselected init image present" >&2
  exit 1
fi
grep -q '^  name: kerosene-staging$' "$manifest"
grep -q 'BITCOIN_NETWORK: testnet3' "$manifest"
grep -q 'SPRING_PROFILES_ACTIVE: staging,kfe' "$manifest"

workloads=$(grep -Ec '^kind: (Deployment|StatefulSet)$' "$manifest")
[ "$workloads" -eq 7 ] || {
  echo "expected seven noncritical application/dependency workloads, got $workloads" >&2
  exit 1
}
