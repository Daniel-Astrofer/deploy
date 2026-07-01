#!/bin/sh
set -eu

VAULT_URL="${VAULT_URL:-http://kerosene-vault:8090/v1/vault/arm}"
MASTER_KEY="${AES_SECRET:-}"
MAX_WAIT_SECONDS="${VAULT_ARM_MAX_WAIT_SECONDS:-120}"
ARM_DIRECTORS="${VAULT_ARM_DIRECTORS:-director-1,director-2}"
DIRECTOR_SECRETS="${VAULT_DIRECTOR_HMAC_SECRETS:-}"

if [ -z "$MASTER_KEY" ]; then
  echo "[vault-arm][error] AES_SECRET is required." >&2
  exit 1
fi

signature_env_name() {
  printf '%s_ARM_SIGNATURE' "$1" | tr '[:lower:]-' '[:upper:]_'
}

director_secret() {
  director="$1"
  old_ifs="$IFS"
  IFS=","
  for entry in $DIRECTOR_SECRETS
  do
    IFS="$old_ifs"
    entry_director="${entry%%:*}"
    entry_secret="${entry#*:}"
    if [ "$entry_director" = "$director" ] && [ "$entry_secret" != "$entry" ]; then
      printf '%s' "$entry_secret"
      return 0
    fi
    IFS=","
  done
  IFS="$old_ifs"
  return 1
}

compute_signature() {
  director="$1"
  secret_b64="$(director_secret "$director" || true)"
  if [ -z "$secret_b64" ] || ! command -v openssl >/dev/null 2>&1; then
    return 1
  fi

  secret_hex="$(printf '%s' "$secret_b64" | base64 -d | od -An -tx1 | tr -d ' \n')"
  if [ -z "$secret_hex" ]; then
    return 1
  fi

  message="vault-arm:v1:${director}:${MASTER_KEY}"
  digest="$(printf '%s' "$message" | openssl dgst -sha256 -mac HMAC -macopt "hexkey:${secret_hex}" -binary | base64 | tr -d '\n')"
  printf 'v1:%s' "$digest"
}

resolve_signature() {
  director="$1"
  computed="$(compute_signature "$director" || true)"
  if [ -n "$computed" ]; then
    printf '%s' "$computed"
    return 0
  fi

  var_name="$(signature_env_name "$director")"
  eval "precomputed=\${$var_name:-}"
  if [ -n "$precomputed" ]; then
    printf '%s' "$precomputed"
    return 0
  fi

  echo "[vault-arm][error] Missing signature for ${director}. Set VAULT_DIRECTOR_HMAC_SECRETS with openssl available or ${var_name}." >&2
  return 1
}

wait_deadline=$(( $(date +%s) + MAX_WAIT_SECONDS ))

submit_approval() {
  director="$1"
  signature="$(resolve_signature "$director")"

  response="$(curl \
    --silent \
    --show-error \
    --write-out '\n%{http_code}' \
    --request POST "$VAULT_URL" \
    --header "X-Director-Id: $director" \
    --header "X-Director-Signature: $signature" \
    --header "Content-Type: application/json" \
    --data "{\"master_key\":\"$MASTER_KEY\"}")"

  http_code="$(printf '%s' "$response" | tail -n 1)"
  body="$(printf '%s' "$response" | sed '$d')"

  if echo "$body" | grep -Fqi "already armed"; then
    echo "$body"
    return 0
  fi

  case "$http_code" in
    2*)
      echo "$body"
      return 0
      ;;
    *)
      echo "$body" >&2
      return 1
      ;;
  esac
}

VAULT_BASE_URL="${VAULT_URL%/v1/vault/arm}"

echo "[vault-arm] waiting for vault endpoint..."
until curl --silent --output /dev/null \
  --connect-timeout 2 \
  --max-time 4 \
  "$VAULT_BASE_URL" 2>/dev/null
do
  if [ "$(date +%s)" -ge "$wait_deadline" ]; then
    echo "[vault-arm][error] Vault did not become reachable within ${MAX_WAIT_SECONDS}s." >&2
    exit 1
  fi

  sleep 2
done
echo "[vault-arm] vault endpoint is reachable."

old_ifs="$IFS"
IFS=","
for director in $ARM_DIRECTORS
do
  IFS="$old_ifs"
  director="$(printf '%s' "$director" | xargs)"
  echo "[vault-arm] submitting quorum approval from ${director}..."
  submit_approval "$director"
  IFS=","
done
IFS="$old_ifs"

echo "[vault-arm] vault is armed."
