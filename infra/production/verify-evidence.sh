#!/usr/bin/env bash
set -euo pipefail

EVIDENCE_DIR="${1:?usage: verify-evidence.sh <evidence-dir> <gate>}"
GATE="${2:?usage: verify-evidence.sh <evidence-dir> <gate>}"
IDENTITY_REGEXP="${KEROSENE_EVIDENCE_CERTIFICATE_IDENTITY_REGEXP:?set trusted certificate identity regexp}"
ISSUER_REGEXP="${KEROSENE_EVIDENCE_OIDC_ISSUER_REGEXP:?set trusted OIDC issuer regexp}"

case "$GATE" in
  independent-audit|penetration-test|recovery-exercise|membership-ceremony|release-verification) ;;
  *)
    echo "[production][evidence] unsupported gate: $GATE" >&2
    exit 4
    ;;
esac

ROOT="$(realpath -e "$EVIDENCE_DIR")"
MANIFEST="$(realpath -e "$ROOT/$GATE.json")"
case "$MANIFEST" in
  "$ROOT"/*) ;;
  *)
    echo "[production][evidence] manifest escapes evidence directory" >&2
    exit 4
    ;;
esac

jq -e --arg gate "$GATE" '
  .schema_version == 1
  and .environment == "production"
  and .gate == $gate
  and .status == "passed"
  and (.issued_at | type == "string")
  and (.expires_at | type == "string")
  and (.artifact.path | type == "string" and length > 0)
  and (.artifact.sha256 | type == "string" and test("^[0-9a-f]{64}$"))
  and (.cosign_bundle | type == "string" and length > 0)
  and (.approvals | type == "array" and length >= 2)
  and ([.approvals[].identity] | all(type == "string" and length > 0))
  and ([.approvals[].identity] | unique | length >= 2)
' "$MANIFEST" >/dev/null || {
  echo "[production][evidence] invalid or unapproved manifest: $MANIFEST" >&2
  exit 4
}

issued_at="$(jq -er '.issued_at' "$MANIFEST")"
expires_at="$(jq -er '.expires_at' "$MANIFEST")"
issued_epoch="$(date -u -d "$issued_at" +%s 2>/dev/null)" || {
  echo "[production][evidence] invalid issued_at for $GATE" >&2
  exit 4
}
expires_epoch="$(date -u -d "$expires_at" +%s 2>/dev/null)" || {
  echo "[production][evidence] invalid expires_at for $GATE" >&2
  exit 4
}
now_epoch="$(date -u +%s)"
if (( issued_epoch > now_epoch || expires_epoch <= now_epoch || expires_epoch <= issued_epoch )); then
  echo "[production][evidence] evidence is not currently valid: $GATE" >&2
  exit 4
fi

resolve_evidence_path() {
  local relative="$1"
  local resolved
  if [[ "$relative" == /* ]]; then
    echo "[production][evidence] absolute evidence path rejected: $relative" >&2
    return 1
  fi
  resolved="$(realpath -e "$ROOT/$relative")"
  case "$resolved" in
    "$ROOT"/*) printf '%s\n' "$resolved" ;;
    *)
      echo "[production][evidence] path escapes evidence directory: $relative" >&2
      return 1
      ;;
  esac
}

ARTIFACT="$(resolve_evidence_path "$(jq -er '.artifact.path' "$MANIFEST")")"
BUNDLE="$(resolve_evidence_path "$(jq -er '.cosign_bundle' "$MANIFEST")")"
expected_sha="$(jq -er '.artifact.sha256' "$MANIFEST")"
actual_sha="$(sha256sum "$ARTIFACT" | awk '{print $1}')"
if [[ "$actual_sha" != "$expected_sha" ]]; then
  echo "[production][evidence] artifact digest mismatch: $GATE" >&2
  exit 4
fi

cosign verify-blob \
  --bundle "$BUNDLE" \
  --certificate-identity-regexp "$IDENTITY_REGEXP" \
  --certificate-oidc-issuer-regexp "$ISSUER_REGEXP" \
  "$ARTIFACT" >/dev/null

echo "[production][evidence] verified: $GATE"
