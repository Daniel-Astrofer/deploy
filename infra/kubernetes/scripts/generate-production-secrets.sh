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

aes_secret="$(rand_b64 32)"
jwt_secret="$(rand_b64 64)"
password_pepper="$(rand_b64 64)"
hmac_secret="$(rand_b64 64)"
postgres_password="$(rand_b64 36)"
redis_password="$(rand_b64 36)"
lnd_wallet_password="$(rand_b64 32)"

cat <<EOF
# Store these in the approved secret manager; never commit them to a service checkout.
# Ops AES loads from AES_SECRET only; treasury custody is vault-mesh (kerosene-vault).
POSTGRES_PASSWORD=${postgres_password}
REDIS_PASSWORD=${redis_password}
AES_SECRET=${aes_secret}
JWT_SECRET=${jwt_secret}
PASSWORD_PEPPER=${password_pepper}
HMAC_SECRET_KEY=${hmac_secret}
LND_WALLET_PASSWORD=${lnd_wallet_password}
EOF
