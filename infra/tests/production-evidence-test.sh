#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR/bin" "$TMP_DIR/evidence"

cat > "$TMP_DIR/bin/cosign" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "$*" == *"--certificate-identity-regexp trusted-identity"* ]]
[[ "$*" == *"--certificate-oidc-issuer-regexp trusted-issuer"* ]]
EOF
chmod +x "$TMP_DIR/bin/cosign"

printf 'external report\n' > "$TMP_DIR/evidence/report.pdf"
printf '{}\n' > "$TMP_DIR/evidence/report.bundle.json"
digest="$(sha256sum "$TMP_DIR/evidence/report.pdf" | awk '{print $1}')"
issued_at="$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)"
expires_at="$(date -u -d '1 hour' +%Y-%m-%dT%H:%M:%SZ)"

cat > "$TMP_DIR/evidence/independent-audit.json" <<EOF
{
  "schema_version": 1,
  "environment": "production",
  "gate": "independent-audit",
  "status": "passed",
  "issued_at": "$issued_at",
  "expires_at": "$expires_at",
  "artifact": {
    "path": "report.pdf",
    "sha256": "$digest"
  },
  "cosign_bundle": "report.bundle.json",
  "approvals": [
    {
      "identity": "auditor@example.test",
      "role": "independent-auditor",
      "approved_at": "$issued_at"
    },
    {
      "identity": "release@example.test",
      "role": "release-manager",
      "approved_at": "$issued_at"
    }
  ]
}
EOF

PATH="$TMP_DIR/bin:$PATH" \
KEROSENE_EVIDENCE_CERTIFICATE_IDENTITY_REGEXP=trusted-identity \
KEROSENE_EVIDENCE_OIDC_ISSUER_REGEXP=trusted-issuer \
  bash "$ROOT/infra/production/verify-evidence.sh" "$TMP_DIR/evidence" independent-audit

sed -i 's/"status": "passed"/"status": "failed"/' "$TMP_DIR/evidence/independent-audit.json"
if PATH="$TMP_DIR/bin:$PATH" \
  KEROSENE_EVIDENCE_CERTIFICATE_IDENTITY_REGEXP=trusted-identity \
  KEROSENE_EVIDENCE_OIDC_ISSUER_REGEXP=trusted-issuer \
  bash "$ROOT/infra/production/verify-evidence.sh" "$TMP_DIR/evidence" independent-audit
then
  echo "Failed evidence was accepted." >&2
  exit 1
fi

echo "Production evidence verification tests passed."
