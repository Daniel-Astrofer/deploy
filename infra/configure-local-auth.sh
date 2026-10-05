#!/usr/bin/env bash
# Bind Core and KFE to one provisioned JWT Secret; never generate/rotate keys implicitly.
set -euo pipefail
set +x

AUTH_KUBECTL="${KEROSENE_KUBECTL:-kubectl}"
AUTH_KUBECONFIG="${KEROSENE_KUBECONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/kerosene/kubeconfig}"
AUTH_NAMESPACE="${KEROSENE_NAMESPACE:-kerosene-local}"
AUTH_JWT_ISSUER="${KEROSENE_AUTH_JWT_ISSUER:-Kerosene-Auth}"
AUTH_JWT_AUDIENCE="${KEROSENE_AUTH_JWT_AUDIENCE:-kerosene-app}"
AUTH_MODE="${1:---check}"
[[ $# -le 1 && ( "$AUTH_MODE" == --check || "$AUTH_MODE" == --verify || "$AUTH_MODE" == --apply ) ]] || {
  echo 'usage: configure-local-auth.sh [--check|--verify|--apply]' >&2; exit 2;
}

auth_kubectl() { "$AUTH_KUBECTL" --kubeconfig "$AUTH_KUBECONFIG" -n "$AUTH_NAMESPACE" "$@"; }

auth_verify_bindings() {
  local auth_target
  for auth_target in kfe-service server; do
    if ! auth_kubectl get deployment "$auth_target" -o json 2>/dev/null | jq -e \
      --arg container "$auth_target" --arg issuer "$AUTH_JWT_ISSUER" --arg audience "$AUTH_JWT_AUDIENCE" '
      def jwt_reference($name):
        [.env[]? | select(.name == $name)] as $entries |
        ($entries | length) == 1 and
        ($entries[0] | .value == null and
          (.valueFrom | type) == "object" and
          (.valueFrom | keys) == ["secretKeyRef"] and
          .valueFrom.secretKeyRef.name == "auth-jwt-signing" and
          .valueFrom.secretKeyRef.key == "JWT_SECRET" and
          (.valueFrom.secretKeyRef.optional // false) == false);
      def claim($name; $expected):
        [.env[]? | select(.name == $name)] as $entries |
        ($entries | length) == 1 and
        ($entries[0] | .value == $expected and .valueFrom == null);
      [.spec.template.spec.containers[]? | select(.name == $container)] as $containers |
      ($containers | length) == 1 and
      ($containers[0] | jwt_reference("JWT_SECRET") and jwt_reference("API_SECRET_TOKEN_SECRET") and
        claim("KFE_AUTH_JWT_ISSUER"; $issuer) and claim("KFE_AUTH_JWT_AUDIENCE"; $audience))
    ' >/dev/null 2>&1; then
      echo "[auth] invalid or unreadable JWT bindings/claims in deployment $auth_target; no key values are displayed" >&2
      return 1
    fi
  done
}

if ! auth_kubectl get secret auth-jwt-signing -o json 2>/dev/null | jq -e '
  (.data.JWT_SECRET // "" | @base64d | gsub("^\\s+|\\s+$"; "") | utf8bytelength) >= 32
' >/dev/null 2>&1; then
  echo '[auth] provision a strong auth-jwt-signing Secret before configuring Core/KFE; no key was generated' >&2
  exit 1
fi
for auth_target in kfe-service server; do
  if ! auth_kubectl get deployment "$auth_target" -o json 2>/dev/null | jq -e --arg container "$auth_target" '
    [.spec.template.spec.containers[]? | select(.name == $container)] | length == 1
  ' >/dev/null 2>&1; then
    echo "[auth] deployment $auth_target must contain exactly one matching application container" >&2
    exit 1
  fi
done
if [[ "$AUTH_MODE" == --check ]]; then
  echo '[auth] JWT Secret and both deployment targets validated (read-only)'
  exit 0
fi
if [[ "$AUTH_MODE" == --verify ]]; then
  auth_verify_bindings
  echo '[auth] JWT Secret, explicit references and issuer/audience verified on both deployment templates (read-only)'
  exit 0
fi
for auth_target in kfe-service server; do
  # Explicit env wins over envFrom and handles Spring's direct property alias.
  auth_patch=$(jq -cn --arg container "$auth_target" --arg issuer "$AUTH_JWT_ISSUER" \
    --arg audience "$AUTH_JWT_AUDIENCE" '{spec:{template:{spec:{containers:[{
    name:$container,env:[
      {name:"JWT_SECRET",value:null,valueFrom:{configMapKeyRef:null,fieldRef:null,resourceFieldRef:null,
        secretKeyRef:{name:"auth-jwt-signing",key:"JWT_SECRET",optional:false}}},
      {name:"API_SECRET_TOKEN_SECRET",value:null,valueFrom:{configMapKeyRef:null,fieldRef:null,resourceFieldRef:null,
        secretKeyRef:{name:"auth-jwt-signing",key:"JWT_SECRET",optional:false}}},
      {name:"KFE_AUTH_JWT_ISSUER",value:$issuer,valueFrom:null},
      {name:"KFE_AUTH_JWT_AUDIENCE",value:$audience,valueFrom:null}
    ]
  }]}}}}')
  if ! auth_kubectl patch deployment "$auth_target" --type=strategic -p "$auth_patch" >/dev/null 2>&1; then
    echo "[auth] failed to bind deployment $auth_target; inspect its references without printing key values" >&2
    exit 1
  fi
  if ! auth_kubectl patch configmap "$auth_target-config" --type=merge \
    -p '{"data":{"JWT_SECRET":null,"API_SECRET_TOKEN_SECRET":null}}' >/dev/null 2>&1; then
    echo "[auth] failed to remove legacy JWT aliases from $auth_target-config" >&2
    exit 1
  fi
done
auth_verify_bindings
echo '[auth] Core/KFE JWT references and claims verified; verify both rollouts/readiness before login'
