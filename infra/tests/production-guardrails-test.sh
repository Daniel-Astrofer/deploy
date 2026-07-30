#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

cat >"$TMP_DIR/good.yaml" <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: core
spec:
  template:
    spec:
      containers:
        - name: core
          image: registry.invalid/core@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
YAML
bash "$ROOT/infra/production/validate-manifest.sh" "$TMP_DIR/good.yaml"

cat >"$TMP_DIR/public.yaml" <<'YAML'
apiVersion: v1
kind: Service
metadata:
  name: forbidden
spec:
  type: NodePort
YAML
if bash "$ROOT/infra/production/validate-manifest.sh" "$TMP_DIR/public.yaml"; then
  echo "NodePort manifest was unexpectedly accepted." >&2
  exit 1
fi

echo "Production guardrail tests passed."
