#!/usr/bin/env bash
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
SCOPE="${1:-}"

fail() {
  echo "[!] SPIRE preflight failed: $*" >&2
  exit 1
}

case "$SCOPE" in
  core)
    namespace="kerosene-staging"
    registrations=(kerosene-auth kerosene-kfe kerosene-node)
    image_annotations=(approved-auth-image approved-kfe-image approved-node-image approved-tor-image)
    ;;
  vault)
    namespace="kerosene-staging-vault"
    registrations=(kerosene-vault kerosene-node)
    image_annotations=(approved-vault-image approved-node-image approved-tor-image)
    ;;
  *) fail "usage: $0 <core|vault>" ;;
esac

command -v "$KUBECTL" >/dev/null 2>&1 || fail "kubectl is required"

gitops_user="system:serviceaccount:kerosene-gitops:kerosene-deployer"
policies=(
  kerosene-spiffe-workloads
  kerosene-spiffe-ephemeral-containers
  kerosene-spiffe-service-accounts
  kerosene-spiffe-registrations
  kerosene-spiffe-namespaces
)

"$KUBECTL" -n kerosene-gitops get serviceaccount/kerosene-deployer >/dev/null 2>&1 \
  || fail "dedicated GitOps service account is not installed"

for policy in "${policies[@]}"; do
  failure_policy="$(
    "$KUBECTL" get "validatingadmissionpolicy/${policy}" \
      -o jsonpath='{.spec.failurePolicy}' 2>/dev/null
  )" || fail "admission policy ${policy} is not installed"
  [[ "$failure_policy" == "Fail" ]] \
    || fail "admission policy ${policy} is not fail-closed"

  warnings="$(
    "$KUBECTL" get "validatingadmissionpolicy/${policy}" \
      -o jsonpath='{range .status.typeChecking.expressionWarnings[*]}{.warning}{"\n"}{end}' 2>/dev/null
  )" || fail "cannot read type-check status for admission policy ${policy}"
  [[ -z "$warnings" ]] \
    || fail "admission policy ${policy} has CEL type-check warnings"

  actions="$(
    "$KUBECTL" get "validatingadmissionpolicybinding/${policy}" \
      -o jsonpath='{range .spec.validationActions[*]}{@}{"\n"}{end}' 2>/dev/null
  )" || fail "admission binding ${policy} is not installed"
  grep -qx Deny <<<"$actions" || fail "admission binding ${policy} does not deny invalid requests"
  grep -qx Audit <<<"$actions" || fail "admission binding ${policy} does not audit invalid requests"
done

trusted_namespaces="$(
  "$KUBECTL" get namespaces \
    -l kerosene.io/spire-trust-domain=staging.kerosene.internal \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort
)" || fail "cannot enumerate namespaces selected by SPIRE"
expected_namespaces=$'kerosene-staging\nkerosene-staging-vault'
[[ "$trusted_namespaces" == "$expected_namespaces" ]] \
  || fail "SPIRE trust-domain namespace set is not exactly kerosene-staging and kerosene-staging-vault"

boundary="$(
  "$KUBECTL" get namespace "$namespace" \
    -o jsonpath='{.metadata.labels.kerosene\.io/workload-identity-boundary}' 2>/dev/null
)" || fail "cannot read identity boundary label for ${namespace}"
[[ "$boundary" == "enforced" ]] || fail "namespace ${namespace} is outside the admission boundary"

for annotation in "${image_annotations[@]}"; do
  approved="$(
    "$KUBECTL" get namespace "$namespace" \
      -o "go-template={{ index .metadata.annotations \"kerosene.io/${annotation}\" }}" 2>/dev/null
  )" || fail "cannot read image allow-list ${annotation} from ${namespace}"
  [[ -n "$approved" ]] || fail "namespace ${namespace} has no image allow-list ${annotation}"
done

[[ "$("$KUBECTL" --as="$gitops_user" auth can-i patch deployments -n "$namespace")" == "yes" ]] \
  || fail "GitOps identity cannot patch deployments in ${namespace}"
[[ "$("$KUBECTL" --as="$gitops_user" auth can-i create pods -n "$namespace")" == "no" ]] \
  || fail "GitOps identity can bypass controllers by creating Pods"
[[ "$("$KUBECTL" --as="$gitops_user" auth can-i get secrets -n "$namespace")" == "no" ]] \
  || fail "GitOps identity can read application Secrets"
[[ "$("$KUBECTL" --as="$gitops_user" auth can-i create deployments -n spire-system)" == "no" ]] \
  || fail "GitOps identity can write into the SPIRE agent namespace"
[[ "$("$KUBECTL" --as="$gitops_user" auth can-i create deployments -n spire-server)" == "no" ]] \
  || fail "GitOps identity can write into the SPIRE server namespace"

"$KUBECTL" get csidriver/csi.spiffe.io >/dev/null 2>&1 \
  || fail "CSIDriver csi.spiffe.io is not installed"

server_counts="$(
  "$KUBECTL" -n spire-server get statefulset/spire-server \
    -o jsonpath='{.spec.replicas} {.status.readyReplicas}' 2>/dev/null
)" || fail "SPIRE Server StatefulSet is not installed"
read -r server_desired server_ready <<<"$server_counts"
[[ "$server_desired" =~ ^[1-9][0-9]*$ && "$server_ready" == "$server_desired" ]] \
  || fail "SPIRE Server/controller is not fully ready (${server_ready:-0}/${server_desired:-0})"

agent_counts="$(
  "$KUBECTL" -n spire-system get daemonset/spire-agent \
    -o jsonpath='{.status.desiredNumberScheduled} {.status.numberReady}' 2>/dev/null
)" || fail "SPIRE Agent DaemonSet is not installed"
read -r agent_desired agent_ready <<<"$agent_counts"
[[ "$agent_desired" =~ ^[1-9][0-9]*$ && "$agent_ready" == "$agent_desired" ]] \
  || fail "SPIRE Agent does not cover every eligible node (${agent_ready:-0}/${agent_desired:-0})"

bundle="$({
  "$KUBECTL" -n spire-system get configmap/spire-bundle \
    -o jsonpath='{.data.bundle\.crt}' 2>/dev/null
})" || fail "SPIRE trust bundle ConfigMap is not readable"
[[ -n "$bundle" ]] || fail "SPIRE trust bundle is empty"

for registration in "${registrations[@]}"; do
  class_name="$({
    "$KUBECTL" get "clusterspiffeid/${registration}" \
      -o jsonpath='{.spec.className}' 2>/dev/null
  })" || fail "registration ${registration} is not installed"
  [[ "$class_name" == "kerosene-staging" ]] \
    || fail "registration ${registration} belongs to unexpected class ${class_name:-<empty>}"

  namespaces_selected="$({
    "$KUBECTL" get "clusterspiffeid/${registration}" \
      -o jsonpath='{.status.stats.namespacesSelected}' 2>/dev/null
  })" || fail "cannot read reconciliation status for ${registration}"
  [[ "$namespaces_selected" =~ ^[1-9][0-9]*$ ]] \
    || fail "registration ${registration} has not selected an authorized namespace"

  entry_failures="$({
    "$KUBECTL" get "clusterspiffeid/${registration}" \
      -o jsonpath='{.status.stats.entryFailures}' 2>/dev/null
  })" || fail "cannot read entry failures for ${registration}"
  render_failures="$({
    "$KUBECTL" get "clusterspiffeid/${registration}" \
      -o jsonpath='{.status.stats.podEntryRenderFailures}' 2>/dev/null
  })" || fail "cannot read render failures for ${registration}"
  [[ "${entry_failures:-0}" == "0" ]] \
    || fail "registration ${registration} has ${entry_failures} SPIRE entry failures"
  [[ "${render_failures:-0}" == "0" ]] \
    || fail "registration ${registration} has ${render_failures} template render failures"
done

echo "[+] SPIRE ${SCOPE} preflight passed."
