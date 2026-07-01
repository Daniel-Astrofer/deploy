#!/usr/bin/env bash
set -euo pipefail

command -v openssl >/dev/null 2>&1 || {
  echo "[secrets][error] openssl is required." >&2
  exit 1
}

rand_b64() {
  local bytes="$1"
  openssl rand -base64 "$bytes" | tr -d '\n'
}

hmac_signature() {
  local director="$1"
  local secret_b64="$2"
  local master_key_b64="$3"
  local secret_hex digest
  secret_hex="$(printf '%s' "$secret_b64" | base64 --decode | od -An -tx1 | tr -d ' \n')"
  digest="$(printf 'vault-arm:v1:%s:%s' "$director" "$master_key_b64" \
    | openssl dgst -sha256 -mac HMAC -macopt "hexkey:${secret_hex}" -binary \
    | base64 | tr -d '\n')"
  printf 'v1:%s' "$digest"
}

aes_secret="$(rand_b64 32)"
jwt_secret="$(rand_b64 64)"
password_pepper="$(rand_b64 64)"
hmac_secret="$(rand_b64 64)"
mpc_master_key="$(rand_b64 32)"
vault_cluster_attestation_secret="$(rand_b64 32)"
postgres_password="$(rand_b64 36)"
redis_password="$(rand_b64 36)"
lnd_wallet_password="$(rand_b64 32)"
director_1_secret="$(rand_b64 32)"
director_2_secret="$(rand_b64 32)"
director_3_secret="$(rand_b64 32)"

cat <<EOF
# Paste these into backend/kerosene/.env, then set domain/xpub/LND-specific values.
POSTGRES_PASSWORD=${postgres_password}
REDIS_PASSWORD=${redis_password}
AES_SECRET=${aes_secret}
JWT_SECRET=${jwt_secret}
PASSWORD_PEPPER=${password_pepper}
HMAC_SECRET_KEY=${hmac_secret}
MPC_MASTER_KEY_B64=${mpc_master_key}
VAULT_CLUSTER_ATTESTATION_SECRET=${vault_cluster_attestation_secret}
LND_WALLET_PASSWORD=${lnd_wallet_password}

VAULT_REQUIRED_APPROVALS=2
VAULT_ARM_DIRECTORS=director-1,director-2
VAULT_DIRECTOR_HMAC_SECRETS=director-1:${director_1_secret},director-2:${director_2_secret},director-3:${director_3_secret}

# Optional precomputed signatures for the Docker Compose arm container.
# They are bound to the AES_SECRET above; regenerate if AES_SECRET changes.
DIRECTOR_1_ARM_SIGNATURE=$(hmac_signature director-1 "$director_1_secret" "$aes_secret")
DIRECTOR_2_ARM_SIGNATURE=$(hmac_signature director-2 "$director_2_secret" "$aes_secret")
DIRECTOR_3_ARM_SIGNATURE=$(hmac_signature director-3 "$director_3_secret" "$aes_secret")
EOF
