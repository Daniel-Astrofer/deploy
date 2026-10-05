#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PRODUCTION_ROOT="$ROOT/infra/production"
source "$PRODUCTION_ROOT/lib/config.sh"
source "$PRODUCTION_ROOT/lib/validation.sh"
source "$PRODUCTION_ROOT/lib/security.sh"
source "$PRODUCTION_ROOT/lib/rollout.sh"
START=0
if [[ "$#" -gt 1 || ( "$#" -eq 1 && "${1:-}" != "--start" ) ]]; then
  echo "Usage: $0 [--start]" >&2
  exit 2
fi
if [[ "${1:-}" == "--start" ]]; then
  START=1
  shift
fi

fail=0
require_commands kubectl kustomize cosign jq || fail=1

OPS_DIR="$(production_ops_dir)"
if [[ -z "$OPS_DIR" || ! -d "$OPS_DIR" ]]; then
  echo "[production][missing] KEROSENE_PRODUCTION_OPS_DIR private checkout" >&2
  fail=1
else
  require_files "$OPS_DIR/kustomization.yaml" "$OPS_DIR/approval.json" || fail=1
fi

EVIDENCE_DIR="$(production_evidence_dir)"
if [[ -z "$EVIDENCE_DIR" || ! -d "$EVIDENCE_DIR" ]]; then
  echo "[production][missing] KEROSENE_PRODUCTION_EVIDENCE_DIR" >&2
  fail=1
else
  if [[ -z "${KEROSENE_EVIDENCE_CERTIFICATE_IDENTITY_REGEXP:-}" ]]; then
    echo "[production][missing] KEROSENE_EVIDENCE_CERTIFICATE_IDENTITY_REGEXP" >&2
    fail=1
  fi
  if [[ -z "${KEROSENE_EVIDENCE_OIDC_ISSUER_REGEXP:-}" ]]; then
    echo "[production][missing] KEROSENE_EVIDENCE_OIDC_ISSUER_REGEXP" >&2
    fail=1
  fi
  for gate in \
    independent-audit \
    penetration-test \
    recovery-exercise \
    membership-ceremony \
    release-verification
  do
    require_files "$EVIDENCE_DIR/$gate.json" || fail=1
  done
fi

require_immutable_images || fail=1

if [[ "$fail" -ne 0 ]]; then
  echo "[production] start blocked; production gates are incomplete." >&2
  exit 3
fi

for gate in \
  independent-audit \
  penetration-test \
  recovery-exercise \
  membership-ceremony \
  release-verification
do
  verify_signed_evidence "$EVIDENCE_DIR" "$gate"
done

manifest="$(mktemp)"
cleanup() { rm -f "$manifest"; }
trap cleanup EXIT
kustomize build "$OPS_DIR" > "$manifest"
bash "$ROOT/infra/scripts/check_architecture_guardrails.sh"
validate_rendered_manifest "$manifest"
kubectl apply --dry-run=client -f "$manifest" >/dev/null

if [[ "$START" -eq 0 ]]; then
  echo "[production] preflight passed; no resources were changed."
  exit 0
fi

if [[ "${KEROSENE_PRODUCTION_CHANGE_ID:-}" == "" ]]; then
  echo "[production][missing] KEROSENE_PRODUCTION_CHANGE_ID" >&2
  exit 3
fi

apply_manifest "$manifest"
