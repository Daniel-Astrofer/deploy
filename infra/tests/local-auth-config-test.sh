#!/usr/bin/env bash
set -euo pipefail
AUTH_TEST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUTH_TEST_TMP="$(mktemp -d)"
export AUTH_TEST_LOG="$AUTH_TEST_TMP/calls"
trap 'rm -f "$AUTH_TEST_LOG"; rmdir "$AUTH_TEST_TMP"' EXIT
export AUTH_TEST_WEAK=0 AUTH_TEST_DRIFT=none AUTH_TEST_DRIFT_TARGET=server
export AUTH_TEST_ALIAS=JWT_SECRET AUTH_TEST_IGNORE_PATCH=0
export KEROSENE_AUTH_JWT_ISSUER=Kerosene-Auth KEROSENE_AUTH_JWT_AUDIENCE=kerosene-app

auth_test_deployment() {
  local auth_test_target="$1" auth_test_drift=none
  if [[ "$auth_test_target" == "$AUTH_TEST_DRIFT_TARGET" ]]; then
    auth_test_drift="$AUTH_TEST_DRIFT"
    if [[ "$AUTH_TEST_IGNORE_PATCH" == 0 ]] && \
      [[ -f "$AUTH_TEST_LOG" ]] && rg -q "patch deployment $auth_test_target " "$AUTH_TEST_LOG"; then
      auth_test_drift=none
    fi
  fi
  jq -cn --arg target "$auth_test_target" --arg drift "$auth_test_drift" --arg alias "$AUTH_TEST_ALIAS" \
    --arg issuer "$KEROSENE_AUTH_JWT_ISSUER" --arg audience "$KEROSENE_AUTH_JWT_AUDIENCE" '
    {spec:{template:{spec:{containers:[{name:$target,env:[
      {name:"JWT_SECRET",valueFrom:{secretKeyRef:{name:"auth-jwt-signing",key:"JWT_SECRET"}}},
      {name:"API_SECRET_TOKEN_SECRET",valueFrom:{secretKeyRef:{name:"auth-jwt-signing",key:"JWT_SECRET"}}},
      {name:"KFE_AUTH_JWT_ISSUER",value:$issuer},
      {name:"KFE_AUTH_JWT_AUDIENCE",value:$audience}
    ]}]}}}} |
    if $drift == "missing-container" then .spec.template.spec.containers[0].name = "sidecar"
    elif $drift == "duplicate-container" then .spec.template.spec.containers += .spec.template.spec.containers
    elif $drift == "sidecar-only" then
      .spec.template.spec.containers += [.spec.template.spec.containers[0] | .name = "sidecar"] |
      .spec.template.spec.containers[0].env = []
    else .spec.template.spec.containers[0].env |= (
      if $drift == "missing" then map(select(.name != $alias))
      elif $drift == "duplicate" then . + [.[] | select(.name == $alias)]
      else map(if .name != $alias then .
        elif $drift == "literal" then {name:.name,value:"SYNTHETIC-KEY-DO-NOT-LOG"}
        elif $drift == "literal-and-reference" then .value = "SYNTHETIC-KEY-DO-NOT-LOG"
        elif $drift == "wrong-secret" then .valueFrom.secretKeyRef.name = "unrelated"
        elif $drift == "wrong-key" then .valueFrom.secretKeyRef.key = "unrelated"
        elif $drift == "optional" then .valueFrom.secretKeyRef.optional = true
        elif $drift == "explicit-required" then .valueFrom.secretKeyRef.optional = false
        elif $drift == "configmap" then .valueFrom = {configMapKeyRef:{name:"unrelated",key:"JWT_SECRET"}}
        elif $drift == "mixed-source" then .valueFrom.configMapKeyRef = {name:"unrelated",key:"JWT_SECRET"}
        elif $drift == "wrong-claim" then .value = "unexpected"
        elif $drift == "claim-reference" then del(.value) | .valueFrom = {configMapKeyRef:{name:"unrelated",key:$alias}}
        else . end)
      end)
    end'
}

auth_kubectl_stub() {
  case "$*" in
    *'get secret auth-jwt-signing'*)
      if [[ "$AUTH_TEST_WEAK" == 1 ]]; then
        printf '{"data":{"JWT_SECRET":"c2hvcnQ="}}\n'
      else
        # Synthetic fixture only, never supplied to a real cluster.
        printf '{"data":{"JWT_SECRET":"MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU2Nzg5MDE="}}\n'
      fi ;;
    *'get deployment kfe-service '*) auth_test_deployment kfe-service ;;
    *'get deployment server '*) auth_test_deployment server ;;
    *'patch '*) printf '%s\n' "$*" >> "$AUTH_TEST_LOG" ;;
    *) return 9 ;;
  esac
}
export -f auth_kubectl_stub auth_test_deployment
export KEROSENE_KUBECTL=auth_kubectl_stub
export KEROSENE_KUBECONFIG=/test/no-real-cluster
export KEROSENE_NAMESPACE=test

auth_test_no_leak() {
  if printf '%s\n' "$AUTH_TEST_OUTPUT" | rg -q 'MDEyMz|012345|c2hvcnQ|SYNTHETIC-KEY-DO-NOT-LOG'; then
    echo 'key fixture leaked in output' >&2; exit 1
  fi
  if [[ -f "$AUTH_TEST_LOG" ]] && rg -q 'MDEyMz|012345|c2hvcnQ|SYNTHETIC-KEY-DO-NOT-LOG' "$AUTH_TEST_LOG"; then
    echo 'key fixture leaked to arguments' >&2; exit 1
  fi
}
auth_test_run() {
  local auth_test_expected="$1" auth_test_mode="$2" auth_test_status=0
  AUTH_TEST_OUTPUT="$(bash "$AUTH_TEST_ROOT/infra/configure-local-auth.sh" "$auth_test_mode" 2>&1)" || auth_test_status=$?
  auth_test_no_leak
  if [[ "$auth_test_expected" == pass && "$auth_test_status" != 0 ]] || \
    [[ "$auth_test_expected" == fail && "$auth_test_status" == 0 ]]; then
    echo "unexpected $auth_test_mode result: target=$AUTH_TEST_DRIFT_TARGET alias=$AUTH_TEST_ALIAS drift=$AUTH_TEST_DRIFT" >&2
    exit 1
  fi
}
auth_test_read_only() {
  [[ ! -e "$AUTH_TEST_LOG" ]] || { echo 'read-only/preflight failure mutated state' >&2; exit 1; }
}

auth_test_run pass --check
auth_test_run pass --verify
auth_test_read_only
for AUTH_TEST_DRIFT_TARGET in server kfe-service; do
  for AUTH_TEST_ALIAS in JWT_SECRET API_SECRET_TOKEN_SECRET; do
    for AUTH_TEST_DRIFT in missing duplicate literal literal-and-reference wrong-secret wrong-key optional configmap mixed-source; do
      auth_test_run fail --verify
      auth_test_read_only
    done
    AUTH_TEST_DRIFT=explicit-required
    auth_test_run pass --verify
  done
  for AUTH_TEST_ALIAS in KFE_AUTH_JWT_ISSUER KFE_AUTH_JWT_AUDIENCE; do
    for AUTH_TEST_DRIFT in missing duplicate wrong-claim claim-reference; do
      auth_test_run fail --verify
      auth_test_read_only
    done
  done
  for AUTH_TEST_DRIFT in missing-container duplicate-container; do
    auth_test_run fail --verify
    auth_test_run fail --apply
    auth_test_read_only
  done
  AUTH_TEST_DRIFT=sidecar-only
  auth_test_run fail --verify
  auth_test_read_only
done

# Bootstrap checks prerequisites without requiring an already repaired binding.
AUTH_TEST_ALIAS=JWT_SECRET AUTH_TEST_DRIFT=literal
auth_test_run pass --check
auth_test_read_only
AUTH_TEST_WEAK=1
for auth_test_mode in --check --verify --apply; do
  auth_test_run fail "$auth_test_mode"
  auth_test_read_only
done
AUTH_TEST_WEAK=0
auth_test_run pass --apply
[[ $(wc -l < "$AUTH_TEST_LOG") == 4 ]] || { echo 'unexpected mutation count' >&2; exit 1; }
for auth_target in server kfe-service; do
  rg -q "patch deployment $auth_target .*auth-jwt-signing" "$AUTH_TEST_LOG"
  rg -q "patch configmap $auth_target-config .*JWT_SECRET.*null" "$AUTH_TEST_LOG"
done
[[ $(rg -c 'API_SECRET_TOKEN_SECRET.*secretKeyRef' "$AUTH_TEST_LOG") == 2 ]]
[[ $(rg -c 'secretKeyRef.*"optional":false' "$AUTH_TEST_LOG") == 2 ]]
[[ $(rg -c 'KFE_AUTH_JWT_ISSUER","value":"Kerosene-Auth"' "$AUTH_TEST_LOG") == 2 ]]
[[ $(rg -c 'KFE_AUTH_JWT_AUDIENCE","value":"kerosene-app"' "$AUTH_TEST_LOG") == 2 ]]

# A successful patch response is insufficient: reread and reject unchanged drift.
AUTH_TEST_IGNORE_PATCH=1
auth_test_run fail --apply
[[ $(wc -l < "$AUTH_TEST_LOG") == 8 ]] || { echo 'apply did not validate after both writes' >&2; exit 1; }

# Explicit contract overrides must remain identical on the two deployments.
AUTH_TEST_DRIFT=none
KEROSENE_AUTH_JWT_ISSUER=Fixture-Auth KEROSENE_AUTH_JWT_AUDIENCE=fixture-app
auth_test_run pass --verify

echo '[PASS] JWT preflight/verification enforce both references and claims, reject drift, and never expose key fixtures'
