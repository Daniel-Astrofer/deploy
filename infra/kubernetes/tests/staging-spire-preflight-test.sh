#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PREFLIGHT="$REPO_ROOT/infra/kubernetes/scripts/preflight-staging-spire.sh"
TMP_DIR="$(mktemp -d)"

cleanup() {
  find "$TMP_DIR" -type f -exec unlink {} \;
  rmdir "$TMP_DIR/bin"
  rmdir "$TMP_DIR"
}
trap cleanup EXIT

mkdir "$TMP_DIR/bin"

cat > "$TMP_DIR/bin/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

case "$*" in
  *"get serviceaccount/kerosene-deployer"*)
    [[ "${SPIRE_TEST_MODE:-ready}" != "missing-admission" ]]
    ;;
  *"get validatingadmissionpolicy/"*".spec.failurePolicy"*)
    printf '%s' 'Fail'
    ;;
  *"get validatingadmissionpolicy/"*"expressionWarnings"*)
    if [[ "${SPIRE_TEST_MODE:-ready}" == "admission-warning" ]]; then
      printf '%s' 'synthetic CEL warning'
    fi
    ;;
  *"get validatingadmissionpolicybinding/"*)
    printf 'Deny\nAudit\n'
    ;;
  *"get namespaces"*"spire-trust-domain=staging.kerosene.internal"*)
    printf 'kerosene-staging\nkerosene-staging-vault\n'
    if [[ "${SPIRE_TEST_MODE:-ready}" == "unexpected-namespace" ]]; then
      printf 'attacker\n'
    fi
    ;;
  *"get namespace kerosene-staging"*"workload-identity-boundary"*|*"get namespace kerosene-staging-vault"*"workload-identity-boundary"*)
    printf '%s' 'enforced'
    ;;
  *"get namespace kerosene-staging"*"go-template="*|*"get namespace kerosene-staging-vault"*"go-template="*)
    printf '%s' 'registry.example/kerosene'
    ;;
  *"auth can-i patch deployments"*)
    printf '%s' 'yes'
    ;;
  *"auth can-i create pods"*)
    [[ "${SPIRE_TEST_MODE:-ready}" == "gitops-pod-bypass" ]] && printf '%s' 'yes' || printf '%s' 'no'
    ;;
  *"auth can-i get secrets"*|*"auth can-i create deployments -n spire-system"*|*"auth can-i create deployments -n spire-server"*)
    printf '%s' 'no'
    ;;
  *"get csidriver/csi.spiffe.io"*)
    [[ "${SPIRE_TEST_MODE:-ready}" != "missing-csi" ]]
    ;;
  *"statefulset/spire-server"*)
    if [[ "${SPIRE_TEST_MODE:-ready}" == "server-not-ready" ]]; then
      printf '1 0'
    else
      printf '1 1'
    fi
    ;;
  *"daemonset/spire-agent"*)
    if [[ "${SPIRE_TEST_MODE:-ready}" == "agent-not-ready" ]]; then
      printf '2 1'
    else
      printf '2 2'
    fi
    ;;
  *"configmap/spire-bundle"*)
    [[ "${SPIRE_TEST_MODE:-ready}" != "empty-bundle" ]] && printf '%s' 'synthetic-ca'
    ;;
  *"clusterspiffeid/"*".spec.className"*)
    printf '%s' 'kerosene-staging'
    ;;
  *"clusterspiffeid/"*"namespacesSelected"*)
    printf '1'
    ;;
  *"clusterspiffeid/"*"entryFailures"*)
    [[ "${SPIRE_TEST_MODE:-ready}" == "entry-failure" ]] && printf '1' || printf '0'
    ;;
  *"clusterspiffeid/"*"podEntryRenderFailures"*)
    printf '0'
    ;;
  *)
    echo "unexpected kubectl call: $*" >&2
    exit 42
    ;;
esac
EOF
chmod +x "$TMP_DIR/bin/kubectl"

KUBECTL="$TMP_DIR/bin/kubectl" bash "$PREFLIGHT" core >/dev/null
KUBECTL="$TMP_DIR/bin/kubectl" bash "$PREFLIGHT" vault >/dev/null

for mode in \
  missing-admission admission-warning unexpected-namespace gitops-pod-bypass \
  missing-csi server-not-ready agent-not-ready empty-bundle entry-failure; do
  if SPIRE_TEST_MODE="$mode" KUBECTL="$TMP_DIR/bin/kubectl" \
    bash "$PREFLIGHT" core >/dev/null 2>&1; then
    echo "[FAIL] SPIRE preflight accepted ${mode}" >&2
    exit 1
  fi
done

echo "[PASS] SPIRE rollout preflight fails closed"
