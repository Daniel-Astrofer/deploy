#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
START=0
if [[ "${1:-}" == "--start" ]]; then
  START=1
  shift
fi

fail=0
require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[production][missing] command: $1" >&2
    fail=1
  }
}
require_file() {
  [[ -f "$1" ]] || {
    echo "[production][missing] file: $1" >&2
    fail=1
  }
}

require_command kubectl
require_command kustomize
require_command cosign

OPS_DIR="${KEROSENE_PRODUCTION_OPS_DIR:-}"
if [[ -z "$OPS_DIR" || ! -d "$OPS_DIR" ]]; then
  echo "[production][missing] KEROSENE_PRODUCTION_OPS_DIR private checkout" >&2
  fail=1
else
  require_file "$OPS_DIR/kustomization.yaml"
  require_file "$OPS_DIR/approval.json"
fi

EVIDENCE_DIR="${KEROSENE_PRODUCTION_EVIDENCE_DIR:-}"
if [[ -z "$EVIDENCE_DIR" || ! -d "$EVIDENCE_DIR" ]]; then
  echo "[production][missing] KEROSENE_PRODUCTION_EVIDENCE_DIR" >&2
  fail=1
else
  for evidence in \
    independent-audit.json \
    penetration-test.json \
    recovery-exercise.json \
    membership-ceremony.json \
    release-verification.json
  do
    require_file "$EVIDENCE_DIR/$evidence"
  done
fi

for name in SERVER_IMAGE KFE_SERVICE_IMAGE WEB_PAGE_IMAGE NODE_IMAGE TOR_IMAGE; do
  value="${!name:-}"
  if [[ ! "$value" =~ @sha256:[0-9a-f]{64}$ ]]; then
    echo "[production][missing] immutable ${name}" >&2
    fail=1
  fi
done

if [[ "$fail" -ne 0 ]]; then
  echo "[production] start blocked; production gates are incomplete." >&2
  exit 3
fi

manifest="$(mktemp)"
cleanup() { rm -f "$manifest"; }
trap cleanup EXIT
kustomize build "$OPS_DIR" > "$manifest"
bash "$ROOT/scripts/check_architecture_guardrails.sh"
bash "$ROOT/infra/production/validate-manifest.sh" "$manifest"
kubectl apply --dry-run=client -f "$manifest" >/dev/null

if [[ "$START" -eq 0 ]]; then
  echo "[production] preflight passed; no resources were changed."
  exit 0
fi

if [[ "${KEROSENE_PRODUCTION_CHANGE_ID:-}" == "" ]]; then
  echo "[production][missing] KEROSENE_PRODUCTION_CHANGE_ID" >&2
  exit 3
fi

kubectl apply --server-side --dry-run=server -f "$manifest" >/dev/null
kubectl apply --server-side -f "$manifest"
echo "[production] private overlay applied; Vault signers were not activated."
