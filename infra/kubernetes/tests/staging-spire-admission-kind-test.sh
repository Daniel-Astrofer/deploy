#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/spire-admission"
GITOPS_USER="system:serviceaccount:kerosene-gitops:kerosene-deployer"
KIND_NODE_IMAGE="kindest/node:v1.32.2@sha256:f226345927d7e348497136874b6d207e0b32cc52154ad8323129352923a3142f"
TEST_TMP_DIR=""
CLUSTER_NAME=""
CREATED_CLUSTER=0

fail() {
  echo "[!] SPIFFE admission Kind test failed: $*" >&2
  exit 1
}

cleanup() {
  if [[ "$CREATED_CLUSTER" == "1" && "$CLUSTER_NAME" == kerosene-admission-* ]]; then
    kind delete cluster --name "$CLUSTER_NAME" >/dev/null 2>&1 || true
  fi
  if [[ -n "$TEST_TMP_DIR" && -d "$TEST_TMP_DIR" ]]; then
    find "$TEST_TMP_DIR" -depth -type f -delete
    find "$TEST_TMP_DIR" -depth -type d -empty -delete
  fi
}
trap cleanup EXIT

for binary in kubectl jq; do
  command -v "$binary" >/dev/null 2>&1 || fail "${binary} is required"
done

if [[ "${KEROSENE_ADMISSION_TEST_USE_EXISTING_KIND:-0}" == "1" ]]; then
  current_context="$(kubectl config current-context)"
  [[ "$current_context" == kind-kerosene-admission-* ]] \
    || fail "existing context must be dedicated and named kind-kerosene-admission-*"
  [[ "$current_context" != "kind-kerosene-local" ]] \
    || fail "refusing to use kerosene-local"
  CLUSTER_NAME="${current_context#kind-}"
else
  command -v kind >/dev/null 2>&1 || fail "kind is required"
  TEST_TMP_DIR="$(mktemp -d)"
  CLUSTER_NAME="${KEROSENE_ADMISSION_TEST_CLUSTER_NAME:-kerosene-admission-$$-${RANDOM}}"
  [[ "$CLUSTER_NAME" =~ ^kerosene-admission-[a-z0-9-]+$ ]] \
    || fail "cluster name must match kerosene-admission-[a-z0-9-]+"
  [[ "$CLUSTER_NAME" != "kerosene-local" ]] || fail "refusing to use kerosene-local"
  if kind get clusters | grep -Fxq "$CLUSTER_NAME"; then
    fail "refusing to reuse existing cluster ${CLUSTER_NAME}"
  fi
  export KUBECONFIG="$TEST_TMP_DIR/kubeconfig"
  kind create cluster --name "$CLUSTER_NAME" --image "$KIND_NODE_IMAGE" \
    --kubeconfig "$KUBECONFIG" --wait 90s
  CREATED_CLUSTER=1
fi

echo "[*] Testing admission in dedicated cluster ${CLUSTER_NAME}..."

kubectl apply -f "$K8S_ROOT/overlays/staging/namespace.yaml" >/dev/null
kubectl apply -f "$K8S_ROOT/overlays/staging-vault/namespace.yaml" >/dev/null
kubectl annotate namespace kerosene-staging \
  kerosene.io/approved-auth-image=registry.example/kerosene-core \
  kerosene.io/approved-kfe-image=registry.example/kerosene-kfe \
  kerosene.io/approved-node-image=registry.example/kerosene-node \
  kerosene.io/approved-tor-image=registry.example/kerosene-tor >/dev/null
kubectl annotate namespace kerosene-staging-vault \
  kerosene.io/approved-vault-image=registry.example/kerosene-vault \
  kerosene.io/approved-node-image=registry.example/kerosene-node \
  kerosene.io/approved-tor-image=registry.example/kerosene-tor >/dev/null
kubectl apply -k "$K8S_ROOT/components/spire/bootstrap" >/dev/null
kubectl apply --server-side --dry-run=server \
  -k "$K8S_ROOT/components/spire/server" >/dev/null
kubectl apply --server-side --dry-run=server \
  -k "$K8S_ROOT/components/spire/agent" >/dev/null
kubectl apply -k "$K8S_ROOT/components/spire/admission" >/dev/null

for attempt in $(seq 1 30); do
  warnings="$(
    kubectl get validatingadmissionpolicies -o json \
      | jq '[.items[].status.typeChecking.expressionWarnings // []] | flatten | length'
  )"
  count="$(kubectl get validatingadmissionpolicies -o json | jq '.items | length')"
  if [[ "$count" == "5" && "$warnings" == "0" ]]; then
    break
  fi
  [[ "$attempt" != "30" ]] || fail "admission policies did not compile without warnings"
  sleep 1
done

expect_denied() {
  local label="$1"
  shift
  local output status
  set +e
  output="$("$@" 2>&1)"
  status=$?
  set -e
  [[ "$status" -ne 0 ]] || fail "${label} was unexpectedly admitted"
  grep -q 'ValidatingAdmissionPolicy' <<<"$output" \
    || fail "${label} failed outside the admission policy: ${output}"
  echo "[+] Denied ${label}"
}

kubectl --as="$GITOPS_USER" apply --server-side --field-manager=kerosene-gitops \
  -f "$FIXTURES/valid-auth.yaml" >/dev/null
for attempt in $(seq 1 30); do
  pod_count="$(
    kubectl -n kerosene-staging get pods \
      -l app.kubernetes.io/name=server --no-headers 2>/dev/null | wc -l
  )"
  [[ "$pod_count" -ge 1 ]] && break
  [[ "$attempt" != "30" ]] || fail "reviewed Deployment did not produce a Pod through built-in controllers"
  sleep 1
done

kubectl --as="$GITOPS_USER" apply --server-side --dry-run=server \
  --field-manager=kerosene-gitops -k "$K8S_ROOT/components/spire/registration" >/dev/null

kubectl kustomize "$K8S_ROOT/overlays/staging-spiffe" \
  | sed \
      -e 's#image: kerosene/server:staging#image: registry.example/kerosene-core@sha256:1111111111111111111111111111111111111111111111111111111111111111#' \
      -e 's#image: kerosene/kfe-service:staging#image: registry.example/kerosene-kfe@sha256:2222222222222222222222222222222222222222222222222222222222222222#' \
      -e 's#image: kerosene/node:staging#image: registry.example/kerosene-node@sha256:5555555555555555555555555555555555555555555555555555555555555555#' \
      -e 's#image: kerosene/tor:staging#image: registry.example/kerosene-tor@sha256:4444444444444444444444444444444444444444444444444444444444444444#' \
  | kubectl --as="$GITOPS_USER" apply --server-side --dry-run=server \
      --field-manager=kerosene-gitops -f - >/dev/null

kubectl kustomize "$K8S_ROOT/overlays/staging-vault-spiffe" \
  | sed \
      -e 's#image: kerosene/vault:staging#image: registry.example/kerosene-vault@sha256:3333333333333333333333333333333333333333333333333333333333333333#' \
      -e 's#image: kerosene/node:staging#image: registry.example/kerosene-node@sha256:5555555555555555555555555555555555555555555555555555555555555555#' \
      -e 's#image: kerosene/tor:staging#image: registry.example/kerosene-tor@sha256:4444444444444444444444444444444444444444444444444444444444444444#' \
  | kubectl --as="$GITOPS_USER" apply --server-side --dry-run=server \
      --field-manager=kerosene-gitops -f - >/dev/null

expect_denied untrusted-controller \
  kubectl -n kerosene-staging patch deployment/server --type=merge --dry-run=server \
    -p '{"metadata":{"annotations":{"kerosene.io/attack":"true"}}}'
expect_denied direct-pod \
  kubectl apply --server-side --dry-run=server -f "$FIXTURES/direct-auth-pod.yaml"
expect_denied direct-replicaset \
  kubectl apply --server-side --dry-run=server -f "$FIXTURES/direct-auth-replicaset.yaml"
expect_denied malicious-namespace \
  kubectl apply --server-side --dry-run=server -f "$FIXTURES/malicious-namespace.yaml"
expect_denied malicious-registration \
  kubectl --as="$GITOPS_USER" apply --server-side --dry-run=server \
    -f "$FIXTURES/malicious-registration.yaml"
expect_denied wrong-vault-node-id \
  kubectl --as="$GITOPS_USER" apply --server-side --dry-run=server \
    -f "$FIXTURES/wrong-vault-node.yaml"
expect_denied tor-socket-theft \
  kubectl --as="$GITOPS_USER" apply --server-side --dry-run=server \
    -f "$FIXTURES/tor-socket-theft.yaml"
expect_denied mutable-image \
  kubectl --as="$GITOPS_USER" -n kerosene-staging patch deployment/server \
    --type=json --dry-run=server \
    -p '[{"op":"replace","path":"/spec/template/spec/containers/0/image","value":"registry.example/kerosene-core:latest"}]'
expect_denied unapproved-image-repository \
  kubectl --as="$GITOPS_USER" -n kerosene-staging patch deployment/server \
    --type=json --dry-run=server \
    -p '[{"op":"replace","path":"/spec/template/spec/containers/0/image","value":"attacker.example/evil@sha256:2222222222222222222222222222222222222222222222222222222222222222"}]'
expect_denied socket-sidecar \
  kubectl --as="$GITOPS_USER" -n kerosene-staging patch deployment/server \
    --type=json --dry-run=server \
    -p '[{"op":"add","path":"/spec/template/spec/containers/-","value":{"name":"attacker","image":"registry.example/kerosene-core@sha256:2222222222222222222222222222222222222222222222222222222222222222","volumeMounts":[{"name":"spiffe-workload-api","mountPath":"/run/spire/sockets","readOnly":true}]}}]'
expect_denied remove-namespace-boundary \
  kubectl --as="$GITOPS_USER" label namespace kerosene-staging \
    kerosene.io/workload-identity-boundary- --dry-run=server
expect_denied mutate-image-allow-list \
  kubectl --as="$GITOPS_USER" annotate namespace kerosene-staging \
    kerosene.io/approved-auth-image=attacker.example/evil --overwrite --dry-run=server
expect_denied mutate-reserved-service-account \
  kubectl -n kerosene-staging patch serviceaccount/server --type=merge --dry-run=server \
    -p '{"metadata":{"annotations":{"kerosene.io/attack":"true"}}}'

[[ "$(kubectl --as="$GITOPS_USER" auth can-i create deployments -n spire-system)" == "no" ]] \
  || fail "GitOps identity can write into spire-system"
[[ "$(kubectl --as="$GITOPS_USER" auth can-i create deployments -n spire-server)" == "no" ]] \
  || fail "GitOps identity can write into spire-server"
[[ "$(kubectl --as="$GITOPS_USER" auth can-i create pods -n kerosene-staging)" == "no" ]] \
  || fail "GitOps identity can create Pods directly"
[[ "$(kubectl --as="$GITOPS_USER" auth can-i get secrets -n kerosene-staging)" == "no" ]] \
  || fail "GitOps identity can read Secrets"

[[ "$(kubectl get namespace kerosene-attacker --ignore-not-found -o name)" == "" ]] \
  || fail "malicious namespace exists after negative tests"
[[ "$(kubectl get clusterspiffeid kerosene-attacker-admin --ignore-not-found -o name)" == "" ]] \
  || fail "malicious ClusterSPIFFEID exists after negative tests"

echo "[+] SPIFFE admission Kind test passed: positive owner chain and negative impersonation cases are enforced."
