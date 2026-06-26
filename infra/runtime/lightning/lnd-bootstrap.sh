#!/bin/sh
set -eu

lnd_host="${LND_HOST:-lnd-bitcoind}"
rest_port="${LND_REST_PORT:-8080}"
lnd_dir="${LND_DATA_DIR:-/lnd}"
timeout_seconds="${LND_BOOTSTRAP_TIMEOUT_SECONDS:-180}"
wallet_password="${LND_WALLET_PASSWORD:-}"
network="${BITCOIN_NETWORK:-mainnet}"
tls_server_name="${LND_TLS_SERVER_NAME:-localhost}"
app_read_gid="${LND_APP_READ_GID:-65532}"
watch_mode="${LND_BOOTSTRAP_WATCH:-false}"
watch_interval_seconds="${LND_BOOTSTRAP_WATCH_INTERVAL_SECONDS:-30}"

if [ -z "$wallet_password" ]; then
  echo "LND_WALLET_PASSWORD must be configured for bootstrap." >&2
  exit 1
fi

if [ "${#wallet_password}" -lt 8 ]; then
  echo "LND_WALLET_PASSWORD must be at least 8 characters long." >&2
  exit 1
fi

tls_cert="${lnd_dir}/tls.cert"
macaroon_path="${lnd_dir}/data/chain/bitcoin/${network}/admin.macaroon"
state_url="https://${tls_server_name}:${rest_port}/v1/state"
password_b64="$(printf '%s' "$wallet_password" | base64 | tr -d '\n')"
curl_tls_args="--cacert ${tls_cert} --connect-to ${tls_server_name}:${rest_port}:${lnd_host}:${rest_port}"
seed_backup_path="${LND_LOCAL_SEED_BACKUP_PATH:-${lnd_dir}/kerosene-local-wallet-seed.json}"

deadline=0

reset_deadline() {
  deadline=$(( $(date +%s) + timeout_seconds ))
}

read_state() {
  if [ ! -f "$tls_cert" ]; then
    return 1
  fi

  # Keep TLS verification enabled while routing the verified service identity to the Docker alias.
  curl -sS $curl_tls_args "$state_url" 2>/dev/null || return 1
}

generate_seed_payload() {
  if [ -n "${LND_CIPHER_SEED_MNEMONIC:-}" ]; then
    printf '%s' "$LND_CIPHER_SEED_MNEMONIC" | sed 's/^/[ /; s/$/ ]/'
    return 0
  fi

  seed_json="$(curl -sS $curl_tls_args "https://${tls_server_name}:${rest_port}/v1/genseed")"
  umask 077
  printf '%s\n' "$seed_json" > "$seed_backup_path"
  sed -n 's/.*"cipher_seed_mnemonic":[[:space:]]*\[\(.*\)\],[[:space:]]*"enciphered_seed".*/[\1]/p' "$seed_backup_path"
}

wait_for_state() {
  while :; do
    if state_json="$(read_state)"; then
      printf '%s' "$state_json"
      return 0
    fi

    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "Timed out waiting for LND state endpoint." >&2
      return 1
    fi

    sleep 2
  done
}

wait_for_wallet_ready() {
  while :; do
    state_json="$(wait_for_state)"

    case "$state_json" in
      *SERVER_ACTIVE*|*RPC_ACTIVE*)
        if [ -f "$macaroon_path" ]; then
          return 0
        fi
        ;;
    esac

    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "Timed out waiting for LND wallet to become ready." >&2
      return 1
    fi

    sleep 2
  done
}

fix_shared_permissions() {
  for dir in \
    "${lnd_dir}" \
    "${lnd_dir}/data" \
    "${lnd_dir}/data/chain" \
    "${lnd_dir}/data/chain/bitcoin" \
    "${lnd_dir}/data/chain/bitcoin/${network}"; do
    if [ -d "$dir" ]; then
      chgrp "$app_read_gid" "$dir" 2>/dev/null || true
      chmod 0550 "$dir"
    fi
  done

  if [ -f "$macaroon_path" ]; then
    chgrp "$app_read_gid" "$macaroon_path" 2>/dev/null || true
    chmod 0440 "$macaroon_path"
  fi

  if [ -f "$tls_cert" ]; then
    chmod 0444 "$tls_cert"
  fi
}

bootstrap_wallet() {
  reset_deadline

  while :; do
    state_json="$(wait_for_state)"

    case "$state_json" in
      *NON_EXISTING*)
        seed_payload="$(generate_seed_payload)"
        if [ -z "$seed_payload" ]; then
          echo "Unable to generate or parse LND cipher seed mnemonic." >&2
          exit 1
        fi
        curl -sS -X POST \
          $curl_tls_args \
          -H "Content-Type: application/json" \
          -d "{\"wallet_password\":\"${password_b64}\",\"cipher_seed_mnemonic\":${seed_payload},\"recovery_window\":0}" \
          "https://${tls_server_name}:${rest_port}/v1/initwallet" >/dev/null
        break
        ;;
      *LOCKED*)
        echo "LND wallet is locked. Unlocking..."
        curl -sS -X POST \
          $curl_tls_args \
          -H "Content-Type: application/json" \
          -d "{\"wallet_password\":\"${password_b64}\"}" \
          "https://${tls_server_name}:${rest_port}/v1/unlockwallet" >/dev/null
        break
        ;;
      *UNLOCKED*|*RPC_ACTIVE*|*SERVER_ACTIVE*)
        break
        ;;
      *WAITING_TO_START*|*STARTING*)
        if [ "$(date +%s)" -ge "$deadline" ]; then
          echo "Timed out waiting for LND to leave startup state: $state_json" >&2
          exit 1
        fi
        sleep 2
        ;;
      *)
        echo "Unexpected LND state payload: $state_json" >&2
        exit 1
        ;;
    esac
  done

  wait_for_wallet_ready
  fix_shared_permissions
}

bootstrap_wallet

if [ "$watch_mode" = "true" ]; then
  echo "LND wallet watch mode enabled."
  while :; do
    sleep "$watch_interval_seconds"
    if state_json="$(read_state)"; then
      case "$state_json" in
        *LOCKED*|*NON_EXISTING*)
          bootstrap_wallet
          ;;
        *UNLOCKED*|*RPC_ACTIVE*|*SERVER_ACTIVE*)
          fix_shared_permissions
          ;;
        *)
          echo "Unexpected LND state payload while watching: $state_json" >&2
          ;;
      esac
    else
      echo "Waiting for LND state endpoint while watching..." >&2
    fi
  done
fi
