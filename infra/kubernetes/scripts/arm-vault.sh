#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/backend-common.sh
source "$SCRIPT_DIR/backend-common.sh"

load_backend_env
require_docker

VAULT_URL="${VAULT_URL:-http://kerosene-vault-local:8090/v1/vault/arm}"
VAULT_NETWORK="${VAULT_NETWORK:-${COMPOSE_PROJECT_NAME}_net_vault}"
MASTER_KEY="${AES_SECRET:-}"
ARM_DIRECTORS="${VAULT_ARM_DIRECTORS:-director-1,director-2}"
DIRECTOR_SECRETS="${VAULT_DIRECTOR_HMAC_SECRETS:-}"

if [[ -z "$MASTER_KEY" ]]; then
  fail "AES_SECRET is missing in $ENV_FILE."
fi
if [[ -z "$DIRECTOR_SECRETS" ]]; then
  fail "VAULT_DIRECTOR_HMAC_SECRETS is missing in $ENV_FILE."
fi
command -v openssl >/dev/null 2>&1 || fail "openssl is required to compute director arm signatures."

director_secret() {
  local director="$1"
  local entry entry_director entry_secret
  IFS=',' read -ra entries <<<"$DIRECTOR_SECRETS"
  for entry in "${entries[@]}"; do
    entry_director="${entry%%:*}"
    entry_secret="${entry#*:}"
    if [[ "$entry_director" == "$director" && "$entry_secret" != "$entry" ]]; then
      printf '%s' "$entry_secret"
      return 0
    fi
  done
  return 1
}

compute_signature() {
  local director="$1"
  local secret_b64 secret_hex digest message
  secret_b64="$(director_secret "$director")" || fail "No HMAC secret configured for director '$director'."
  secret_hex="$(printf '%s' "$secret_b64" | base64 --decode | od -An -tx1 | tr -d ' \n')"
  [[ -n "$secret_hex" ]] || fail "Failed to decode HMAC secret for director '$director'."
  message="vault-arm:v1:${director}:${MASTER_KEY}"
  digest="$(printf '%s' "$message" | openssl dgst -sha256 -mac HMAC -macopt "hexkey:${secret_hex}" -binary | base64 | tr -d '\n')"
  printf 'v1:%s' "$digest"
}

echo "[vault] Arming vault through Docker network: $VAULT_NETWORK"

IFS=',' read -ra directors <<<"$ARM_DIRECTORS"
for director in "${directors[@]}"; do
  director="$(xargs <<<"$director")"
  [[ -n "$director" ]] || continue
  body=""
  http_code=""
  response=""
  signature="$(compute_signature "$director")"
  echo "[vault] Submitting quorum approval from $director..."
  response="$(docker run --rm --network "$VAULT_NETWORK" curlimages/curl:8.10.1 \
    --silent \
    --show-error \
    --write-out $'\n%{http_code}' \
    --request POST "$VAULT_URL" \
    --header "X-Director-Id: $director" \
    --header "X-Director-Signature: $signature" \
    --header "Content-Type: application/json" \
    --data "{\"master_key\":\"$MASTER_KEY\"}")" || {
      fail "Failed to contact Vault while submitting approval from $director."
    }

  http_code="${response##*$'\n'}"
  body="${response%$'\n'*}"

  if [[ "$http_code" =~ ^2 ]]; then
    printf '%s\n' "$body"
    continue
  fi

  if grep -Fqi "already armed" <<<"$body"; then
    printf '%s\n' "$body"
    continue
  fi

  printf '%s\n' "$body" >&2
  fail "Vault rejected approval from $director (HTTP $http_code)."
done

echo "[vault] Quorum submitted. If the second response says the vault is ARMED, the vault is ready."
