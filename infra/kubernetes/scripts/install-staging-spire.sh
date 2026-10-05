#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GITOPS_USER="system:serviceaccount:kerosene-gitops:kerosene-deployer"

fail() {
  echo "[!] SPIRE staging install failed: $*" >&2
  exit 1
}

command -v "$KUBECTL" >/dev/null 2>&1 || fail "kubectl is required"

for image_var in SERVER_IMAGE KFE_SERVICE_IMAGE VAULT_IMAGE NODE_IMAGE TOR_IMAGE; do
  image_ref="${!image_var:-}"
  [[ "$image_ref" =~ ^.+@sha256:[0-9a-f]{64}$ ]] \
    || fail "${image_var} must be an immutable image reference ending in @sha256:<64 lowercase hex chars>"
done

image_repository() {
  local image_ref="$1"
  printf '%s' "${image_ref%@sha256:*}"
}

auth_repository="$(image_repository "$SERVER_IMAGE")"
kfe_repository="$(image_repository "$KFE_SERVICE_IMAGE")"
vault_repository="$(image_repository "$VAULT_IMAGE")"
node_repository="$(image_repository "$NODE_IMAGE")"
tor_repository="$(image_repository "$TOR_IMAGE")"

echo "[*] Installing the pinned SPIRE CRD, namespaces and control-plane RBAC..."
"$KUBECTL" apply --server-side --field-manager=kerosene-security-bootstrap \
  -k "$K8S_ROOT/components/spire/bootstrap"

admission_exists=0
if "$KUBECTL" get validatingadmissionpolicy/kerosene-spiffe-namespaces >/dev/null 2>&1; then
  admission_exists=1
fi

if [[ "$admission_exists" == "0" ]]; then
  echo "[*] Creating the two reviewed trust-domain namespaces before admission activation..."
  "$KUBECTL" apply --server-side --field-manager=kerosene-security-bootstrap \
    -f "$K8S_ROOT/overlays/staging/namespace.yaml"
  "$KUBECTL" apply --server-side --field-manager=kerosene-security-bootstrap \
    -f "$K8S_ROOT/overlays/staging-vault/namespace.yaml"

  "$KUBECTL" annotate namespace kerosene-staging --overwrite \
    "kerosene.io/approved-auth-image=${auth_repository}" \
    "kerosene.io/approved-kfe-image=${kfe_repository}" \
    "kerosene.io/approved-node-image=${node_repository}" \
    "kerosene.io/approved-tor-image=${tor_repository}"
  "$KUBECTL" annotate namespace kerosene-staging-vault --overwrite \
    "kerosene.io/approved-vault-image=${vault_repository}" \
    "kerosene.io/approved-node-image=${node_repository}" \
    "kerosene.io/approved-tor-image=${tor_repository}"
else
  echo "[*] Admission is already active; verifying its immutable image repositories..."
  expected_annotations=(
    "kerosene-staging|kerosene.io/approved-auth-image|${auth_repository}"
    "kerosene-staging|kerosene.io/approved-kfe-image|${kfe_repository}"
    "kerosene-staging|kerosene.io/approved-node-image|${node_repository}"
    "kerosene-staging|kerosene.io/approved-tor-image|${tor_repository}"
    "kerosene-staging-vault|kerosene.io/approved-vault-image|${vault_repository}"
    "kerosene-staging-vault|kerosene.io/approved-node-image|${node_repository}"
    "kerosene-staging-vault|kerosene.io/approved-tor-image|${tor_repository}"
  )
  for entry in "${expected_annotations[@]}"; do
    IFS='|' read -r namespace annotation expected <<<"$entry"
    actual="$(
      "$KUBECTL" get namespace "$namespace" \
        -o "go-template={{ index .metadata.annotations \"${annotation}\" }}"
    )"
    [[ "$actual" == "$expected" ]] \
      || fail "${namespace} approves ${actual:-<empty>} for ${annotation}; immutable expected value is ${expected}"
  done
fi

trusted_namespaces="$(
  "$KUBECTL" get namespaces \
    -l kerosene.io/spire-trust-domain=staging.kerosene.internal \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort
)"
[[ "$trusted_namespaces" == $'kerosene-staging\nkerosene-staging-vault' ]] \
  || fail "unexpected namespace already carries the staging SPIRE trust-domain label"

echo "[*] Activating fail-closed admission and the least-privilege GitOps identity..."
"$KUBECTL" apply --server-side --field-manager=kerosene-security-bootstrap \
  -k "$K8S_ROOT/components/spire/admission"

echo "[*] Applying the four reviewed registrations as the GitOps identity..."
"$KUBECTL" --as="$GITOPS_USER" apply --server-side \
  --field-manager=kerosene-gitops \
  -k "$K8S_ROOT/components/spire/registration"

echo "[*] Installing the isolated SPIRE server and node agent..."
"$KUBECTL" apply --server-side --field-manager=kerosene-security-bootstrap \
  -k "$K8S_ROOT/components/spire/server"
"$KUBECTL" apply --server-side --field-manager=kerosene-security-bootstrap \
  -k "$K8S_ROOT/components/spire/agent"

"$KUBECTL" -n spire-server rollout status statefulset/spire-server --timeout=10m
"$KUBECTL" -n spire-system rollout status daemonset/spire-agent --timeout=10m

KUBECTL="$KUBECTL" bash "$SCRIPT_DIR/preflight-staging-spire.sh" core
KUBECTL="$KUBECTL" bash "$SCRIPT_DIR/preflight-staging-spire.sh" vault

echo "[+] SPIRE staging identity plane, admission boundary and GitOps writer are ready."
