#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KUBECTL="${KUBECTL:-kubectl}"
TARGET="${KEROSENE_SPIRE_TARGET:-staging}"
EXPECTED_CRD_SHA256="f35a66a3b1dedb2793c465f03502df05035d4fb243a7d1313c5cf7dfbe3af2fd"
TMP_DIR="$(mktemp -d)"

cleanup() {
  find "$TMP_DIR" -type f -exec unlink {} \;
  rmdir "$TMP_DIR"
}
trap cleanup EXIT

fail() {
  echo "[!] SPIRE staging gate failed: $*" >&2
  exit 1
}

if [[ "$TARGET" != "staging" ]]; then
  fail "the public SPIRE component is staging-only; production requires HA storage and cluster-specific API/kubelet CIDRs"
fi

command -v "$KUBECTL" >/dev/null 2>&1 || fail "kubectl is required"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"

render() {
  local source="$1"
  local output="$2"
  "$KUBECTL" kustomize "$source" > "$output"
  [[ -s "$output" ]] || fail "empty render for $source"
}

require() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  grep -Eq "$pattern" "$file" || fail "$message"
}

reject() {
  local file="$1"
  local pattern="$2"
  local message="$3"
  if grep -Eq "$pattern" "$file"; then
    fail "$message"
  fi
}

require_count() {
  local file="$1"
  local pattern="$2"
  local expected="$3"
  local message="$4"
  local actual
  actual="$(grep -Ec "$pattern" "$file" || true)"
  [[ "$actual" -eq "$expected" ]] || fail "$message (expected $expected, got $actual)"
}

actual_crd_sha256="$(sha256sum "$K8S_ROOT/components/spire/bootstrap/clusterspiffeid-crd.yaml" | awk '{print $1}')"
[[ "$actual_crd_sha256" == "$EXPECTED_CRD_SHA256" ]] \
  || fail "vendored ClusterSPIFFEID CRD checksum changed without provenance update"

render "$K8S_ROOT/components/spire/bootstrap" "$TMP_DIR/bootstrap.yaml"
render "$K8S_ROOT/components/spire/server" "$TMP_DIR/server.yaml"
render "$K8S_ROOT/components/spire/agent" "$TMP_DIR/agent.yaml"
render "$K8S_ROOT/components/spire/admission" "$TMP_DIR/admission.yaml"
render "$K8S_ROOT/components/spire/registration" "$TMP_DIR/registration.yaml"
render "$K8S_ROOT/overlays/staging-spiffe" "$TMP_DIR/staging-spiffe.yaml"
render "$K8S_ROOT/overlays/staging-vault-spiffe" "$TMP_DIR/staging-vault-spiffe.yaml"

require "$K8S_ROOT/../runtime/web/nginx.k8s.conf" 'location ~ \^/\(kfe\|api/public/kfe\|api/admin/kfe\)' "web gateway does not own every public KFE namespace"
require "$K8S_ROOT/../runtime/web/nginx.k8s.conf" 'proxy_pass http://kerosene_core;' "web gateway does not route KFE APIs through Auth"
reject "$K8S_ROOT/../runtime/web/nginx.k8s.conf" 'kerosene_kfe|kfe-service:8080' "web gateway still routes directly to KFE"

require "$TMP_DIR/bootstrap.yaml" '^  name: spire-server$' "restricted SPIRE server namespace is missing"
require "$TMP_DIR/bootstrap.yaml" '^  name: spire-system$' "privileged SPIRE agent namespace is missing"
require "$TMP_DIR/bootstrap.yaml" 'pod-security.kubernetes.io/enforce: restricted' "server namespace is not restricted"
require "$TMP_DIR/bootstrap.yaml" 'pod-security.kubernetes.io/enforce: privileged' "agent privilege exception is missing"
require "$TMP_DIR/bootstrap.yaml" '^  - endpoints$' "controller cannot watch service endpoints"
require "$TMP_DIR/bootstrap.yaml" 'kerosene.io/security-boundary: spire-control-plane' "SPIRE server namespace boundary is not explicit"
require "$TMP_DIR/bootstrap.yaml" 'kerosene.io/security-boundary: spire-node-agent' "SPIRE agent namespace boundary is not explicit"

require_count "$TMP_DIR/admission.yaml" '^kind: ValidatingAdmissionPolicy$' 5 "SPIFFE admission policy count drifted"
require_count "$TMP_DIR/admission.yaml" '^kind: ValidatingAdmissionPolicyBinding$' 5 "SPIFFE admission binding count drifted"
require_count "$TMP_DIR/admission.yaml" '^  failurePolicy: Fail$' 5 "SPIFFE admission is not uniformly fail-closed"
require_count "$TMP_DIR/admission.yaml" '^  validationActions:$' 5 "SPIFFE admission bindings do not all declare enforcement actions"
require_count "$TMP_DIR/admission.yaml" '^  - Deny$' 5 "SPIFFE admission bindings do not all deny invalid requests"
require_count "$TMP_DIR/admission.yaml" '^  - Audit$' 5 "SPIFFE admission bindings do not all audit invalid requests"
require "$TMP_DIR/admission.yaml" 'system:serviceaccount:kerosene-gitops:kerosene-deployer' "dedicated GitOps writer identity is missing"
require "$TMP_DIR/admission.yaml" 'system:serviceaccount:kube-system:deployment-controller' "Deployment owner controller identity is not constrained"
require "$TMP_DIR/admission.yaml" 'system:serviceaccount:kube-system:replicaset-controller' "ReplicaSet owner controller identity is not constrained"
require "$TMP_DIR/admission.yaml" 'system:serviceaccount:kube-system:statefulset-controller' "StatefulSet owner controller identity is not constrained"
require "$TMP_DIR/admission.yaml" 'kerosene.io/workload-identity-boundary' "identity namespace boundary selector is missing"
require "$TMP_DIR/admission.yaml" 'kerosene.io/approved-auth-image' "Auth image repository allow-list is missing"
require "$TMP_DIR/admission.yaml" 'kerosene.io/approved-kfe-image' "KFE image repository allow-list is missing"
require "$TMP_DIR/admission.yaml" 'kerosene.io/approved-vault-image' "Vault image repository allow-list is missing"
require "$TMP_DIR/admission.yaml" 'kerosene.io/approved-node-image' "Node image repository allow-list is missing"
require "$TMP_DIR/admission.yaml" 'kerosene.io/approved-tor-image' "Tor image repository allow-list is missing"
require "$TMP_DIR/admission.yaml" 'resources:$' "GitOps RBAC resources are missing"
reject "$TMP_DIR/admission.yaml" 'namespace: spire-(system|server)' "application GitOps identity is bound inside a SPIRE control-plane namespace"

require_count "$TMP_DIR/server.yaml" 'image: .+@sha256:[0-9a-f]{64}$' 2 "SPIRE server images are not all immutable"
require_count "$TMP_DIR/agent.yaml" 'image: .+@sha256:[0-9a-f]{64}$' 3 "SPIRE agent/CSI images are not all immutable"
require "$TMP_DIR/server.yaml" 'trust_domain = "staging.kerosene.internal"' "server trust domain drifted"
require "$TMP_DIR/server.yaml" 'socket_path = "/tmp/spire-server/private/api.sock"' "server admin socket path drifted"
require "$TMP_DIR/server.yaml" 'mountPath: /spire-server' "controller cannot reach the server admin socket"
require "$TMP_DIR/server.yaml" 'spireServerSocketPath: /spire-server/api.sock' "controller server socket path drifted"
require "$TMP_DIR/server.yaml" 'name: ctrl-health' "controller health port is absent or exceeds the Kubernetes name limit"
reject "$TMP_DIR/server.yaml" 'controller-health' "controller health port exceeds the Kubernetes 15-character name limit"
require "$TMP_DIR/server.yaml" 'name: ENABLE_WEBHOOKS' "unused controller webhooks were not disabled"
grep -A1 'name: ENABLE_WEBHOOKS' "$TMP_DIR/server.yaml" | grep -q 'value: "false"' \
  || fail "controller webhooks are enabled without admission RBAC/service"
require "$TMP_DIR/server.yaml" 'clusterSPIFFEIDs: true' "ClusterSPIFFEID reconciliation is disabled"
require "$TMP_DIR/server.yaml" 'clusterFederatedTrustDomains: false' "unvendored federation CR reconciliation is enabled"
require "$TMP_DIR/server.yaml" 'clusterStaticEntries: false' "unvendored static entry reconciliation is enabled"
require "$TMP_DIR/server.yaml" 'className: kerosene-staging' "controller class scope drifted"
require "$TMP_DIR/server.yaml" 'entryIDPrefix: kerosene-staging' "controller entry ownership prefix drifted"
require "$TMP_DIR/agent.yaml" 'server_address = "spire-server.spire-server.svc"' "agent does not target the isolated server service"
require "$TMP_DIR/agent.yaml" 'socket_path = "/run/spire/sockets/spire-agent.sock"' "Workload API socket path drifted"
require_count "$TMP_DIR/agent.yaml" 'name: MY_NODE_NAME' 2 "agent and CSI driver do not both receive the node name"
require "$TMP_DIR/agent.yaml" 'automountServiceAccountToken: false' "agent pod exposes its default API token to sidecars"
require "$TMP_DIR/agent.yaml" 'token_path = "/var/run/secrets/kubelet/token"' "agent does not use the isolated kubelet token"

require_count "$TMP_DIR/registration.yaml" '^kind: ClusterSPIFFEID$' 4 "workload registration count drifted"
require_count "$TMP_DIR/registration.yaml" '^  className: kerosene-staging$' 4 "workload registrations are not controller-class scoped"
require "$TMP_DIR/registration.yaml" 'spiffe://staging.kerosene.internal/service/auth' "Auth SPIFFE ID is missing"
require "$TMP_DIR/registration.yaml" 'spiffe://staging.kerosene.internal/service/kfe' "KFE SPIFFE ID is missing"
grep -A2 'service/vault/node/' "$TMP_DIR/registration.yaml" \
  | grep -q 'vault.kerosene.io/node-id' \
  || fail "Vault identity is not node-specific"
grep -A3 'service/node/plane/' "$TMP_DIR/registration.yaml" \
  | grep -q 'kerosene.io/discovery-plane' \
  || fail "Node identity is not plane-specific"
grep -A3 'service/node/plane/' "$TMP_DIR/registration.yaml" \
  | grep -q 'PodMeta.Name' \
  || fail "Node identity is not instance-specific"
for selector in server kfe-service vault kerosene-node; do
  require "$TMP_DIR/registration.yaml" "k8s:container-name:${selector}" "container selector ${selector} is missing"
done

require_count "$TMP_DIR/staging-spiffe.yaml" 'value: unix:///run/spire/sockets/spire-agent.sock' 3 "Core workloads do not all receive the Workload API endpoint"
require_count "$TMP_DIR/staging-spiffe.yaml" 'driver: csi.spiffe.io' 3 "Core workloads do not all use the CSI socket"
require "$TMP_DIR/staging-spiffe.yaml" 'kerosene.io/workload-identity-boundary: enforced' "Core namespace is outside the admission boundary"
require "$TMP_DIR/staging-spiffe.yaml" 'docker.io/library/busybox:1.37.0@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0' "Node init image is not pinned to the reviewed digest"
require "$TMP_DIR/staging-spiffe.yaml" 'kerosene.io/spiffe-role: auth' "Auth workload label is missing"
require "$TMP_DIR/staging-spiffe.yaml" 'kerosene.io/spiffe-role: kfe' "KFE workload label is missing"
require "$TMP_DIR/staging-spiffe.yaml" 'kerosene.io/discovery-plane: bank' "Bank Node plane label is missing"
require_count "$TMP_DIR/staging-spiffe.yaml" 'name: KEROSENE_WORKLOAD_IDENTITY_ENABLED' 2 "Auth and KFE do not both enable workload identity"
require_count "$TMP_DIR/staging-spiffe.yaml" 'spiffe://staging.kerosene.internal/service/auth' 2 "Auth SPIFFE identity is not configured symmetrically"
require_count "$TMP_DIR/staging-spiffe.yaml" 'spiffe://staging.kerosene.internal/service/kfe' 2 "KFE SPIFFE identity is not configured symmetrically"
require "$TMP_DIR/staging-spiffe.yaml" 'value: https://kfe-service:8443' "Auth does not target the KFE mTLS connector"
require "$TMP_DIR/staging-spiffe.yaml" 'value: https://server:8443' "KFE does not target the Auth mTLS connector"
require_count "$TMP_DIR/staging-spiffe.yaml" 'name: internal-mtls' 4 "Auth/KFE Deployments and Services do not all expose the mTLS port"
require_count "$TMP_DIR/staging-spiffe.yaml" 'containerPort: 8443' 2 "Auth/KFE internal connectors do not both listen on 8443"
require_count "$TMP_DIR/staging-spiffe.yaml" 'port: 8443' 6 "Auth/KFE Service or NetworkPolicy mTLS port count drifted"
require "$TMP_DIR/staging-spiffe.yaml" 'name: kfe-service-secrets' "dedicated KFE secrets are not wired"
require "$TMP_DIR/staging-spiffe.yaml" 'key: fee-quote-signing-secret' "dedicated fee quote signing key is not wired"
reject "$TMP_DIR/staging-spiffe.yaml" 'KFE_INTERNAL_SHARED_SECRET|kfe-internal-shared-secret' "legacy Auth/KFE transport secret is still mounted"
reject "$TMP_DIR/staging-spiffe.yaml" 'http://(server|kfe-service):8080' "legacy Auth/KFE HTTP route is still configured"

server_network_policy="$(awk '
  BEGIN { RS = "---" }
  /(^|\n)kind: NetworkPolicy\n/ && /\n  name: server-network\n/ { print }
' "$TMP_DIR/staging-spiffe.yaml")"
kfe_network_policy="$(awk '
  BEGIN { RS = "---" }
  /(^|\n)kind: NetworkPolicy\n/ && /\n  name: kfe-service-network\n/ { print }
' "$TMP_DIR/staging-spiffe.yaml")"
web_network_policy="$(awk '
  BEGIN { RS = "---" }
  /(^|\n)kind: NetworkPolicy\n/ && /\n  name: web-page-network\n/ { print }
' "$TMP_DIR/staging-spiffe.yaml")"
[[ -n "$server_network_policy" && -n "$kfe_network_policy" && -n "$web_network_policy" ]] \
  || fail "Auth/KFE/Web NetworkPolicies are missing"
grep -A5 'app.kubernetes.io/name: kfe-service' <<<"$server_network_policy" \
  | grep -q 'port: 8443' \
  || fail "Auth NetworkPolicy does not isolate KFE traffic on 8443"
grep -A5 'app.kubernetes.io/name: server' <<<"$kfe_network_policy" \
  | grep -q 'port: 8443' \
  || fail "KFE NetworkPolicy does not isolate Auth traffic on 8443"
if grep -A5 'app.kubernetes.io/name: kfe-service' <<<"$server_network_policy" \
  | grep -q 'port: 8080'; then
  fail "Auth NetworkPolicy still permits KFE on the public connector"
fi
if grep -A5 'app.kubernetes.io/name: server' <<<"$kfe_network_policy" \
  | grep -q 'port: 8080'; then
  fail "KFE NetworkPolicy still permits Auth on the public connector"
fi
if grep -q 'app.kubernetes.io/name: kfe-service' <<<"$web_network_policy"; then
  fail "Web NetworkPolicy still permits direct KFE egress"
fi
core_node_service_account="$(awk '
  BEGIN { RS = "---" }
  /(^|\n)kind: ServiceAccount\n/ && /\n  name: kerosene-node\n/ { print }
' "$TMP_DIR/staging-spiffe.yaml")"
grep -q '^  namespace: kerosene-staging$' <<<"$core_node_service_account" \
  || fail "Core Node ServiceAccount has no explicit staging namespace"

require_count "$TMP_DIR/staging-vault-spiffe.yaml" 'value: unix:///run/spire/sockets/spire-agent.sock' 2 "Vault workloads do not all receive the Workload API endpoint"
require_count "$TMP_DIR/staging-vault-spiffe.yaml" 'driver: csi.spiffe.io' 2 "Vault workloads do not all use the CSI socket"
require "$TMP_DIR/staging-vault-spiffe.yaml" 'kerosene.io/workload-identity-boundary: enforced' "Vault namespace is outside the admission boundary"
require "$TMP_DIR/staging-vault-spiffe.yaml" 'vault.kerosene.io/node-id: staging-vault-1' "Vault node identity label is missing"
vault_node_service_account="$(awk '
  BEGIN { RS = "---" }
  /(^|\n)kind: ServiceAccount\n/ && /\n  name: kerosene-node\n/ { print }
' "$TMP_DIR/staging-vault-spiffe.yaml")"
grep -q '^  namespace: kerosene-staging-vault$' <<<"$vault_node_service_account" \
  || fail "Vault-plane Node ServiceAccount has no explicit staging namespace"
grep -A3 'name: VAULT_NODE_ID' "$TMP_DIR/staging-vault-spiffe.yaml" \
  | grep -q "fieldPath: metadata.labels\['vault.kerosene.io/node-id'\]" \
  || fail "Vault process and SPIFFE registration do not share one node ID source"

echo "[+] SPIRE staging manifests and fail-closed promotion gate passed."
